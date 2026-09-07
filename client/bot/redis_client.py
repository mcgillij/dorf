import redis

from bot.config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

# redis-py 8.x defaults socket_timeout to 5s, which breaks long-blocking
# commands (BLPOP/BRPOP in workers). None restores the pre-8.x behavior.
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_timeout=None,
)
