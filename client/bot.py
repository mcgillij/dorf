import os
import tempfile
import requests
import discord
from discord.ext import commands
from pydub import AudioSegment
import asyncio
import io
# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
WORKSPACE = "a-new-workspace"
SESSION_ID = "identifier-to-partition-chats-by-external-id"
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True

# Create the bot instance with a command prefix
bot = commands.Bot(command_prefix='!', intents=INTENTS)

class DerfBot:
    def __init__(self, auth_token, workspace, session_id):
        self.auth_token = auth_token
        self.workspace = workspace
        self.session_id = session_id

    def get_response(self, message):
        url = f"http://localhost:3001/api/v1/workspace/test/chat"
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
    # async def mimic_and_play(self, ctx, text_response):
        # if not text_response:
            # await ctx.send("No valid text to convert to audio.")
            # return

        # try:
            # # Generate audio with mimic3 and save it to a file
            # os.system(f'mimic3 --output-naming id --output-dir=/home/j/dorf/client/output/ --csv "1|{text_response}"')

            # # Load the generated WAV file into an AudioSegment object
            # audio_segment = AudioSegment.from_wav("/home/j/dorf/client/output/1.wav")

            # # Convert the AudioSegment to Opus format for Discord
            # opus_data = io.BytesIO()
            # audio_segment.export(opus_data, format="opus")
            # opus_data.seek(0)

            # # Play the audio in the Discord voice channel
            # if ctx.author.voice:
                # vc = await ctx.author.voice.channel.connect()
                # vc.play(discord.FFmpegOpusAudio(source=opus_data), after=lambda e: print(f'Player error: {e}') if e else None)
                
                # while vc.is_playing() or vc.is_paused():
                    # await asyncio.sleep(0.1)
                
                # await vc.disconnect()
            # else:
                # await ctx.send("You are not connected to a voice channel.")
        # except Exception as e:
            # print(f"Error playing audio in the Discord voice channel: {e}")
            # await ctx.send("An error occurred while playing audio. Please try again later.")
    # async def mimic_and_play(self, ctx, text_response):
            # if not text_response:
                # await ctx.send("No valid text to convert to audio.")
                # return

            # try:
                # # Generate audio with mimic3 and save it to a file
                # os.system(f'mimic3 --output-naming id --output-dir=/home/j/dorf/client/output/ --csv "1|{text_response}"')

                # # Load the generated WAV file into an AudioSegment object
                # audio_segment = AudioSegment.from_wav("/home/j/dorf/client/output/1.wav")

                # # Convert the AudioSegment to PCM format for Discord
                # pcm_data = io.BytesIO()
                # audio_segment.export(pcm_data, format="pcm_s16_be")
                # pcm_data.seek(0)

                # # Play the audio in the Discord voice channel
                # if ctx.author.voice:
                    # vc = await ctx.author.voice.channel.connect()
                    # vc.play(discord.FFmpegPCMAudio(pcm_data), after=lambda e: print(f'Player error: {e}') if e else None)
                    
                    # while vc.is_playing() or vc.is_paused():
                        # await asyncio.sleep(0.1)
                    
                    # await vc.disconnect()
                # else:
                    # await ctx.send("You are not connected to a voice channel.")
            # except Exception as e:
                # print(f"Error playing audio in the Discord voice channel: {e}")
                # await ctx.send("An error occurred while playing audio. Please try again later.")
    # async def mimic_and_play(self, ctx, text_response):
        # if not text_response:
            # await ctx.send("No valid text to convert to audio.")
            # return

        # try:
            # # Run the mimic3 command and capture its output
            # result = os.popen(f'mimic3 --stdout "{text_response}"').read()
            
            # # Convert the output to an AudioSegment (assuming it's in WAV format)
            # audio_segment = AudioSegment.from_file(io.BytesIO(result), format='wav')
            
            # # Play the audio in the Discord voice channel
            # if ctx.author.voice:
                # vc = await ctx.author.voice.channel.connect()
                # vc.play(discord.FFmpegPCMAudio(audio_segment.export(format="mp3")))
                # while vc.is_playing():
                    # await asyncio.sleep(1)
                # await vc.disconnect()
            # else:
                # await ctx.send("You are not connected to a voice channel.")
        # except Exception as e:
            # print(f"Error playing audio in the Discord voice channel: {e}")
            # await ctx.send("An error occurred while playing audio. Please try again later.")

# Create an instance of DerfBot
derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)

@bot.command()
async def derf(ctx, *, message):
    text_response = derf_bot.get_response(message)
    await ctx.send("Here is the response from the API:")
    await ctx.send(text_response)
    
    # Optionally play audio if needed
    await derf_bot.mimic_and_play(ctx, text_response)

# Event listener for when the bot has switched from offline to online.
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')

# Run the bot with your token
bot.run(os.getenv("DISCORD_BOT_TOKEN", ""))

