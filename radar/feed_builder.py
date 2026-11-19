from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
import html
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from .filters import compute_age_days, compute_breakout_score
from .models import RepoItem
from .sources import sanitize_http_url

AI_TOKEN_RE = re.compile(r"(?<![A-Z])(AI|LLM|AGENT|RAG|GPT|AIGC)(?![A-Z])")


def classify_bucket(sources: List[str]) -> str:
    if any("Trending" in s for s in sources):
        return "trending"
    if any(s.startswith(("雷达:", "关注开发者:")) or s == "GitHub Releases" for s in sources):
        return "radar"
    return "community"


def resolve_site_and_feeds_urls(output_cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Resolve (site_base_url, feeds_base_url) from RADAR_PUBLIC_BASE_URL or output.public_base_url."""
    cfg = output_cfg or {}
    env_base = os.environ.get("RADAR_PUBLIC_BASE_URL", "").strip().rstrip("/")
    cfg_base = str(cfg.get("public_base_url") or "http://localhost:8088/feeds").strip().rstrip("/")
    raw = env_base or cfg_base or "http://localhost:8088/feeds"
    if raw.endswith("/feeds"):
        site_url = raw[: -len("/feeds")] or "http://localhost:8088"
        feeds_url = raw
    else:
        site_url = raw
        feeds_url = f"{raw}/feeds"
    return site_url, feeds_url


def normalize_macro_category(tag: str) -> str:
    t = (tag or "").strip()
    if not t:
        return ""
    up = t.upper()
    if (
        AI_TOKEN_RE.search(up)
        or "智能" in t
        or "模型" in t
        or "深度学习" in t
        or "机器学习" in t
        or "多模态" in t
    ):
        return "AI"
    return t


def apply_category_quota(
    items: List[RepoItem],
    max_share: float,
    *,
    limit: Optional[int] = None,
    strict_drop: bool = False,
    window_size: int = 10,
) -> List[RepoItem]:
    """Prevent a single theme (especially AI) from flooding every screenful or exceeding max_share."""
    target_limit = limit if limit is not None else len(items)
    if target_limit <= 0 or not items:
        return []
    if max_share <= 0.0 or max_share >= 1.0 or len(items) < 5:
        return list(items[:target_limit])

    macro_tags = [normalize_macro_category(r.category_tag) for r in items]
    non_empty_macros = {m for m in macro_tags if m}
    # If fewer than 2 categories exist, quota interleaving has no diverse alternatives
    if len(non_empty_macros) < 2 and not strict_drop:
        return list(items[:target_limit])

    max_per_window = max(1, int(round(window_size * max_share)))
    eff_total_base = min(len(items), target_limit)
    max_total_per_cat = max(2, math.ceil(eff_total_base * max_share))

    remaining = list(items)
    selected: List[RepoItem] = []
    deferred: List[RepoItem] = []
    total_counts: Dict[str, int] = {}

    while remaining and len(selected) < target_limit:
        win_start = (len(selected) // window_size) * window_size
        win_slice = selected[win_start:]
        win_counts: Dict[str, int] = {}
        for prev in win_slice:
            mc = normalize_macro_category(prev.category_tag)
            if mc:
                win_counts[mc] = win_counts.get(mc, 0) + 1

        picked_idx = -1
        for idx, cand in enumerate(remaining):
            mc = normalize_macro_category(cand.category_tag)
            if not mc:
                picked_idx = idx
                break
            if strict_drop and total_counts.get(mc, 0) >= max_total_per_cat:
                continue
            if win_counts.get(mc, 0) < max_per_window:
                picked_idx = idx
                break

        if picked_idx >= 0:
            chosen = remaining.pop(picked_idx)
            mc = normalize_macro_category(chosen.category_tag)
            if mc:
                total_counts[mc] = total_counts.get(mc, 0) + 1
            selected.append(chosen)
        else:
            # No remaining item fits current window quota
            if strict_drop:
                # Drop items that already hit total cap; if none left at all, break
                still_allowed = [
                    r
                    for r in remaining
                    if not normalize_macro_category(r.category_tag)
                    or total_counts.get(normalize_macro_category(r.category_tag), 0) < max_total_per_cat
                ]
                if not still_allowed:
                    break
                chosen = still_allowed[0]
                remaining.remove(chosen)
                mc = normalize_macro_category(chosen.category_tag)
                if mc:
                    total_counts[mc] = total_counts.get(mc, 0) + 1
                selected.append(chosen)
            else:
                # Move the head item into deferred and try to fill window with diverse items;
                # if all remaining share the same capped category, promote head so slots aren't wasted.
                rem_macros = {normalize_macro_category(r.category_tag) for r in remaining}
                if len(rem_macros) <= 1:
                    chosen = remaining.pop(0)
                    selected.append(chosen)
                else:
                    deferred.append(remaining.pop(0))

    if not strict_drop and len(selected) < target_limit and deferred:
        need = target_limit - len(selected)
        selected.extend(deferred[:need])

    return selected[:target_limit]


def _item_guid(item: RepoItem) -> str:
    if item.release_tag:
        return f"github-radar:{item.canonical_key}:{item.release_tag}"
    return f"github-radar:{item.canonical_key}"


def _build_sparkline_points(
    item: RepoItem, snapshot_points: Optional[List[int]], age_days: Optional[float]
) -> List[int]:
    pts = [int(x) for x in (snapshot_points or []) if x is not None]
    if len(pts) >= 2:
        return pts[-7:]
    stars = max(0, int(item.stars or 0))
    d24 = max(0, int(item.stars_delta_24h or 0))
    d7 = max(0, int(item.stars_delta_7d or 0))
    if d7 > d24 > 0:
        return [max(0, stars - d7), max(0, stars - d24), stars]
    if d24 > 0:
        return [max(0, stars - d24), stars]
    if age_days is not None and age_days <= 14 and stars > 0:
        return [0, stars]
    if len(pts) == 1 and stars > 0:
        return [pts[0], stars] if pts[0] != stars else [stars, stars]
    return [stars, stars] if stars > 0 else []


def write_web_json(
    *,
    items: List[RepoItem],
    status: Dict[str, Any],
    out_path: Path,
    sparklines_map: Optional[Dict[str, List[int]]] = None,
    now_utc: Optional[datetime] = None,
) -> None:
    now = now_utc or datetime.now(timezone.utc)
    payload_items: List[Dict[str, Any]] = []
    smap = sparklines_map or {}

    for item in items:
        owner, _, name = item.repo_name.partition("/")
        if not name:
            owner, name = "", item.repo_name
        safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
        safe_disc = sanitize_http_url(item.discussion_url)
        age_days = compute_age_days(item.created_at, now_utc=now)
        breakout = compute_breakout_score(item, now_utc=now)
        spark = _build_sparkline_points(
            item, smap.get(item.canonical_key) or item.star_history, age_days
        )

        payload_items.append(
            {
                "key": item.canonical_key,
                "repo_name": item.repo_name,
                "owner": owner,
                "name": name,
                "url": safe_url,
                "description": item.description,
                "desc_cn": item.desc_cn,
                "ai_highlight": item.ai_highlight,
                "category_tag": item.category_tag,
                "language": item.language or "",
                "stars": int(item.stars),
                "forks": int(item.forks),
                "open_issues": int(item.open_issues),
                "stars_delta_24h": int(item.stars_delta_24h),
                "stars_delta_7d": int(item.stars_delta_7d),
                "breakout_score": breakout,
                "age_days": round(age_days, 1) if age_days is not None else None,
                "community_score": int(item.community_score),
                "sparkline": spark,
                "topics": list(item.topics),
                "license_name": item.license_name,
                "release_tag": item.release_tag,
                "created_at": item.created_at,
                "pushed_at": item.pushed_at,
                "sources": list(item.sources),
                "multi_source": len(item.sources) >= 2,
                "bucket": classify_bucket(item.sources),
                "discussion_url": safe_disc,
                "discovered_at": item.discovered_at,
            }
        )

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
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
        star_text = f"Stars: {item.stars:,}"
        if item.stars_delta_24h > 0:
            star_text += f" (+{item.stars_delta_24h:,} 今日)"
        meta_bits.append(star_text)
    if item.license_name:
        meta_bits.append(f"协议: {html.escape(item.license_name)}")
    if item.release_tag:
        meta_bits.append(f"版本: {html.escape(item.release_tag)}")
    if item.sources:
        meta_bits.append("来源: " + html.escape(" + ".join(item.sources)))
    if meta_bits:
        parts.append(f"<p>{' | '.join(meta_bits)}</p>")

    if item.topics:
        parts.append("<p>标签: " + html.escape(", ".join(item.topics)) + "</p>")

    safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
    safe_disc = sanitize_http_url(item.discussion_url)
    links = [f'<a href="{html.escape(safe_url)}">GitHub 仓库</a>']
    if safe_disc:
        links.append(f'<a href="{html.escape(safe_disc)}">社区讨论 / Release</a>')
    parts.append("<p>" + " · ".join(links) + "</p>")
    return "\n".join(parts)


def _format_item_title(item: RepoItem) -> str:
    lang_tag = f"[{item.language}] " if item.language else ""
    cat_tag = f"[{item.category_tag}] " if item.category_tag else ""
    rel_tag = f"[{item.release_tag}] " if item.release_tag else ""
    delta_tag = f"[+{item.stars_delta_24h}★] " if item.stars_delta_24h > 0 else ""
    display_title = f"{cat_tag}{lang_tag}{rel_tag}{delta_tag}{item.repo_name}"
    if item.ai_highlight:
        display_title += f" — {item.ai_highlight}"
    elif item.desc_cn:
        display_title += f" — {item.desc_cn[:50]}"
    return display_title


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
        safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
        ET.SubElement(el, "title").text = _format_item_title(item)
        ET.SubElement(el, "link").text = safe_url
        guid = ET.SubElement(el, "guid", {"isPermaLink": "false"})
        guid.text = _item_guid(item)
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


def write_json_feed(
    *,
    title: str,
    description: str,
    home_page_url: str,
    feed_url: str,
    items: List[RepoItem],
    out_path: Path,
    now_utc: Optional[datetime] = None,
) -> None:
    """Write JSON Feed Version 1.1 (https://www.jsonfeed.org/version/1.1/)."""
    now = now_utc or datetime.now(timezone.utc)
    feed_items: List[Dict[str, Any]] = []
    for item in items:
        safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
        safe_disc = sanitize_http_url(item.discussion_url)
        tags: List[str] = []
        for t in [item.category_tag, item.language or "", *item.sources, *item.topics]:
            ts = str(t or "").strip()
            if ts and ts not in tags:
                tags.append(ts)

        pub_iso = now.isoformat(timespec="seconds")
        if item.discovered_at:
            try:
                dt = datetime.fromisoformat(item.discovered_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                pub_iso = dt.isoformat(timespec="seconds")
            except ValueError:
                pass

        entry: Dict[str, Any] = {
            "id": _item_guid(item),
            "url": safe_url,
            "title": _format_item_title(item),
            "content_html": _build_item_html(item),
            "summary": item.ai_highlight or item.desc_cn or item.description,
            "date_published": pub_iso,
            "tags": tags,
            "_radar": {
                "repo_name": item.repo_name,
                "stars": int(item.stars),
                "stars_delta_24h": int(item.stars_delta_24h),
                "breakout_score": compute_breakout_score(item, now_utc=now),
                "bucket": classify_bucket(item.sources),
                "multi_source": len(item.sources) >= 2,
            },
        }
        if safe_disc:
            entry["external_url"] = safe_disc
        feed_items.append(entry)

    doc = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": title,
        "home_page_url": home_page_url.rstrip("/") + "/",
        "feed_url": feed_url,
        "description": description,
        "language": "zh-CN",
        "items": feed_items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)


def write_opml(*, site_base_url: str, feeds_base_url: str, out_path: Path) -> None:
    site = site_base_url.rstrip("/")
    fbase = feeds_base_url.rstrip("/")
    opml = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "GitHub Radar 每日开源精选订阅"
    body = ET.SubElement(opml, "body")

    all_grp = ET.SubElement(body, "outline", {"text": "全源聚合", "title": "全源聚合"})
    ET.SubElement(
        all_grp,
        "outline",
        {
            "type": "rss",
            "text": "GitHub Radar 全源精选",
            "title": "GitHub Radar 全源精选",
            "xmlUrl": f"{fbase}/all_discoveries.xml",
            "htmlUrl": f"{site}/",
        },
    )

    gh_grp = ET.SubElement(body, "outline", {"text": "GitHub 分榜", "title": "GitHub 分榜"})
    ET.SubElement(
        gh_grp,
        "outline",
        {
            "type": "rss",
            "text": "GitHub Trending（全语言去噪）",
            "title": "GitHub Trending（全语言去噪）",
            "xmlUrl": f"{fbase}/github_trending.xml",
            "htmlUrl": "https://github.com/trending",
        },
    )
    ET.SubElement(
        gh_grp,
        "outline",
        {
            "type": "rss",
            "text": "GitHub 黑马雷达 & Release 追踪",
            "title": "GitHub 黑马雷达 & Release 追踪",
            "xmlUrl": f"{fbase}/github_radar.xml",
            "htmlUrl": "https://github.com/search",
        },
    )

    cm_grp = ET.SubElement(body, "outline", {"text": "外部极客社区", "title": "外部极客社区"})
    ET.SubElement(
        cm_grp,
        "outline",
        {
            "type": "rss",
            "text": "开源社区发现（Show HN / HelloGitHub / HuggingFace / Reddit）",
            "title": "开源社区发现（Show HN / HelloGitHub / HuggingFace / Reddit）",
            "xmlUrl": f"{fbase}/community_picks.xml",
            "htmlUrl": "https://news.ycombinator.com/show",
        },
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(ET.tostring(opml, encoding="utf-8", xml_declaration=True))


def write_llms_txt(
    *,
    site_base_url: str,
    feeds_base_url: str,
    items: List[RepoItem],
    status: Dict[str, Any],
    out_path: Path,
    now_utc: Optional[datetime] = None,
) -> None:
    """Generate /llms.txt following llmstxt.org conventions for zero-setup AI Agent consumption."""
    now = now_utc or datetime.now(timezone.utc)
    site = site_base_url.rstrip("/")
    fbase = feeds_base_url.rstrip("/")
    run_date = str(status.get("last_run_date") or now.strftime("%Y-%m-%d"))
    trend_summary = str(status.get("trend_summary") or "").strip()

    lines = [
        "# GitHub Radar · 每日开源精选画刊",
        "",
        "> 每日自动聚合 GitHub Trending、新锐黑马检索、Show HN、HelloGitHub、HuggingFace Daily Papers、GitHub Releases 与极客社区的高信噪比开源项目，已完成反刷星、黑名单去噪、Star 增速与黑马爆发度计算及中文一句话导读。",
        "",
        f"- 更新日期: {run_date}",
        f"- Web 画刊: {site}/",
        "",
        "## 机器可读数据接口 (Machine-Readable Endpoints)",
        "",
        f"- [全量结构化 JSON (含 7d Sparkline / 黑马指数 / 信源健康度)]({fbase}/data.json): 推荐 AI Agent 首选读取，支持按语言、分类、增速筛选",
        f"- [JSON Feed 1.1]({fbase}/feed.json): 标准流式订阅接口",
        f"- [全源聚合 RSS 2.0]({fbase}/all_discoveries.xml): 全量去噪条目",
        f"- [GitHub Trending 分榜 RSS]({fbase}/github_trending.xml): 日榜热门项目",
        f"- [黑马雷达与 Release 分榜 RSS]({fbase}/github_radar.xml): 早期高爆发黑马与版本发布",
        f"- [极客社区分榜 RSS]({fbase}/community_picks.xml): Show HN / HelloGitHub / HuggingFace Papers",
        f"- [OPML 一键导入包]({fbase}/feeds.opml): RSS 阅读器订阅集合",
        "",
    ]

    if trend_summary:
        lines.extend(
            [
                "## 今日开源风向标",
                "",
                trend_summary,
                "",
            ]
        )

    lines.extend(["## 本期精选项目速览 (Top Curated Repositories)", ""])
    for item in items[:25]:
        safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
        breakout = compute_breakout_score(item, now_utc=now)
        meta = []
        if item.category_tag:
            meta.append(item.category_tag)
        if item.language:
            meta.append(item.language)
        star_s = f"★{item.stars:,}"
        if item.stars_delta_24h > 0:
            star_s += f" (+{item.stars_delta_24h:,}/24h)"
        meta.append(star_s)
        if breakout >= 20:
            meta.append(f"爆发度 {breakout:.1f}★/d")
        if len(item.sources) >= 2:
            meta.append(f"多源×{len(item.sources)}")
        summary = (item.ai_highlight or item.desc_cn or item.description or "").strip()
        lines.append(f"- [{item.repo_name}]({safe_url}) ({' · '.join(meta)}): {summary}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
