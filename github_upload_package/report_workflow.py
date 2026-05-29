from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from config import AppConfig
from health_parser import HealthHistoryAnalyzer
from notifier import send_serverchan
from report_generator import generate_report, generate_weekly_report, normalize_energy_units_in_text
from rss_writer import write_report_rss
from utils import find_latest_json

logger = logging.getLogger(__name__)

ReportKind = Literal["daily", "weekly", "today_partial"]


@dataclass(slots=True)
class ReportResult:
    kind: ReportKind
    title: str
    report: str
    sent: bool
    target_date: str
    debug_path: str | None = None
    rss_path: str | None = None


def _write_debug_summary(analyzer: HealthHistoryAnalyzer, summary: dict, filename: str) -> str:
    debug_summary = analyzer.build_debug_data_summary(summary)
    debug_path = Path(__file__).resolve().parent / filename
    debug_path.write_text(json.dumps(debug_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Debug summary written: %s", debug_path)
    return str(debug_path)


def _send_report(report: str, sendkey: str | None) -> bool:
    report = normalize_energy_units_in_text(report)
    title = report.splitlines()[0].strip() if report.strip() else "Hope 健康报告"
    return send_serverchan(title=title, content=report, sendkey=sendkey or "")


def _publish_rss_report(config: AppConfig, report: str, title: str, guid: str) -> str:
    return write_report_rss(
        report=normalize_energy_units_in_text(report),
        title=title,
        guid=guid,
        output_path=config.rss_output_path,
        feed_title=config.rss_feed_title,
        feed_link=config.rss_feed_link,
        max_items=config.rss_max_items,
    )


def run_daily_report(
    config: AppConfig,
    partial_today: bool = False,
    notify: bool = True,
    publish_rss: bool = False,
) -> ReportResult:
    latest_json = find_latest_json(config.health_export_dir)
    logger.info("Latest health file: %s", latest_json)

    analyzer = HealthHistoryAnalyzer(config.health_export_dir)
    if partial_today:
        summary = analyzer.build_summary(days=config.report_days, auto_switch_incomplete_today=False)
    else:
        summary = analyzer.build_summary(days=config.report_days, auto_switch_incomplete_today=True)

    logger.info("Parsed target date: %s", summary.get("target_date"))
    if summary["selection_meta"].get("auto_switched_from_today_partial"):
        logger.info(
            "Today is incomplete; auto-switched report target from %s to %s.",
            summary["selection_meta"].get("latest_file_date"),
            summary["selection_meta"].get("selected_target_date"),
        )

    debug_name = "debug_data_summary_today_partial.json" if partial_today else "debug_data_summary.json"
    debug_path = _write_debug_summary(analyzer, summary, debug_name)

    report = generate_report(
        summary=summary,
        api_key=config.openai_api_key,
        model=config.openai_model,
        timeout_seconds=config.openai_timeout_seconds,
    )
    report = normalize_energy_units_in_text(report)
    logger.info("Report generated.")
    title = report.splitlines()[0].strip()
    sent = False
    if notify:
        sent = _send_report(report, config.serverchan_sendkey)
        if sent:
            logger.info("Notification sent.")
        else:
            logger.info("Notification skipped or not sent.")
    else:
        logger.info("Notification disabled.")

    rss_path = None
    if publish_rss:
        guid_prefix = "health-today-partial" if partial_today else "health-daily"
        rss_path = _publish_rss_report(config, report, title, f"{guid_prefix}-{summary['target_date']}")
        logger.info("RSS feed updated: %s", rss_path)

    return ReportResult(
        kind="today_partial" if partial_today else "daily",
        title=title,
        report=report,
        sent=sent,
        target_date=summary["target_date"],
        debug_path=debug_path,
        rss_path=rss_path,
    )


def run_weekly_report(config: AppConfig, notify: bool = True, publish_rss: bool = False) -> ReportResult:
    latest_json = find_latest_json(config.health_export_dir)
    logger.info("Latest health file: %s", latest_json)

    analyzer = HealthHistoryAnalyzer(config.health_export_dir)
    summary = analyzer.build_summary(days=config.report_days, auto_switch_incomplete_today=True)
    logger.info("Weekly report target date: %s", summary.get("target_date"))
    if summary["selection_meta"].get("auto_switched_from_today_partial"):
        logger.info(
            "Today is incomplete; weekly base target switched from %s to %s.",
            summary["selection_meta"].get("latest_file_date"),
            summary["selection_meta"].get("selected_target_date"),
        )

    debug_path = _write_debug_summary(analyzer, summary, "debug_weekly_summary.json")
    report = normalize_energy_units_in_text(generate_weekly_report(summary))
    logger.info("Weekly report generated.")
    title = report.splitlines()[0].strip()
    sent = False
    if notify:
        sent = _send_report(report, config.serverchan_sendkey)
        if sent:
            logger.info("Notification sent.")
        else:
            logger.info("Notification skipped or not sent.")
    else:
        logger.info("Notification disabled.")

    rss_path = None
    if publish_rss:
        rss_path = _publish_rss_report(
            config,
            report,
            title,
            f"health-weekly-{summary['file_window_start']}-{summary['file_window_end']}",
        )
        logger.info("RSS feed updated: %s", rss_path)

    return ReportResult(
        kind="weekly",
        title=title,
        report=report,
        sent=sent,
        target_date=summary["target_date"],
        debug_path=debug_path,
        rss_path=rss_path,
    )
