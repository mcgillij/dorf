""" AudioCapture class to capture and save audio per user. """
import os
import json
import wave
import time
from pydub import AudioSegment
import discord
from discord.ext.voice_recv import AudioSink, VoiceData
import redis
from dotenv import load_dotenv
from .utilities import RingBuffer
import numpy as np


load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

class VoiceActivityDetector:
    def __init__(self, 
                 silence_threshold=-50,
                 silence_duration=1.0,
                 min_speech_duration=0.5
                ):
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.min_speech_duration = min_speech_duration
        self.last_audio_time = None
        self.speech_start_time = None
        self.is_speaking = False
        self.last_packet_time = None
        self.first_packet_received = False
        print(f"VAD initialized with threshold={silence_threshold}, silence_duration={silence_duration}")

    def get_audio_level(self, pcm_data):
        try:
            float_data = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            rms = np.sqrt(np.mean(float_data**2))
            db = 20 * np.log10(rms) if rms > 0 else -float('inf')
            return db
        except Exception as e:
            print(f"Error calculating audio level: {e}")
            return -float('inf')

    def process_audio(self, pcm_data, current_time):
        # Only update last_packet_time when we first start receiving audio
        if not self.first_packet_received:
            self.first_packet_received = True
            self.last_packet_time = current_time
            print("First packet received")
            
        audio_level = self.get_audio_level(pcm_data)
        print(f"Audio level: {audio_level:.2f} dB")

        if audio_level > self.silence_threshold:
            self.last_audio_time = current_time
            if not self.is_speaking:
                self.is_speaking = True
                self.speech_start_time = current_time
                print(f"Speech started at {self.speech_start_time}")
            return False
        
        return False

    def check_ptt_release(self, current_time):
        if not self.first_packet_received or self.last_packet_time is None:
            return False
            
        time_since_last_packet = current_time - self.last_packet_time
        print(f"Checking PTT release: {time_since_last_packet:.3f}s since last packet")
        
        # Consider PTT released if no packets received for 0.5 seconds
        if time_since_last_packet > 0.5:
            if self.is_speaking:
                print("PTT release detected!")
                self.is_speaking = False
                self.first_packet_received = False  # Reset for next speech segment
                self.last_packet_time = None
                return True
        else:
            self.last_packet_time = current_time  # Update timestamp only if we're still receiving packets
            
        return False

class RingBufferAudioSink(AudioSink):
    def __init__(self, buffer_size=1024 * 1024, output_dir="user_audio"):
        self.ring_buffers = {}
        self.buffer_size = buffer_size
        self.output_dir = output_dir
        self.vad_detectors = {}
        self.last_check_time = {}
        os.makedirs(self.output_dir, exist_ok=True)
        print("RingBufferAudioSink initialized")

    def write(self, member, data: VoiceData):
        try:
            current_time = time.time()
            
            if member.id not in self.ring_buffers:
                print(f"Creating new buffer and VAD for user {member.id}")
                self.ring_buffers[member.id] = RingBuffer(self.buffer_size)
                self.vad_detectors[member.id] = VoiceActivityDetector(
                    silence_threshold=-45,
                    silence_duration=0.8,
                    min_speech_duration=0.3
                )
                self.last_check_time[member.id] = current_time

            self.ring_buffers[member.id].write(data.pcm)
            vad = self.vad_detectors[member.id]

            # Process audio data for VAD
            vad.process_audio(data.pcm, current_time)
            #self.vad_detectors[member.id].process_audio(data.pcm, current_time)

            # Check for PTT release periodically
            if current_time - self.last_check_time[member.id] > 0.1:  # Check every 100ms
                self.last_check_time[member.id] = current_time
                if vad.check_ptt_release(current_time):
                    print(f"PTT release detected for user {member.id} - saving audio")
                    self.save_user_audio(member.id)

        except Exception as e:
            print(f"Error in write method: {e}")

    def save_user_audio(self, user_id):
        try:
            print(f"Attempting to save audio for user {user_id}")
            ring_buffer = self.ring_buffers[user_id]
            pcm_data = ring_buffer.read_all()
            
            if pcm_data:
                print(f"Got PCM data of length {len(pcm_data)}")
                converted_path = save_audio(user_id, pcm_data, self.output_dir)
                redis_client.lpush('whisper_queue', json.dumps({
                    "user_id": user_id,
                    "audio_path": converted_path
                }))
                print(f"Saved audio to {converted_path}")
            else:
                print("No PCM data to save")
                
            ring_buffer.clear()
        except Exception as e:
            print(f"Error in save_user_audio: {e}")

    def save(self):
        print("Manual save triggered")
        for user_id in list(self.ring_buffers.keys()):
            self.save_user_audio(user_id)

    def cleanup(self):
        pass

    def cleanup2(self):
        self.ring_buffers.clear()
        self.vad_detectors.clear()
        self.last_check_time.clear()
        print("RingBufferAudioSink cleanup completed")

    def wants_opus(self):
        return False

def save_audio(user_id: int, pcm_data, output_dir: str) -> str:
    try:
        os.makedirs(output_dir, exist_ok=True)
        original_path = os.path.join(output_dir, f"{user_id}-original.wav")
        converted_path = os.path.join(output_dir, f"{user_id}.wav")

        print(f"Saving original audio to {original_path}")
        with wave.open(original_path, "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48000)
            wav_file.writeframes(pcm_data)

        print("Converting audio to Whisper format")
        audio = AudioSegment.from_file(original_path, format="wav")
        audio.set_channels(1).set_frame_rate(16000).export(
            converted_path, format="wav", codec="pcm_s16le"
        )

        os.remove(original_path)
        print(f"Successfully saved and converted audio to {converted_path}")
        return converted_path
    except Exception as e:
        print(f"Error in save_audio: {e}")
        return None

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
