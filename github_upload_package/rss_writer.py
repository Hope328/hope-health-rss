from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


@dataclass(slots=True)
class RssItem:
    title: str
    link: str
    guid: str
    description: str
    pub_date: datetime


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def _resolve_output_path(output_path: str) -> Path:
    path = Path(output_path).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _item_from_element(element: ET.Element) -> RssItem:
    title = _safe_text(element.findtext("title"))
    link = _safe_text(element.findtext("link"))
    guid = _safe_text(element.findtext("guid"))
    description = _safe_text(element.findtext("description"))
    pub_date_text = _safe_text(element.findtext("pubDate"))
    try:
        pub_date = datetime.strptime(pub_date_text, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        pub_date = datetime.now().astimezone()
    return RssItem(title=title, link=link, guid=guid, description=description, pub_date=pub_date)


def _load_existing_items(path: Path) -> list[RssItem]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    return [_item_from_element(item) for item in channel.findall("item")]


def _append_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child


def write_report_rss(
    *,
    report: str,
    title: str,
    guid: str,
    output_path: str,
    feed_title: str,
    feed_link: str,
    max_items: int = 30,
    pub_date: datetime | None = None,
) -> str:
    path = _resolve_output_path(output_path)
    now = pub_date or datetime.now().astimezone()
    link = f"{feed_link.rstrip('#')}#{guid}"
    new_item = RssItem(
        title=title,
        link=link,
        guid=guid,
        description=report,
        pub_date=now,
    )

    existing = _load_existing_items(path)
    items_by_guid = {new_item.guid: new_item}
    for item in existing:
        if item.guid not in items_by_guid:
            items_by_guid[item.guid] = item
    items = sorted(items_by_guid.values(), key=lambda item: item.pub_date, reverse=True)[:max_items]

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _append_text(channel, "title", feed_title)
    _append_text(channel, "link", feed_link)
    _append_text(channel, "description", "Hope 的小米手环 / Apple Health 减脂健康报告")
    _append_text(channel, "language", "zh-CN")
    _append_text(channel, "lastBuildDate", format_datetime(now))

    for item in items:
        item_el = ET.SubElement(channel, "item")
        _append_text(item_el, "title", item.title)
        _append_text(item_el, "link", item.link)
        guid_el = _append_text(item_el, "guid", item.guid)
        guid_el.set("isPermaLink", "false")
        _append_text(item_el, "pubDate", format_datetime(item.pub_date))
        _append_text(item_el, "description", item.description)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return str(path)
