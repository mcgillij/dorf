""" AudioCapture class to capture and save audio per user. """
import os
import wave
import numpy as np
from pydub import AudioSegment
from discord.ext import voice_recv
from discord.ext.voice_recv import VoiceData



class AudioCapture(voice_recv.AudioSink):
    """
    A custom audio sink to capture and save audio per user.
    """
    def __init__(self, output_dir="user_audio"):
        self.output_dir = output_dir
        self.user_audio_data = {}

        # Ensure the output directory exists
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def write(self, member, data: VoiceData):
        """
        Collects PCM audio data for each user.
        Args:
            member: The user whose audio is being processed.
            data: The VoiceData object containing PCM audio.
        """
        pcm_bytes = data.pcm  # Extract the raw PCM data from VoiceData
        if member.id not in self.user_audio_data:
            self.user_audio_data[member.id] = []
        self.user_audio_data[member.id].append(pcm_bytes)

    def save(self):
        """
        Saves captured audio for each user to separate .wav files.
        """
        if not self.user_audio_data:
            print("No audio data to save.")
            return

        for user_id, audio_data in self.user_audio_data.items():
            if not audio_data:
                print(f"No audio data for user {user_id}.")
                continue

            # Convert the raw PCM data into a NumPy array
            pcm_data = b"".join(audio_data)
            np_data = np.frombuffer(pcm_data, dtype=np.int16)

            # Save the original WAV file
            original_path = os.path.join(self.output_dir, f"{user_id}_original.wav")
            with wave.open(original_path, "wb") as wav_file:
                wav_file.setnchannels(2)  # stereo
                wav_file.setsampwidth(2)  # 16-bit pcm
                wav_file.setframerate(48000)  # discord uses 48khz sample rate
                wav_file.writeframes(np_data.tobytes())

            print(f"Saved original audio for user {user_id} to {original_path}.")

            # Convert to Whisper-compatible format
            converted_path = os.path.join(self.output_dir, f"{user_id}.wav")
            audio = AudioSegment.from_file(original_path, format="wav")
            audio = audio.set_channels(1)  # Mono
            audio = audio.set_frame_rate(16000)  # 16 kHz sample rate
            audio.export(converted_path, format="wav", codec="pcm_s16le")

            # now we can delete the original file
            os.remove(original_path)

            print(f"Converted audio for user {user_id} to {converted_path}.")

    def cleanup(self):
        """
        Clean up resources if needed.
        """
        pass

    def wants_opus(self):
        """
        Return False because we want PCM audio data, not Opus.
        """
        return False
