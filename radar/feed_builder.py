from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
import html
import json
from pathlib import Path
from typing import Any, Dict, List
import xml.etree.ElementTree as ET

from .models import RepoItem


def classify_bucket(sources: List[str]) -> str:
    if any("Trending" in s for s in sources):
        return "trending"
    if any(s.startswith(("雷达:", "关注开发者:")) for s in sources):
        return "radar"
    return "community"


def write_web_json(
    *,
    items: List[RepoItem],
    status: Dict[str, Any],
    out_path: Path,
) -> None:
    payload_items: List[Dict[str, Any]] = []
    for item in items:
        owner, _, name = item.repo_name.partition("/")
        if not name:
            owner, name = "", item.repo_name
        payload_items.append(
            {
                "key": item.canonical_key,
                "repo_name": item.repo_name,
                "owner": owner,
                "name": name,
                "url": item.url,
                "description": item.description,
                "desc_cn": item.desc_cn,
                "ai_highlight": item.ai_highlight,
                "category_tag": item.category_tag,
                "language": item.language or "",
                "stars": int(item.stars),
                "forks": int(item.forks),
                "sources": list(item.sources),
                "bucket": classify_bucket(item.sources),
                "discussion_url": item.discussion_url,
                "discovered_at": item.discovered_at,
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "total": len(payload_items),
        "items": payload_items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)


def _build_item_html(item: RepoItem) -> str:
    parts: List[str] = []
    if item.ai_highlight:
        parts.append(
            f"<p><strong>💡 一句话亮点：</strong>{html.escape(item.ai_highlight)}</p>"
        )
    if item.desc_cn:
        parts.append(f"<p><strong>中文简介：</strong>{html.escape(item.desc_cn)}</p>")
    if item.description and item.description != item.desc_cn:
        parts.append(f"<p><strong>原文简介：</strong>{html.escape(item.description)}</p>")

    meta_bits = []
    if item.category_tag:
        meta_bits.append(f"分类: {html.escape(item.category_tag)}")
    if item.language:
        meta_bits.append(f"语言: {html.escape(item.language)}")
    if item.stars:
        meta_bits.append(f"Stars: {item.stars:,}")
    if item.sources:
        meta_bits.append("来源: " + html.escape(" + ".join(item.sources)))
    if meta_bits:
        parts.append(f"<p>{' | '.join(meta_bits)}</p>")

    links = [f'<a href="{html.escape(item.url)}">GitHub 仓库</a>']
    if item.discussion_url:
        links.append(f'<a href="{html.escape(item.discussion_url)}">社区讨论</a>')
    parts.append("<p>" + " · ".join(links) + "</p>")
    return "\n".join(parts)


def write_rss_feed(
    *,
    title: str,
    description: str,
    link: str,
    items: List[RepoItem],
    out_path: Path,
) -> None:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for item in items:
        el = ET.SubElement(channel, "item")
        lang_tag = f"[{item.language}] " if item.language else ""
        cat_tag = f"[{item.category_tag}] " if item.category_tag else ""
        display_title = f"{cat_tag}{lang_tag}{item.repo_name}"
        if item.ai_highlight:
            display_title += f" — {item.ai_highlight}"
        elif item.desc_cn:
            display_title += f" — {item.desc_cn[:50]}"

        ET.SubElement(el, "title").text = display_title
        ET.SubElement(el, "link").text = item.url
        guid = ET.SubElement(el, "guid", {"isPermaLink": "false"})
        guid.text = f"github-radar:{item.canonical_key}"
        ET.SubElement(el, "description").text = _build_item_html(item)
        if item.category_tag:
            ET.SubElement(el, "category").text = item.category_tag
        for src in item.sources:
            ET.SubElement(el, "category").text = src

        pub_dt = datetime.now(timezone.utc)
        if item.discovered_at:
            try:
                pub_dt = datetime.fromisoformat(item.discovered_at.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        ET.SubElement(el, "pubDate").text = format_datetime(pub_dt)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    out_path.write_bytes(xml_bytes)
