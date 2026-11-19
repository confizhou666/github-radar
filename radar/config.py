from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
import yaml


@dataclass
class RadarConfig:
    sources: Dict[str, Any] = field(default_factory=dict)
    filters: Dict[str, Any] = field(default_factory=dict)
    translation: Dict[str, Any] = field(default_factory=dict)
    ai_highlight: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RadarConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            sources=dict(raw.get("sources") or {}),
            filters=dict(raw.get("filters") or {}),
            translation=dict(raw.get("translation") or {}),
            ai_highlight=dict(raw.get("ai_highlight") or {}),
            output=dict(raw.get("output") or {}),
        )
