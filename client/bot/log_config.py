import logging


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    FORMAT = "%(asctime)s - %(message)s"
    logging.basicConfig(format=FORMAT)
    logger.addHandler(logging.FileHandler("derf.log"))
    logger.setLevel(logging.DEBUG)
    return logger
