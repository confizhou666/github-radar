from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from .config import RadarConfig
from .feed_builder import classify_bucket, write_rss_feed, write_web_json
from .filters import deduplicate_and_filter
from .models import RepoItem
from .sources import collect_all_sources, enrich_github_metadata
from .translator import generate_ai_highlights, translate_free_zh

logger = logging.getLogger(__name__)


class RadarStore:
    """SQLite persistence for deduplicated seen repositories and translation cache."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS repos (
                canonical_key TEXT PRIMARY KEY,
                repo_name TEXT NOT NULL,
                url TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                language TEXT,
                stars INTEGER NOT NULL DEFAULT 0,
                forks INTEGER NOT NULL DEFAULT 0,
                sources_json TEXT NOT NULL DEFAULT '[]',
                discussion_url TEXT NOT NULL DEFAULT '',
                desc_cn TEXT NOT NULL DEFAULT '',
                ai_highlight TEXT NOT NULL DEFAULT '',
                category_tag TEXT NOT NULL DEFAULT '',
                kept INTEGER NOT NULL DEFAULT 1,
                discovered_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def get_existing_keys(self) -> set[str]:
        rows = self.conn.execute("SELECT canonical_key FROM repos").fetchall()
        return {str(r["canonical_key"]) for r in rows}

    def upsert_repo(self, item: RepoItem, *, kept: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO repos (
                canonical_key, repo_name, url, description, language, stars, forks,
                sources_json, discussion_url, desc_cn, ai_highlight, category_tag, kept, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET
                stars = MAX(repos.stars, excluded.stars),
                forks = MAX(repos.forks, excluded.forks),
                sources_json = excluded.sources_json,
                desc_cn = CASE WHEN excluded.desc_cn != '' THEN excluded.desc_cn ELSE repos.desc_cn END,
                ai_highlight = CASE WHEN excluded.ai_highlight != '' THEN excluded.ai_highlight ELSE repos.ai_highlight END,
                category_tag = CASE WHEN excluded.category_tag != '' THEN excluded.category_tag ELSE repos.category_tag END
            """,
            (
                item.canonical_key,
                item.repo_name,
                item.url,
                item.description,
                item.language,
                int(item.stars),
                int(item.forks),
                json.dumps(item.sources, ensure_ascii=False),
                item.discussion_url,
                item.desc_cn,
                item.ai_highlight,
                item.category_tag,
                1 if kept else 0,
                item.discovered_at,
            ),
        )
        self.conn.commit()

    def load_recent_kept(self, limit: int = 120) -> List[RepoItem]:
        rows = self.conn.execute(
            "SELECT * FROM repos WHERE kept = 1 ORDER BY discovered_at DESC, stars DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out: List[RepoItem] = []
        for r in rows:
            out.append(
                RepoItem(
                    repo_name=r["repo_name"],
                    url=r["url"],
                    description=r["description"],
                    language=r["language"],
                    stars=int(r["stars"]),
                    forks=int(r["forks"]),
                    sources=json.loads(r["sources_json"] or "[]"),
                    discussion_url=r["discussion_url"],
                    desc_cn=r["desc_cn"],
                    ai_highlight=r["ai_highlight"],
                    category_tag=r["category_tag"],
                    discovered_at=r["discovered_at"],
                )
            )
        return out

    def close(self) -> None:
        self.conn.close()


def run_pipeline(
    cfg: RadarConfig,
    base_dir: Path,
    *,
    raw_items_override: Optional[List[RepoItem]] = None,
    translate_fn: Callable[[str], str] = translate_free_zh,
    ai_runner: Optional[Callable[[str], str]] = None,
    enable_ai: bool = True,
    skip_enrich: bool = False,
) -> Dict[str, Any]:
    data_dir = base_dir / "data"
    feeds_dir = base_dir / "feeds"
    data_dir.mkdir(parents=True, exist_ok=True)
    feeds_dir.mkdir(parents=True, exist_ok=True)

    store = RadarStore(data_dir / "radar.db")
    try:
        raw_items = (
            raw_items_override
            if raw_items_override is not None
            else collect_all_sources(cfg.sources)
        )
        if raw_items_override is None and not skip_enrich:
            enrich_github_metadata(raw_items, max_enrich=20)

        kept_candidates, filter_stats = deduplicate_and_filter(raw_items, cfg.filters)
        existing_keys = store.get_existing_keys()
        fresh_items = [r for r in kept_candidates if r.canonical_key not in existing_keys]
        existing_updates = [r for r in kept_candidates if r.canonical_key in existing_keys]

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Translate descriptions only for fresh items
        if cfg.translation.get("enabled", True):
            for item in fresh_items:
                item.discovered_at = now_iso
                item.desc_cn = translate_fn(item.description)
        else:
            for item in fresh_items:
                item.discovered_at = now_iso
                item.desc_cn = item.description

        # AI one-sentence highlights + semantic spam check in 1 batch
        ai_results: Dict[str, Dict[str, Any]] = {}
        if enable_ai and cfg.ai_highlight.get("enabled", True) and fresh_items:
            ai_results = generate_ai_highlights(fresh_items, cfg.ai_highlight, runner=ai_runner)

        kept_new_count = 0
        ai_dropped_count = 0
        for item in fresh_items:
            ai_info = ai_results.get(item.canonical_key)
            if ai_info:
                if not ai_info.get("keep", True):
                    store.upsert_repo(item, kept=False)
                    ai_dropped_count += 1
                    continue
                item.ai_highlight = ai_info.get("highlight", "")
                item.category_tag = ai_info.get("category_tag", "")
            store.upsert_repo(item, kept=True)
            kept_new_count += 1

        # Update star/source merges for previously seen items without re-translating
        for item in existing_updates:
            item.discovered_at = now_iso
            store.upsert_repo(item, kept=True)

        max_feed = int(cfg.output.get("max_feed_items") or 120)
        status_payload = {
            "last_run_at": datetime.now().isoformat(timespec="seconds"),
            "last_run_date": datetime.now().strftime("%Y-%m-%d"),
            "raw_count": filter_stats["raw_count"],
            "unique_count": filter_stats["unique_count"],
            "dropped_count": filter_stats["dropped_count"] + ai_dropped_count,
            "new_count": kept_new_count,
            "feed_total": 0,
        }
        feed_total = _write_all_outputs(store, cfg, feeds_dir, status_payload, max_feed=max_feed)
        status_payload["feed_total"] = feed_total
        (data_dir / "status.json").write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return status_payload
    finally:
        store.close()


def _write_all_outputs(
    store: RadarStore,
    cfg: RadarConfig,
    feeds_dir: Path,
    status_payload: Dict[str, Any],
    *,
    max_feed: int = 120,
) -> int:
    recent_kept = store.load_recent_kept(limit=max_feed)
    web_max = max(max_feed, int(cfg.output.get("max_web_items") or 300))
    web_items = store.load_recent_kept(limit=web_max)
    base_url = str(cfg.output.get("public_base_url") or "http://localhost:8088/feeds").rstrip("/")

    write_rss_feed(
        title="GitHub 每日趣闻精选（全源聚合）",
        description="全语言聚合 · 黑名单去噪 · 跨源去重 · 中文翻译与一句话亮点",
        link=f"{base_url}/all_discoveries.xml",
        items=recent_kept,
        out_path=feeds_dir / "all_discoveries.xml",
    )

    trending_items: List[RepoItem] = []
    radar_items: List[RepoItem] = []
    community_items: List[RepoItem] = []
    for item in recent_kept:
        bucket = classify_bucket(item.sources)
        if bucket == "trending":
            trending_items.append(item)
        elif bucket == "radar":
            radar_items.append(item)
        else:
            community_items.append(item)

    write_rss_feed(
        title="GitHub Trending（全语言精选）",
        description="GitHub 日榜去噪与中文亮点",
        link=f"{base_url}/github_trending.xml",
        items=trending_items,
        out_path=feeds_dir / "github_trending.xml",
    )
    write_rss_feed(
        title="GitHub 定向雷达 & 开发者关注",
        description="低星黑马 / 创作者动态精选",
        link=f"{base_url}/github_radar.xml",
        items=radar_items,
        out_path=feeds_dir / "github_radar.xml",
    )
    write_rss_feed(
        title="开源社区发现（Show HN / HelloGitHub / Reddit）",
        description="跨社区精选开源项目",
        link=f"{base_url}/community_picks.xml",
        items=community_items,
        out_path=feeds_dir / "community_picks.xml",
    )

    status_for_web = dict(status_payload)
    status_for_web["feed_total"] = len(recent_kept)
    write_web_json(
        items=web_items,
        status=status_for_web,
        out_path=feeds_dir / "data.json",
    )
    return len(recent_kept)


def export_only(cfg: RadarConfig, base_dir: Path) -> Dict[str, Any]:
    """Re-export static JSON and RSS XML directly from existing radar.db without fetching or calling AI."""
    data_dir = base_dir / "data"
    feeds_dir = base_dir / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "status.json"
    status_payload: Dict[str, Any] = {}
    if status_path.exists():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status_payload = {}
    if not status_payload:
        status_payload = {
            "last_run_at": datetime.now().isoformat(timespec="seconds"),
            "last_run_date": datetime.now().strftime("%Y-%m-%d"),
            "new_count": 0,
            "feed_total": 0,
        }

    store = RadarStore(data_dir / "radar.db")
    try:
        max_feed = int(cfg.output.get("max_feed_items") or 120)
        feed_total = _write_all_outputs(store, cfg, feeds_dir, status_payload, max_feed=max_feed)
        status_payload["feed_total"] = feed_total
        return status_payload
    finally:
        store.close()
