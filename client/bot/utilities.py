import os
import re
import threading
import dotenv
from random import randint
import aiohttp
import asyncio
import traceback
import redis
import hashlib
import logging

dotenv.load_dotenv()

timeout = aiohttp.ClientTimeout(total=120)

logger = logging.getLogger(__name__)
FORMAT = "%(asctime)s - %(message)s"
logging.basicConfig(format=FORMAT)
logger.addHandler(logging.FileHandler("derf.log"))
logger.setLevel(logging.DEBUG)

# Constants for API interaction
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
WORKSPACE = "birthright"
SESSION_ID = "my-session-id"
LLM_HOST = os.getenv("LLM_HOST", "")

redis_client = redis.Redis(decode_responses=True)
# filtered words
filtered_keywords = {
    "behavior driven development",
    "QA",
    "BDD",
    "pytest",
    "testing",
    "gherkin",
    "test",
    "specflow",
    "cypress",
    "playwrite",
}  # Add the keywords to filter

filtered_responses = [
    "Nice try nerd!",
    "Nice try, but you're still a noob.",
    "Almost there, but not close enough.",
    "You missed by a mile!",
    "Not quite, keep trying harder!",
    "Close, but you need to step it up.",
    "You were almost there, but not quite.",
    "Good effort, now go learn more.",
    "Almost had it, but not quite enough.",
    "Nice shot, just a little off.",
    "Almost got it, but still missing the mark.",
    "You're close, but need to practice more.",
    "Almost there, but you’re slipping.",
    "Good try, now go study up.",
    "Not exactly right, but keep trying!",
    "Close enough for a joke, but not real.",
    "Nice effort, just need a bit more focus.",
    "You were almost there, but still off.",
    "Good start, now go get it right.",
    "Almost had it, but missed by a long shot.",
    "Nice attempt, but you're not there yet.",
    "Close, but you need to work harder.",
    "You were almost there, but still off-base.",
    "Good effort, now go get it right.",
    "Almost got it, just a little more.",
    "Nice try, but the answer eludes you.",
    "Close enough for a laugh, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost had it, just need to focus more.",
    "Nice try, but the answer is eluding you.",
    "Close enough for a joke, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost got it, just need to focus more.",
    "Nice attempt, but the answer is elusive.",
    "Close enough for a laugh, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost had it, just need to focus more.",
    "Nice try, but the answer is elusive.",
]


def filter_message(message: str) -> bool:
    return any(keyword.lower() in message.lower() for keyword in filtered_keywords)


def generate_unique_id(ctx, message: str) -> str:
    """Generates a unique ID based on context and message."""
    return hashlib.md5(
        f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()
    ).hexdigest()


async def poll_redis_for_key(key: str, timeout: float = 0.5) -> str:
    """Polls Redis for a key and returns its value when found."""
    while True:
        response = redis_client.get(key)
        if response:
            redis_client.delete(key)
            return response.decode("utf-8") if isinstance(response, bytes) else response
        await asyncio.sleep(timeout)


def split_message(message: str, max_length: int = 2000) -> list[str]:
    """Splits a message into chunks of a maximum length."""
    return [message[i : i + max_length] for i in range(0, len(message), max_length)]


class DerfBot:
    def __init__(self, auth_token: str, workspace: str, session_id: str):
        self.auth_token = auth_token
        self.workspace = workspace
        self.session_id = session_id

    async def get_summarizer_response(self, message: str) -> str:
        url = f"http://{LLM_HOST}/api/v1/workspace/summarizer/chat"
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        data = {
            "message": message,
            "mode": "chat",
            "sessionId": randint(0, 1000000),
            "attachments": [],
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        logger.error(
                            f"Error: {response.status} - {await response.text()}"
                        )
                        return ""
            except asyncio.TimeoutError:
                logger.error("Request timed out.")
                return "The summarizer request timed out. Please try again later."
            except Exception as e:
                logger.error(f"Exception during API call: {e}")
                return "An error occurred while processing the summarizer request. Please try again later."

    async def get_response(self, message: str) -> str:
        url = f"http://{LLM_HOST}/api/v1/workspace/{self.workspace}/chat"
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        data = {
            "message": message,
            "mode": "chat",
            "sessionId": self.session_id,
            "attachments": [],
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("textResponse", "")
                    else:
                        logger.error(
                            f"Error: {response.status} - {await response.text()}"
                        )
                        return ""
            except asyncio.TimeoutError:
                logger.error("Request timed out.")
                return "The request timed out. Please try again later."
            except Exception as e:
                logger.error(f"Exception during API call: {e}")
                traceback.print_exc()
                return "An error occurred while processing the request. Please try again later."


derf_bot = DerfBot(AUTH_TOKEN, WORKSPACE, SESSION_ID)


def split_text(text):  # This shouldn't be needed anymore since moving mostly to kokoro
    """
    Splits the text into chunks using newlines (\n) or periods (.) as delimiters.
    """
    return [chunk.strip() for chunk in re.split(r"[.\n]", text) if chunk.strip()]


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
                data = data[-self.size :]
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
                data = self.buffer[self.read_ptr :] + self.buffer[: self.write_ptr]
            else:
                # Read from the used portion
                data = self.buffer[self.read_ptr : self.write_ptr]

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
