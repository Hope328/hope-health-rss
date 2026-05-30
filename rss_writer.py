from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


@dataclass(slots=True)
class FeedItem:
    title: str
    guid: str
    description: str
    pub_date: datetime


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_existing(path: Path) -> list[FeedItem]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    items: list[FeedItem] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        desc = item.findtext("description") or ""
        raw_date = (item.findtext("pubDate") or "").strip()
        try:
            dt = datetime.strptime(raw_date, "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            dt = datetime.now().astimezone()
        if guid:
            items.append(FeedItem(title=title, guid=guid, description=desc, pub_date=dt))
    return items


def _build_xml(feed_title: str, feed_link: str, items: Iterable[FeedItem]) -> ET.ElementTree:
    now = datetime.now().astimezone()
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = feed_title
    ET.SubElement(channel, "link").text = feed_link
    ET.SubElement(channel, "description").text = "Daily and weekly health reports"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(now)

    for it in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = it.title
        ET.SubElement(item, "link").text = f"{feed_link}#{it.guid}"
        guid = ET.SubElement(item, "guid")
        guid.text = it.guid
        guid.set("isPermaLink", "false")
        ET.SubElement(item, "pubDate").text = format_datetime(it.pub_date)
        ET.SubElement(item, "description").text = it.description

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    return tree


def update_feed(*, output_path: str, feed_title: str, feed_link: str, title: str, guid: str, description: str, max_items: int) -> str:
    path = _resolve(output_path)
    now = datetime.now().astimezone()

    current = _load_existing(path)
    by_guid = {it.guid: it for it in current}
    by_guid[guid] = FeedItem(title=title, guid=guid, description=description, pub_date=now)

    final_items = sorted(by_guid.values(), key=lambda i: i.pub_date, reverse=True)[:max_items]
    tree = _build_xml(feed_title, feed_link, final_items)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return str(path)
