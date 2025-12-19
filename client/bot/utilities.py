import re
import os
from random import randint, choice
import aiohttp
import asyncio
import traceback
import hashlib
import logging
import time

import discord
from discord.ext.voice_recv import VoiceRecvClient
from bot.redis_client import redis_client
from bot.config import LLM_HOST
from bot.constants import FILTERED_KEYWORDS
from bot.audio_capture import RingBufferAudioSink

timeout = aiohttp.ClientTimeout(total=120)

logger = logging.getLogger(__name__)


def patch_voice_recv_opus_decoder() -> None:
    """Best-effort runtime patch for discord-ext-voice-recv.

    The upstream library can raise `discord.opus.OpusError: corrupted stream` while decoding.
    That exception can crash the PacketRouter thread, which then stops delivery of audio and
    forces reconnect churn.

    This patch makes Opus decode failures drop the offending packet and recreate the decoder
    instance, allowing capture to continue.
    """

    try:
        from discord.ext.voice_recv import opus as vr_opus
    except Exception:
        return

    if getattr(vr_opus, "_DERF_PATCHED_OPUS", False):
        return

    try:
        original_decode_packet = vr_opus.OpusDecoder._decode_packet
    except Exception:
        return

    def _decode_packet_safe(self, packet):
        try:
            return original_decode_packet(self, packet)
        except discord.opus.OpusError as e:
            # Drop the corrupted packet and reset decoder state.
            logger.warning("voice_recv OpusError (dropping packet): %s", e)
            try:
                self._decoder = vr_opus.Decoder()
            except Exception:
                pass
            return packet, b""

    vr_opus.OpusDecoder._decode_packet = _decode_packet_safe
    vr_opus._DERF_PATCHED_OPUS = True


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
    logger.debug(f"Original text: {text}")

    async def replace_match(match: re.Match) -> str:
        user_id = int(match.group(1))
        if ctx.guild is None:
            logger.warning("Guild is None, cannot resolve user ID")
            return f"@unknown-user"

        user = ctx.guild.get_member(user_id)
        if user:
            return f"<@{user.id}>"  # Properly formatted Discord mention
        logger.warning(f"User ID {user_id} not found in guild")
        return f"@unknown-user"

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

    logger.debug(f"Processed text: {text}")
    return text


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
        response = await asyncio.to_thread(redis_client.get, key)
        # Redis returns None when missing; empty strings are valid values and must
        # not cause an infinite wait.
        if response is not None:
            await asyncio.to_thread(redis_client.delete, key)
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


async def start_capture(guild, channel, bot, *, force_restart: bool = False):
    logger.info(f"Starting capture in {channel.name} for bot {bot.name}")
    try:
        # Ensure we don't race multiple start_capture() calls (voice state updates can be noisy).
        capture_lock = getattr(bot, "_voice_capture_lock", None)
        if capture_lock is None:
            capture_lock = asyncio.Lock()
            bot._voice_capture_lock = capture_lock

        async with capture_lock:
            # Prefer an existing VoiceClient from bot.voice_clients (it can exist while
            # guild.voice_client is still None during the handshake).
            vc = next((v for v in bot.voice_clients if v.guild == guild), None) or guild.voice_client

            if vc is None:
                logger.info("Not connected yet. Connecting to voice...")
                vc = await channel.connect(cls=VoiceRecvClient)

            # If a connect is in-flight, wait briefly rather than issuing a second connect.
            if vc is not None and not vc.is_connected():
                deadline = time.time() + 10.0
                while not vc.is_connected() and time.time() < deadline:
                    await asyncio.sleep(0.25)

            if vc is None or not vc.is_connected():
                logger.error("Failed to connect to voice, aborting capture.")
                return

            # Avoid resetting capture on every voice join event. If we are already
            # listening, keep the existing sink to prevent intermittent dropouts.
            # But allow a forced restart for health recovery.
            if vc.is_listening() and not force_restart:
                logger.debug("Already listening; leaving existing sink running.")
                return
            if vc.is_listening() and force_restart:
                logger.warning("Force restarting capture sink.")
                try:
                    vc.stop_listening()
                except Exception:
                    pass

            ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
            # Initialize timestamps so watchdog doesn't treat a fresh sink as "ancient".
            ring_buffer_sink.last_packet_time = time.time()

            # Expose for watchdog/debug
            bot.voice_capture_sink = ring_buffer_sink
            bot.voice_capture_started_at = time.time()
            vc.listen(ring_buffer_sink)
            logger.info(f"Recording started in channel {channel.name}")

        # Note: the sink receives all members by default; we do not need per-member
        # initialization here.

    except Exception as e:
        logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(bot, *, force_reconnect: bool = False):
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

    # Ensure we don't run overlapping connect attempts (watchdog + voice events + playback worker).
    connect_lock = getattr(bot, "_voice_connect_lock", None)
    if connect_lock is None:
        connect_lock = asyncio.Lock()
        bot._voice_connect_lock = connect_lock

    async with connect_lock:
        # Check existing connections in the guild
        current_vc = next((vc for vc in bot.voice_clients if vc.guild == guild), None)

        try:
            if force_reconnect and current_vc:
                # Suppress duplicate reconnect triggers from on_voice_state_update while we intentionally
                # churn the connection for recovery.
                bot._suppress_voice_reconnect_until = time.time() + 10.0

                logger.warning("Force reconnect requested; disconnecting voice client.")
                try:
                    await current_vc.disconnect(force=True)
                except Exception:
                    pass
                current_vc = None

            if not current_vc:
                await voice_channel.connect(cls=VoiceRecvClient)
                logger.info(f"Connected to {voice_channel.name}")
            else:
                # Enforce VoiceRecvClient so receive/capture continues to work even
                # after reconnects initiated elsewhere.
                if not isinstance(current_vc, VoiceRecvClient):
                    logger.warning(
                        "Existing voice client is not VoiceRecvClient; reconnecting with VoiceRecvClient."
                    )
                    await current_vc.disconnect(force=True)
                    await voice_channel.connect(cls=VoiceRecvClient)
                    logger.info(f"Reconnected to {voice_channel.name}")
                    return

                # Check if already connected to the correct channel
                if current_vc.channel and current_vc.channel.id != voice_channel.id:
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


async def voice_capture_watchdog(
    bot,
    *,
    check_interval_seconds: float = 10.0,
    stale_seconds: float = 20.0,
):
    """Best-effort watchdog.

    If discord.ext.voice_recv hits an internal Opus decode failure (e.g. "corrupted stream"),
    the packet router thread can die and capture can silently stop. This watchdog detects
    stale audio delivery and forces a reconnect + sink restart.
    """

    while True:
        try:
            await asyncio.sleep(check_interval_seconds)

            try:
                guild_id = int(os.getenv("GUILD_ID", "0"))
                voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", "0"))
            except ValueError:
                continue

            if not guild_id or not voice_channel_id:
                continue

            guild = discord.utils.get(bot.guilds, id=guild_id)
            if not guild:
                continue

            channel = guild.get_channel(voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                continue

            vc = guild.voice_client
            if not vc or not vc.is_connected():
                continue

            if not vc.is_listening():
                continue

            # If there are no humans, don't churn connections.
            has_humans = any(not m.bot for m in channel.members)
            if not has_humans:
                continue

            sink = getattr(bot, "voice_capture_sink", None)
            if not sink:
                continue

            # Use internal voice_recv thread health rather than "time since audio packet".
            # Discord does not send RTP when nobody is speaking; using RTP timestamps causes
            # constant reconnect loops during normal silence.
            reader = getattr(vc, "_reader", None)
            if reader is None:
                continue

            dead_parts: list[str] = []
            for name in ("packet_router", "event_router", "keepalive"):
                part = getattr(reader, name, None)
                if part is not None and hasattr(part, "is_alive") and not part.is_alive():
                    dead_parts.append(name)

            reader_error = getattr(reader, "error", None)
            if reader_error is not None:
                dead_parts.append("reader_error")

            logger.debug(
                "voice_capture_watchdog: listening=%s humans=%s dead_parts=%s",
                True,
                has_humans,
                dead_parts,
            )

            if dead_parts:
                logger.warning(
                    "voice_capture_watchdog: capture pipeline unhealthy (dead_parts=%s). Reconnecting + restarting capture.",
                    dead_parts,
                )
                await connect_to_voice(bot, force_reconnect=True)
                await start_capture(guild, channel, bot, force_restart=True)

        except Exception as e:
            logger.exception(f"voice_capture_watchdog error: {e}")
