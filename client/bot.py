""" Derfbot, the logical iteration of DORFBOT """
import os
import tempfile
import discord
from discord.ext import commands
from pydub import AudioSegment
import asyncio
import redis
import hashlib
import dotenv
from typing import List
import traceback
import logging
import aiohttp
from random import randint

dotenv.load_dotenv()

# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
WORKSPACE = "a-new-workspace"
SESSION_ID = "my-session-id"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True

logging.basicConfig(level=logging.DEBUG)

context_dict = {}

# Create the bot instance with a command prefix
bot = commands.Bot(command_prefix='!', intents=INTENTS)

# Initialize Redis client
redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)

class DerfBot:
    def __init__(self, auth_token: str, workspace: str, session_id: str):
        self.auth_token = auth_token
        self.workspace = workspace
        self.session_id = session_id

    async def get_summarizer_response(self, message: str) -> str:
        url = f"http://localhost:3001/api/v1/workspace/summarizer/chat"
        headers = {
            'accept': 'application/json',
            'Authorization': f'Bearer {self.auth_token}',
            'Content-Type': 'application/json'
        }
        data = {
            "message": message,
            "mode": "chat",
            "sessionId": randint(0, 1000000),
            "attachments": []
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json=data, timeout=20) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except aiohttp.ClientTimeout:
                print("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                return ""

    async def get_response(self, message: str) -> str:
        url = f"http://localhost:3001/api/v1/workspace/{self.workspace}/chat"
        headers = {
            'accept': 'application/json',
            'Authorization': f'Bearer {self.auth_token}',
            'Content-Type': 'application/json'
        }
        data = {
            "message": message,
            "mode": "chat",
            "sessionId": self.session_id,
            "attachments": []
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json=data, timeout=20) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except aiohttp.ClientTimeout:
                print("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                return ""

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

                # Push the processed audio back into Redis for playback
                redis_client.lpush('playback_queue', f"{unique_id}|{opus_path}")

            finally:
                # Clean up temporary files
                if text_file_path and os.path.exists(text_file_path):
                    os.remove(text_file_path)
        except Exception as e:
            print(f"Error processing audio generation task: {e}")
            traceback.print_exc()

async def playback_task():
    """
    Continuously process playback requests from the Redis queue.
    """
    while True:
        try:
            playback_data = redis_client.rpop('playback_queue')
            if not playback_data:
                await asyncio.sleep(1)
                continue

            unique_id, opus_path = playback_data.split("|", 1)
            ctx = context_dict.get(unique_id)
            if not ctx:
                print(f"No context found for playback: {unique_id}")
                continue

            voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
            if not voice_client or not voice_client.is_connected():
                print("Voice client not connected.")
                continue

            # Play the generated audio
            audio_source = await discord.FFmpegOpusAudio.from_probe(
                opus_path, method='fallback', options='-vn -b:a 128k'
            )
            voice_client.play(
                audio_source,
                after=lambda e: print(f'Player error: {e}') if e else None
            )

            # Wait for the audio to finish playing
            while voice_client.is_playing():
                await asyncio.sleep(0.1)

            # Clean up the opus file
            if os.path.exists(opus_path):
                os.remove(opus_path)

        except Exception as e:
            print(f"Error processing playback task: {e}")
            traceback.print_exc()

def split_message(message: str, max_length: int = 2000) -> List[str]:
    """
    Split a message into chunks that fit within the specified character limit.
    :param message: The original message to be split.
    :param max_length: The maximum length of each chunk.
    :return: A list of message chunks.
    """
    if len(message) <= max_length:
        return [message]
    
    chunks = []
    while message:
        # Find a suitable place to split the message
        end_index = min(max_length, len(message)) - 1
        while end_index > 0 and message[end_index] != ' ':
            end_index -= 1
        # If no space was found, force split at max_length
        if end_index <= 0:
            end_index = max_length - 1
        # Append the chunk and strip leading spaces from the remaining message
        chunks.append(message[:end_index + 1].strip())
        message = message[end_index + 1:].strip()
    return chunks

@bot.command()
async def derf(ctx, *, message: str):
    unique_id = hashlib.md5(f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()).hexdigest()
    print(f"Unique ID: {unique_id}")

    # Store the ctx object in the dictionary
    if unique_id not in context_dict:
        context_dict[unique_id] = ctx

    # Get the bot's text response
    text_response = await derf_bot.get_response(message)
    if not text_response:
        await ctx.send("Failed to fetch a response. Please try again.")
        return
    print(f"Response: {text_response}")
    chunked_responses = split_message(text_response, 2000)
    for response in chunked_responses:
        await ctx.send(response)
    #determine if the text is long enough to summarize
    summarized = False
    summary_response = ""
    if len(text_response) > 1000:
        summary_response = await derf_bot.get_summarizer_response(text_response)
        if summary_response:
            summarized = True
            await ctx.send(f"Here is a summary of the response: {summary_response}")

    if not summarized:
        # Queue each line for audio processing
        dialog_lines = text_response.strip().splitlines()
        for idx, line in enumerate(dialog_lines, start=1):
            redis_client.lpush('audio_queue', f"{unique_id}|{idx}|{line.strip()}")
    else:
        dialog_lines = summary_response.strip().splitlines()
        for idx, line in enumerate(dialog_lines, start=1):
            redis_client.lpush('audio_queue', f"{unique_id}|{idx}|{line.strip()}")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')
    asyncio.create_task(connect_to_voice_channel_on_ready())
    asyncio.create_task(mimic_audio_task())
    asyncio.create_task(playback_task())

async def connect_to_voice_channel_on_ready():
    guild_id = int(os.getenv("GUILD_ID", ""))
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    guild = discord.utils.get(bot.guilds, id=guild_id)
    if guild:
        voice_channel = discord.utils.get(guild.voice_channels, id=voice_channel_id)
        if voice_channel:
            try:
                await voice_channel.connect()
                print(f"Connected to voice channel: {voice_channel.name}")
            except discord.ClientException as e:
                print(f"Already connected or error connecting: {e}")
        else:
            print("Voice channel not found.")
    else:
        print("Guild not found.")

derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)
# Run the bot with your token
bot.run(os.getenv("DISCORD_BOT_TOKEN", ""))

