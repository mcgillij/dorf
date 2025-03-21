import os
import re
import tempfile
import asyncio
from pydub import AudioSegment
import traceback
from bot.commands import bot, redis_client
from bot.utilities import logger

import numpy as np
from kokoro import KPipeline
import soundfile as sf

pipeline = KPipeline(lang_code="a")  # english
TTS_ENGINE = "kokoro" # or use the mimic3 docker container
TTS_VOICE = "am_adam"  # "af_nicole" funny whispering lady

def process_kokoro_audio(line_text, voice, output_wav):
    """Sync function to handle kokoro TTS generation and file writing."""
    try:
        generator = pipeline(line_text, voice)
        audio_segments = []
        for _, _, audio in generator:
            audio_segments.append(audio)

        if not audio_segments:
            raise RuntimeError("No audio generated by kokoro")

        full_audio = np.concatenate(audio_segments)  # Ensure numpy is imported
        sf.write(output_wav, full_audio, 24000)
    except Exception as e:
        logger.error(f"Kokoro processing failed: {str(e)}")
        raise

def convert_wav_to_opus(wav_path, opus_path):
    """Sync function to convert WAV to OPUS."""
    audio_segment = AudioSegment.from_wav(wav_path)
    audio_segment.export(opus_path, format="opus", parameters=["-b:a", "128k"])

async def replace_userids(text: str) -> str:
    """
    Replacing Discord user IDs with their display names.

    Args:
        text (str): The text to preprocess.

    Returns:
        str: The processed text.
    """
    # Regex to match Discord user mentions (e.g., <@123456789012345678>)
    mention_pattern = r"<@!?(\d+)>"

    def replace_mention(match):
        user_id = int(match.group(1))
        user = bot.get_user(user_id)
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
            "--output-naming",
            "id",
            "--output-dir",
            output_dir,
            "--csv",
        ]
        # Run subprocess
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Pass text file content to subprocess via stdin
        with open(text_file_path, "rb") as file:
            stdout, stderr = await process.communicate(input=file.read())

        if process.returncode != 0:
            raise RuntimeError(
                f"Mimic3 subprocess failed with error: {stderr.decode('utf-8')}"
            )
        return stdout.decode("utf-8")
    except Exception as e:
        logger.error(f"Error in run_mimic3_subprocess: {e}")
        traceback.print_exc()
        return None


async def mimic_audio_task():
    output_dir = "/home/j/dorf/client/output/"
    loop = asyncio.get_event_loop()  # Reuse the same event loop
    while True:
        task_data = await loop.run_in_executor(
            None,
            redis_client.rpop, 
            "audio_queue"
        )

        if not task_data:
            await asyncio.sleep(1)
            continue

        unique_id, line_number, line_text = task_data.split("|", 2)
        line_text = await replace_userids(line_text)  # Assuming this is async

        num_users = (
            len(bot.voice_clients[0].channel.members) - 1
            if bot.voice_clients else 0
        )

        if num_users < 1:
            logger.info(f"Skipping audio generation for {num_users} users.")
            continue

        wav_path = os.path.join(output_dir, f"{line_number}.wav")

        if TTS_ENGINE == 'mimic3':
            # Assume run_mimic3_subprocess is async and properly non-blocking
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp_file:
                tmp_file.write(line_text)
                tmp_file.flush()

                await run_mimic3_subprocess(output_dir, tmp_file.name)  # Ensure this is an awaitable

        elif TTS_ENGINE == 'kokoro':
            try:
                await loop.run_in_executor(
                    None,
                    process_kokoro_audio,
                    line_text,
                    TTS_VOICE,
                    wav_path
                )
            except Exception as e:
                logger.error(f"Kokoro error for {line_text}: {str(e)}")
                continue

        else:
            logger.warning("Unknown engine. Skipping.")
            continue

        if not os.path.exists(wav_path):
            logger.error(f"WAV missing: {wav_path}")
            continue

        # Convert to OPUS
        with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as tmp_opus:
            opus_path = tmp_opus.name

            await loop.run_in_executor(
                None,
                convert_wav_to_opus,
                wav_path,
                opus_path
            )

            # Push to playback queue without blocking
            await loop.run_in_executor(
                None,
                redis_client.lpush, 
                "playback_queue",
                f"{unique_id}|{opus_path}"
            )
