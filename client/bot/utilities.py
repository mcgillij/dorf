import os
import re
import dotenv
from random import randint
import aiohttp
import asyncio
import traceback
import redis
import hashlib
from bot.log_config import setup_logger

dotenv.load_dotenv()

timeout = aiohttp.ClientTimeout(total=120)

logger = setup_logger(__name__)

# Constants for API interaction
LLM_HOST = os.getenv("LLM_HOST", "")
GUILD_ID = os.getenv("GUILD_ID", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

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
