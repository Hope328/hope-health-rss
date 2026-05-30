from __future__ import annotations

from datetime import datetime
from typing import Any

from openai import OpenAI


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "暂无数据"
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    else:
        s = str(v)
    return f"{s}{suffix}"


def _activity_level(steps: int | None) -> str:
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


def _score(summary: dict[str, Any]) -> tuple[int, str]:
    today = summary["today"]
    steps = today.get("steps")
    sleep = today.get("sleep_hours")

    pts = 0
    possible = 0
    if steps is not None:
        possible += 4
        if steps < 1000:
            pts += 0
        elif steps < 3000:
            pts += 1
        elif steps < 5000:
            pts += 2
        elif steps < 7000:
            pts += 3
        else:
            pts += 4

    if sleep is not None:
        possible += 2
        if sleep < 6:
            pts += 0
        elif sleep < 7:
            pts += 1
        else:
            pts += 2

    possible += 1
    pts += 1

    score = round(pts / possible * 10) if possible else 0
    if score >= 8:
        status = "推进中"
    elif score >= 5:
        status = "维持中"
    elif score >= 3:
        status = "偏离但可修正"
    else:
        status = "需要恢复"
    return score, status


def generate_rule_report(summary: dict[str, Any], weekly: bool = False) -> str:
    if weekly:
        return _generate_weekly(summary)
    return _generate_daily(summary)


def _generate_daily(summary: dict[str, Any]) -> str:
    today = summary["today"]
    avg = summary["averages"]
    score, status = _score(summary)
    activity = _activity_level(today.get("steps"))

    lines = [
        f"【Hope 减脂日报｜{summary['target_date']}】",
        "",
        "一、今日结论",
        f"今天活动水平：{activity}。减脂有效性评分 {score}/10，状态：{status}。",
        "",
        "二、核心数据",
        f"- 步数：{_fmt(today.get('steps'), ' 步')}",
        f"- 距离：{_fmt(today.get('distance_km'), ' km')}",
        f"- 活动能量：{_fmt(today.get('active_energy'), ' kcal')}",
        f"- 睡眠：{_fmt(today.get('sleep_hours'), ' 小时')}",
        "",
        "三、趋势变化（近7天基准）",
        f"- 平均步数：{_fmt(avg.get('steps'), ' 步')}",
        f"- 平均距离：{_fmt(avg.get('distance_km'), ' km')}",
        f"- 平均活动能量：{_fmt(avg.get('active_energy'), ' kcal')}",
        f"- 平均睡眠：{_fmt(avg.get('sleep_hours'), ' 小时')}",
        "",
        "四、明日行动",
        "- 最低步数目标：6000 步；理想步数目标：8000 步。",
        "- 饮食：保留蛋白质和主食，不做补偿式节食。",
        "- 睡眠：尽量保障 7 小时。",
        "",
        "五、一句话提醒",
        "今天没做到完美也没关系，明天把最低目标守住，趋势就会往目标走。",
    ]
    return "\n".join(lines)


def _generate_weekly(summary: dict[str, Any]) -> str:
    avg = summary["averages"]
    window = summary["window"]
    lines = [
        f"【Hope 减脂周报｜{window['start']} ~ {window['end']}】",
        "",
        "一、本周结论",
        "这周重点看趋势，不看单日波动。",
        "",
        "二、核心数据",
        f"- 平均步数：{_fmt(avg.get('steps'), ' 步')}",
        f"- 平均距离：{_fmt(avg.get('distance_km'), ' km')}",
        f"- 平均活动能量：{_fmt(avg.get('active_energy'), ' kcal')}",
        f"- 平均睡眠：{_fmt(avg.get('sleep_hours'), ' 小时')}",
        "",
        "三、下周行动",
        "- 每天先守住 6000 步，理想 8000 步。",
        "- 忙时拆成 2 次 15 分钟快走。",
        "- 少一份零食比突然节食更可持续。",
    ]
    return "\n".join(lines)


def generate_report(summary: dict[str, Any], *, api_key: str | None, model: str, timeout_seconds: int, weekly: bool = False) -> str:
    draft = generate_rule_report(summary, weekly=weekly)
    if not api_key:
        return draft

    client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    prompt = (
        "请将以下中文健康报告润色成适合手机阅读的版本，"
        "保持结构和数值不变，长度控制在900字以内，能量单位统一kcal：\n\n"
        + draft
    )
    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": "你是温和务实的健康助手，不做医学诊断。"},
                {"role": "user", "content": prompt},
            ],
        )
        text = (resp.output_text or "").strip()
        if text:
            return text
    except Exception:
        return draft
    return draft
