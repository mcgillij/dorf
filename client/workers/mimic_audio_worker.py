import os
import re
import tempfile
import asyncio
from pydub import AudioSegment
import traceback
from bot.commands import bot, redis_client

async def preprocess_text_for_audio(text: str) -> str:
    """
    Preprocesses text for audio generation by:
    1. Stripping out Discord emojis.
    2. Replacing Discord user IDs with their display names.

    Args:
        text (str): The text to preprocess.
        guild (discord.Guild): The guild object to resolve user IDs.

    Returns:
        str: The processed text.
    """
    # Regex to match custom Discord emojis (e.g., <:emoji_name:1234567890>)
    emoji_pattern = r":\w+:"
    text = re.sub(emoji_pattern, "", text)

    # Regex to match Discord user mentions (e.g., <@123456789012345678>)
    mention_pattern = r"<@!?(\d+)>"

    def replace_mention(match):
        user_id = int(match.group(1))
        print(f"User ID: {user_id}")
        user = bot.get_user(user_id)
        print(f"User: {user}")
        return user.display_name if user else f"User{user_id}"

    text = re.sub(mention_pattern, replace_mention, text)

    # Remove excess whitespace caused by emoji/mention removal
    return re.sub(r"\s+", " ", text).strip()

async def run_mimic3_subprocess(output_dir, text_file_path):
    """
    Runs the Mimic 3 subprocess asynchronously.
    """
    try:
        command = [
            "mimic3",
            "--output-naming", "id",
            "--output-dir", output_dir,
            "--csv"
        ]
        # Run subprocess
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Pass text file content to subprocess via stdin
        with open(text_file_path, "rb") as file:
            stdout, stderr = await process.communicate(input=file.read())

        if process.returncode != 0:
            raise RuntimeError(f"Mimic3 subprocess failed with error: {stderr.decode('utf-8')}")
        return stdout.decode('utf-8')
    except Exception as e:
        print(f"Error in run_mimic3_subprocess: {e}")
        traceback.print_exc()
        return None

async def mimic_audio_task():
    """
    Continuously process audio generation requests from the Redis queue.
    """
    output_dir = "/home/j/dorf/client/output/"
    while True:
        try:
            task_data = redis_client.rpop('audio_queue')
            if not task_data:
                await asyncio.sleep(1)
                continue

            unique_id, line_number, line_text = task_data.split("|", 2)
            text_file_path = None
            print(f"Processing audio generation task: {line_number} - {line_text}")
            line_text = await preprocess_text_for_audio(line_text)
            print(f"Postprocessed text: {line_text}")

            # Check the number of users in the voice channel
            num_users = len(bot.voice_clients[0].channel.members) - 1 if bot.voice_clients else 0
            if num_users < 1:
                print(f"Skipping audio generation as there are only {num_users} users in the voice channel.")
                continue

            try:
                # Create temporary text file for the line
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as text_file:
                    text_file.write(f"{line_number}|{line_text}")
                    text_file.flush()
                    text_file_path = text_file.name

                # Generate TTS audio using Mimic 3
                print(f"Generating audio for line {line_number}: {line_text}")
                await run_mimic3_subprocess(output_dir, text_file_path)

                # Path to the generated WAV file
                wav_path = os.path.join(output_dir, f"{line_number}.wav")
                if not os.path.exists(wav_path):
                    print(f"Failed to generate audio for line {line_number}: {line_text}")
                    continue

                # Convert WAV to Opus
                audio_segment = AudioSegment.from_wav(wav_path)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.opus') as temp_file:
                    opus_path = temp_file.name
                    audio_segment.export(
                        opus_path,
                        format="opus",
                        parameters=["-b:a", "128k"]
                    )
                if os.path.exists(opus_path):
                    redis_client.lpush('playback_queue', f"{unique_id}|{opus_path}")
                else:
                    print(f"Opus file does not exist: {opus_path}")

            finally:
                # Clean up temporary files
                if text_file_path and os.path.exists(text_file_path):
                    os.remove(text_file_path)
        except Exception as e:
            print(f"Error processing audio generation task: {e}")
            traceback.print_exc()
