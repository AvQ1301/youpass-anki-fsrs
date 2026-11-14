from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8844
    database_path: Path = Path("data/youpass_anki.db")
    listing_url: str = "https://youpass.vn/luyen-thi/ielts/reading?quiz_type=quiz"
    api_url: str = "https://api.youpass.vn"
    exercise_base: str = "https://e-learning.youpass.vn/practice"
    crawler_mode: str = "api"
    crawl_delay: float = 0.35
    crawl_max_retries: int = 3
    crawl_enrich_details: bool = True
    playwright_profile: Path = Path("data/browser-profile")
    anki_url: str = "http://127.0.0.1:8765"
    anki_key: str | None = None
    anki_deck: str = "IELTS Reading - YouPass"
    anki_model: str = "YouPass IELTS Reading"
    review_policy: str = "due_only"
    easy_min: float = 100.0
    good_min: float = 85.0
    hard_min: float = 60.0

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "Settings":
        if env_file:
            load_dotenv(env_file)
        value = cls(
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=int(os.getenv("APP_PORT", "8844")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/youpass_anki.db")),
            listing_url=os.getenv("YOUPASS_LISTING_URL", cls.listing_url),
            api_url=os.getenv("YOUPASS_API_URL", cls.api_url),
            exercise_base=os.getenv("YOUPASS_EXERCISE_BASE", cls.exercise_base),
            crawler_mode=os.getenv("CRAWLER_MODE", "api").lower(),
            crawl_delay=float(os.getenv("CRAWL_DELAY_SECONDS", "0.35")),
            crawl_max_retries=int(os.getenv("CRAWL_MAX_RETRIES", "3")),
            crawl_enrich_details=os.getenv("CRAWL_ENRICH_DETAILS", "true").lower() in {"1", "true", "yes"},
            playwright_profile=Path(os.getenv("PLAYWRIGHT_PROFILE", "data/browser-profile")),
            anki_url=os.getenv("ANKI_CONNECT_URL", "http://127.0.0.1:8765").rstrip("/"),
            anki_key=os.getenv("ANKI_CONNECT_KEY") or None,
            anki_deck=os.getenv("ANKI_DECK", "IELTS Reading - YouPass"),
            anki_model=os.getenv("ANKI_MODEL", "YouPass IELTS Reading"),
            review_policy=os.getenv("REVIEW_POLICY", "due_only"),
            easy_min=float(os.getenv("RATING_EASY_MIN", "100")),
            good_min=float(os.getenv("RATING_GOOD_MIN", "85")),
            hard_min=float(os.getenv("RATING_HARD_MIN", "60")),
        )
        value.validate()
        value.database_path.parent.mkdir(parents=True, exist_ok=True)
        value.playwright_profile.parent.mkdir(parents=True, exist_ok=True)
        return value

    def validate(self) -> None:
        if not (0 <= self.hard_min <= self.good_min <= self.easy_min <= 100):
            raise ValueError("rating thresholds must satisfy 0 <= hard <= good <= easy <= 100")
        if self.review_policy not in {"due_only", "allow_not_due"}:
            raise ValueError("REVIEW_POLICY must be due_only or allow_not_due")
        if self.crawler_mode not in {"api", "playwright"}:
            raise ValueError("CRAWLER_MODE must be api or playwright")
