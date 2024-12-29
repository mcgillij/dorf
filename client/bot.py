import os
import tempfile
import requests
import discord
from discord.ext import commands
from pydub import AudioSegment
import asyncio
import redis
import hashlib

# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
WORKSPACE = "a-new-workspace"
SESSION_ID = "identifier-to-partition-chats-by-external-id"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True

context_dict = {}

# Create the bot instance with a command prefix
bot = commands.Bot(command_prefix='!', intents=INTENTS)

class DerfBot:
    def __init__(self, auth_token, workspace, session_id):
        self.auth_token = auth_token
        self.workspace = workspace
        self.session_id = session_id

    def get_response(self, message):
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

    async def mimic_and_play(self, ctx, text_response):
        if not text_response:
            await ctx.send("No valid text to convert to audio.")
            return

        try:
            # Generate audio with mimic3 and save it to a file
            os.system(f'mimic3 --output-naming id --output-dir=/home/j/dorf/client/output/ --csv "1|{text_response}"')

            # Load the generated WAV file into an AudioSegment object
            wav_path = "/home/j/dorf/client/output/1.wav"
            audio_segment = AudioSegment.from_wav(wav_path)

            # Convert the AudioSegment to Opus format for Discord
            with tempfile.NamedTemporaryFile(delete=False, suffix='.opus') as temp_file:
                opus_path = temp_file.name
                audio_segment.export(opus_path, format="opus")

            # Play the audio in the Discord voice channel
            if ctx.author.voice:
                vc = await ctx.author.voice.channel.connect()
                vc.play(discord.FFmpegOpusAudio(source=opus_path), after=lambda e: print(f'Player error: {e}') if e else None)
                
                while vc.is_playing() or vc.is_paused():
                    await asyncio.sleep(0.1)
                
                # Clean up the temporary file
                os.remove(opus_path)
                await vc.disconnect()
            else:
                await ctx.send("You are not connected to a voice channel.")
        except Exception as e:
            print(f"Error playing audio in the Discord voice channel: {e}")
            await ctx.send("An error occurred while playing audio. Please try again later.")


derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)

# Initialize Redis client
redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)  # Use the container name here

@bot.command()
async def derf(ctx, *, message):
    unique_id = hashlib.md5(f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()).hexdigest()
    print(f"Unique ID: {unique_id}")
    # Store the ctx object in the dictionary
    if unique_id not in context_dict:
        context_dict[unique_id] = ctx
    # Add the command with context to the Redis queue
    redis_client.lpush('derf_queue', f"{unique_id}:{message}")

# Event listener for when the bot has switched from offline to online.
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')

    # Start the Redis queue processing task
    asyncio.create_task(process_redis_queue())

async def process_redis_queue():
    while True:
        try:
            message_with_context = redis_client.rpop('derf_queue')
            if message_with_context is None:
                await asyncio.sleep(1)  # Wait a bit before checking again
                continue

            # Parse the unique identifier and message from the queue item
            unique_id, message = message_with_context.split(":", 1)
            print(f"Processing message: {message} for {unique_id}")
            
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


def split_message(message, max_length=2000):
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
