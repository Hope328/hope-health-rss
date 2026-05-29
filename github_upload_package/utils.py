from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Australia/Brisbane")


def find_latest_json(directory: str) -> str:
    target_dir = Path(directory).expanduser()

    if not directory.strip():
        raise ValueError("HEALTH_EXPORT_DIR 未配置，请先在 .env 中设置健康导出目录。")
    if not target_dir.exists():
        raise FileNotFoundError(f"健康导出目录不存在：{target_dir}")
    if not target_dir.is_dir():
        raise NotADirectoryError(f"HEALTH_EXPORT_DIR 不是目录：{target_dir}")

    json_files = [
        path for path in target_dir.iterdir() if path.is_file() and path.suffix.lower() == ".json"
    ]

    if not json_files:
        raise FileNotFoundError(
            f"目录 {target_dir} 下没有找到 JSON 文件。请确认 Health Auto Export 已同步到电脑。"
        )

    latest_file = max(json_files, key=lambda item: item.stat().st_mtime)
    return str(latest_file.resolve())


def parse_health_date(date_str: str) -> date:
    cleaned = (date_str or "").strip()
    if not cleaned:
        raise ValueError("健康日期字符串为空。")

    formats = (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=LOCAL_TZ)
            return parsed.astimezone(LOCAL_TZ).date()
        except ValueError:
            continue

    raise ValueError(f"无法解析健康日期：{date_str}")


def safe_round(value, digits: int = 1):
    if value is None:
        return None
    return round(value, digits)


def format_optional(value, suffix: str = "") -> str:
    if value is None:
        return "暂无数据"
    return f"{value}{suffix}"
