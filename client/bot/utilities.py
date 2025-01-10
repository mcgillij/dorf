import os
import re
import threading
import dotenv
from random import randint
from typing import List
import aiohttp
import asyncio
import traceback

dotenv.load_dotenv()

timeout = aiohttp.ClientTimeout(total=20)

# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
WORKSPACE = "a-new-workspace"
SESSION_ID = "my-session-id"


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
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except asyncio.TimeoutError:
                print("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                return "An error occurred while processing the request. Please try again later."

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
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except asyncio.TimeoutError:
                print("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                traceback.print_exc()
                return "An error occurred while processing the request. Please try again later."


derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)

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

def split_text(text):
    """
    Splits the text into chunks using newlines (\n) or periods (.) as delimiters.
    """
    return [chunk.strip() for chunk in re.split(r'[.\n]', text) if chunk.strip()]


class WhisperClient:
    async def get_text(self, audio_file_path: str) -> str:
        url = f"http://127.0.0.1:8080/inference"
        headers = {
            'accept': 'application/json',
        }
        
        files = {"file": open(audio_file_path, "rb")}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, data=files) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("text", "")
                    else:
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except asyncio.TimeoutError:
                print("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                traceback.print_exc()
                return "An error occurred while processing the request. Please try again later."


class RingBuffer:
    def __init__(self, size: int):
        self.buffer = bytearray(size)
        self.size = size
        self.write_ptr = 0
        self.read_ptr = 0
        self.is_full = False
        self.lock = threading.Lock()

    def write(self, data: bytes):
        with self.lock:
            data_len = len(data)
            if data_len > self.size:
                # If data exceeds buffer size, write only the last chunk
                data = data[-self.size:]
                data_len = len(data)

            # Write data in a circular manner
            for byte in data:
                self.buffer[self.write_ptr] = byte
                self.write_ptr = (self.write_ptr + 1) % self.size
                if self.is_full:
                    self.read_ptr = (self.read_ptr + 1) % self.size
                self.is_full = self.write_ptr == self.read_ptr

    def read_all(self) -> bytes:
        with self.lock:
            if not self.is_full and self.write_ptr == self.read_ptr:
                # Buffer is empty
                return b""

            if self.is_full:
                # Read from the full buffer
                data = self.buffer[self.read_ptr:] + self.buffer[:self.write_ptr]
            else:
                # Read from the used portion
                data = self.buffer[self.read_ptr:self.write_ptr]

            self.read_ptr = self.write_ptr  # Mark buffer as read
            self.is_full = False
            return bytes(data)

    def is_empty(self) -> bool:
        with self.lock:
            return not self.is_full and self.write_ptr == self.read_ptr

    def clear(self):
        with self.lock:
            self.write_ptr = 0
            self.read_ptr = 0
            self.is_full = False
