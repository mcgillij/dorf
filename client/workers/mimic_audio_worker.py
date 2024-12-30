import os
import tempfile
import asyncio
import redis
from pydub import AudioSegment
import traceback
from bot.commands import bot


redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)

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
                os.system(f'mimic3 --output-naming id --output-dir={output_dir} --csv < {text_file_path}')

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
