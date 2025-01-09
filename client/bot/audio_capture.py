""" AudioCapture class to capture and save audio per user. """
import os
import json
import wave
from pydub import AudioSegment
import discord
from discord.ext.voice_recv import AudioSink, VoiceData
import redis
from dotenv import load_dotenv
from .utilities import RingBuffer


load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class RingBufferAudioSink(AudioSink):
    def __init__(self, buffer_size=1024 * 1024, output_dir="user_audio"):
        self.ring_buffers = {}  # One buffer per user
        self.buffer_size = buffer_size
        self.output_dir = output_dir

        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

    def write(self, member, data: VoiceData):
        """
        Writes PCM data into the user's ring buffer.
        """
        if member.id not in self.ring_buffers:
            self.ring_buffers[member.id] = RingBuffer(self.buffer_size)

        self.ring_buffers[member.id].write(data.pcm)

    def save(self):
        """
        Saves all buffered audio for each user to separate .wav files.
        """
        for user_id, ring_buffer in self.ring_buffers.items():
            pcm_data = ring_buffer.read_all()
            if not pcm_data:
                print(f"No audio data to save for user {user_id}.")
                continue
            converted_path = save_audio(user_id, pcm_data, self.output_dir)

            redis_client.lpush('whisper_queue', json.dumps({"user_id": user_id, "audio_path": converted_path}))

    def cleanup(self):
        pass

    def cleanup2(self):
        """
        Cleans up all audio data stored in the sink.
        """
        #self.user_audio_data.clear()
        self.ring_buffers.clear()
        print("RingBufferAudioSink cleanup")

    def wants_opus(self):
        """
        Return False because we want PCM audio data, not Opus.
        """
        return False

def save_audio(user_id: int, pcm_data, output_dir: str) -> str:
    """
    Saves PCM audio data to a Whisper-compatible WAV file.
    :param user_id: The ID of the user whose audio is being saved.
    :param pcm_data: Raw PCM audio data.
    :param output_dir: Directory to save audio files.
    """
    os.makedirs(output_dir, exist_ok=True)

    # File paths
    original_path = os.path.join(output_dir, f"{user_id}-original.wav")
    converted_path = os.path.join(output_dir, f"{user_id}.wav")

    # Save original WAV file
    with wave.open(original_path, "wb") as wav_file:
        wav_file.setnchannels(2)  # Stereo
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(48000)  # 48 kHz
        wav_file.writeframes(pcm_data)
    print(f"Saved original audio for user {user_id} to {original_path}.")

    # Convert to Whisper-compatible format
    audio = AudioSegment.from_file(original_path, format="wav")
    audio.set_channels(1).set_frame_rate(16000).export(converted_path, format="wav", codec="pcm_s16le")
    print(f"Converted audio for user {user_id} to {converted_path}.")

    # Clean up original file
    os.remove(original_path)
    print(f"Deleted original audio file for user {user_id}: {original_path}.")
    return converted_path


class VoiceRecvClient(discord.VoiceProtocol):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        print("VoiceRecvClient init")
        super().__init__(client, channel)
        self.audio_sink = None

    async def on_ready(self):
        print("VoiceRecvClient on_ready")
        if self.audio_sink:
            await self.send_audio_packet(b'', True)

    async def send_audio_packet(self, data, is_last=False):
        print(f"Sending audio packet to sink: {self.audio_sink}")
        if not self.audio_sink:
            return
        await self.audio_sink.read(data)
