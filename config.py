from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"


@dataclass(slots=True)
class AppConfig:
    health_export_dir: str
    openai_api_key: str | None
    openai_model: str
    openai_timeout_seconds: int
    rss_output_path: str
    rss_feed_title: str
    rss_feed_link: str
    rss_max_items: int
    report_days: int


def _to_positive_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        n = int(value)
    except ValueError:
        return default
    return n if n > 0 else default


def load_config() -> AppConfig:
    if DOTENV_PATH.exists():
        load_dotenv(DOTENV_PATH, encoding="utf-8-sig")

    return AppConfig(
        health_export_dir=os.getenv("HEALTH_EXPORT_DIR", "health_exports").strip() or "health_exports",
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        openai_timeout_seconds=_to_positive_int(os.getenv("OPENAI_TIMEOUT_SECONDS"), 8),
        rss_output_path=os.getenv("RSS_OUTPUT_PATH", "public/health-report.xml").strip() or "public/health-report.xml",
        rss_feed_title=os.getenv("RSS_FEED_TITLE", "Hope Health Report").strip() or "Hope Health Report",
        rss_feed_link=os.getenv("RSS_FEED_LINK", "https://example.com/health-report.xml").strip() or "https://example.com/health-report.xml",
        rss_max_items=_to_positive_int(os.getenv("RSS_MAX_ITEMS"), 30),
        report_days=_to_positive_int(os.getenv("REPORT_DAYS"), 7),
    )
