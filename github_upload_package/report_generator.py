from __future__ import annotations

import json
import logging
import queue
import re
import threading
from datetime import date, datetime, time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
KJ_TO_KCAL = 1 / 4.184

GOAL_CONTEXT = {
    "height_cm": 155,
    "weight_kg": 56,
    "body_fat_percent": 27,
    "target_body_fat_percent": 24,
    "target_window": "两个月",
}

FORBIDDEN_REPORT_TERMS = ("source_file", "debug", "breakdown", "实际读取最新导出文件", "目标日期采用文件", "文件路径")


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _metric(summary: dict[str, Any], metric_key: str) -> dict[str, Any]:
    return summary["metric_stats"][metric_key]


def _fmt_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "暂无数据"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _fmt_value(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "暂无数据"
    return f"{_fmt_number(value, digits=digits)}{suffix}"


def _energy_kcal(value: Any, unit: str | None) -> float | None:
    if value is None:
        return None
    clean_unit = (unit or "").strip().lower()
    if clean_unit == "kj":
        return round(float(value) * KJ_TO_KCAL, 1)
    return round(float(value), 1)


def _format_energy(value: Any, unit: str | None) -> str:
    return _fmt_value(_energy_kcal(value, unit), " kcal", 0)


def _kcal_food_reference(active_energy_kcal: float | None) -> str:
    if active_energy_kcal is None:
        return "暂无"
    if active_energy_kcal < 80:
        return "约半个小苹果"
    apples = active_energy_kcal / 80
    return f"约 {apples:.1f} 个小苹果的能量"


def _sleep_text(today: dict[str, Any]) -> str:
    hours = today.get("sleep_hours")
    if hours is None:
        return "暂无数据"
    label = "卧床估算" if today.get("sleep_basis") == "in_bed" else "手环睡眠估算"
    return f"{_fmt_number(hours, 2)} 小时（{label}）"


def _activity_bucket(steps: int | float | None) -> str:
    if steps is None:
        return "无法判断"
    if steps < 1000:
        return "极低活动"
    if steps < 3000:
        return "低活动"
    if steps < 5000:
        return "最低有效活动"
    if steps < 7000:
        return "减脂合格"
    return "减脂友好"


def _steps_score(steps: int | float | None) -> int | None:
    if steps is None:
        return None
    if steps < 1000:
        return 0
    if steps < 3000:
        return 1
    if steps < 5000:
        return 2
    if steps < 7000:
        return 3
    return 4


def _sleep_score(hours: int | float | None) -> int | None:
    if hours is None:
        return None
    if hours < 6:
        return 0
    if hours < 7:
        return 1
    return 2


def _daily_scoring(summary: dict[str, Any]) -> dict[str, Any]:
    today = summary["today"]
    points = 0
    possible = 0

    step_score = _steps_score(today.get("steps"))
    if step_score is not None:
        points += step_score
        possible += 4

    sleep_score = _sleep_score(today.get("sleep_hours"))
    if sleep_score is not None:
        points += sleep_score
        possible += 2

    points += 1
    possible += 1

    score = round(points / possible * 10) if possible else 0
    if today.get("sleep_hours") is not None and today["sleep_hours"] < 6 and (today.get("steps") or 0) >= 7000:
        status = "推进中，但需要恢复"
    elif score >= 8:
        status = "推进中"
    elif score >= 5:
        status = "维持中"
    elif score >= 3:
        status = "偏离但可修正"
    else:
        status = "需要恢复"

    return {"score": score, "status": status}


def _tomorrow_targets(today_steps: int | float | None, today_sleep: int | float | None) -> dict[str, Any]:
    if today_steps is None:
        return {"minimum": 5000, "ideal": 7500, "exercise": "20-30 分钟轻快走，先补足基础活动。"}
    if today_steps < 1000:
        return {"minimum": 4500, "ideal": 7000, "exercise": "分 2-3 次走满 30 分钟，不用惩罚式高强度。"}
    if today_steps < 3000:
        return {"minimum": 5500, "ideal": 7500, "exercise": "饭后走 2 次，每次 15 分钟。"}
    if today_steps < 5000:
        return {"minimum": 6000, "ideal": 8000, "exercise": "加一段 20 分钟快走。"}
    if today_steps < 7000:
        return {"minimum": 6500, "ideal": 8500, "exercise": "可加 15-20 分钟快走或轻力量。"}
    if today_sleep is not None and today_sleep < 6:
        return {"minimum": 6000, "ideal": 8000, "exercise": "只做恢复式散步，别硬上强度。"}
    return {"minimum": 6000, "ideal": 9000, "exercise": "不必额外惩罚训练，正常活动加 15 分钟拉伸即可。"}


def _distance_text(today: dict[str, Any]) -> str:
    distance = today.get("distance_km")
    if distance is None:
        return "暂无数据"
    if today.get("distance_estimated_from_steps"):
        return f"{_fmt_value(distance, ' km', 2)}（按步数估算）"
    return _fmt_value(distance, " km", 2)


def _core_data_lines(summary: dict[str, Any]) -> list[str]:
    today = summary["today"]
    active_kcal = _energy_kcal(today.get("active_energy"), today.get("active_energy_unit"))
    lines = [
        f"- 步数：{_fmt_value(today.get('steps'), ' 步', 0)}（{_activity_bucket(today.get('steps'))}）",
        f"- 距离：{_distance_text(today)}",
        f"- 活动能量：{_fmt_value(active_kcal, ' kcal', 0)}（直觉参考：{_kcal_food_reference(active_kcal)}）",
        f"- 睡眠：{_sleep_text(today)}",
    ]
    if today.get("resting_energy") is not None:
        lines.append(f"- 静息能量：{_format_energy(today.get('resting_energy'), today.get('resting_energy_unit'))}（仅作参考）")
    if today.get("weight") is not None:
        lines.append(f"- 体重：{_fmt_value(today['weight'], ' kg', 2)}")
    if today.get("is_period"):
        lines.append("- 经期：有记录，今天优先稳节奏和恢复。")
    return lines


def _today_conclusion(summary: dict[str, Any], scoring: dict[str, Any]) -> str:
    today = summary["today"]
    steps = today.get("steps")
    active_kcal = _energy_kcal(today.get("active_energy"), today.get("active_energy_unit"))

    if summary["report_kind"] == "partial":
        last_times = [
            detail.get("last_record_time")
            for detail in summary.get("target_date_completeness", {}).values()
            if detail.get("last_record_time") is not None
        ]
        cutoff = max(last_times).strftime("%H:%M") if last_times else "当前"
        return (
            f"截至 {cutoff}，这是一份当日进度，不当作完整日报。"
            f"当前评分 {scoring['score']}/10，状态：{scoring['status']}。"
        )

    if steps is None:
        activity = "今天缺少步数，不能强判断活动表现。"
    elif steps < 3000:
        activity = "今天活动量偏低，对减脂目标推动有限。"
    elif steps < 7000:
        activity = "今天活动量能维持节奏，但推动力还可以再高一点。"
    else:
        activity = "今天活动量对减脂目标是加分的。"

    energy = "" if active_kcal is None else f"活动能量约 {active_kcal:.0f} kcal，"
    return f"{activity}{energy}减脂有效性评分 {scoring['score']}/10，状态：{scoring['status']}。"


def _impact_text(summary: dict[str, Any]) -> str:
    today = summary["today"]
    steps = today.get("steps")
    sleep = today.get("sleep_hours")
    distance_note = ""
    if today.get("distance_estimated_from_steps"):
        distance_note = "距离字段和步数不匹配，已用步数估算，避免把残缺距离当真。"

    if steps is None:
        impact = "今天缺少最关键的活动指标，只能做有限判断。"
    elif steps < 3000:
        impact = "单日低活动不会毁掉目标，但连续低活动会拖慢体脂下降。"
    elif steps < 7000:
        impact = "今天更像守住节奏，明天把步数推到 8000 左右会更贴近两个月降 3% 体脂的目标。"
    else:
        impact = "今天步数已经达到减脂友好区间，属于真正推进目标的一天。"

    if sleep is not None and sleep < 6:
        impact += " 但睡眠偏短，明天不要用高强度硬补，先保恢复。"
    elif sleep is not None and sleep >= 7:
        impact += " 睡眠也比较配合，有利于把活动成果留住。"
    if distance_note:
        impact += f" {distance_note}"
    return impact


def _action_plan_lines(summary: dict[str, Any]) -> list[str]:
    today = summary["today"]
    targets = _tomorrow_targets(today.get("steps"), today.get("sleep_hours"))
    if today.get("is_period"):
        diet = "蛋白质、热食和水分优先，别用节食硬压。"
        recovery = "保暖、拉伸或泡脚，强度降一点。"
    else:
        diet = "每餐保留蛋白质，主食别乱砍，少一份零食比补偿式节食更稳。"
        recovery = "睡前 30 分钟减少屏幕，尽量保证 7 小时左右。"
    return [
        f"- 明日最低：{targets['minimum']} 步；理想：{targets['ideal']} 步。",
        f"- 运动：{targets['exercise']}",
        f"- 饮食：{diet}",
        f"- 睡眠/恢复：{recovery}",
        "- 不要做：不要补偿式节食，也不要用高强度惩罚自己。",
    ]


def _sync_note(summary: dict[str, Any]) -> str:
    today = summary["today"]
    notes: list[str] = []
    if today.get("heart_avg") is None:
        notes.append("心率未同步到 Apple Health，今天不分析心率。")
    if today.get("distance_estimated_from_steps"):
        notes.append("距离按步数估算。")
    if not notes:
        return ""
    return "数据提醒：" + " ".join(notes)


def generate_rule_based_report(summary: dict[str, Any]) -> str:
    scoring = _daily_scoring(summary)
    title = (
        f"【Hope 减脂进度｜{summary['target_date']}】"
        if summary["report_kind"] == "partial"
        else f"【Hope 减脂日报｜{summary['target_date']}】"
    )
    lines = [
        title,
        "",
        "一、今日结论",
        _today_conclusion(summary, scoring),
        "",
        "二、核心数据",
        *_core_data_lines(summary),
        "",
        "三、对目标的影响",
        _impact_text(summary),
        "",
        "四、明日行动",
        *_action_plan_lines(summary),
        "",
        "五、一句话提醒",
        "今天的数据只用来帮你调整明天，不用来审判你；把最低步数和睡眠守住，节奏就接得上。",
    ]
    sync_note = _sync_note(summary)
    if sync_note:
        lines.append(sync_note)
    return "\n".join(lines)


def _weekly_active_energy_values(summary: dict[str, Any]) -> dict[str, Any]:
    values_by_date: dict[str, float] = {}
    for record in summary["history"]:
        value = record.get("active_energy")
        if value is None:
            continue
        values_by_date[record["date"]] = max(values_by_date.get(record["date"], 0.0), float(value))
    if not values_by_date:
        return {"total": None, "average": None, "days": 0}
    total = sum(values_by_date.values())
    return {"total": round(total, 1), "average": round(total / len(values_by_date), 1), "days": len(values_by_date)}


def generate_weekly_report(summary: dict[str, Any]) -> str:
    steps_stat = _metric(summary, "steps")
    distance_stat = _metric(summary, "distance")
    sleep_stat = _metric(summary, "sleep")
    energy = _weekly_active_energy_values(summary)
    complete_step_days = [
        record
        for record in summary["history"]
        if record.get("steps") is not None and record["completeness"]["steps"]["is_complete_day"]
    ]
    best_day = max(complete_step_days, key=lambda item: item["steps"], default=None)
    low_days = [record["date"] for record in complete_step_days if record["steps"] < 3000]

    avg_steps = steps_stat.get("average")
    if avg_steps is None:
        conclusion = "本周步数样本不足，先不做强趋势判断。"
    elif avg_steps >= 7000:
        conclusion = "本周整体在推进减脂目标，基础活动量不错。"
    elif avg_steps >= 5000:
        conclusion = "本周大体维持住了，但还需要减少低活动日。"
    else:
        conclusion = "本周活动推动力偏弱，下周先把日均步数拉起来。"

    best_text = "暂无数据" if best_day is None else f"{best_day['date']}（{_fmt_value(best_day['steps'], ' 步', 0)}）"
    low_text = "无明显低活动日" if not low_days else "低活动日：" + "、".join(low_days)

    return "\n".join(
        [
            f"【Hope 减脂周报｜{summary['file_window_start']} ~ {summary['file_window_end']}】",
            "",
            "一、本周结论",
            conclusion,
            "",
            "二、核心数据",
            f"- 日均步数：{_fmt_value(avg_steps, ' 步', 0)}",
            f"- 日均距离：{_fmt_value(distance_stat.get('average'), ' km', 2)}",
            f"- 日均睡眠：{_fmt_value(sleep_stat.get('average'), ' 小时', 2)}",
            f"- 活动能量：{_fmt_value(energy.get('total'), ' kcal', 0)}（{energy.get('days', 0)} 天有记录）",
            f"- 最高步数日：{best_text}",
            f"- {low_text}",
            "",
            "三、对目标的影响",
            "这周看的是方向，不是完美。步数和活动能量越稳定，两个月体脂从 27% 往 24% 走的阻力就越小。",
            "",
            "四、下周行动",
            "- 每天先守住 6000 步，理想目标 8000 步。",
            "- 忙的时候拆成 2 次 15 分钟快走，不等周末补。",
            "- 饮食先稳蛋白质和主食，少一份零食，比突然节食更可靠。",
            "",
            "五、一句话提醒",
            "下周不用狠，只要少掉几天低活动，趋势就会明显好看很多。",
        ]
    )


_KJ_TEXT_PATTERN = re.compile(r"(?P<value>-?\d+(?:\.\d+)?)\s*(?:k\s*j|千焦|K\s*J)", re.IGNORECASE)
_KJ_UNIT_PATTERN = re.compile(r"(?:k\s*j|千焦|K\s*J)", re.IGNORECASE)


def normalize_energy_units_in_text(text: str) -> str:
    if not text:
        return text

    def replace_match(match: re.Match[str]) -> str:
        kcal = float(match.group("value")) * KJ_TO_KCAL
        return f"{kcal:.1f}".rstrip("0").rstrip(".") + " kcal"

    normalized = _KJ_TEXT_PATTERN.sub(replace_match, text)
    return _KJ_UNIT_PATTERN.sub("kcal", normalized)


def _report_has_forbidden_debug_text(report: str) -> bool:
    lower = report.lower()
    return any(term.lower() in lower for term in FORBIDDEN_REPORT_TERMS)


def generate_ai_report(summary: dict[str, Any], api_key: str, model: str, timeout_seconds: int = 12) -> str:
    from openai import OpenAI

    draft_report = generate_rule_based_report(summary)
    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    response = client.responses.create(
        model=model or DEFAULT_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "你是 Hope 的减脂日报助手。只能基于提供的数据做生活方式分析，不做医学诊断。"
                    "输出要简洁、有行动感，不要写工程日志、文件路径、source breakdown 或 debug 信息。"
                    "能量单位只允许 kcal。不要改动数字事实。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请把下面底稿轻微润色成手机可读的中文报告，保留原结构和全部数字。"
                    "总长度控制在 900 字以内，不要增加没有的数据。\n\n"
                    f"关键摘要：{json.dumps(_json_safe(summary), ensure_ascii=False)[:6000]}\n\n"
                    f"底稿：\n{draft_report}"
                ),
            },
        ],
    )
    report = normalize_energy_units_in_text((response.output_text or "").strip())
    required = ("今日结论", "核心数据", "明日行动")
    if not report or any(item not in report for item in required):
        raise RuntimeError("OpenAI 返回内容结构不完整。")
    if _report_has_forbidden_debug_text(report) or len(report) > 1400:
        raise RuntimeError("OpenAI 返回内容过长或包含工程调试信息。")
    return report


def generate_report(
    summary: dict[str, Any],
    api_key: str | None = None,
    model: str | None = None,
    timeout_seconds: int = 12,
) -> str:
    clean_api_key = (api_key or "").strip()
    if not clean_api_key:
        logger.info("OPENAI_API_KEY 未配置，使用本地规则版日报。")
        return normalize_energy_units_in_text(generate_rule_based_report(summary))

    result_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(
                (
                    "ok",
                    generate_ai_report(
                        summary=summary,
                        api_key=clean_api_key,
                        model=model or DEFAULT_MODEL,
                        timeout_seconds=timeout_seconds,
                    ),
                )
            )
        except Exception as exc:
            result_queue.put(("error", str(exc)))

    thread = threading.Thread(target=worker, name="openai-report", daemon=True)
    thread.start()
    try:
        status, payload = result_queue.get(timeout=max(1, timeout_seconds))
    except queue.Empty:
        logger.warning("OpenAI 日报生成超过 %s 秒，直接使用本地规则版。", timeout_seconds)
        return normalize_energy_units_in_text(generate_rule_based_report(summary))

    if status == "ok":
        return payload

    logger.warning("OpenAI 日报生成失败，回退到本地规则版：%s", payload)
    return normalize_energy_units_in_text(generate_rule_based_report(summary))
