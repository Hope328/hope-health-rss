from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
PREFERRED_SOURCE_KEYS = ("xiaomi", "mi fitness", "小米", "с���")


def _parse_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _file_date(path: Path) -> date | None:
    m = DATE_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _is_xiaomi(source: str) -> bool:
    s = source.lower()
    return any(k in s for k in PREFERRED_SOURCE_KEYS)


def _kcal(qty: float, unit: str | None) -> float:
    u = (unit or "").strip().lower()
    if u == "kj":
        return qty / 4.184
    return qty


@dataclass(slots=True)
class DayData:
    day: date
    steps: int | None
    distance_km: float | None
    active_kcal: float | None
    sleep_hours: float | None


class HealthDataStore:
    def __init__(self, export_dir: str):
        self.export_dir = Path(export_dir).expanduser()

    def _json_files(self) -> list[Path]:
        if not self.export_dir.exists() or not self.export_dir.is_dir():
            raise FileNotFoundError(f"Health export dir not found: {self.export_dir}")
        files = [p for p in self.export_dir.iterdir() if p.is_file() and p.suffix.lower() == ".json"]
        if not files:
            raise FileNotFoundError(f"No JSON files in {self.export_dir}")

        # GitHub Actions checkout may flatten mtime; prioritize date in filename first.
        return sorted(
            files,
            key=lambda p: (
                _file_date(p) or date.min,
                p.stat().st_mtime,
                p.name,
            ),
        )

    def _parse_days_from_file(self, path: Path) -> dict[date, DayData]:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        metrics = ((data.get("data") or {}).get("metrics") or [])
        if not isinstance(metrics, list):
            return {}

        fallback_day = _file_date(path)

        steps_by_day_source: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        distance_by_day_source: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        active_by_day_source: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        sleep_by_day: dict[date, float] = defaultdict(float)

        for metric in metrics:
            name = str(metric.get("name") or "")
            unit = metric.get("units") if isinstance(metric.get("units"), str) else None
            points = metric.get("data") or []
            if not isinstance(points, list):
                continue

            if name == "step_count":
                for p in points:
                    qty = _to_float(p.get("qty"))
                    dt = _parse_dt(str(p.get("date") or ""))
                    day = dt.date() if dt is not None else fallback_day
                    if qty is None or day is None:
                        continue
                    source = str(p.get("source") or p.get("sourceName") or "unknown")
                    steps_by_day_source[day][source] += qty

            elif name == "walking_running_distance":
                for p in points:
                    qty = _to_float(p.get("qty"))
                    dt = _parse_dt(str(p.get("date") or ""))
                    day = dt.date() if dt is not None else fallback_day
                    if qty is None or day is None:
                        continue
                    source = str(p.get("source") or p.get("sourceName") or "unknown")
                    distance_by_day_source[day][source] += qty

            elif name in {"active_energy", "active_energy_burned"}:
                for p in points:
                    qty = _to_float(p.get("qty"))
                    dt = _parse_dt(str(p.get("date") or ""))
                    day = dt.date() if dt is not None else fallback_day
                    if qty is None or day is None:
                        continue
                    source = str(p.get("source") or p.get("sourceName") or "unknown")
                    active_by_day_source[day][source] += _kcal(qty, unit)

            elif name == "sleep_analysis":
                for p in points:
                    dt = _parse_dt(str(p.get("date") or ""))
                    day = dt.date() if dt is not None else fallback_day
                    if day is None:
                        continue

                    sleep_piece = None
                    for key in ("totalSleep", "asleep", "core", "deep", "rem"):
                        q = _to_float(p.get(key))
                        if q is not None and q > 0:
                            sleep_piece = q
                            break
                    if sleep_piece is None:
                        ib = _to_float(p.get("inBed"))
                        if ib is not None and ib > 0:
                            sleep_piece = ib

                    if sleep_piece is not None:
                        sleep_by_day[day] += sleep_piece

        def pick_value(d: dict[str, float]) -> float | None:
            if not d:
                return None
            xiaomi = {k: v for k, v in d.items() if _is_xiaomi(k)}
            chosen = xiaomi if xiaomi else d
            return max(chosen.values())

        all_days = set(steps_by_day_source) | set(distance_by_day_source) | set(active_by_day_source) | set(sleep_by_day)
        out: dict[date, DayData] = {}
        for day in all_days:
            steps = pick_value(steps_by_day_source.get(day, {}))
            distance = pick_value(distance_by_day_source.get(day, {}))
            active = pick_value(active_by_day_source.get(day, {}))
            sleep_total = sleep_by_day.get(day)
            out[day] = DayData(
                day=day,
                steps=int(round(steps)) if steps is not None else None,
                distance_km=round(distance, 2) if distance is not None else None,
                active_kcal=round(active, 1) if active is not None else None,
                sleep_hours=round(sleep_total, 2) if sleep_total and sleep_total > 0 else None,
            )
        return out

    def load_days(self) -> list[DayData]:
        merged: dict[date, DayData] = {}
        for path in self._json_files():
            day_map = self._parse_days_from_file(path)
            for day, item in day_map.items():
                old = merged.get(day)
                if old is None:
                    merged[day] = item
                    continue
                merged[day] = DayData(
                    day=day,
                    steps=max(x for x in [old.steps, item.steps] if x is not None) if (old.steps is not None or item.steps is not None) else None,
                    distance_km=max(x for x in [old.distance_km, item.distance_km] if x is not None) if (old.distance_km is not None or item.distance_km is not None) else None,
                    active_kcal=max(x for x in [old.active_kcal, item.active_kcal] if x is not None) if (old.active_kcal is not None or item.active_kcal is not None) else None,
                    sleep_hours=max(x for x in [old.sleep_hours, item.sleep_hours] if x is not None) if (old.sleep_hours is not None or item.sleep_hours is not None) else None,
                )
        return sorted(merged.values(), key=lambda d: d.day)


def build_summary(export_dir: str, report_days: int) -> dict[str, Any]:
    store = HealthDataStore(export_dir)
    days = store.load_days()
    if not days:
        raise ValueError("No valid health days found")

    latest = days[-1]
    today = datetime.now().date()
    if latest.day == today and len(days) > 1:
        target = days[-2]
    else:
        target = latest

    window_end = target.day
    window_start = window_end - timedelta(days=max(report_days, 1) - 1)
    history = [d for d in days if window_start <= d.day <= window_end]

    def avg(vals: list[float | int | None], digits: int) -> float | None:
        nums = [float(v) for v in vals if v is not None]
        if len(nums) < 1:
            return None
        return round(mean(nums), digits)

    summary = {
        "target_date": target.day.isoformat(),
        "today": {
            "steps": target.steps,
            "distance_km": target.distance_km,
            "active_energy": target.active_kcal,
            "active_energy_unit": "kcal",
            "sleep_hours": target.sleep_hours,
        },
        "history": [
            {
                "date": d.day.isoformat(),
                "steps": d.steps,
                "distance_km": d.distance_km,
                "active_energy": d.active_kcal,
                "sleep_hours": d.sleep_hours,
            }
            for d in history
        ],
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": len(history),
        },
        "averages": {
            "steps": avg([d.steps for d in history], 0),
            "distance_km": avg([d.distance_km for d in history], 2),
            "active_energy": avg([d.active_kcal for d in history], 1),
            "sleep_hours": avg([d.sleep_hours for d in history], 2),
        },
    }
    return summary
