from __future__ import annotations

import argparse
from datetime import datetime

from config import load_config
from health_parser import build_summary
from report_generator import generate_report
from rss_writer import update_feed


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate health RSS report")
    p.add_argument("--weekly", action="store_true", help="Generate weekly report")
    p.add_argument("--rss-only", action="store_true", help="Only write RSS output")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config()

    summary = build_summary(cfg.health_export_dir, cfg.report_days)
    report = generate_report(
        summary,
        api_key=cfg.openai_api_key,
        model=cfg.openai_model,
        timeout_seconds=cfg.openai_timeout_seconds,
        weekly=args.weekly,
    )

    if args.weekly:
        title = f"Hope Weekly Report | {summary['window']['start']} to {summary['window']['end']}"
        guid = f"weekly-{summary['window']['start']}-{summary['window']['end']}"
    else:
        title = f"Hope Daily Report | {summary['target_date']}"
        guid = f"daily-{summary['target_date']}"

    output = update_feed(
        output_path=cfg.rss_output_path,
        feed_title=cfg.rss_feed_title,
        feed_link=cfg.rss_feed_link,
        title=title,
        guid=guid,
        description=report,
        max_items=cfg.rss_max_items,
    )

    if not args.rss_only:
        print(report)
    print(f"RSS updated: {output} @ {datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
