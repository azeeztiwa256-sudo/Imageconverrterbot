import logging
from config import cfg

def setup_logging():
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    # Telegram client libs are noisy at DEBUG
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

log = logging.getLogger("imageconv")
