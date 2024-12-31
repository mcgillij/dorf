import os
import re
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

