from __future__ import annotations

import html
import json
import logging
import os
import re
from typing import Any, Dict, List
import urllib.request

from .feed_builder import apply_category_quota
from .models import RepoItem
from .sources import sanitize_http_url

logger = logging.getLogger(__name__)


def _clean_utf8(text: str) -> str:
    if not text:
        return ""
    return str(text).encode("utf-8", errors="replace").decode("utf-8")


def _compact_summary(item: RepoItem, max_chars: int = 240) -> str:
    raw = _clean_utf8(item.ai_highlight or item.desc_cn or item.description or "")
    collapsed = re.sub(r"\s+", " ", raw).strip()
    if len(collapsed) > max_chars:
        return collapsed[: max_chars - 1].rstrip() + "…"
    return collapsed


def _truncate_utf8_safe(
    text: str, *, max_chars: int, max_bytes: int, html_mode: bool = False
) -> str:
    s = _clean_utf8(text)
    if len(s) <= max_chars and len(s.encode("utf-8")) <= max_bytes:
        return s

    budget_chars = max(16, max_chars - 12)
    budget_bytes = max(32, max_bytes - 24)
    s = s[:budget_chars]
    while s and len(s.encode("utf-8")) > budget_bytes:
        s = s[:-1]

    if html_mode:
        last_lt = s.rfind("<")
        last_gt = s.rfind(">")
        if last_lt > last_gt:
            s = s[:last_lt]
        s = re.sub(r"&[a-zA-Z0-9#]*$", "", s)
        if s.count("<b>") > s.count("</b>"):
            s += "</b>"
        if s.count("<a ") > s.count("</a>"):
            s += "</a>"

    return s.rstrip() + "…"


def _open_url(req: urllib.request.Request, *, timeout: int = 12, proxy: str = ""):
    eff_proxy = proxy or os.environ.get("RADAR_PROXY", "").strip()
    if eff_proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": eff_proxy, "https": eff_proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def filter_digest_items(items: List[RepoItem], cfg: Dict[str, Any]) -> List[RepoItem]:
    min_sources = int(cfg.get("min_sources") or 1)
    min_stars = int(cfg.get("min_stars") or 0)
    langs = {str(x).strip().lower() for x in (cfg.get("languages") or []) if str(x).strip()}
    keywords = [str(k).strip().lower() for k in (cfg.get("keywords") or []) if str(k).strip()]
    top_n = max(1, int(cfg.get("top_n") or 10))
    max_cat_share = float(cfg.get("max_category_share") or 0.0)

    matched: List[RepoItem] = []
    for item in items:
        if len(item.sources) < min_sources:
            continue
        if item.stars < min_stars:
            continue
        if langs and (item.language or "").strip().lower() not in langs:
            continue
        if keywords:
            hay = f"{item.repo_name} {item.ai_highlight} {item.desc_cn} {item.description}".lower()
            if not any(k in hay for k in keywords):
                continue
        matched.append(item)

    if max_cat_share > 0.0 and len(matched) >= 5:
        return apply_category_quota(matched, max_cat_share, limit=top_n, strict_drop=True)
    return matched[:top_n]


def _format_markdown_entry(idx: int, item: RepoItem) -> str:
    badges = []
    if len(item.sources) >= 2:
        badges.append(f"🔥多源×{len(item.sources)}")
    if item.release_tag:
        badges.append(item.release_tag)
    if item.category_tag:
        badges.append(item.category_tag)
    if item.language:
        badges.append(item.language)
    star_str = f"★{item.stars:,}"
    if item.stars_delta_24h > 0:
        star_str += f" (+{item.stars_delta_24h:,})"
    badges.append(star_str)
    safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
    summary = _compact_summary(item)
    rname = _clean_utf8(item.repo_name)
    entry_lines = [f"{idx}. **[{rname}]({safe_url})** · {' · '.join(badges)}"]
    if summary:
        entry_lines.append(f"   {summary}")
    return _clean_utf8("\n".join(entry_lines))


def _format_telegram_html_entry(idx: int, item: RepoItem) -> str:
    badges = []
    if len(item.sources) >= 2:
        badges.append(f"🔥多源×{len(item.sources)}")
    if item.release_tag:
        badges.append(html.escape(_clean_utf8(item.release_tag)))
    if item.category_tag:
        badges.append(html.escape(_clean_utf8(item.category_tag)))
    if item.language:
        badges.append(html.escape(_clean_utf8(item.language)))
    star_str = f"★{item.stars:,}"
    if item.stars_delta_24h > 0:
        star_str += f" (+{item.stars_delta_24h:,})"
    badges.append(star_str)
    safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
    summary = _compact_summary(item)
    rname = _clean_utf8(item.repo_name)
    line1 = (
        f'{idx}. <a href="{html.escape(safe_url)}"><b>{html.escape(rname)}</b></a>'
        f" · {' · '.join(badges)}"
    )
    if summary:
        return f"{line1}\n   {html.escape(summary)}"
    return line1


def build_digest_markdown(
    items: List[RepoItem], status: Dict[str, Any], cfg: Dict[str, Any]
) -> str:
    date_label = str(status.get("last_run_date") or "今日")
    new_count = int(status.get("new_count") or 0)
    trend_summary = str(status.get("trend_summary") or "").strip()
    header = f"📡 **GitHub Radar 每日精选简报 ({date_label})**"
    sub = f"本次新收录 {new_count} 项 · 精选推送 {len(items)} 项"
    lines = [header, sub]
    if trend_summary:
        lines.extend(["", f"> 🧭 **今日风向标**：{trend_summary}"])
    lines.append("")

    for idx, item in enumerate(items, 1):
        lines.append(_format_markdown_entry(idx, item))
    return _clean_utf8("\n".join(lines).strip())


def build_digest_telegram_html(
    items: List[RepoItem], status: Dict[str, Any], *, part_suffix: str = ""
) -> str:
    date_label = html.escape(_clean_utf8(str(status.get("last_run_date") or "今日")))
    new_count = int(status.get("new_count") or 0)
    trend_summary = _clean_utf8(str(status.get("trend_summary") or "").strip())
    sfx = f" {html.escape(part_suffix)}" if part_suffix else ""
    lines = [
        f"📡 <b>GitHub Radar 每日精选简报 ({date_label}){sfx}</b>",
        f"本次新收录 {new_count} 项 · 精选推送 {len(items)} 项",
    ]
    if trend_summary and (not part_suffix or part_suffix.startswith("(1/")):
        lines.extend(["", f"🧭 <b>风向标：</b>{html.escape(trend_summary)}"])
    lines.append("")
    for idx, item in enumerate(items, 1):
        lines.append(_format_telegram_html_entry(idx, item))
    return _clean_utf8("\n".join(lines).strip())


def _provider_limits(provider: str) -> tuple[int, int]:
    """Return (max_chars, max_utf8_bytes) per single webhook message."""
    p = provider.strip().lower()
    if p == "wecom":
        return (3800, 3900)  # WeCom strictly enforces 4096 UTF-8 bytes
    if p == "discord":
        return (1900, 7500)  # Discord strictly enforces 2000 Unicode chars
    if p == "telegram":
        return (3900, 11500)  # Telegram strictly enforces 4096 chars
    if p == "dingtalk":
        return (12000, 18000)
    if p == "feishu":
        return (15000, 24000)
    return (30000, 60000)


def chunk_digest_items(
    provider: str,
    items: List[RepoItem],
    status: Dict[str, Any],
) -> List[tuple[List[RepoItem], str]]:
    """Split items into safe message chunks by item boundaries without slicing mid-link/tag."""
    if not items:
        return []
    p = provider.strip().lower()
    max_chars, max_bytes = _provider_limits(p)
    date_label = _clean_utf8(str(status.get("last_run_date") or "今日"))
    new_count = int(status.get("new_count") or 0)
    trend_summary = _clean_utf8(str(status.get("trend_summary") or "").strip())[:320]

    def _render_chunk(
        chunk_rows: List[tuple[int, RepoItem]], *, include_trend: bool, part_suffix: str = ""
    ) -> str:
        sfx = f" {part_suffix}" if part_suffix else ""
        if p == "telegram":
            head = [
                f"📡 <b>GitHub Radar 每日精选简报 ({html.escape(date_label)}){html.escape(sfx)}</b>",
                f"本次新收录 {new_count} 项 · 精选推送 {len(items)} 项",
            ]
            if include_trend and trend_summary:
                head.extend(["", f"🧭 <b>风向标：</b>{html.escape(trend_summary)}"])
            head.append("")
            body = [_format_telegram_html_entry(i, it) for i, it in chunk_rows]
            return _clean_utf8("\n".join(head + body).strip())

        head = [
            f"📡 **GitHub Radar 每日精选简报 ({date_label}){sfx}**",
            f"本次新收录 {new_count} 项 · 精选推送 {len(items)} 项",
        ]
        if include_trend and trend_summary:
            head.extend(["", f"> 🧭 **今日风向标**：{trend_summary}"])
        head.append("")
        body = [_format_markdown_entry(i, it) for i, it in chunk_rows]
        return _clean_utf8("\n".join(head + body).strip())

    raw_groups: List[List[tuple[int, RepoItem]]] = []
    cur_group: List[tuple[int, RepoItem]] = []

    for idx, item in enumerate(items, 1):
        trial = cur_group + [(idx, item)]
        rendered = _render_chunk(
            trial, include_trend=(len(raw_groups) == 0), part_suffix="(99/99)"
        )
        if cur_group and (
            len(rendered) > max_chars or len(rendered.encode("utf-8")) > max_bytes
        ):
            raw_groups.append(cur_group)
            cur_group = [(idx, item)]
        else:
            cur_group = trial

    if cur_group:
        raw_groups.append(cur_group)

    total_parts = len(raw_groups)
    out: List[tuple[List[RepoItem], str]] = []
    for part_idx, grp in enumerate(raw_groups, 1):
        suffix = f"({part_idx}/{total_parts})" if total_parts > 1 else ""
        text = _render_chunk(grp, include_trend=(part_idx == 1), part_suffix=suffix)
        text = _truncate_utf8_safe(
            text, max_chars=max_chars, max_bytes=max_bytes, html_mode=(p == "telegram")
        )
        out.append(([it for _, it in grp], text))
    return out


def build_webhook_payload(
    provider: str, title: str, markdown_text: str, items: List[RepoItem]
) -> Dict[str, Any]:
    p = provider.strip().lower()
    safe_title = _clean_utf8(title)
    safe_md = _clean_utf8(markdown_text)
    if p == "feishu":
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": safe_title},
                    "template": "orange",
                },
                "elements": [{"tag": "markdown", "content": safe_md}],
            },
        }
    if p == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": safe_title, "text": safe_md}}
    if p == "wecom":
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": _truncate_utf8_safe(safe_md, max_chars=4096, max_bytes=4090)
            },
        }
    if p == "discord":
        return {
            "content": _truncate_utf8_safe(safe_md, max_chars=1990, max_bytes=7800)
        }
    if p == "telegram":
        # If caller passed pre-built Telegram HTML (contains <b> or <a href=), keep it; else build safe HTML from items
        is_html = "<b>" in safe_md or "<a href=" in safe_md
        tg_text = (
            safe_md
            if is_html
            else (
                build_digest_telegram_html(items, {})
                if items
                else html.escape(safe_md)
            )
        )
        return {
            "text": _truncate_utf8_safe(
                tg_text, max_chars=4050, max_bytes=12000, html_mode=True
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    return {
        "title": safe_title,
        "markdown": safe_md,
        "items": [
            {
                "repo_name": _clean_utf8(it.repo_name),
                "url": sanitize_http_url(it.url) or f"https://github.com/{_clean_utf8(it.repo_name)}",
                "stars": it.stars,
                "stars_delta_24h": it.stars_delta_24h,
                "sources": [_clean_utf8(s) for s in it.sources],
                "highlight": _clean_utf8(it.ai_highlight or it.desc_cn or it.description),
            }
            for it in items
        ],
    }


def build_webhook_payloads(
    provider: str, title: str, items: List[RepoItem], status: Dict[str, Any]
) -> List[Dict[str, Any]]:
    chunks = chunk_digest_items(provider, items, status)
    total = len(chunks)
    payloads: List[Dict[str, Any]] = []
    for idx, (chunk_items, chunk_text) in enumerate(chunks, 1):
        chunk_title = f"{title} ({idx}/{total})" if total > 1 else title
        payloads.append(build_webhook_payload(provider, chunk_title, chunk_text, chunk_items))
    return payloads


def send_notifications(
    items: List[RepoItem],
    status: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    dry_run: bool = False,
    proxy: str = "",
) -> bool:
    if not dry_run and not cfg.get("enabled", False):
        return False

    digest_items = filter_digest_items(items, cfg)
    if not digest_items:
        logger.info("没有满足推送条件的项目，跳过通知")
        return False

    markdown_text = build_digest_markdown(digest_items, status, cfg)
    if dry_run:
        print(markdown_text)
        return True

    provider = str(cfg.get("provider") or "generic").strip().lower()
    webhook_url = str(
        cfg.get("webhook_url") or os.environ.get("RADAR_WEBHOOK_URL", "") or ""
    ).strip()

    chat_id = ""
    if provider == "telegram":
        bot_token = str(
            cfg.get("telegram_bot_token") or os.environ.get("RADAR_TG_BOT_TOKEN", "") or ""
        ).strip()
        chat_id = str(
            cfg.get("telegram_chat_id") or os.environ.get("RADAR_TG_CHAT_ID", "") or ""
        ).strip()
        if not bot_token or not chat_id:
            logger.info("Telegram 推送未配置 bot_token / chat_id，跳过")
            return False
        webhook_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    else:
        if not webhook_url:
            logger.info("未配置 webhook_url / RADAR_WEBHOOK_URL，跳过通知")
            return False

    payloads = build_webhook_payloads(provider, "GitHub Radar 每日精选", digest_items, status)
    if not payloads:
        return False

    sent_ok = True
    for idx, payload in enumerate(payloads, 1):
        if provider == "telegram":
            payload["chat_id"] = chat_id
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _open_url(req, timeout=12, proxy=proxy) as resp:
                logger.info(
                    "已发送 %s 简报通知分片 %s/%s (HTTP %s)",
                    provider,
                    idx,
                    len(payloads),
                    resp.status,
                )
        except Exception as exc:
            logger.warning("发送 %s 通知分片 %s/%s 失败: %s", provider, idx, len(payloads), exc)
            sent_ok = False
    return sent_ok
