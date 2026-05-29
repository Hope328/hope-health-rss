from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from utils import parse_health_date, safe_round

logger = logging.getLogger(__name__)

FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
COMPLETE_DAY_CUTOFF = time(hour=20, minute=0)
EARLY_INCOMPLETE_CUTOFF = time(hour=3, minute=0)
TODAY_COMPLETE_ALLOWED_AFTER = time(hour=21, minute=0)
LOCAL_TZ = ZoneInfo("Australia/Brisbane")
PREFERRED_ACTIVITY_SOURCE_KEYWORDS = ("小米运动健康", "小米", "xiaomi", "mi fitness")
ESTIMATED_STRIDE_METERS = 0.62
MIN_REASONABLE_KM_PER_1000_STEPS = 0.35

CUMULATIVE_METRICS: dict[str, dict[str, Any]] = {
    "steps": {
        "source_names": ["step_count"],
        "unit_digits": 0,
        "min_record_count": 6,
        "output_field": "steps",
    },
    "distance": {
        "source_names": ["walking_running_distance"],
        "unit_digits": 2,
        "min_record_count": 1,
        "output_field": "distance_km",
    },
    "active_energy": {
        "source_names": ["active_energy", "active_energy_burned"],
        "unit_digits": 1,
        "min_record_count": 1,
        "output_field": "active_energy",
    },
    "resting_energy": {
        "source_names": ["basal_energy_burned"],
        "unit_digits": 1,
        "min_record_count": 6,
        "output_field": "resting_energy",
    },
    "exercise_minutes": {
        "source_names": ["apple_exercise_time", "exercise_time"],
        "unit_digits": 1,
        "min_record_count": 1,
        "output_field": "exercise_minutes",
    },
    "stand_hours": {
        "source_names": ["apple_stand_hour", "stand_hour"],
        "unit_digits": 1,
        "min_record_count": 1,
        "output_field": "stand_hours",
    },
}

NON_CUMULATIVE_METRICS: dict[str, dict[str, Any]] = {
    "sleep": {"field": "sleep_hours", "digits": 2},
    "heart_rate": {"field": "heart_avg", "digits": 1},
    "workouts": {"field": "workouts", "digits": None},
    "hrv": {"field": "hrv", "digits": 1},
    "weight": {"field": "weight", "digits": 2},
}


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _round_number(value: float | None, digits: int = 1) -> int | float | None:
    if value is None:
        return None
    rounded = round(float(value), digits)
    if digits == 0 or float(rounded).is_integer():
        return int(round(rounded))
    return rounded


def _energy_qty_to_kcal(qty: float, unit: str | None) -> float:
    clean_unit = (unit or "").strip().lower()
    if clean_unit in {"kj", "kilojoule", "kilojoules"}:
        return qty / 4.184
    if clean_unit in {"kcal", "cal", "calorie", "calories"}:
        return qty
    return qty


def _mean_or_none(values: list[float], digits: int = 1) -> int | float | None:
    if not values:
        return None
    return _round_number(mean(values), digits)


def _percent_change(today_value: int | float | None, average_value: int | float | None) -> float | None:
    if today_value is None or average_value in (None, 0):
        return None
    return round(((float(today_value) - float(average_value)) / float(average_value)) * 100, 1)


def _estimate_distance_from_steps(steps: int | float | None) -> float | None:
    if steps is None:
        return None
    return safe_round(float(steps) * ESTIMATED_STRIDE_METERS / 1000, 2)


def _distance_is_plausible_for_steps(steps: int | float | None, distance_km: int | float | None) -> bool:
    if steps is None or distance_km is None:
        return True
    if float(steps) < 1000:
        return True
    km_per_1000_steps = float(distance_km) * 1000 / float(steps)
    return km_per_1000_steps >= MIN_REASONABLE_KM_PER_1000_STEPS


def _trend_reliability_label(complete_days: int, window_days: int) -> str:
    if complete_days >= window_days and window_days > 0:
        return "高"
    if complete_days >= 3:
        return "中"
    return "低"


def _parse_filename_date(path: Path) -> date | None:
    match = FILENAME_DATE_RE.search(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()


def _parse_health_datetime(date_str: str) -> datetime | None:
    cleaned = (date_str or "").strip()
    if not cleaned:
        return None

    formats = (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=LOCAL_TZ)
            return parsed.astimezone(LOCAL_TZ)
        except ValueError:
            continue
    return None


def _entry_datetime(entry: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = entry.get(key)
        if value:
            parsed = _parse_health_datetime(str(value))
            if parsed is not None:
                return parsed
    return None


def _entry_start_datetime(entry: dict[str, Any]) -> datetime | None:
    return _entry_datetime(entry, "startDate", "start", "date")


def _entry_end_datetime(entry: dict[str, Any]) -> datetime | None:
    return _entry_datetime(entry, "endDate", "end", "date")


def _entry_source(entry: dict[str, Any]) -> str:
    for key in ("sourceName", "source", "sourceBundleId", "device"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "未知来源"


def _is_preferred_activity_source(source: str) -> bool:
    lowered = source.lower()
    return any(keyword.lower() in lowered for keyword in PREFERRED_ACTIVITY_SOURCE_KEYWORDS)


def _choose_activity_source_value(
    source_breakdown: dict[str, dict[str, Any]],
    value_key: str,
) -> tuple[str | None, float | None]:
    if not source_breakdown:
        return None, None

    preferred_items = [
        (source, detail)
        for source, detail in source_breakdown.items()
        if _is_preferred_activity_source(source) and detail.get(value_key) is not None
    ]
    candidates = preferred_items or [
        (source, detail)
        for source, detail in source_breakdown.items()
        if detail.get(value_key) is not None
    ]
    if not candidates:
        return None, None

    source, detail = max(candidates, key=lambda item: float(item[1].get(value_key) or 0))
    return source, float(detail[value_key])


def extract_active_energy(metrics: list[dict[str, Any]], target_date: date | None = None) -> dict[str, Any]:
    """
    从 AutoExportHealth JSON 的 metrics 中提取 active_energy。
    返回 kcal、原始数值、单位和来源；如果同一天有多条记录，会先加总再换算。
    """
    total_kcal = 0.0
    raw_total = 0.0
    raw_unit: str | None = None
    sources: list[str] = []
    source_breakdown: dict[str, dict[str, Any]] = {}
    points: list[dict[str, Any]] = []
    first_dt: datetime | None = None
    last_dt: datetime | None = None

    for metric in metrics or []:
        if not isinstance(metric, dict):
            continue
        if metric.get("name") not in {"active_energy", "active_energy_burned"}:
            continue

        unit = metric.get("units") if isinstance(metric.get("units"), str) else None
        for item in metric.get("data") or []:
            if not isinstance(item, dict):
                continue
            qty = _to_number(item.get("qty"))
            start_dt = _entry_start_datetime(item)
            end_dt = _entry_end_datetime(item) or start_dt
            if qty is None or start_dt is None:
                continue
            if target_date is not None and start_dt.date() != target_date:
                continue

            if raw_unit is None:
                raw_unit = unit
            raw_total += float(qty)
            kcal = _energy_qty_to_kcal(float(qty), unit)
            total_kcal += kcal
            first_dt = start_dt if first_dt is None or start_dt < first_dt else first_dt
            last_dt = end_dt if last_dt is None or end_dt > last_dt else last_dt

            source = _entry_source(item)
            if source not in sources:
                sources.append(source)
            source_info = source_breakdown.setdefault(
                source,
                {
                    "kcal": 0.0,
                    "raw_qty": 0.0,
                    "raw_unit": unit,
                    "record_count": 0,
                    "first_record_time": None,
                    "last_record_time": None,
                },
            )
            source_info["kcal"] += kcal
            source_info["raw_qty"] += float(qty)
            source_info["record_count"] += 1
            if source_info["first_record_time"] is None or start_dt < source_info["first_record_time"]:
                source_info["first_record_time"] = start_dt
            if source_info["last_record_time"] is None or end_dt > source_info["last_record_time"]:
                source_info["last_record_time"] = end_dt
            points.append(
                {
                    "time": start_dt.timetz().replace(tzinfo=None),
                    "qty": kcal,
                    "raw_qty": float(qty),
                    "source": source,
                }
            )

    if not points:
        return {
            "kcal": None,
            "raw_qty": None,
            "raw_unit": raw_unit,
            "source": None,
            "record_count": 0,
            "first_record_time": None,
            "last_record_time": None,
            "coverage_hours": None,
            "source_breakdown": {},
            "points": [],
        }

    for source_info in source_breakdown.values():
        source_info["kcal"] = _round_number(source_info["kcal"], 1)
        source_info["raw_qty"] = safe_round(source_info["raw_qty"], 2)

    adopted_source, adopted_kcal = _choose_activity_source_value(source_breakdown, "kcal")
    adopted_raw_qty = None
    adopted_raw_unit = raw_unit
    if adopted_source is not None:
        adopted_detail = source_breakdown[adopted_source]
        adopted_raw_qty = adopted_detail.get("raw_qty")
        adopted_raw_unit = adopted_detail.get("raw_unit") or raw_unit
    else:
        adopted_source = "、".join(sources) if sources else None
        adopted_kcal = total_kcal
        adopted_raw_qty = raw_total

    return {
        "kcal": _round_number(adopted_kcal, 1),
        "raw_qty": safe_round(adopted_raw_qty, 2) if adopted_raw_qty is not None else None,
        "raw_unit": adopted_raw_unit,
        "source": adopted_source,
        "record_count": len(points),
        "first_record_datetime": first_dt,
        "last_record_datetime": last_dt,
        "first_record_time": first_dt.timetz().replace(tzinfo=None) if first_dt is not None else None,
        "last_record_time": last_dt.timetz().replace(tzinfo=None) if last_dt is not None else None,
        "coverage_hours": round((last_dt - first_dt).total_seconds() / 3600, 1) if first_dt and last_dt else None,
        "source_breakdown": source_breakdown,
        "points": sorted(points, key=lambda item: item["time"]),
    }


def _time_to_str(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def _datetime_to_str(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _public_source_breakdown(source_breakdown: dict[str, dict[str, Any]], value_key: str = "value") -> dict[str, dict[str, Any]]:
    public: dict[str, dict[str, Any]] = {}
    for source, detail in source_breakdown.items():
        public[source] = {
            value_key: detail.get(value_key),
            "raw_qty": detail.get("raw_qty"),
            "raw_unit": detail.get("raw_unit"),
            "record_count": detail.get("record_count", 0),
            "first_record_time": _datetime_to_str(detail.get("first_record_time")),
            "last_record_time": _datetime_to_str(detail.get("last_record_time")),
        }
    return public


class HealthParser:
    def __init__(self, json_path: str):
        self.json_path = Path(json_path)
        self.payload: dict[str, Any] = {}
        self.health_data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if self.payload:
            return self.payload

        if not self.json_path.exists():
            raise FileNotFoundError(f"健康导出文件不存在：{self.json_path}")

        with self.json_path.open("r", encoding="utf-8-sig") as file:
            self.payload = json.load(file)

        if not isinstance(self.payload, dict):
            raise ValueError("健康导出 JSON 顶层结构不是对象。")

        self.health_data = self.payload.get("data") or {}
        if not isinstance(self.health_data, dict):
            raise ValueError("健康导出 JSON 中的 data 字段格式不正确。")

        return self.payload

    def infer_file_date(self) -> date:
        self.load()
        filename_date = _parse_filename_date(self.json_path)
        if filename_date is not None:
            return filename_date

        for metric in self.health_data.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            for entry in metric.get("data") or []:
                if not isinstance(entry, dict):
                    continue
                raw_date = entry.get("date")
                if raw_date:
                    return parse_health_date(str(raw_date))

        raise ValueError(f"无法从文件名或内容中推断日期：{self.json_path}")

    def get_metric_entries(self, metric_names: list[str] | tuple[str, ...] | str) -> list[dict[str, Any]]:
        self.load()
        names = {metric_names} if isinstance(metric_names, str) else set(metric_names)
        entries: list[dict[str, Any]] = []
        for metric in self.health_data.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            if metric.get("name") not in names:
                continue
            for item in metric.get("data") or []:
                if isinstance(item, dict):
                    entry = dict(item)
                    entry["_metric_name"] = metric.get("name")
                    entry["_metric_units"] = metric.get("units")
                    entries.append(entry)
        return entries

    def get_metric_units(self, metric_names: list[str] | tuple[str, ...] | str) -> str | None:
        self.load()
        names = {metric_names} if isinstance(metric_names, str) else set(metric_names)
        for metric in self.health_data.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            if metric.get("name") not in names:
                continue
            units = metric.get("units")
            if isinstance(units, str) and units.strip():
                return units
        return None

    def get_cumulative_daily_details(
        self,
        metric_names: list[str] | tuple[str, ...],
        digits: int,
        prefer_activity_source: bool = False,
    ) -> dict[date, dict[str, Any]]:
        bucket: dict[date, list[dict[str, Any]]] = defaultdict(list)

        for entry in self.get_metric_entries(metric_names):
            start_dt = _entry_start_datetime(entry)
            end_dt = _entry_end_datetime(entry) or start_dt
            if start_dt is None:
                continue
            qty = _to_number(entry.get("qty"))
            if qty is None:
                continue
            bucket[start_dt.date()].append(
                {
                    "datetime": start_dt,
                    "end_datetime": end_dt,
                    "qty": float(qty),
                    "source": _entry_source(entry),
                }
            )

        result: dict[date, dict[str, Any]] = {}
        for day, points in bucket.items():
            sorted_points = sorted(points, key=lambda item: item["datetime"])
            first_dt = sorted_points[0]["datetime"]
            last_dt = max((point.get("end_datetime") or point["datetime"]) for point in sorted_points)
            source_breakdown: dict[str, dict[str, Any]] = {}
            for point in sorted_points:
                source = point["source"]
                source_info = source_breakdown.setdefault(
                    source,
                    {
                        "value": 0.0,
                        "record_count": 0,
                        "first_record_time": None,
                        "last_record_time": None,
                    },
                )
                source_info["value"] += point["qty"]
                source_info["record_count"] += 1
                point_start = point["datetime"]
                point_end = point.get("end_datetime") or point_start
                if source_info["first_record_time"] is None or point_start < source_info["first_record_time"]:
                    source_info["first_record_time"] = point_start
                if source_info["last_record_time"] is None or point_end > source_info["last_record_time"]:
                    source_info["last_record_time"] = point_end

            for source_info in source_breakdown.values():
                source_info["value"] = _round_number(source_info["value"], digits)
            adopted_source = None
            adopted_value = _round_number(sum(item["qty"] for item in sorted_points), digits)
            if prefer_activity_source:
                adopted_source, chosen_value = _choose_activity_source_value(source_breakdown, "value")
                if chosen_value is not None:
                    adopted_value = _round_number(chosen_value, digits)
            result[day] = {
                "value": adopted_value,
                "adopted_source": adopted_source,
                "first_record_datetime": first_dt,
                "last_record_datetime": last_dt,
                "first_record_time": first_dt.timetz().replace(tzinfo=None),
                "last_record_time": last_dt.timetz().replace(tzinfo=None),
                "record_count": len(sorted_points),
                "coverage_hours": round((last_dt - first_dt).total_seconds() / 3600, 1),
                "source_breakdown": source_breakdown,
                "points": [
                    {
                        "time": point["datetime"].timetz().replace(tzinfo=None),
                        "qty": point["qty"],
                        "source": point["source"],
                    }
                    for point in sorted_points
                ],
            }
        return result

    def get_heart_rate_map(self) -> dict[date, dict[str, int | float | None]]:
        bucket: dict[date, dict[str, list[float]]] = defaultdict(
            lambda: {"Avg": [], "Min": [], "Max": []}
        )

        for entry in self.get_metric_entries("heart_rate"):
            raw_date = entry.get("date")
            try:
                day = parse_health_date(str(raw_date))
            except ValueError:
                logger.warning("跳过无法解析日期的 heart_rate 记录：%s", raw_date)
                continue

            if any(key in entry for key in ("Avg", "Min", "Max")):
                for key in ("Avg", "Min", "Max"):
                    numeric_value = _to_number(entry.get(key))
                    if numeric_value is not None:
                        bucket[day][key].append(numeric_value)
            else:
                numeric_value = _to_number(entry.get("qty"))
                if numeric_value is not None:
                    bucket[day]["Avg"].append(numeric_value)
                    bucket[day]["Min"].append(numeric_value)
                    bucket[day]["Max"].append(numeric_value)

        result: dict[date, dict[str, int | float | None]] = {}
        for day, stats in bucket.items():
            result[day] = {
                "Avg": _mean_or_none(stats["Avg"], 1),
                "Min": _round_number(min(stats["Min"]), 1) if stats["Min"] else None,
                "Max": _round_number(max(stats["Max"]), 1) if stats["Max"] else None,
            }
        return result

    def get_sleep_map(self) -> dict[date, dict[str, Any]]:
        bucket: dict[date, list[dict[str, Any]]] = defaultdict(list)

        for entry in self.get_metric_entries("sleep_analysis"):
            raw_date = entry.get("date")
            try:
                day = parse_health_date(str(raw_date))
            except ValueError:
                logger.warning("跳过无法解析日期的 sleep_analysis 记录：%s", raw_date)
                continue
            bucket[day].append(entry)

        result: dict[date, dict[str, Any]] = {}
        for day, entries in bucket.items():
            total_hours = 0.0
            used_in_bed_only = True
            for entry in entries:
                total_sleep = _to_number(entry.get("totalSleep"))
                asleep = _to_number(entry.get("asleep"))
                core = _to_number(entry.get("core"))
                deep = _to_number(entry.get("deep"))
                rem = _to_number(entry.get("rem"))
                in_bed = _to_number(entry.get("inBed"))

                if total_sleep and total_sleep > 0:
                    total_hours += total_sleep
                    used_in_bed_only = False
                    continue

                sleep_stages = [value for value in (asleep, core, deep, rem) if value and value > 0]
                if sleep_stages:
                    total_hours += sum(sleep_stages)
                    used_in_bed_only = False
                    continue

                if in_bed and in_bed > 0:
                    total_hours += in_bed

            if total_hours > 0:
                result[day] = {
                    "hours": round(total_hours, 2),
                    "basis": "in_bed" if used_in_bed_only else "sleep",
                }
        return result

    def get_cycle_dates(self) -> set[date]:
        self.load()
        cycle_tracking = self.health_data.get("cycleTracking") or []
        cycle_dates: set[date] = set()

        if not isinstance(cycle_tracking, list):
            return cycle_dates

        for record in cycle_tracking:
            if not isinstance(record, dict):
                continue

            try:
                start_date = parse_health_date(str(record.get("start", "")))
            except ValueError:
                logger.warning("跳过无法解析日期的 cycleTracking 记录：%s", record)
                continue

            try:
                end_date = parse_health_date(str(record.get("end", "")))
            except ValueError:
                end_date = start_date

            current = min(start_date, end_date)
            final = max(start_date, end_date)
            while current <= final:
                cycle_dates.add(current)
                current += timedelta(days=1)

        return cycle_dates

    def get_workouts_by_date(self) -> dict[date, list[dict[str, Any]]]:
        self.load()
        workouts = self.health_data.get("workouts") or []
        workouts_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)

        if not isinstance(workouts, list):
            return {}

        for workout in workouts:
            if not isinstance(workout, dict):
                continue

            raw_start = workout.get("start")
            try:
                workout_date = parse_health_date(str(raw_start))
            except ValueError:
                logger.warning("跳过无法解析日期的 workout 记录：%s", raw_start)
                continue

            distance_info = workout.get("distance") if isinstance(workout.get("distance"), dict) else {}
            step_info = workout.get("stepCount") if isinstance(workout.get("stepCount"), dict) else {}
            speed_info = workout.get("speed") if isinstance(workout.get("speed"), dict) else {}
            duration_seconds = _to_number(workout.get("duration"))

            workouts_by_date[workout_date].append(
                {
                    "name": workout.get("name") or "未命名运动",
                    "duration_minutes": safe_round(duration_seconds / 60, 1) if duration_seconds is not None else None,
                    "distance_km": safe_round(_to_number(distance_info.get("qty")), 2),
                    "steps": _round_number(_to_number(step_info.get("qty")), 0),
                    "speed_kmh": safe_round(_to_number(speed_info.get("qty")), 2),
                }
            )

        return dict(workouts_by_date)

    def get_daily_latest_value_map(self, metric_names: list[str] | tuple[str, ...]) -> dict[date, float]:
        latest: dict[date, tuple[datetime, float]] = {}

        for entry in self.get_metric_entries(metric_names):
            dt = _parse_health_datetime(str(entry.get("date", "")))
            qty = _to_number(entry.get("qty"))
            if dt is None or qty is None:
                continue
            day = dt.date()
            current = latest.get(day)
            if current is None or dt > current[0]:
                latest[day] = (dt, float(qty))

        return {day: value for day, (_, value) in latest.items()}

    def build_day_record(self) -> dict[str, Any]:
        day = self.infer_file_date()
        cumulative_daily = {
            metric_key: self.get_cumulative_daily_details(
                meta["source_names"],
                meta["unit_digits"],
                prefer_activity_source=metric_key in {"steps", "active_energy"},
            )
            for metric_key, meta in CUMULATIVE_METRICS.items()
        }
        heart_map = self.get_heart_rate_map()
        sleep_map = self.get_sleep_map()
        cycle_dates = self.get_cycle_dates()
        workouts_by_date = self.get_workouts_by_date()
        weight_map = self.get_daily_latest_value_map(("body_mass",))
        hrv_map = self.get_daily_latest_value_map(("heart_rate_variability_sdnn",))
        active_energy_info = extract_active_energy(self.health_data.get("metrics") or [], target_date=day)

        cumulative_metrics: dict[str, dict[str, Any]] = {}
        for metric_key, meta in CUMULATIVE_METRICS.items():
            detail = cumulative_daily[metric_key].get(day, {})
            cumulative_metrics[metric_key] = {
                "value": detail.get("value"),
                "unit": self.get_metric_units(meta["source_names"]),
                "first_record_datetime": detail.get("first_record_datetime"),
                "last_record_datetime": detail.get("last_record_datetime"),
                "first_record_time": detail.get("first_record_time"),
                "last_record_time": detail.get("last_record_time"),
                "record_count": detail.get("record_count", 0),
                "coverage_hours": detail.get("coverage_hours"),
                "source_breakdown": detail.get("source_breakdown", {}),
                "points": detail.get("points", []),
            }

        if active_energy_info["kcal"] is not None:
            cumulative_metrics["active_energy"].update(
                {
                    "value": active_energy_info["kcal"],
                    "unit": "kcal",
                    "raw_qty": active_energy_info["raw_qty"],
                    "raw_unit": active_energy_info["raw_unit"],
                    "source": active_energy_info["source"],
                    "first_record_datetime": active_energy_info["first_record_datetime"],
                    "last_record_datetime": active_energy_info["last_record_datetime"],
                    "first_record_time": active_energy_info["first_record_time"],
                    "last_record_time": active_energy_info["last_record_time"],
                    "record_count": active_energy_info["record_count"],
                    "coverage_hours": active_energy_info["coverage_hours"],
                    "source_breakdown": active_energy_info["source_breakdown"],
                    "points": active_energy_info["points"],
                }
            )

        sleep_info = sleep_map.get(day) or {}
        heart_info = heart_map.get(day) or {}
        source_file_path = self.json_path.resolve()
        source_file_mtime = datetime.fromtimestamp(source_file_path.stat().st_mtime, tz=LOCAL_TZ)
        record_datetimes = [
            value
            for detail in cumulative_metrics.values()
            for value in (detail.get("first_record_datetime"), detail.get("last_record_datetime"))
            if value is not None
        ]
        first_health_record = min(record_datetimes) if record_datetimes else None
        last_health_record = max(record_datetimes) if record_datetimes else None
        steps_value = cumulative_metrics["steps"]["value"]
        raw_distance_km = cumulative_metrics["distance"]["value"]
        distance_km = raw_distance_km
        estimated_distance_km = _estimate_distance_from_steps(steps_value)
        distance_estimated_from_steps = False
        distance_quality_note = None

        if estimated_distance_km is not None and raw_distance_km is None:
            distance_km = estimated_distance_km
            distance_estimated_from_steps = True
            distance_quality_note = "未读取到可靠距离，已按小米步数估算距离。"
        elif estimated_distance_km is not None and not _distance_is_plausible_for_steps(steps_value, raw_distance_km):
            distance_km = estimated_distance_km
            distance_estimated_from_steps = True
            distance_quality_note = (
                f"原始距离 {raw_distance_km} km 与 {steps_value} 步明显不匹配，"
                "已按小米步数估算距离。"
            )

        return {
            "date": day.isoformat(),
            "date_obj": day,
            "source_file": str(source_file_path),
            "source_file_modified_time": source_file_mtime.isoformat(timespec="seconds"),
            "source_file_modified_ts": source_file_mtime.timestamp(),
            "data_window_start": datetime.combine(day, time.min, tzinfo=LOCAL_TZ).isoformat(timespec="seconds"),
            "data_window_end": datetime.combine(day, time.max, tzinfo=LOCAL_TZ).replace(microsecond=0).isoformat(timespec="seconds"),
            "first_health_record_time": first_health_record.isoformat(timespec="seconds") if first_health_record else None,
            "last_health_record_time": last_health_record.isoformat(timespec="seconds") if last_health_record else None,
            "steps": steps_value,
            "steps_source_breakdown": cumulative_metrics["steps"].get("source_breakdown", {}),
            "distance_km": distance_km,
            "distance_km_raw": raw_distance_km,
            "distance_estimated_from_steps": distance_estimated_from_steps,
            "distance_estimate_stride_m": ESTIMATED_STRIDE_METERS if distance_estimated_from_steps else None,
            "distance_quality_note": distance_quality_note,
            "active_energy": cumulative_metrics["active_energy"]["value"],
            "active_energy_unit": cumulative_metrics["active_energy"]["unit"],
            "active_energy_raw_qty": cumulative_metrics["active_energy"].get("raw_qty"),
            "active_energy_raw_unit": cumulative_metrics["active_energy"].get("raw_unit"),
            "active_energy_source": cumulative_metrics["active_energy"].get("source"),
            "active_energy_source_breakdown": cumulative_metrics["active_energy"].get("source_breakdown", {}),
            "resting_energy": cumulative_metrics["resting_energy"]["value"],
            "resting_energy_unit": cumulative_metrics["resting_energy"]["unit"],
            "exercise_minutes": cumulative_metrics["exercise_minutes"]["value"],
            "exercise_minutes_unit": cumulative_metrics["exercise_minutes"]["unit"],
            "stand_hours": cumulative_metrics["stand_hours"]["value"],
            "stand_hours_unit": cumulative_metrics["stand_hours"]["unit"],
            "heart_avg": heart_info.get("Avg"),
            "heart_min": heart_info.get("Min"),
            "heart_max": heart_info.get("Max"),
            "sleep_hours": sleep_info.get("hours"),
            "sleep_basis": sleep_info.get("basis"),
            "workouts": workouts_by_date.get(day, []),
            "is_period": day in cycle_dates,
            "weight": safe_round(weight_map.get(day), 2) if weight_map.get(day) is not None else None,
            "weight_unit": self.get_metric_units(("body_mass",)),
            "hrv": safe_round(hrv_map.get(day), 1) if hrv_map.get(day) is not None else None,
            "hrv_unit": self.get_metric_units(("heart_rate_variability_sdnn",)),
            "cumulative_metrics": cumulative_metrics,
            "available_metrics": sorted(
                metric.get("name")
                for metric in (self.health_data.get("metrics") or [])
                if isinstance(metric, dict) and metric.get("name")
            ),
        }


class HealthHistoryAnalyzer:
    def __init__(self, export_dir: str, now: datetime | None = None):
        self.export_dir = Path(export_dir).expanduser()
        self.now = now or datetime.now().astimezone()

    @property
    def today(self) -> date:
        return self.now.date()

    def get_json_files(self) -> list[Path]:
        if not self.export_dir.exists():
            raise FileNotFoundError(f"健康导出目录不存在：{self.export_dir}")
        if not self.export_dir.is_dir():
            raise NotADirectoryError(f"HEALTH_EXPORT_DIR 不是目录：{self.export_dir}")

        files = [path for path in self.export_dir.iterdir() if path.is_file() and path.suffix.lower() == ".json"]
        if not files:
            raise FileNotFoundError(
                f"目录 {self.export_dir} 下没有找到 JSON 文件。请确认 Health Auto Export 已同步到电脑。"
            )
        return sorted(files, key=lambda path: (path.stat().st_mtime, path.name))

    def load_day_records(self, recent_days: int = 14) -> list[dict[str, Any]]:
        files = self.get_json_files()
        dated_files = [(path, _parse_filename_date(path)) for path in files]
        known_dates = [file_date for _, file_date in dated_files if file_date is not None]
        if known_dates and recent_days > 0:
            latest_date = max(known_dates)
            cutoff_date = latest_date - timedelta(days=recent_days - 1)
            files = [
                path
                for path, file_date in dated_files
                if file_date is None or file_date >= cutoff_date
            ]

        records_by_date: dict[date, dict[str, Any]] = {}
        for path in files:
            parser = HealthParser(str(path))
            record = parser.build_day_record()
            self._attach_completeness(record)
            existing = records_by_date.get(record["date_obj"])
            if existing is None:
                records_by_date[record["date_obj"]] = record
            else:
                records_by_date[record["date_obj"]] = self._merge_duplicate_day_records(existing, record)

        records = list(records_by_date.values())
        records.sort(key=lambda item: item["date_obj"])
        return records

    def _merge_duplicate_day_records(self, first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
        """Merge duplicate exports for the same day, keeping larger activity values."""
        base, incoming = (
            (first, second)
            if first.get("source_file_modified_ts", 0) >= second.get("source_file_modified_ts", 0)
            else (second, first)
        )

        for metric_key, output_field in (("steps", "steps"), ("active_energy", "active_energy")):
            incoming_value = incoming.get(output_field)
            base_value = base.get(output_field)
            if incoming_value is None:
                continue
            if base_value is None or float(incoming_value) > float(base_value):
                self._copy_cumulative_metric(base, incoming, metric_key, output_field)

        # The adopted file path should explain where the adopted activity values came from.
        chosen_for_activity = max(
            (first, second),
            key=lambda record: float(record.get("steps") or 0) + float(record.get("active_energy") or 0),
        )
        if chosen_for_activity is not base:
            for key in ("source_file", "source_file_modified_time", "source_file_modified_ts"):
                base[key] = chosen_for_activity.get(key)

        self._attach_completeness(base)
        return base

    @staticmethod
    def _copy_cumulative_metric(target: dict[str, Any], source: dict[str, Any], metric_key: str, output_field: str) -> None:
        target[output_field] = source.get(output_field)
        target["cumulative_metrics"][metric_key] = source["cumulative_metrics"][metric_key]
        target["completeness"][metric_key] = source["completeness"][metric_key]
        if metric_key == "steps":
            target["steps_source_breakdown"] = source.get("steps_source_breakdown", {})
            for key in (
                "distance_km",
                "distance_km_raw",
                "distance_estimated_from_steps",
                "distance_estimate_stride_m",
                "distance_quality_note",
            ):
                if key in source:
                    target[key] = source.get(key)
        elif metric_key == "active_energy":
            for key in (
                "active_energy_unit",
                "active_energy_raw_qty",
                "active_energy_raw_unit",
                "active_energy_source",
                "active_energy_source_breakdown",
            ):
                target[key] = source.get(key)

    def _attach_completeness(self, record: dict[str, Any]) -> None:
        completeness: dict[str, dict[str, Any]] = {}
        for metric_key, meta in CUMULATIVE_METRICS.items():
            metric_info = record["cumulative_metrics"][metric_key]
            first_time = metric_info.get("first_record_time")
            last_time = metric_info.get("last_record_time")
            record_count = int(metric_info.get("record_count") or 0)
            coverage_hours = metric_info.get("coverage_hours")
            completeness[metric_key] = {
                "first_record_time": first_time,
                "last_record_time": last_time,
                "record_count": record_count,
                "coverage_hours": coverage_hours,
                "is_complete_day": self._is_complete_day(
                    target_date=record["date_obj"],
                    last_record_time=last_time,
                    record_count=record_count,
                    min_record_count=meta["min_record_count"],
                ),
            }
        if (
            record.get("distance_estimated_from_steps")
            and completeness.get("steps", {}).get("is_complete_day")
        ):
            steps_completeness = completeness["steps"]
            completeness["distance"] = {
                "first_record_time": steps_completeness["first_record_time"],
                "last_record_time": steps_completeness["last_record_time"],
                "record_count": steps_completeness["record_count"],
                "coverage_hours": steps_completeness["coverage_hours"],
                "is_complete_day": True,
            }
        record["completeness"] = completeness

    def _is_complete_day(
        self,
        target_date: date,
        last_record_time: time | None,
        record_count: int,
        min_record_count: int,
    ) -> bool:
        if last_record_time is None or record_count < min_record_count:
            return False
        if last_record_time <= EARLY_INCOMPLETE_CUTOFF:
            return False

        if target_date < self.today:
            return last_record_time >= COMPLETE_DAY_CUTOFF

        if target_date == self.today:
            if self.now.time().replace(tzinfo=None) < TODAY_COMPLETE_ALLOWED_AFTER:
                return False
            return last_record_time >= COMPLETE_DAY_CUTOFF

        return False

    def _is_formal_daily_target(self, record: dict[str, Any]) -> bool:
        # In iPhone + Xiaomi band mode, steps and active energy are the
        # reliable day-completeness signals. Auxiliary metrics such as distance
        # or resting energy may be sparse and should not force a switch back to
        # yesterday when the wearable activity data is already complete.
        core_metrics = [
            metric_key
            for metric_key in ("steps", "active_energy")
            if record["cumulative_metrics"][metric_key].get("record_count", 0) > 0
        ]
        if not core_metrics:
            return False
        return any(record["completeness"][metric_key]["is_complete_day"] for metric_key in core_metrics)

    def _find_record_by_date(self, records: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
        return next((record for record in records if record["date_obj"] == target_date), None)

    def select_auto_target_date(self, records: list[dict[str, Any]]) -> tuple[date, dict[str, Any]]:
        latest_record = max(records, key=lambda item: item["date_obj"])
        latest_date = latest_record["date_obj"]

        if latest_date == self.today and not self._is_formal_daily_target(latest_record):
            yesterday = self.today - timedelta(days=1)
            yesterday_record = self._find_record_by_date(records, yesterday)
            if yesterday_record is not None and self._is_formal_daily_target(yesterday_record):
                return yesterday, {
                    "auto_switched_from_today_partial": True,
                    "latest_file_date": latest_date.isoformat(),
                    "selected_target_date": yesterday.isoformat(),
                }

        return latest_date, {
            "auto_switched_from_today_partial": False,
            "latest_file_date": latest_date.isoformat(),
            "selected_target_date": latest_date.isoformat(),
        }

    def _same_time_value(self, record: dict[str, Any], metric_key: str, cutoff_time: time) -> float | None:
        points = record["cumulative_metrics"][metric_key].get("points") or []
        if not points:
            return None

        values = [point["qty"] for point in points if point.get("time") is not None and point["time"] <= cutoff_time]
        if not values:
            return None
        return float(sum(values))

    def _strip_internal_points(self, record: dict[str, Any]) -> dict[str, Any]:
        public_record = dict(record)
        public_cumulative = {}
        for metric_key, detail in record["cumulative_metrics"].items():
            source_breakdown = detail.get("source_breakdown", {})
            value_key = "kcal" if metric_key == "active_energy" else "value"
            public_cumulative[metric_key] = {
                "value": detail.get("value"),
                "unit": detail.get("unit"),
                "first_record_datetime": _datetime_to_str(detail.get("first_record_datetime")),
                "last_record_datetime": _datetime_to_str(detail.get("last_record_datetime")),
                "first_record_time": detail.get("first_record_time"),
                "last_record_time": detail.get("last_record_time"),
                "record_count": detail.get("record_count"),
                "coverage_hours": detail.get("coverage_hours"),
                "source_breakdown": _public_source_breakdown(source_breakdown, value_key=value_key),
            }
        public_record["cumulative_metrics"] = public_cumulative
        public_record["steps_source_breakdown"] = _public_source_breakdown(
            record["cumulative_metrics"]["steps"].get("source_breakdown", {}),
            value_key="value",
        )
        public_record["active_energy_source_breakdown"] = _public_source_breakdown(
            record["cumulative_metrics"]["active_energy"].get("source_breakdown", {}),
            value_key="kcal",
        )
        return public_record

    def build_summary(
        self,
        days: int = 7,
        target_date: date | None = None,
        auto_switch_incomplete_today: bool = False,
    ) -> dict[str, Any]:
        report_days = days if days > 0 else 7
        records = self.load_day_records(recent_days=max(report_days + 7, 14))
        records_by_date = {record["date_obj"]: record for record in records}
        latest_export_file = max(self.get_json_files(), key=lambda path: path.stat().st_mtime)
        latest_export_modified = datetime.fromtimestamp(latest_export_file.stat().st_mtime, tz=LOCAL_TZ)

        selection_meta: dict[str, Any] = {
            "auto_switched_from_today_partial": False,
            "latest_file_date": max(record["date_obj"] for record in records).isoformat(),
        }

        if target_date is None:
            if auto_switch_incomplete_today:
                target_date, selection_meta = self.select_auto_target_date(records)
            else:
                target_date = max(record["date_obj"] for record in records)

        target_record = records_by_date.get(target_date)
        if target_record is None:
            raise ValueError("未找到目标日期对应的健康记录。")

        report_kind = "daily" if self._is_formal_daily_target(target_record) else "partial"
        baseline_excludes_incomplete_target_date = report_kind == "partial"

        baseline_end = target_date if report_kind == "daily" else target_date - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=report_days - 1)
        baseline_records = [
            record for record in records if baseline_start <= record["date_obj"] <= baseline_end
        ]

        metric_valid_days: dict[str, int] = {}
        metric_complete_days: dict[str, int] = {}
        metric_stats: dict[str, dict[str, Any]] = {}
        changes_vs_7d: dict[str, float | None] = {}
        averages: dict[str, int | float | None] = {}

        for metric_key, meta in CUMULATIVE_METRICS.items():
            output_field = meta["output_field"]
            valid_records = [record for record in baseline_records if record.get(output_field) is not None]
            complete_records = [
                record
                for record in baseline_records
                if record.get(output_field) is not None and record["completeness"][metric_key]["is_complete_day"]
            ]
            valid_days = len(valid_records)
            complete_days = len(complete_records)
            average_value = _mean_or_none(
                [float(record[output_field]) for record in complete_records],
                meta["unit_digits"],
            )

            same_time_average = None
            same_time_valid_days = 0
            same_time_change_pct = None
            target_last_time = target_record["completeness"][metric_key]["last_record_time"]
            if report_kind == "partial" and target_last_time is not None:
                same_time_values: list[float] = []
                for record in complete_records:
                    value = self._same_time_value(record, metric_key, target_last_time)
                    if value is not None:
                        same_time_values.append(value)
                same_time_valid_days = len(same_time_values)
                same_time_average = _mean_or_none(same_time_values, meta["unit_digits"])
                same_time_change_pct = _percent_change(target_record.get(output_field), same_time_average)

            metric_valid_days[metric_key] = valid_days
            metric_complete_days[metric_key] = complete_days
            averages[output_field] = average_value
            changes_vs_7d[f"{metric_key}_pct"] = (
                _percent_change(target_record.get(output_field), average_value)
                if report_kind == "daily"
                else None
            )
            metric_stats[metric_key] = {
                "field": output_field,
                "today_present": target_record.get(output_field) is not None,
                "today_value": target_record.get(output_field),
                "valid_days": valid_days,
                "complete_days": complete_days,
                "trend_reliability": _trend_reliability_label(complete_days, report_days),
                "can_trend": complete_days >= 3,
                "is_complete_window": complete_days >= report_days,
                "average": average_value,
                "change_pct": changes_vs_7d[f"{metric_key}_pct"],
                "same_time_average": same_time_average,
                "same_time_valid_days": same_time_valid_days,
                "same_time_change_pct": same_time_change_pct,
                "target_is_complete_day": target_record["completeness"][metric_key]["is_complete_day"],
            }

        for metric_key, meta in NON_CUMULATIVE_METRICS.items():
            field = meta["field"]
            if metric_key == "workouts":
                valid_records = [record for record in baseline_records if record.get(field)]
                valid_days = len(valid_records)
                complete_days = valid_days
                average_value = None
                today_present = bool(target_record.get(field))
                today_value = target_record.get(field) or []
                change_pct = None
            else:
                valid_records = [record for record in baseline_records if record.get(field) is not None]
                valid_days = len(valid_records)
                complete_days = valid_days
                digits = meta["digits"] if meta["digits"] is not None else 1
                average_value = _mean_or_none([float(record[field]) for record in valid_records], digits)
                today_present = target_record.get(field) is not None
                today_value = target_record.get(field)
                change_pct = _percent_change(today_value, average_value) if valid_days >= 3 else None
                averages[field] = average_value
                changes_vs_7d[f"{metric_key}_pct"] = change_pct

            metric_valid_days[metric_key] = valid_days
            metric_complete_days[metric_key] = complete_days
            metric_stats[metric_key] = {
                "field": field,
                "today_present": today_present,
                "today_value": today_value,
                "valid_days": valid_days,
                "complete_days": complete_days,
                "trend_reliability": _trend_reliability_label(complete_days, report_days),
                "can_trend": complete_days >= 3,
                "is_complete_window": complete_days >= report_days,
                "average": average_value,
                "change_pct": change_pct,
                "target_is_complete_day": today_present if metric_key != "workouts" else today_present,
            }

        metric_valid_days["file_days"] = len(baseline_records)
        metric_complete_days["file_days"] = len(baseline_records)

        today_steps = target_record.get("steps")
        today_distance = target_record.get("distance_km")
        today_active_energy = target_record.get("active_energy")
        today_sleep = target_record.get("sleep_hours")
        today_heart_avg = target_record.get("heart_avg")
        today_heart_max = target_record.get("heart_max")

        data_quality_score = 0
        if today_steps is not None:
            data_quality_score += 20
        if today_distance is not None:
            data_quality_score += 10
        if any(target_record.get(key) is not None for key in ("heart_avg", "heart_min", "heart_max")):
            data_quality_score += 20
        if today_sleep is not None:
            data_quality_score += 20
        if today_active_energy is not None:
            data_quality_score += 15
        if target_record.get("workouts"):
            data_quality_score += 10
        if any(metric_stats[key]["can_trend"] for key in ("steps", "distance", "sleep", "heart_rate", "active_energy")):
            data_quality_score += 5

        low_activity_days = [
            record["date"]
            for record in baseline_records
            if record.get("steps") is not None
            and record["completeness"]["steps"]["is_complete_day"]
            and float(record["steps"]) < 3000
        ]

        consecutive_low_activity = False
        latest_complete_step_records = [
            record
            for record in records
            if record.get("steps") is not None and record["completeness"]["steps"]["is_complete_day"]
        ]
        if len(latest_complete_step_records) >= 2:
            last_two = latest_complete_step_records[-2:]
            consecutive_low_activity = all(float(record["steps"]) < 3000 for record in last_two)

        heart_rate_attention = bool(
            (today_heart_avg is not None and float(today_heart_avg) >= 90)
            or (today_heart_max is not None and float(today_heart_max) >= 170)
        )

        combo_signals: list[str] = []
        if today_steps is not None and today_steps < 3000 and heart_rate_attention:
            combo_signals.append("低步数 + 高心率")
        if today_sleep is not None and today_sleep < 6 and heart_rate_attention:
            combo_signals.append("低睡眠 + 高心率")
        if today_steps is not None and today_steps >= 12000 and today_sleep is not None and today_sleep < 6:
            combo_signals.append("高活动 + 低恢复")

        partial_day_suspected = any(
            not target_record["completeness"][metric_key]["is_complete_day"]
            and target_record["completeness"][metric_key]["record_count"] > 0
            for metric_key in CUMULATIVE_METRICS
        )
        if partial_day_suspected:
            combo_signals.append("数据可能只覆盖了部分时间段")

        activity_level = self._classify_activity_level(
            today_steps=today_steps,
            distance_km=today_distance,
            active_energy=today_active_energy,
        )

        target_date_completeness = {
            metric_key: {
                "first_record_time": target_record["completeness"][metric_key]["first_record_time"],
                "last_record_time": target_record["completeness"][metric_key]["last_record_time"],
                "record_count": target_record["completeness"][metric_key]["record_count"],
                "coverage_hours": target_record["completeness"][metric_key]["coverage_hours"],
                "is_complete_day": target_record["completeness"][metric_key]["is_complete_day"],
            }
            for metric_key in CUMULATIVE_METRICS
        }

        summary = {
            "latest_date": target_date.isoformat(),
            "target_date": target_date.isoformat(),
            "target_date_obj": target_date,
            "report_kind": report_kind,
            "window_days": report_days,
            "file_window_start": baseline_start.isoformat(),
            "file_window_end": baseline_end.isoformat(),
            "file_days_count": len(baseline_records),
            "records_in_window": len(baseline_records),
            "source_files": [record["source_file"] for record in baseline_records],
            "latest_export_file": str(latest_export_file.resolve()),
            "latest_export_file_modified_time": latest_export_modified.isoformat(timespec="seconds"),
            "today": self._strip_internal_points(target_record),
            "history": [self._strip_internal_points(record) for record in baseline_records],
            "averages": averages,
            "metric_stats": metric_stats,
            "metric_valid_days": metric_valid_days,
            "metric_complete_days": metric_complete_days,
            "changes_vs_7d": changes_vs_7d,
            "data_quality": {
                "score": data_quality_score,
                "steps": today_steps is not None,
                "distance": today_distance is not None,
                "heart_rate": any(target_record.get(key) is not None for key in ("heart_avg", "heart_min", "heart_max")),
                "sleep": today_sleep is not None,
                "active_energy": today_active_energy is not None,
                "workouts": bool(target_record.get("workouts")),
                "period": bool(target_record.get("is_period")),
                "weight": target_record.get("weight") is not None,
                "hrv": target_record.get("hrv") is not None,
                "partial_day_suspected": partial_day_suspected,
            },
            "activity_level": activity_level,
            "low_activity_days": low_activity_days,
            "consecutive_low_activity": consecutive_low_activity,
            "signals": {
                "heart_rate_attention": heart_rate_attention,
                "combo_signals": combo_signals,
            },
            "target_date_completeness": target_date_completeness,
            "baseline_excludes_incomplete_target_date": baseline_excludes_incomplete_target_date,
            "selection_meta": selection_meta,
            "steps_valid_days": metric_valid_days["steps"],
            "distance_valid_days": metric_valid_days["distance"],
            "sleep_valid_days": metric_valid_days["sleep"],
            "heart_rate_valid_days": metric_valid_days["heart_rate"],
            "active_energy_valid_days": metric_valid_days["active_energy"],
            "workouts_valid_days": metric_valid_days["workouts"],
            "hrv_valid_days": metric_valid_days["hrv"],
            "weight_valid_days": metric_valid_days["weight"],
            "steps_complete_days": metric_complete_days["steps"],
            "distance_complete_days": metric_complete_days["distance"],
            "active_energy_complete_days": metric_complete_days["active_energy"],
            "resting_energy_complete_days": metric_complete_days["resting_energy"],
            "exercise_minutes_complete_days": metric_complete_days["exercise_minutes"],
            "stand_hours_complete_days": metric_complete_days["stand_hours"],
        }
        return summary

    @staticmethod
    def _classify_activity_level(
        today_steps: int | float | None,
        distance_km: int | float | None,
        active_energy: int | float | None,
    ) -> str:
        if today_steps is None and distance_km is None and active_energy is None:
            return "无法判断"
        if today_steps is not None:
            if today_steps < 3000:
                return "偏低"
            if today_steps > 12000:
                return "偏高"
            return "正常"
        if distance_km is not None:
            if distance_km < 2:
                return "偏低"
            if distance_km > 8:
                return "偏高"
            return "正常"
        return "正常"

    def build_debug_data_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "report_kind": summary["report_kind"],
            "target_date": summary["target_date"],
            "latest_file_date": summary["selection_meta"].get("latest_file_date"),
            "selected_target_date": summary["selection_meta"].get("selected_target_date", summary["target_date"]),
            "latest_export_file": summary.get("latest_export_file"),
            "latest_export_file_modified_time": summary.get("latest_export_file_modified_time"),
            "target_source_file": summary["today"].get("source_file"),
            "target_source_file_modified_time": summary["today"].get("source_file_modified_time"),
            "data_window": {
                "start": summary["today"].get("data_window_start"),
                "end": summary["today"].get("data_window_end"),
            },
            "actual_health_record_window": {
                "first": summary["today"].get("first_health_record_time"),
                "last": summary["today"].get("last_health_record_time"),
            },
            "target_source_breakdown": {
                "steps": summary["today"].get("steps_source_breakdown", {}),
                "active_energy": summary["today"].get("active_energy_source_breakdown", {}),
            },
            "final_adopted_values": {
                "steps": summary["today"].get("steps"),
                "distance_km": summary["today"].get("distance_km"),
                "distance_km_raw": summary["today"].get("distance_km_raw"),
                "distance_estimated_from_steps": summary["today"].get("distance_estimated_from_steps"),
                "active_energy_kcal": summary["today"].get("active_energy"),
            },
            "file_days_count": summary["file_days_count"],
            "metric_valid_days": summary["metric_valid_days"],
            "metric_complete_days": summary["metric_complete_days"],
            "target_date_completeness": {
                metric_key: {
                    "first_record_time": _time_to_str(detail["first_record_time"]),
                    "last_record_time": _time_to_str(detail["last_record_time"]),
                    "record_count": detail["record_count"],
                    "coverage_hours": detail["coverage_hours"],
                    "is_complete_day": detail["is_complete_day"],
                }
                for metric_key, detail in summary["target_date_completeness"].items()
            },
            "baseline_excludes_incomplete_target_date": summary["baseline_excludes_incomplete_target_date"],
            "file_window_start": summary["file_window_start"],
            "file_window_end": summary["file_window_end"],
            "auto_switched_from_today_partial": summary["selection_meta"].get("auto_switched_from_today_partial", False),
        }
