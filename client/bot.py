import os
import tempfile
import requests
import discord
from discord.ext import commands
from pydub import AudioSegment
import asyncio
import redis
import hashlib
import dotenv
from typing import List
import traceback
import subprocess

dotenv.load_dotenv()

# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
WORKSPACE = "a-new-workspace"
SESSION_ID = "my-session-id"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True

context_dict = {}

# Create the bot instance with a command prefix
bot = commands.Bot(command_prefix='!', intents=INTENTS)

class DerfBot:
    def __init__(self, auth_token: str, workspace: str, session_id: str):
        self.auth_token = auth_token
        self.workspace = workspace
        self.session_id = session_id

    def get_response(self, message: str) -> str:
        url = f"http://localhost:3001/api/v1/workspace/{WORKSPACE}/chat"
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
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json().get("textResponse", "")
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return ""

    async def mimic_and_play(self, ctx, text_response: str):
        if not text_response:
            await ctx.send("No valid text to convert to audio.")
            return

        try:
            # Split text_response into multiple lines
            dialog_lines = text_response.strip().splitlines()
            if not dialog_lines:
                await ctx.send("No valid text to convert to audio.")
                return

            output_dir = "/home/j/dorf/client/output/"
            voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

            # Check if already connected to a voice channel
            if not voice_client or not voice_client.is_connected():
                if ctx.author.voice:
                    voice_client = await ctx.author.voice.channel.connect()
                else:
                    await ctx.send("You are not connected to a voice channel.")
                    return

            # Process each line of dialog
            for idx, line in enumerate(dialog_lines, start=1):
                line = line.strip()
                if not line:
                    continue

                # Create temporary text file for the current line
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as text_file:
                    text_file.write(f"{idx}|{line}")
                    text_file.flush()
                    text_file_path = text_file.name

                try:
                    # Generate TTS audio using Mimic 3
                    print(f"Generating audio for line {idx}: {line}")
                    os.system(f'mimic3 --output-naming id --output-dir={output_dir} < {text_file_path}')

                    # Path to the generated WAV file
                    wav_path = os.path.join(output_dir, f"{idx}.wav")
                    if not os.path.exists(wav_path):
                        await ctx.send(f"Failed to generate audio for line {idx}: {line}")
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

                    # Play the generated audio
                    audio_source = await discord.FFmpegOpusAudio.from_probe(
                        opus_path, method='fallback', options='-vn -b:a 128k'
                    )
                    voice_client.play(
                        audio_source,
                        after=lambda e: print(f'Player error: {e}') if e else None
                    )

                    # Wait for the current audio to finish playing
                    while voice_client.is_playing():
                        await asyncio.sleep(0.1)

                    # Clean up temporary files for the current line
                    os.remove(opus_path)
                    os.remove(wav_path)

                finally:
                    # Ensure temporary text file is removed
                    os.remove(text_file_path)

            # Disconnect from the voice channel if no one is left in the channel
            if not any(vc for vc in ctx.guild.voice_channels if voice_client.channel in vc.members):
                await voice_client.disconnect()

        except Exception as e:
            print(f"Error playing audio in Discord voice channel: {e}")
            traceback.print_exc()
            await ctx.send("An error occurred while playing audio. Please try again later.")

derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)

# Initialize Redis client
redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)  # Use the container name here

@bot.command()
async def derf(ctx, *, message: str):
    unique_id = hashlib.md5(f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()).hexdigest()
    print(f"Unique ID: {unique_id}")
    # Store the ctx object in the dictionary
    if unique_id not in context_dict:
        context_dict[unique_id] = ctx
    # Add the command with context to the Redis queue
    redis_client.lpush('derf_queue', f"{unique_id}:{ctx.author.id}:{message}")

# Event listener for when the bot has switched from offline to online.
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')
    asyncio.create_task(connect_to_voice_channel_on_ready())
    # Start the Redis queue processing task
    asyncio.create_task(process_redis_queue())

async def connect_to_voice_channel_on_ready():
    guild_id = int(os.getenv("GUILD_ID", ""))  # Guild ID (Server)
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))  # Voice Channel ID
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

async def process_redis_queue():
    while True:
        try:
            message_with_context = redis_client.rpop('derf_queue')
            if message_with_context is None:
                await asyncio.sleep(1)  # Wait a bit before checking again
                continue

            # Parse the unique identifier and message from the queue item
            unique_id, username, message = message_with_context.split(":", 2)
            print(f"Processing {username}'s message: {message} for {unique_id}")
            # Retrieve the ctx object from the dictionary
            if unique_id not in context_dict:
                print(f"No context found for {unique_id}")
                continue
            ctx = context_dict[unique_id]

            text_response = derf_bot.get_response(message)
            # Split the text response into chunks if it exceeds the limit
            split_responses = split_message(text_response, 2000)

            # Send each chunk as a separate message
            for response in split_responses:
                if ctx.channel:
                    await ctx.channel.send(response)

            # Play audio in the Discord voice channel
            await derf_bot.mimic_and_play(ctx, text_response)

        except Exception as e:
            print(f"An error occurred while processing queue: {e}")


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
        end_index = min(max_length, len(message))
        while end_index > 0 and message[end_index] != ' ':
            end_index -= 1
        if end_index == 0:  # If no space found, split at max_length
            end_index = max_length
        chunks.append(message[:end_index])
        message = message[end_index:]
    return chunks

# Run the bot with your token
bot.run(os.getenv("DISCORD_BOT_TOKEN", ""))
