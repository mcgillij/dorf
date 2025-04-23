from bot.config import WHEREAMI

# Define constants for queue names
DERF_RESPONSE_QUEUE = "voice_response_queue"
DERF_RESPONSE_KEY_PREFIX = "response_queue"
DERF_SUMMARIZER_QUEUE = "summarizer_queue"

NIC_RESPONSE_QUEUE = "voice_nic_response_queue"
NIC_RESPONSE_KEY_PREFIX = "response_nic_queue"
NIC_SUMMARIZER_QUEUE = "summarizer_nic_queue"

# Redis queue names as constants
WHISPER_QUEUE = "whisper_queue"
VOICE_RESPONSE_QUEUE = "voice_response_queue"
VOICE_NIC_RESPONSE_QUEUE = "voice_nic_response_queue"

# TTS Voice Settings
TTS_ENGINE = "kokoro"  # or use the mimic3 docker container
TTS_VOICE = "am_adam"
TTS_VOICE_NICOLE = "af_nicole"

# LLM Search assist
MAX_PREDICTION_ROUNDS = 10

# LLM Responses
LONG_RESPONSE_THRESHOLD = 1000
# Commands
EMOJI_DB = "emojis.db"

# temporary conditional based on my local env
WORKSPACE = ""
if WHEREAMI == "server":
    WORKSPACE = "birthright"
else:
    WORKSPACE = "a-new-workspace"  # used on my desktop

NIC_WORKSPACE = "nic"
SESSION_ID = "my-session-id"
NIC_SESSION_ID = "my-session-id"

SPACK_DIR = "spack/"
FRIEREN_DIR = "frieren/"

# Leveling
XP_DB = "xp_users.db"
XP_COOLDOWN_SECONDS = 10  # 1 minute cooldown between XP gains per user

LEVEL_THRESHOLDS = lambda lvl: 5 * (lvl**2) + 50 * lvl + 100

LEVEL_ROLE_MAPPING = {
    0: 1364048765727801344,  # Wanderer
    5: 1364042278930219120,  # Noob
    10: 1364042608757837965,  # Scrub
    15: 1364045146521600041,  # Squire
    20: 1364042802643865621,  # Knight
    25: 1364043033036722276,  # Spellblade
    30: 1364045512659046410,  # Berzerker
    35: 1364045794180726825,  # Paladin
    40: 1364043151475605655,  # Archmage
    45: 1364043083590795397,  # Dragonlord
    50: 1364043193003544690,  # Einherjar
}

# filtered words from bot responses
FILTERED_KEYWORDS = {
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

FILTERED_RESPONSES = [
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
