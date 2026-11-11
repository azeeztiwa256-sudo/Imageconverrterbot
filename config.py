import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip().isdigit()]

@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: list[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS", "")))
    max_file_mb: int = int(os.getenv("MAX_FILE_MB", "20"))
    max_batch: int = int(os.getenv("MAX_BATCH", "10"))
    daily_limit: int = int(os.getenv("DAILY_FREE_LIMIT", "100"))
    temp_dir: Path = Path(os.getenv("TEMP_DIR", "/tmp/imageconv"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def validate(self):
        if not self.token:
            raise SystemExit("BOT_TOKEN is not set")
        if not self.admin_ids:
            raise SystemExit("ADMIN_IDS is not set")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

cfg = Config()
cfg.validate()
