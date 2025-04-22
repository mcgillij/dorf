import logging


def setup_logging():
    FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=FORMAT,
        handlers=[
            logging.FileHandler("bot.log"),
            logging.StreamHandler(),  # Optional: also print logs to console
        ],
    )
