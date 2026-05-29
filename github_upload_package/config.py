from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"
DOTENV_EXAMPLE_PATH = BASE_DIR / ".env.example"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_REPORT_DAYS = 7
DEFAULT_OPENAI_TIMEOUT_SECONDS = 4
DEFAULT_RSS_OUTPUT_PATH = "public/health-report.xml"
DEFAULT_RSS_MAX_ITEMS = 30


@dataclass(slots=True)
class AppConfig:
    health_export_dir: str
    openai_api_key: str | None
    openai_model: str
    serverchan_sendkey: str | None
    report_days: int
    trigger_host: str
    trigger_port: int
    trigger_token: str | None
    openai_timeout_seconds: int
    rss_output_path: str
    rss_feed_title: str
    rss_feed_link: str
    rss_max_items: int


def _parse_positive_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


def load_config() -> AppConfig:
    if DOTENV_PATH.exists():
        load_dotenv(DOTENV_PATH, encoding="utf-8-sig")
    elif DOTENV_EXAMPLE_PATH.exists():
        load_dotenv(DOTENV_EXAMPLE_PATH, encoding="utf-8-sig")

    return AppConfig(
        health_export_dir=os.getenv("HEALTH_EXPORT_DIR", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        serverchan_sendkey=os.getenv("SERVERCHAN_SENDKEY", "").strip() or None,
        report_days=_parse_positive_int(os.getenv("REPORT_DAYS"), DEFAULT_REPORT_DAYS),
        trigger_host=os.getenv("TRIGGER_HOST", "127.0.0.1").strip() or "127.0.0.1",
        trigger_port=_parse_positive_int(os.getenv("TRIGGER_PORT"), 8787),
        trigger_token=os.getenv("TRIGGER_TOKEN", "").strip() or None,
        openai_timeout_seconds=_parse_positive_int(os.getenv("OPENAI_TIMEOUT_SECONDS"), DEFAULT_OPENAI_TIMEOUT_SECONDS),
        rss_output_path=os.getenv("RSS_OUTPUT_PATH", DEFAULT_RSS_OUTPUT_PATH).strip() or DEFAULT_RSS_OUTPUT_PATH,
        rss_feed_title=os.getenv("RSS_FEED_TITLE", "Hope 减脂健康报告").strip() or "Hope 减脂健康报告",
        rss_feed_link=os.getenv("RSS_FEED_LINK", "https://example.com/health-report.xml").strip()
        or "https://example.com/health-report.xml",
        rss_max_items=_parse_positive_int(os.getenv("RSS_MAX_ITEMS"), DEFAULT_RSS_MAX_ITEMS),
    )
