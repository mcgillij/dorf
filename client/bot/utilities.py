import re
import os
import uuid
from random import randint, choice
import aiohttp
import asyncio
import traceback
import logging
import time

import discord
from discord.ext.voice_recv import VoiceRecvClient
from bot.redis_client import redis_client
from bot.config import LLM_HOST
from bot.constants import FILTERED_KEYWORDS
from bot.audio_capture import RingBufferAudioSink

timeout = aiohttp.ClientTimeout(total=int(os.getenv("LLM_TIMEOUT_S", "60")))


class LLMRequestError(Exception):
    """The LLM backend failed after retries (timeout/unreachable/error status)."""


logger = logging.getLogger(__name__)


def patch_voice_recv_opus_decoder() -> None:
    """Runtime patches for discord-ext-voice-recv.

    1. Opus decode failures must never kill the PacketRouter thread ("corrupted
       stream" would stop all audio delivery) — drop the packet and reset.
    2. Discord voice is end-to-end encrypted with DAVE (MLS). Calls do NOT
       downgrade when a non-DAVE receiver (this bot) is present, so after
       transport decryption every real voice frame is still MLS ciphertext and
       opus decode fails on ~all of them. davey (shipped with
       discord.py[voice]) already maintains the DaveSession for sending; this
       patch reuses it to decrypt inbound frames per sender, mirroring the
       upstream PR (imayhaveborkedit/discord-ext-voice-recv#58).
    """

    try:
        from discord.ext.voice_recv import opus as vr_opus
    except Exception:
        return

    if not getattr(vr_opus, "_DERF_PATCHED_OPUS", False):
        patched_any = False

        # Newer discord-ext-voice-recv versions use PacketDecoder.
        PacketDecoder = getattr(vr_opus, "PacketDecoder", None)
        if PacketDecoder is not None:
            try:
                original_pop_data = PacketDecoder.pop_data

                def pop_data_safe(self, *args, **kwargs):
                    try:
                        return original_pop_data(self, *args, **kwargs)
                    except discord.opus.OpusError as e:
                        # Important: do not let this escape, or PacketRouter thread dies.
                        logger.warning("voice_recv OpusError (dropping packet): %s", e)
                        try:
                            self.reset()
                        except Exception:
                            pass
                        return None

                PacketDecoder.pop_data = pop_data_safe
                patched_any = True
            except Exception:
                pass

            # Extra safety: patch _decode_packet too (some versions may call it in other contexts).
            try:
                original_decode_packet = PacketDecoder._decode_packet

                def decode_packet_safe(self, packet):
                    try:
                        return original_decode_packet(self, packet)
                    except discord.opus.OpusError as e:
                        logger.warning(
                            "voice_recv OpusError in _decode_packet (dropping): %s", e
                        )
                        try:
                            # Recreate decoder to reset state.
                            self._decoder = vr_opus.Decoder()
                        except Exception:
                            pass
                        return packet, b""

                PacketDecoder._decode_packet = decode_packet_safe
                patched_any = True
            except Exception:
                pass

        # Backward compatibility: older versions used OpusDecoder.
        OpusDecoder = getattr(vr_opus, "OpusDecoder", None)
        if OpusDecoder is not None:
            try:
                original_decode_packet = OpusDecoder._decode_packet

                def _decode_packet_safe(self, packet):
                    try:
                        return original_decode_packet(self, packet)
                    except discord.opus.OpusError as e:
                        logger.warning("voice_recv OpusError (dropping packet): %s", e)
                        try:
                            self._decoder = vr_opus.Decoder()
                        except Exception:
                            pass
                        return packet, b""

                OpusDecoder._decode_packet = _decode_packet_safe
                patched_any = True
            except Exception:
                pass

        vr_opus._DERF_PATCHED_OPUS = bool(patched_any)

    if not getattr(vr_opus, "_DERF_PATCHED_DAVE", False):
        _patch_voice_recv_dave(vr_opus)


def _patch_voice_recv_dave(vr_opus) -> None:
    """Decrypt DAVE (E2EE / MLS) frames on the receive path.

    After Discord's DAVE rollout the transport-decrypted payload is still
    MLS ciphertext for every real voice frame; without this patch opus decode
    fails with 'corrupted stream' on essentially all of them (only unencrypted
    silence/keepalive frames decode, which is why capture appeared to work in
    brief stretches).
    """
    try:
        import davey
    except ImportError:
        logger.info("davey not installed; skipping DAVE decrypt patch.")
        return

    PacketDecoder = getattr(vr_opus, "PacketDecoder", None)
    if PacketDecoder is None:
        return

    def _dave_decrypt(self, packet) -> None:
        data = getattr(packet, "decrypted_data", None)
        if not packet or not data:
            return

        state = getattr(self.sink.voice_client, "_connection", None)
        session = getattr(state, "dave_session", None)
        if (
            session is None
            or not session.ready
            or getattr(state, "dave_protocol_version", 0) == 0
        ):
            return

        user_id = self._cached_id
        if user_id is None:
            # SSRC not mapped to a user yet — without the sender we cannot
            # pick the right ratchet; leave the frame for normal handling.
            return

        try:
            packet.decrypted_data = session.decrypt(
                int(user_id), davey.MediaType.audio, bytes(data)
            )
        except Exception as e:
            # Expected for passthrough (unencrypted) frames; anything else is
            # still better decoded-or-dropped than crashing the router.
            logger.debug("DAVE decrypt failed for ssrc %s: %s", self.ssrc, e)

    def _process_packet_with_dave(self, packet):
        # Resolve the sender FIRST: DAVE decryption needs the user id to pick
        # the right ratchet, and the stock implementation decodes before the
        # SSRC is ever mapped to a user.
        member = self._get_cached_member()
        if member is None:
            self._cached_id = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
            member = self._get_cached_member()

        self._dave_decrypt(packet)

        pcm = None
        if not self.sink.wants_opus():
            packet, pcm = self._decode_packet(packet)

        data = vr_opus.VoiceData(packet, member, pcm=pcm)
        self._last_seq = packet.sequence
        self._last_ts = packet.timestamp
        return data

    PacketDecoder._dave_decrypt = _dave_decrypt
    PacketDecoder._process_packet = _process_packet_with_dave
    vr_opus._DERF_PATCHED_DAVE = True
    logger.info("Patched voice_recv with DAVE (E2EE) receive decryption.")


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


async def preprocess_mentions(ctx, message: str) -> tuple[str, dict]:
    """
    Replace Discord mentions with placeholders and return the mapping.
    Returns: (processed_message, mention_map)
    """
    import re

    mention_map = {}
    placeholder_counter = 1

    def replace_mention(match):
        nonlocal placeholder_counter
        user_id = match.group(1)
        placeholder = f"{{{{user{placeholder_counter}}}}}"  # e.g., {{user1}}

        # Store original mention
        mention_map[placeholder] = f"<@{user_id}>"

        placeholder_counter += 1
        return placeholder

    # Regex for user mentions (supports <@user_id> and <@!user_id>)
    processed_message = re.sub(r"<@!?(\d+)>", replace_mention, message)
    return processed_message, mention_map


async def postprocess_mentions(ctx, response: str, mention_map: dict) -> str:
    """
    Replace placeholders in LLM response with original mentions.
    Also handle any @IDs that match the mapped users.
    """
    # First, replace placeholders
    for placeholder, mention in mention_map.items():
        response = response.replace(placeholder, mention)

    # Then, replace any @IDs that are in the map
    import re

    def replace_id(match):
        user_id = match.group(1)
        mention = f"<@{user_id}>"
        # Check if this ID is in our map (as value)
        for m in mention_map.values():
            if m == mention:
                return mention
        return match.group(0)  # Leave as is if not in map

    response = re.sub(r"@(\d+)", replace_id, response)
    return response


def filter_message(message: str) -> bool:
    return any(keyword.lower() in message.lower() for keyword in FILTERED_KEYWORDS)


def generate_unique_id(ctx, message: str) -> str:
    """Generates a unique ID for a text-command request.

    Previously this was an md5 of context+message, which collided whenever the
    same user sent the same message twice; identical IDs then shared the same
    response key and mention map. uuid4 has no such collision.
    """
    return uuid.uuid4().hex


async def poll_redis_for_key(key: str, timeout: float = 0.5) -> str:
    """Polls Redis for a key and returns its value when found.

    Note: this is intentionally unbounded (used by legacy flows). For bounded waits,
    use `poll_redis_for_key_with_timeout`.
    """

    while True:
        response = await asyncio.to_thread(redis_client.get, key)
        # Redis returns None when missing; empty strings are valid values and must
        # not cause an infinite wait.
        if response is not None:
            await asyncio.to_thread(redis_client.delete, key)
            return response.decode("utf-8") if isinstance(response, bytes) else response
        await asyncio.sleep(timeout)


async def poll_redis_for_key_with_timeout(
    key: str,
    *,
    poll_interval_s: float = 0.5,
    max_wait_s: float = 15.0,
    delete: bool = True,
) -> str | None:
    """Poll Redis for `key` for up to `max_wait_s` seconds.

    Returns:
        - The decoded value (including empty string) if the key is found.
        - None if the deadline elapses.
    """

    deadline = time.monotonic() + max_wait_s
    while True:
        response = await asyncio.to_thread(redis_client.get, key)
        if response is not None:
            if delete:
                await asyncio.to_thread(redis_client.delete, key)
            return response.decode("utf-8") if isinstance(response, bytes) else response

        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(poll_interval_s)


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
                logger.error("Summarizer request timed out.")
                return ""
            except Exception as e:
                logger.error(f"Exception during summarizer API call: {e}")
                return ""

    async def get_response(self, message: str) -> str:
        """Ask the LLM workspace for a response.

        Retries once, then raises LLMRequestError so callers can requeue/dead-
        letter the request instead of speaking an error message aloud.
        """
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

        attempts = 2
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, headers=headers, json=data
                    ) as response:
                        if response.status == 200:
                            json_response = await response.json()
                            return json_response.get("textResponse", "") or ""
                        body = await response.text()
                        logger.error(
                            "llm.error attempt=%s status=%s body=%s",
                            attempt,
                            response.status,
                            (body[:500] + "…") if len(body) > 500 else body,
                        )
                        last_error = LLMRequestError(f"status {response.status}")
            except asyncio.TimeoutError:
                logger.error("llm.timeout attempt=%s", attempt)
                last_error = LLMRequestError("timeout")
            except LLMRequestError:
                raise
            except Exception as e:
                logger.error("llm.exception attempt=%s error=%s", attempt, e)
                last_error = LLMRequestError(str(e))

            if attempt < attempts:
                await asyncio.sleep(1.0)

        raise last_error or LLMRequestError("unknown error")


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
            vc = (
                next((v for v in bot.voice_clients if v.guild == guild), None)
                or guild.voice_client
            )

            if vc is not None and not vc.is_connected():
                # Wait briefly for an in-flight connect before treating it as a zombie.
                deadline = time.time() + 10.0
                while not vc.is_connected() and time.time() < deadline:
                    await asyncio.sleep(0.25)
                if not vc.is_connected():
                    # A dead client left in place would make later connect attempts
                    # believe we are already connected; tear it down and reconnect.
                    logger.warning(
                        "Stale/disconnected voice client; cleaning up and reconnecting."
                    )
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    vc = None

            if vc is None:
                logger.info("Not connected yet. Connecting to voice...")
                vc = await channel.connect(cls=VoiceRecvClient)
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

            # 48kHz stereo s16le is ~192 KB/s; the buffer must hold a full
            # max_chunk_seconds (10s ≈ 1.92 MB) without overwriting its own
            # start. 4 MiB ≈ 22s of headroom.
            ring_buffer_sink = RingBufferAudioSink(
                bot=bot,
                buffer_size=4 * 1024 * 1024,
                output_dir=os.getenv("USER_AUDIO_DIR", "user_audio"),
            )
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

            # A client that lingers after a failed handshake would make the checks
            # below believe we are connected; drop it and start fresh.
            if current_vc and not current_vc.is_connected():
                logger.warning(
                    "Existing voice client is disconnected; cleaning up and reconnecting."
                )
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
                # Check if already connected to the correct channel
                elif current_vc.channel and current_vc.channel.id != voice_channel.id:
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

            # Every reconnect path must end with a live listener, otherwise the bot
            # stays connected but deaf until a human re-joins the channel.
            await start_capture(guild, voice_channel, bot)
        except Exception as e:
            logger.exception(f"Connection error: {str(e)}")


async def voice_capture_watchdog(bot, *, check_interval_seconds: float = 10.0):
    """Best-effort watchdog.

    If discord.ext.voice_recv hits an internal Opus decode failure (e.g. "corrupted stream"),
    the packet router thread can die and capture can silently stop. This watchdog detects
    stale audio delivery and forces a reconnect + sink restart. It also revives dead
    connections and dead listeners while humans are present.
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

            # If there are no humans, don't churn connections.
            has_humans = any(not m.bot for m in channel.members)
            if not has_humans:
                continue

            vc = (
                next((v for v in bot.voice_clients if v.guild == guild), None)
                or guild.voice_client
            )

            # Rescue missing/disconnected clients: previously the watchdog only
            # checked listener thread health, so a dead connection or a listener
            # killed by voice_client.stop() stayed dead until a human re-joined.
            if vc is None or not vc.is_connected():
                logger.warning(
                    "voice_capture_watchdog: no connected voice client while humans present; reconnecting."
                )
                await connect_to_voice(bot)
                continue

            if not vc.is_listening():
                logger.warning(
                    "voice_capture_watchdog: connected but not listening; restarting capture."
                )
                await start_capture(guild, channel, bot)
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
                if (
                    part is not None
                    and hasattr(part, "is_alive")
                    and not part.is_alive()
                ):
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

        except Exception as e:
            logger.exception(f"voice_capture_watchdog error: {e}")
