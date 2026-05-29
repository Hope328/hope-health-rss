from __future__ import annotations

import argparse
import logging
import sys

from config import load_config
from report_workflow import run_daily_report, run_weekly_report

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Apple Health daily or weekly reports.")
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Generate and send the weekly report.",
    )
    parser.add_argument(
        "--today-partial",
        action="store_true",
        help="Generate and send a partial report for today instead of auto-switching to yesterday.",
    )
    parser.add_argument(
        "--rss",
        action="store_true",
        help="Also publish the generated report into the RSS feed.",
    )
    parser.add_argument(
        "--rss-only",
        action="store_true",
        help="Publish the generated report into the RSS feed without push notifications.",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Do not send push notifications.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()

    try:
        args = parse_args()
        config = load_config()
        notify = not (args.no_notify or args.rss_only)
        publish_rss = args.rss or args.rss_only

        if args.weekly:
            result = run_weekly_report(config, notify=notify, publish_rss=publish_rss)
        else:
            result = run_daily_report(
                config,
                partial_today=args.today_partial,
                notify=notify,
                publish_rss=publish_rss,
            )

        if not args.rss_only:
            print(result.report)
        if result.rss_path:
            logger.info("RSS feed: %s", result.rss_path)
        return 0
    except Exception as exc:
        logger.error("程序运行失败：%s", exc)
        logger.debug("程序异常详情", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
