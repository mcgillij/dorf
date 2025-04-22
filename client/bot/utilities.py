import re
from random import randint
import aiohttp
import asyncio
import traceback
import hashlib
import logging
from bot.redis_client import redis_client
from bot.config import LLM_HOST
from bot.constants import FILTERED_KEYWORDS

timeout = aiohttp.ClientTimeout(total=120)

logger = logging.getLogger(__name__)


async def replace_userids_with_username(text: str) -> str:
    logger.info("Replacing user IDs with usernames")
    logger.debug(f"{text=}")

    async def replace_match(match: re.Match) -> str:
        user_id = int(match.group(1))
        username = await sanitize_userid(user_id)
        return username

    async def replace_pattern(pattern: str, text: str) -> str:
        matches = list(re.finditer(pattern, text))
        if not matches:
            return text

        # Build the new text progressively
        new_text = []
        last_end = 0
        for match in matches:
            new_text.append(text[last_end : match.start()])
            new_text.append(await replace_match(match))
            last_end = match.end()
        new_text.append(text[last_end:])
        return "".join(new_text)

    patterns = [r"<@(\d+)>", r"@(\d+)", r"(\d+):", r"(\d+),"]
    for pattern in patterns:
        text = await replace_pattern(pattern, text)

    logger.debug(f"{text=}")
    return text


async def sanitize_userid(user_id: int):
    return f"<@{user_id}>"


def filter_message(message: str) -> bool:
    return any(keyword.lower() in message.lower() for keyword in FILTERED_KEYWORDS)


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
    if message:
        return [message[i : i + max_length] for i in range(0, len(message), max_length)]
    else:
        return []


class LLMClient:
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


def split_text(text):  # This shouldn't be needed anymore since moving mostly to kokoro
    """
    Splits the text into chunks using newlines (\n) or periods (.) as delimiters.
    """
    return [chunk.strip() for chunk in re.split(r"[.\n]", text) if chunk.strip()]
