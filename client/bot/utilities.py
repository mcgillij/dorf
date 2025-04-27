import re
import os
from random import randint, choice
import aiohttp
import asyncio
import traceback
import hashlib
import logging

import discord
from discord.ext.voice_recv import VoiceRecvClient
from bot.redis_client import redis_client
from bot.config import LLM_HOST
from bot.constants import FILTERED_KEYWORDS
from bot.audio_capture import RingBufferAudioSink

timeout = aiohttp.ClientTimeout(total=120)

logger = logging.getLogger(__name__)


def get_random_image_path(directory):
    """
    Returns a random image file path from the specified directory.

    Args:
        directory (str): The path to the directory containing images.

    Returns:
        str: The full path to a randomly selected image file, or None if no images are found.
    """
    try:
        image_files = [
            entry.name
            for entry in os.scandir(directory)
            if entry.is_file()
            and entry.name.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
        ]  # Filter for common image extensions

        if not image_files:
            logger.info(
                f"No images found in directory: {directory}"
            )  # helpful debug message
            return None

        random_image = choice(image_files)
        return os.path.join(directory, random_image)  # Construct the full path
    except FileNotFoundError:
        logger.info(f"Directory not found: {directory}")
        return None
    except Exception as e:
        logger.info(f"An error occurred: {e}")  # Catch other potential errors
        return None


async def replace_userids_with_username(ctx, text: str) -> str:
    logger.info("Replacing user IDs with usernames")
    logger.debug(f"{text=}")

    async def replace_match(match: re.Match) -> str:
        user_id = int(match.group(1))
        # lookup the user id with the ctx to get the displayname
        user = ctx.guild.get_member(user_id)
        if user:
            username = user.display_name
        else:
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


async def start_capture(guild, channel, bot):
    logger.info(f"Starting capture in {channel.name} for bot {bot.name}")
    try:
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            logger.info("Not connected yet. Connecting to voice...")
            vc = await channel.connect(cls=VoiceRecvClient)

        if vc is None or not vc.is_connected():
            logger.error("Failed to connect to voice, aborting capture.")
            return

        if vc.is_listening():
            logger.info("Already listening, resetting sink...")
            vc.stop_listening()

        ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        logger.info(f"Recording started in channel {channel.name}")
        logger.info(f"Sweeping channel {channel.name} for existing members...")
        for member in channel.members:
            if member.bot:
                continue
            logger.info(
                f"Detected existing member {member.display_name}. Initializing capture."
            )
            await bot.handle_voice_state_update(member, None, member.voice)

    except Exception as e:
        logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(bot):
    logger.info(f"BOT STARTING in connect_to_voice: {bot}")
    try:
        guild_id = int(os.getenv("GUILD_ID", ""))
        voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    except ValueError:
        logger.error("GUILD_ID or VOICE_CHANNEL_ID is not a valid integer.")
        return

    if not guild_id or not voice_channel_id:
        logger.error(
            "GUILD_ID or VOICE_CHANNEL_ID is missing in the environment variables."
        )
        return
    guild = discord.utils.get(bot.guilds, id=guild_id)

    if not guild:
        logger.error("Guild not found.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not isinstance(voice_channel, discord.VoiceChannel):
        logger.error(f"Invalid or non-existent voice channel: {voice_channel_id}")
        return

    # Check existing connections in the guild
    current_vc = next((vc for vc in bot.voice_clients if vc.guild == guild), None)

    try:
        if not current_vc:
            await voice_channel.connect(cls=VoiceRecvClient)
            logger.info(f"Connected to {voice_channel.name}")
        else:
            # Check if already connected to the correct channel
            if current_vc.channel.id != voice_channel.id:
                # Move existing client or reconnect?
                try:
                    await current_vc.move_to(voice_channel)  # Attempt move first
                    logger.info(f"Moved to {voice_channel.name}")
                except discord.errors.InvalidData as e:
                    logger.error(f"Move failed: {e}. Reconnecting...")
                    await current_vc.disconnect()
                    await voice_channel.connect(cls=VoiceRecvClient)
            else:
                logger.debug("Already connected to the correct channel.")
    except Exception as e:
        logger.exception(f"Connection error: {str(e)}")
