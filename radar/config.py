from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, timezone, tzinfo
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo
import yaml

logger = logging.getLogger(__name__)

SHANGHAI_FALLBACK_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_SHANGHAI_EQUIV_ZONES = {
    "Asia/Shanghai",
    "Asia/Chongqing",
    "Asia/Hong_Kong",
    "Asia/Taipei",
    "Asia/Singapore",
}
_FIXED_OFFSET_RE = re.compile(r"^([+-])(\d{1,2})(?::?(\d{2}))?$")


def resolve_timezone(output_cfg: Optional[Dict[str, Any]] = None) -> tzinfo:
    """Resolve business timezone (defaults to Asia/Shanghai) with zero-dependency fallback."""
    cfg = output_cfg or {}
    raw_tz = (
        os.environ.get("RADAR_TIMEZONE", "").strip()
        or str(cfg.get("timezone") or "").strip()
        or os.environ.get("TZ", "").strip()
        or "Asia/Shanghai"
    )
    up = raw_tz.upper()
    if up in {"UTC", "Z", "ETC/UTC", "GMT"}:
        return timezone.utc

    m = _FIXED_OFFSET_RE.match(up.replace("UTC", "").strip())
    if m and (raw_tz.startswith(("+", "-")) or up.startswith("UTC")):
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        mins = int(m.group(3) or 0)
        return timezone(sign * timedelta(hours=hours, minutes=mins))

    try:
        return ZoneInfo(raw_tz)
    except Exception:
        if raw_tz not in _SHANGHAI_EQUIV_ZONES:
            logger.warning("无法加载时区 %r，已回退至 Asia/Shanghai (+08:00)", raw_tz)
        return SHANGHAI_FALLBACK_TZ


@dataclass
class RadarConfig:
    sources: Dict[str, Any] = field(default_factory=dict)
    filters: Dict[str, Any] = field(default_factory=dict)
    translation: Dict[str, Any] = field(default_factory=dict)
    ai_highlight: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    network: Dict[str, Any] = field(default_factory=dict)
    notifications: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RadarConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            sources=dict(raw.get("sources") or {}),
            filters=dict(raw.get("filters") or {}),
            translation=dict(raw.get("translation") or {}),
            ai_highlight=dict(raw.get("ai_highlight") or {}),
            output=dict(raw.get("output") or {}),
            network=dict(raw.get("network") or {}),
            notifications=dict(raw.get("notifications") or {}),
        )

