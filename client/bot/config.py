import os
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")  # To LLM
if not AUTH_TOKEN:
    raise ValueError(
        "AUTH_TOKEN is missing. Please set it in the environment variables."
    )
NIC_DISCORD_BOT_TOKEN = os.getenv("NIC_DISCORD_BOT_TOKEN", "")
if not NIC_DISCORD_BOT_TOKEN:
    raise ValueError(
        "NIC_DISCORD_BOT_TOKEN is missing. Please set it in the environment variables."
    )
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
if not DISCORD_BOT_TOKEN:
    raise ValueError(
        "DISCORD_BOT_TOKEN is missing. Please set it in the environment variables."
    )
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Constants for LLM API interaction
LLM_HOST = os.getenv("LLM_HOST", "")
# Discord guild id
GUILD_ID = os.getenv("GUILD_ID", "")
# Voice Channel id
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", ""))
# Chat channel id
CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", ""))
WHEREAMI = os.getenv("WHEREAMI", "")


class AvatarState(Enum):
    IDLE = "idle"
    TALKING = "talking"
    THINKING = "thinking"
    DRAWING = "drawing"
