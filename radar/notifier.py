from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List
import urllib.request

from .models import RepoItem

logger = logging.getLogger(__name__)


def filter_digest_items(items: List[RepoItem], cfg: Dict[str, Any]) -> List[RepoItem]:
    min_sources = int(cfg.get("min_sources") or 1)
    min_stars = int(cfg.get("min_stars") or 0)
    langs = {str(x).strip().lower() for x in (cfg.get("languages") or []) if str(x).strip()}
    keywords = [str(k).strip().lower() for k in (cfg.get("keywords") or []) if str(k).strip()]
    top_n = max(1, int(cfg.get("top_n") or 10))

    picked: List[RepoItem] = []
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
        picked.append(item)
        if len(picked) >= top_n:
            break
    return picked


def build_digest_markdown(
    items: List[RepoItem], status: Dict[str, Any], cfg: Dict[str, Any]
) -> str:
    date_label = str(status.get("last_run_date") or "今日")
    new_count = int(status.get("new_count") or 0)
    header = f"📡 **GitHub Radar 每日精选简报 ({date_label})**"
    sub = f"本次新收录 {new_count} 项 · 精选推送 {len(items)} 项"
    lines = [header, sub, ""]

    for idx, item in enumerate(items, 1):
        badges = []
        if len(item.sources) >= 2:
            badges.append(f"🔥多源×{len(item.sources)}")
        if item.category_tag:
            badges.append(item.category_tag)
        if item.language:
            badges.append(item.language)
        star_str = f"★{item.stars:,}"
        if item.stars_delta_24h > 0:
            star_str += f" (+{item.stars_delta_24h:,})"
        badges.append(star_str)
        summary = item.ai_highlight or item.desc_cn or item.description
        lines.append(f"{idx}. **[{item.repo_name}]({item.url})** · {' · '.join(badges)}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines).strip()


def build_webhook_payload(
    provider: str, title: str, markdown_text: str, items: List[RepoItem]
) -> Dict[str, Any]:
    p = provider.strip().lower()
    if p == "feishu":
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "orange",
                },
                "elements": [{"tag": "markdown", "content": markdown_text}],
            },
        }
    if p == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}
    if p == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": markdown_text}}
    if p == "discord":
        return {"content": markdown_text[:1900]}
    if p == "telegram":
        return {
            "text": markdown_text[:4000],
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
    return {
        "title": title,
        "markdown": markdown_text,
        "items": [
            {
                "repo_name": it.repo_name,
                "url": it.url,
                "stars": it.stars,
                "stars_delta_24h": it.stars_delta_24h,
                "sources": it.sources,
                "highlight": it.ai_highlight or it.desc_cn or it.description,
            }
            for it in items
        ],
    }


def send_notifications(
    items: List[RepoItem],
    status: Dict[str, Any],
    cfg: Dict[str, Any],
    *,
    dry_run: bool = False,
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

    if provider == "telegram":
        bot_token = str(cfg.get("telegram_bot_token") or os.environ.get("RADAR_TG_BOT_TOKEN", "") or "").strip()
        chat_id = str(cfg.get("telegram_chat_id") or os.environ.get("RADAR_TG_CHAT_ID", "") or "").strip()
        if not bot_token or not chat_id:
            logger.info("Telegram 推送未配置 bot_token / chat_id，跳过")
            return False
        webhook_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = build_webhook_payload(provider, "GitHub Radar 每日精选", markdown_text, digest_items)
        payload["chat_id"] = chat_id
    else:
        if not webhook_url:
            logger.info("未配置 webhook_url / RADAR_WEBHOOK_URL，跳过通知")
            return False
        payload = build_webhook_payload(provider, "GitHub Radar 每日精选", markdown_text, digest_items)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            logger.info("已发送 %s 简报通知 (HTTP %s, %s 条)", provider, resp.status, len(digest_items))
            return True
    except Exception as exc:
        logger.warning("发送 %s 通知失败: %s", provider, exc)
        return False
