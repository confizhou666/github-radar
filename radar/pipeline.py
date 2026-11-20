from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, tzinfo
import gzip
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request

from .config import RadarConfig, resolve_timezone
from .feed_builder import (
    apply_category_quota,
    classify_bucket,
    resolve_site_and_feeds_urls,
    write_json_feed,
    write_llms_txt,
    write_opml,
    write_rss_feed,
    write_web_json,
)
from .filters import is_noisy_repo, merge_raw_items, prefilter_merged_items
from .models import RepoItem
from .notifier import send_notifications
from .sources import (
    UA,
    _needs_enrichment,
    collect_all_sources,
    enrich_github_metadata,
    resolve_github_token,
    resolve_proxy,
    sanitize_http_url,
)
from .translator import (
    build_fallback_trend_summary,
    classify_repo_category,
    generate_ai_highlights,
    translate_free_zh,
)

logger = logging.getLogger(__name__)


class RadarStore:
    """SQLite persistence for deduplicated repositories, translation cache, and star snapshots."""

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
                open_issues INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                pushed_at TEXT NOT NULL DEFAULT '',
                sources_json TEXT NOT NULL DEFAULT '[]',
                discussion_url TEXT NOT NULL DEFAULT '',
                desc_cn TEXT NOT NULL DEFAULT '',
                ai_highlight TEXT NOT NULL DEFAULT '',
                category_tag TEXT NOT NULL DEFAULT '',
                stars_delta_24h INTEGER NOT NULL DEFAULT 0,
                stars_delta_7d INTEGER NOT NULL DEFAULT 0,
                topics_json TEXT NOT NULL DEFAULT '[]',
                license_name TEXT NOT NULL DEFAULT '',
                release_tag TEXT NOT NULL DEFAULT '',
                    community_score INTEGER NOT NULL DEFAULT 0,
                last_seen_date TEXT NOT NULL DEFAULT '',
                metadata_enriched INTEGER NOT NULL DEFAULT 0,
                ai_rejected INTEGER NOT NULL DEFAULT 0,
                kept INTEGER NOT NULL DEFAULT 1,
                discovered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS repo_snapshots (
                canonical_key TEXT NOT NULL,
                snap_date TEXT NOT NULL,
                stars INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (canonical_key, snap_date)
            );
            """
        )
        existing_cols = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(repos)").fetchall()
        }
        migrations = [
            ("open_issues", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("pushed_at", "TEXT NOT NULL DEFAULT ''"),
            ("stars_delta_24h", "INTEGER NOT NULL DEFAULT 0"),
            ("stars_delta_7d", "INTEGER NOT NULL DEFAULT 0"),
            ("topics_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("license_name", "TEXT NOT NULL DEFAULT ''"),
            ("release_tag", "TEXT NOT NULL DEFAULT ''"),
            ("community_score", "INTEGER NOT NULL DEFAULT 0"),
            ("last_seen_date", "TEXT NOT NULL DEFAULT ''"),
            ("metadata_enriched", "INTEGER NOT NULL DEFAULT 0"),
            ("ai_rejected", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for col, ddl in migrations:
            if col not in existing_cols:
                self.conn.execute(f"ALTER TABLE repos ADD COLUMN {col} {ddl}")
        self.conn.execute(
            "UPDATE repos SET metadata_enriched = 1 WHERE metadata_enriched = 0 AND created_at != ''"
        )
        self.conn.execute(
            "UPDATE repos SET ai_rejected = 1 WHERE kept = 0 AND ai_rejected = 0 AND ai_highlight = 'AI 判定为低质或套壳噪音仓库'"
        )
        self.conn.commit()

    def count_repos(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM repos").fetchone()
        return int(row["cnt"] if row else 0)

    def get_existing_keys(self) -> set[str]:
        rows = self.conn.execute("SELECT canonical_key FROM repos").fetchall()
        return {str(r["canonical_key"]) for r in rows}

    def get_existing_meta(self) -> Dict[str, Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT canonical_key, release_tag, kept, ai_highlight, ai_rejected FROM repos"
        ).fetchall()
        return {
            str(r["canonical_key"]): {
                "release_tag": str(r["release_tag"] or ""),
                "kept": bool(r["kept"]),
                "ai_highlight": str(r["ai_highlight"] or ""),
                "ai_rejected": bool(r["ai_rejected"]),
            }
            for r in rows
        }

    def get_negative_cache_items(self) -> Dict[str, Tuple[RepoItem, str, bool]]:
        """Load kept=0 rows so previously identified noisy repos avoid redundant API enrichment."""
        rows = self.conn.execute("SELECT * FROM repos WHERE kept = 0").fetchall()
        return {
            str(r["canonical_key"]): (
                self._row_to_item(r),
                str(r["last_seen_date"] or ""),
                bool(r["ai_rejected"]),
            )
            for r in rows
        }

    def backfill_missing_categories(self) -> int:
        """Populate deterministic Chinese category_tag for any kept=1 rows lacking one."""
        rows = self.conn.execute(
            "SELECT * FROM repos WHERE kept = 1 AND (category_tag = '' OR category_tag IS NULL)"
        ).fetchall()
        updated = 0
        for r in rows:
            item = self._row_to_item(r)
            tag = classify_repo_category(item)
            if tag:
                self.conn.execute(
                    "UPDATE repos SET category_tag = ? WHERE canonical_key = ?",
                    (tag, str(r["canonical_key"])),
                )
                updated += 1
        if updated:
            self.conn.commit()
        return updated

    def compute_deltas(
        self, canonical_key: str, snap_date: str, current_stars: int
    ) -> Tuple[int, int]:
        """Compute (delta_24h, delta_7d) against historical snapshots prior to snap_date."""
        try:
            today_dt = datetime.strptime(snap_date, "%Y-%m-%d").date()
        except ValueError:
            return 0, 0
        cutoff_7d = (today_dt - timedelta(days=7)).isoformat()
        cutoff_24h = (today_dt - timedelta(days=2)).isoformat()

        rows = self.conn.execute(
            """
            SELECT snap_date, stars FROM repo_snapshots
            WHERE canonical_key = ? AND snap_date < ? AND snap_date >= ?
            ORDER BY snap_date ASC
            """,
            (canonical_key, snap_date, cutoff_7d),
        ).fetchall()
        if not rows:
            return 0, 0

        delta_7d = max(0, int(current_stars) - int(rows[0]["stars"]))
        prev_recent = [r for r in rows if str(r["snap_date"]) >= cutoff_24h]
        delta_24h = (
            max(0, int(current_stars) - int(prev_recent[-1]["stars"])) if prev_recent else 0
        )
        return delta_24h, max(delta_24h, delta_7d)

    def record_snapshot(self, canonical_key: str, snap_date: str, stars: int) -> None:
        self.conn.execute(
            """
            INSERT INTO repo_snapshots (canonical_key, snap_date, stars)
            VALUES (?, ?, ?)
            ON CONFLICT(canonical_key, snap_date) DO UPDATE SET
                stars = MAX(repo_snapshots.stars, excluded.stars)
            """,
            (canonical_key, snap_date, int(stars)),
        )
        self.conn.commit()

    def decay_stale_deltas(self, snap_date: str) -> None:
        """Reset 24h/7d deltas on stored repos not observed on snap_date so historical spikes never freeze."""
        try:
            today_dt = datetime.strptime(snap_date, "%Y-%m-%d").date()
            cutoff_7d = (today_dt - timedelta(days=7)).isoformat()
        except ValueError:
            return

        self.conn.execute(
            """
            UPDATE repos
            SET stars_delta_24h = 0
            WHERE stars_delta_24h != 0
              AND (
                (last_seen_date != '' AND last_seen_date < ?)
                OR (last_seen_date = '' AND substr(discovered_at, 1, 10) < ?)
              )
            """,
            (snap_date, snap_date),
        )
        self.conn.execute(
            """
            UPDATE repos
            SET stars_delta_7d = 0
            WHERE stars_delta_7d != 0
              AND (
                (last_seen_date != '' AND last_seen_date < ?)
                OR (last_seen_date = '' AND substr(discovered_at, 1, 10) < ?)
              )
            """,
            (cutoff_7d, cutoff_7d),
        )
        self.conn.commit()

    def _row_to_item(self, r: sqlite3.Row) -> RepoItem:
        return RepoItem(
            repo_name=str(r["repo_name"]),
            url=str(r["url"]),
            description=str(r["description"] or ""),
            language=r["language"],
            stars=int(r["stars"] or 0),
            forks=int(r["forks"] or 0),
            open_issues=int(r["open_issues"] or 0),
            created_at=str(r["created_at"] or ""),
            pushed_at=str(r["pushed_at"] or ""),
            sources=json.loads(r["sources_json"] or "[]"),
            discussion_url=str(r["discussion_url"] or ""),
            desc_cn=str(r["desc_cn"] or ""),
            ai_highlight=str(r["ai_highlight"] or ""),
            category_tag=str(r["category_tag"] or ""),
            discovered_at=str(r["discovered_at"] or ""),
            stars_delta_24h=int(r["stars_delta_24h"] or 0),
            stars_delta_7d=int(r["stars_delta_7d"] or 0),
            topics=json.loads(r["topics_json"] or "[]"),
            license_name=str(r["license_name"] or ""),
            release_tag=str(r["release_tag"] or ""),
            community_score=int(r["community_score"] or 0),
            metadata_enriched=bool(r["metadata_enriched"] or r["created_at"]),
        )

    def reapply_filters(
        self, filters_cfg: Dict[str, Any], *, now_utc: Optional[datetime] = None
    ) -> int:
        """Re-evaluate stored items against current whitelist, blacklist, and anti-star-farm rules."""
        whitelist = [
            str(w or "").strip().lower()
            for w in (filters_cfg.get("whitelist_repos") or [])
            if str(w or "").strip()
        ]
        if whitelist:
            placeholders = ",".join("?" for _ in whitelist)
            self.conn.execute(
                f"UPDATE repos SET kept = 1, ai_rejected = 0 WHERE kept = 0 AND canonical_key IN ({placeholders})",
                tuple(whitelist),
            )
            self.conn.commit()

        rows = self.conn.execute("SELECT * FROM repos WHERE kept = 1").fetchall()
        purged = 0
        for r in rows:
            item = self._row_to_item(r)
            drop, _ = is_noisy_repo(item, filters_cfg, now_utc=now_utc, pre_enrich=False)
            if drop:
                self.conn.execute(
                    "UPDATE repos SET kept = 0 WHERE canonical_key = ?",
                    (str(r["canonical_key"]),),
                )
                purged += 1
        if purged:
            self.conn.commit()
        return purged

    def upsert_repo(
        self,
        item: RepoItem,
        *,
        kept: bool,
        seen_date: str = "",
        force_refresh_discovered: bool = False,
        ai_rejected: bool = False,
    ) -> None:
        key = item.canonical_key
        safe_url = sanitize_http_url(item.url) or f"https://github.com/{item.repo_name}"
        safe_disc = sanitize_http_url(item.discussion_url)
        existing = self.conn.execute(
            "SELECT * FROM repos WHERE canonical_key = ?", (key,)
        ).fetchone()

        if existing is None:
            eff_seen = seen_date or (item.discovered_at[:10] if item.discovered_at else "")
            is_enriched = 1 if (item.metadata_enriched or item.created_at) else 0
            self.conn.execute(
                """
                INSERT INTO repos (
                    canonical_key, repo_name, url, description, language, stars, forks,
                    open_issues, created_at, pushed_at, sources_json, discussion_url,
                    desc_cn, ai_highlight, category_tag,
                    stars_delta_24h, stars_delta_7d, topics_json, license_name, release_tag,
                    community_score, last_seen_date, metadata_enriched, ai_rejected, kept, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    item.repo_name,
                    safe_url,
                    item.description,
                    item.language,
                    int(item.stars),
                    int(item.forks),
                    int(item.open_issues),
                    item.created_at,
                    item.pushed_at,
                    json.dumps(list(dict.fromkeys(item.sources)), ensure_ascii=False),
                    safe_disc,
                    item.desc_cn,
                    item.ai_highlight,
                    item.category_tag,
                    int(item.stars_delta_24h),
                    int(item.stars_delta_7d),
                    json.dumps(list(dict.fromkeys(item.topics))[:8], ensure_ascii=False),
                    item.license_name,
                    item.release_tag,
                    int(item.community_score),
                    eff_seen,
                    is_enriched,
                    1 if ai_rejected else 0,
                    1 if kept else 0,
                    item.discovered_at,
                ),
            )
            self.conn.commit()
            return

        old_sources = json.loads(existing["sources_json"] or "[]")
        merged_sources = list(dict.fromkeys([*old_sources, *item.sources]))
        old_topics = json.loads(existing["topics_json"] or "[]")
        merged_topics = list(dict.fromkeys([*old_topics, *item.topics]))[:8]

        old_desc = str(existing["description"] or "")
        if force_refresh_discovered and item.description:
            new_desc = item.description
        elif item.description and (not old_desc or old_desc.startswith("Sponsor Star ")):
            new_desc = item.description
        else:
            new_desc = old_desc

        old_desc_cn = str(existing["desc_cn"] or "")
        if force_refresh_discovered and item.desc_cn:
            new_desc_cn = item.desc_cn
        elif item.desc_cn:
            new_desc_cn = item.desc_cn
        else:
            new_desc_cn = old_desc_cn

        old_seen = str(existing["last_seen_date"] or "")
        eff_seen = seen_date or old_seen
        if seen_date and old_seen == seen_date:
            new_d24 = max(int(existing["stars_delta_24h"] or 0), int(item.stars_delta_24h))
            new_d7 = max(int(existing["stars_delta_7d"] or 0), int(item.stars_delta_7d))
        elif seen_date:
            new_d24 = int(item.stars_delta_24h)
            new_d7 = int(item.stars_delta_7d)
        else:
            new_d24 = (
                int(item.stars_delta_24h)
                if item.stars_delta_24h > 0
                else int(existing["stars_delta_24h"] or 0)
            )
            new_d7 = (
                int(item.stars_delta_7d)
                if item.stars_delta_7d > 0
                else int(existing["stars_delta_7d"] or 0)
            )

        new_stars = int(item.stars) if item.metadata_enriched else max(int(existing["stars"] or 0), int(item.stars))
        new_forks = int(item.forks) if item.metadata_enriched else max(int(existing["forks"] or 0), int(item.forks))
        new_discovered = (
            item.discovered_at
            if (force_refresh_discovered and item.discovered_at)
            else str(existing["discovered_at"])
        )
        new_disc_url = (
            safe_disc
            if (force_refresh_discovered and safe_disc)
            else (safe_disc or str(existing["discussion_url"] or ""))
        )
        new_kept = (1 if kept else 0) if not kept else int(existing["kept"])
        if force_refresh_discovered and kept:
            new_kept = 1
        new_enriched = (
            1
            if (item.metadata_enriched or item.created_at or int(existing["metadata_enriched"] or 0))
            else 0
        )
        if ai_rejected:
            new_ai_rejected = 1
        elif new_kept == 1:
            new_ai_rejected = 0
        else:
            new_ai_rejected = int(existing["ai_rejected"] or 0)

        self.conn.execute(
            """
            UPDATE repos SET
                repo_name = ?,
                url = ?,
                description = ?,
                language = COALESCE(?, language),
                stars = ?,
                forks = ?,
                open_issues = MAX(open_issues, ?),
                created_at = CASE WHEN ? != '' THEN ? ELSE created_at END,
                pushed_at = CASE WHEN ? != '' THEN ? ELSE pushed_at END,
                sources_json = ?,
                discussion_url = ?,
                desc_cn = ?,
                ai_highlight = CASE WHEN ? != '' THEN ? ELSE ai_highlight END,
                category_tag = CASE WHEN ? != '' THEN ? ELSE category_tag END,
                stars_delta_24h = ?,
                stars_delta_7d = ?,
                topics_json = ?,
                license_name = CASE WHEN ? != '' THEN ? ELSE license_name END,
                release_tag = CASE WHEN ? != '' THEN ? ELSE release_tag END,
                community_score = MAX(community_score, ?),
                last_seen_date = ?,
                metadata_enriched = ?,
                ai_rejected = ?,
                kept = ?,
                discovered_at = ?
            WHERE canonical_key = ?
            """,
            (
                item.repo_name or str(existing["repo_name"]),
                safe_url,
                new_desc,
                item.language,
                new_stars,
                new_forks,
                int(item.open_issues),
                item.created_at,
                item.created_at,
                item.pushed_at,
                item.pushed_at,
                json.dumps(merged_sources, ensure_ascii=False),
                new_disc_url,
                new_desc_cn,
                item.ai_highlight,
                item.ai_highlight,
                item.category_tag,
                item.category_tag,
                new_d24,
                new_d7,
                json.dumps(merged_topics, ensure_ascii=False),
                item.license_name,
                item.license_name,
                item.release_tag,
                item.release_tag,
                int(item.community_score),
                eff_seen,
                new_enriched,
                new_ai_rejected,
                new_kept,
                new_discovered,
                key,
            ),
        )
        self.conn.commit()

    def load_unhighlighted_kept(self, limit: int = 40) -> List[RepoItem]:
        rows = self.conn.execute(
            """
            SELECT * FROM repos
            WHERE kept = 1 AND (ai_highlight = '' OR ai_highlight IS NULL)
            ORDER BY substr(discovered_at, 1, 10) DESC,
                     json_array_length(sources_json) DESC,
                     stars_delta_24h DESC,
                     stars DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def apply_ai_backfill(self, canonical_key: str, ai_info: Dict[str, Any]) -> bool:
        """Apply AI highlight/category/spam verdict to an existing DB item. Returns True if dropped."""
        if not ai_info.get("keep", True):
            rej_hl = str(ai_info.get("highlight") or "AI 判定为低质或套壳噪音仓库")
            self.conn.execute(
                "UPDATE repos SET kept = 0, ai_rejected = 1, category_tag = ?, ai_highlight = ? WHERE canonical_key = ?",
                (
                    str(ai_info.get("category_tag") or ""),
                    rej_hl,
                    canonical_key,
                ),
            )
            self.conn.commit()
            return True

        self.conn.execute(
            """
            UPDATE repos
            SET ai_highlight = CASE WHEN ? != '' THEN ? ELSE ai_highlight END,
                category_tag = CASE WHEN ? != '' THEN ? ELSE category_tag END
            WHERE canonical_key = ?
            """,
            (
                str(ai_info.get("highlight") or ""),
                str(ai_info.get("highlight") or ""),
                str(ai_info.get("category_tag") or ""),
                str(ai_info.get("category_tag") or ""),
                canonical_key,
            ),
        )
        self.conn.commit()
        return False

    def load_recent_kept(self, limit: int = 120) -> List[RepoItem]:
        rows = self.conn.execute(
            """
            SELECT * FROM repos
            WHERE kept = 1
            ORDER BY substr(discovered_at, 1, 10) DESC,
                     json_array_length(sources_json) DESC,
                     stars_delta_24h DESC,
                     stars DESC,
                     discovered_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def load_sparklines(self, keys: List[str], *, days: int = 7) -> Dict[str, List[int]]:
        if not keys:
            return {}
        out: Dict[str, List[int]] = {}
        for start in range(0, len(keys), 200):
            chunk = keys[start : start + 200]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"""
                SELECT canonical_key, snap_date, stars
                FROM repo_snapshots
                WHERE canonical_key IN ({placeholders})
                ORDER BY canonical_key ASC, snap_date ASC
                """,
                tuple(chunk),
            ).fetchall()
            for r in rows:
                k = str(r["canonical_key"])
                out.setdefault(k, []).append(int(r["stars"] or 0))
        for k in list(out.keys()):
            out[k] = out[k][-days:]
        return out

    def export_state_gzip(
        self,
        out_path: Path,
        *,
        max_repos: int = 1800,
        snapshot_days: int = 14,
        now_biz: Optional[datetime] = None,
    ) -> None:
        """Export a compact gzipped JSON state snapshot (public repo metadata only) for Pages cache recovery."""
        ref_now = now_biz or datetime.now(timezone.utc)
        repo_rows = self.conn.execute(
            "SELECT * FROM repos ORDER BY discovered_at DESC LIMIT ?", (int(max_repos),)
        ).fetchall()
        cutoff = (ref_now.date() - timedelta(days=snapshot_days)).isoformat()
        snap_rows = self.conn.execute(
            "SELECT canonical_key, snap_date, stars FROM repo_snapshots WHERE snap_date >= ? ORDER BY snap_date ASC",
            (cutoff,),
        ).fetchall()

        doc = {
            "schema": 1,
            "exported_at": ref_now.isoformat(timespec="seconds"),
            "repos": [dict(r) for r in repo_rows],
            "snapshots": [dict(s) for s in snap_rows],
        }
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".gz.tmp")
        with gzip.open(tmp_path, "wb", compresslevel=6) as gz:
            gz.write(raw)
        tmp_path.replace(out_path)

    def import_state_gzip_bytes(self, gz_bytes: bytes) -> int:
        """Restore repos and snapshots from a gzipped (or proxy-decompressed) JSON state snapshot."""
        if not gz_bytes:
            return 0
        if gz_bytes[:2] == b"\x1f\x8b":
            raw = gzip.decompress(gz_bytes).decode("utf-8")
        else:
            raw = gz_bytes.decode("utf-8")
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            return 0

        def _safe_json_list_str(val: Any) -> str:
            if isinstance(val, list):
                return json.dumps([str(x) for x in val], ensure_ascii=False)
            try:
                parsed = json.loads(str(val or "[]"))
                if isinstance(parsed, list):
                    return json.dumps([str(x) for x in parsed], ensure_ascii=False)
            except Exception:
                pass
            return "[]"

        repos = doc.get("repos") or []
        snapshots = doc.get("snapshots") or []
        imported = 0
        for r in repos:
            if not isinstance(r, dict):
                continue
            key = str(r.get("canonical_key") or "").strip().lower()
            rname = str(r.get("repo_name") or "").strip()
            if not key or "/" not in key or not rname:
                continue
            is_enriched = 1 if (r.get("metadata_enriched") or r.get("created_at")) else 0
            is_ai_rej = (
                1
                if (
                    r.get("ai_rejected")
                    or (
                        not r.get("kept", 1)
                        and str(r.get("ai_highlight") or "") == "AI 判定为低质或套壳噪音仓库"
                    )
                )
                else 0
            )
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO repos (
                    canonical_key, repo_name, url, description, language, stars, forks,
                    open_issues, created_at, pushed_at, sources_json, discussion_url,
                    desc_cn, ai_highlight, category_tag,
                    stars_delta_24h, stars_delta_7d, topics_json, license_name, release_tag,
                    community_score, last_seen_date, metadata_enriched, ai_rejected, kept, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    rname,
                    sanitize_http_url(str(r.get("url") or "")) or f"https://github.com/{rname}",
                    str(r.get("description") or ""),
                    r.get("language"),
                    int(r.get("stars") or 0),
                    int(r.get("forks") or 0),
                    int(r.get("open_issues") or 0),
                    str(r.get("created_at") or ""),
                    str(r.get("pushed_at") or ""),
                    _safe_json_list_str(r.get("sources_json")),
                    sanitize_http_url(str(r.get("discussion_url") or "")),
                    str(r.get("desc_cn") or ""),
                    str(r.get("ai_highlight") or ""),
                    str(r.get("category_tag") or ""),
                    int(r.get("stars_delta_24h") or 0),
                    int(r.get("stars_delta_7d") or 0),
                    _safe_json_list_str(r.get("topics_json")),
                    str(r.get("license_name") or ""),
                    str(r.get("release_tag") or ""),
                    int(r.get("community_score") or 0),
                    str(r.get("last_seen_date") or ""),
                    is_enriched,
                    is_ai_rej,
                    1 if r.get("kept", 1) else 0,
                    str(r.get("discovered_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ),
            )
            if cur.rowcount > 0:
                imported += 1

        for s in snapshots:
            if not isinstance(s, dict):
                continue
            skey = str(s.get("canonical_key") or "").strip().lower()
            sdate = str(s.get("snap_date") or "").strip()
            if skey and sdate:
                self.conn.execute(
                    """
                    INSERT INTO repo_snapshots (canonical_key, snap_date, stars)
                    VALUES (?, ?, ?)
                    ON CONFLICT(canonical_key, snap_date) DO UPDATE SET
                        stars = MAX(repo_snapshots.stars, excluded.stars)
                    """,
                    (skey, sdate, int(s.get("stars") or 0)),
                )
        self.conn.commit()
        return imported

    def close(self) -> None:
        self.conn.close()


def restore_state_from_source(store: RadarStore, source: str, *, proxy: str = "") -> int:
    """Restore state_backup.json.gz from a local file path or HTTP(S) URL."""
    src = (source or "").strip()
    if not src:
        return 0
    if src.startswith(("http://", "https://")):
        req = urllib.request.Request(src, headers={"User-Agent": UA})
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
            ctx = opener.open(req, timeout=15)
        else:
            ctx = urllib.request.urlopen(req, timeout=15)
        with ctx as resp:
            return store.import_state_gzip_bytes(resp.read())

    path = Path(src)
    if path.is_file():
        return store.import_state_gzip_bytes(path.read_bytes())
    return 0


def _try_auto_restore_remote_state(store: RadarStore, cfg: RadarConfig, *, proxy: str = "") -> int:
    if store.count_repos() > 0:
        return 0
    explicit_url = os.environ.get("RADAR_STATE_BACKUP_URL", "").strip()
    pub_env = os.environ.get("RADAR_PUBLIC_BASE_URL", "").strip()
    if not explicit_url and not pub_env:
        return 0
    _, feeds_url = resolve_site_and_feeds_urls(cfg.output)
    backup_url = explicit_url or f"{feeds_url}/state_backup.json.gz"
    try:
        restored = restore_state_from_source(store, backup_url, proxy=proxy)
        if restored > 0:
            logger.info("已从线上状态备份 (%s) 恢复 %s 条历史记录", backup_url, restored)
        return restored
    except Exception as exc:
        logger.debug("线上状态备份未命中或首次部署 (%s): %s", backup_url, exc)
        return 0


def _is_fallback_trend_summary(text: str) -> bool:
    return str(text or "").strip().startswith("本期在榜 ")


def _apply_quota_by_date_groups(items: List[RepoItem], max_share: float) -> List[RepoItem]:
    if max_share <= 0.0 or max_share >= 1.0 or len(items) < 5:
        return items
    grouped: Dict[str, List[RepoItem]] = {}
    order_keys: List[str] = []
    for it in items:
        dk = (it.discovered_at or "")[:10] or "unknown"
        if dk not in grouped:
            grouped[dk] = []
            order_keys.append(dk)
        grouped[dk].append(it)
    out: List[RepoItem] = []
    for dk in order_keys:
        out.extend(apply_category_quota(grouped[dk], max_share, strict_drop=False))
    return out


def run_pipeline(
    cfg: RadarConfig,
    base_dir: Path,
    *,
    raw_items_override: Optional[List[RepoItem]] = None,
    translate_fn: Callable[[str], str] = translate_free_zh,
    ai_runner: Optional[Callable[[str], str]] = None,
    enable_ai: bool = True,
    skip_enrich: bool = False,
    enable_notify: bool = True,
    notify_dry_run: bool = False,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    data_dir = base_dir / "data"
    feeds_dir = base_dir / "feeds"
    data_dir.mkdir(parents=True, exist_ok=True)
    feeds_dir.mkdir(parents=True, exist_ok=True)

    proxy = resolve_proxy(cfg.network)
    github_token = resolve_github_token(cfg.network)
    eff_now_utc = now_utc or datetime.now(timezone.utc)
    if eff_now_utc.tzinfo is None:
        eff_now_utc = eff_now_utc.replace(tzinfo=timezone.utc)
    biz_tz = resolve_timezone(cfg.output)
    now_biz = eff_now_utc.astimezone(biz_tz)
    now_iso = now_biz.isoformat(timespec="seconds")
    snap_date = now_biz.strftime("%Y-%m-%d")

    store = RadarStore(data_dir / "radar.db")
    try:
        if raw_items_override is None:
            _try_auto_restore_remote_state(store, cfg, proxy=proxy)

        # Re-apply updated filters on existing stored rows so tightened rules immediately purge historical noise
        store.reapply_filters(cfg.filters, now_utc=eff_now_utc)

        source_health: Dict[str, Dict[str, Any]] = {}
        raw_items = (
            raw_items_override
            if raw_items_override is not None
            else collect_all_sources(cfg.sources, cfg.network, health_out=source_health)
        )

        # 1. Merge cross-source duplicates + local zero-cost pre-filter + negative noise cache BEFORE spending API quota
        existing_meta_before = store.get_existing_meta()
        neg_cache = store.get_negative_cache_items()
        merged_items = merge_raw_items(raw_items)
        pre_survivors_raw, pre_dropped = prefilter_merged_items(
            merged_items, cfg.filters, now_utc=eff_now_utc
        )
        neg_ttl_cutoff = (now_biz.date() - timedelta(days=7)).isoformat()
        pre_survivors: List[RepoItem] = []
        for r in pre_survivors_raw:
            neg_entry = neg_cache.get(r.canonical_key)
            if neg_entry is not None:
                cached_item, cached_seen, cached_ai_rejected = neg_entry
                is_new_release = bool(r.release_tag and r.release_tag != cached_item.release_tag)
                if not is_new_release:
                    if cached_ai_rejected:
                        pre_dropped += 1
                        continue
                    if cached_item.metadata_enriched:
                        hydrated = merge_raw_items([cached_item, r])[0]
                        if _needs_enrichment(r) and cached_seen and cached_seen >= neg_ttl_cutoff:
                            hydrated.metadata_enriched = True
                            still_drop, _ = is_noisy_repo(
                                hydrated, cfg.filters, now_utc=eff_now_utc, pre_enrich=False
                            )
                            if still_drop:
                                pre_dropped += 1
                                continue
                        hydrated.metadata_enriched = bool(r.metadata_enriched)
                        r = hydrated
            pre_survivors.append(r)

        survivor_keys = {r.canonical_key for r in pre_survivors}
        for r in merged_items:
            if r.canonical_key not in survivor_keys and existing_meta_before.get(r.canonical_key, {}).get("kept"):
                store.upsert_repo(r, kept=False, seen_date=snap_date)

        # 2. Enrich missing stars/language/created_at/topics via GraphQL batch or REST
        if raw_items_override is None and not skip_enrich:
            max_enrich = cfg.network.get("max_enrich")
            enrich_github_metadata(
                pre_survivors,
                max_enrich=int(max_enrich) if max_enrich is not None else None,
                proxy=proxy,
                github_token=github_token,
            )

        # 3. Full post-enrichment quality & anti-star-farm filtering (persisting kept=0 negative cache)
        kept_candidates: List[RepoItem] = []
        post_dropped = 0
        for repo in pre_survivors:
            drop, _ = is_noisy_repo(repo, cfg.filters, now_utc=eff_now_utc, pre_enrich=False)
            if drop:
                post_dropped += 1
                if not repo.discovered_at:
                    repo.discovered_at = now_iso
                store.upsert_repo(repo, kept=False, seen_date=snap_date)
            else:
                kept_candidates.append(repo)

        kept_candidates.sort(
            key=lambda r: (len(r.sources), r.stars_delta_24h, r.stars), reverse=True
        )

        existing_meta = store.get_existing_meta()
        fresh_items: List[RepoItem] = []
        release_refresh_keys: set[str] = set()
        revived_keys: set[str] = set()
        existing_updates: List[RepoItem] = []
        active_candidates: List[RepoItem] = []

        for r in kept_candidates:
            meta = existing_meta.get(r.canonical_key)
            if meta is None:
                fresh_items.append(r)
                active_candidates.append(r)
            elif r.release_tag and r.release_tag != meta["release_tag"]:
                release_refresh_keys.add(r.canonical_key)
                fresh_items.append(r)
                active_candidates.append(r)
            elif not meta["kept"]:
                if meta["ai_rejected"]:
                    post_dropped += 1
                    continue
                revived_keys.add(r.canonical_key)
                fresh_items.append(r)
                active_candidates.append(r)
            else:
                existing_updates.append(r)
                active_candidates.append(r)

        kept_candidates = active_candidates
        filter_stats = {
            "raw_count": len(raw_items),
            "unique_count": len(merged_items),
            "dropped_count": pre_dropped + post_dropped,
        }

        # 4. Compute star deltas against previous snapshots, record today's snapshot, and decay stale deltas
        for item in kept_candidates:
            d24, d7 = store.compute_deltas(item.canonical_key, snap_date, item.stars)
            item.stars_delta_24h = max(item.stars_delta_24h, d24)
            item.stars_delta_7d = max(item.stars_delta_24h, d7)
            store.record_snapshot(item.canonical_key, snap_date, item.stars)
        store.decay_stale_deltas(snap_date)

        # 5. Translate descriptions concurrently for fresh / new-release items
        if cfg.translation.get("enabled", True) and fresh_items:
            def _do_translate(it: RepoItem) -> str:
                if translate_fn is translate_free_zh:
                    return translate_free_zh(it.description, proxy=proxy)
                return translate_fn(it.description)

            max_workers = min(6, len(fresh_items))
            if max_workers >= 2:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    translated_list = list(pool.map(_do_translate, fresh_items))
                for item, zh in zip(fresh_items, translated_list):
                    item.discovered_at = now_iso
                    item.desc_cn = zh
            else:
                for item in fresh_items:
                    item.discovered_at = now_iso
                    item.desc_cn = _do_translate(item)
        else:
            for item in fresh_items:
                item.discovered_at = now_iso
                item.desc_cn = item.description

        # 6. Persist metadata updates for already-known items first
        for item in existing_updates:
            store.upsert_repo(item, kept=True, seen_date=snap_date)

        # Preserve same-day LLM trend summary when re-running with 0 fresh items (refreshing fallback summaries)
        prev_trend_summary = ""
        status_file = data_dir / "status.json"
        if status_file.exists():
            try:
                old_st = json.loads(status_file.read_text(encoding="utf-8"))
                if old_st.get("last_run_date") == snap_date:
                    cand_summary = str(old_st.get("trend_summary") or "").strip()
                    if cand_summary and not _is_fallback_trend_summary(cand_summary):
                        prev_trend_summary = cand_summary
            except Exception:
                pass

        # 7. Chunked AI highlights + historical backfill for stored items missing highlights
        ai_results: Dict[str, Dict[str, Any]] = {}
        trend_summary_box: List[str] = []
        backfill_items: List[RepoItem] = []

        if enable_ai and cfg.ai_highlight.get("enabled", True):
            backfill_limit = max(0, int(cfg.ai_highlight.get("backfill_limit") or 40))
            if backfill_limit > 0:
                fresh_key_set = {x.canonical_key for x in fresh_items}
                backfill_items = [
                    b
                    for b in store.load_unhighlighted_kept(limit=backfill_limit)
                    if b.canonical_key not in fresh_key_set
                ]
            ai_queue = [*fresh_items, *backfill_items]
            want_new_summary = bool(fresh_items) or not prev_trend_summary
            if ai_queue:
                ai_results = generate_ai_highlights(
                    ai_queue,
                    cfg.ai_highlight,
                    runner=ai_runner,
                    proxy=proxy,
                    github_token=github_token,
                    trend_summary_out=trend_summary_box if want_new_summary else None,
                )

        kept_new_items: List[RepoItem] = []
        fresh_ai_dropped = 0
        for item in fresh_items:
            ai_info = ai_results.get(item.canonical_key)
            force_refresh = (
                item.canonical_key in release_refresh_keys or item.canonical_key in revived_keys
            )
            if ai_info:
                if not ai_info.get("keep", True):
                    item.ai_highlight = str(
                        ai_info.get("highlight") or "AI 判定为低质或套壳噪音仓库"
                    )
                    item.category_tag = str(ai_info.get("category_tag") or "")
                    store.upsert_repo(item, kept=False, seen_date=snap_date, ai_rejected=True)
                    fresh_ai_dropped += 1
                    continue
                item.ai_highlight = ai_info.get("highlight", "")
                item.category_tag = ai_info.get("category_tag", "")
            if not item.category_tag:
                item.category_tag = classify_repo_category(item)
            store.upsert_repo(
                item,
                kept=True,
                seen_date=snap_date,
                force_refresh_discovered=force_refresh,
            )
            kept_new_items.append(item)

        backfill_ai_dropped = 0
        for bf_item in backfill_items:
            ai_info = ai_results.get(bf_item.canonical_key)
            if ai_info:
                if store.apply_ai_backfill(bf_item.canonical_key, ai_info):
                    backfill_ai_dropped += 1

        store.backfill_missing_categories()

        max_feed = int(cfg.output.get("max_feed_items") or 120)
        run_kept_count = max(0, len(kept_candidates) - fresh_ai_dropped)
        run_dropped_count = filter_stats["dropped_count"] + fresh_ai_dropped
        status_payload: Dict[str, Any] = {
            "timezone": str(
                os.environ.get("RADAR_TIMEZONE", "").strip()
                or cfg.output.get("timezone")
                or "Asia/Shanghai"
            ),
            "last_run_at": now_iso,
            "last_run_date": snap_date,
            "raw_count": filter_stats["raw_count"],
            "unique_count": filter_stats["unique_count"],
            "run_kept_count": run_kept_count,
            "dropped_count": run_dropped_count,
            "backfill_dropped_count": backfill_ai_dropped,
            "new_count": len(kept_new_items),
            "feed_total": 0,
            "max_category_share": float(cfg.output.get("max_category_share") or 0.0),
            "trend_summary": trend_summary_box[0] if trend_summary_box else prev_trend_summary,
            "source_health": source_health,
        }
        feed_total = _write_all_outputs(
            store,
            cfg,
            feeds_dir,
            status_payload,
            max_feed=max_feed,
            base_dir=base_dir,
            now_utc=eff_now_utc,
            biz_tz=biz_tz,
        )
        status_payload["feed_total"] = feed_total
        status_file.write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if enable_notify and (cfg.notifications.get("enabled", False) or notify_dry_run):
            only_new = bool(cfg.notifications.get("only_new", True))
            notify_pool = kept_new_items if only_new else store.load_recent_kept(limit=max_feed)
            if not notify_pool and notify_dry_run:
                notify_pool = store.load_recent_kept(limit=max_feed)
            send_notifications(
                notify_pool,
                status_payload,
                cfg.notifications,
                dry_run=notify_dry_run,
                proxy=proxy,
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
    base_dir: Optional[Path] = None,
    now_utc: Optional[datetime] = None,
    biz_tz: Optional[tzinfo] = None,
) -> int:
    now = now_utc or datetime.now(timezone.utc)
    eff_tz = biz_tz or resolve_timezone(cfg.output)
    now_biz = now.astimezone(eff_tz)
    max_cat_share = float(cfg.output.get("max_category_share") or 0.0)
    raw_recent = store.load_recent_kept(limit=max_feed)
    recent_kept = _apply_quota_by_date_groups(raw_recent, max_cat_share)

    web_max = max(max_feed, int(cfg.output.get("max_web_items") or 300))
    raw_web_items = store.load_recent_kept(limit=web_max)
    web_items = _apply_quota_by_date_groups(raw_web_items, max_cat_share)

    if not status_payload.get("trend_summary"):
        status_payload["trend_summary"] = build_fallback_trend_summary(
            web_items, new_count=int(status_payload.get("new_count") or 0)
        )

    site_base_url, feeds_base_url = resolve_site_and_feeds_urls(cfg.output)

    write_rss_feed(
        title="GitHub 每日趣闻精选（全源聚合）",
        description="全语言聚合 · 黑名单与反刷星去噪 · 跨源去重 · 中文翻译与一句话亮点",
        link=f"{feeds_base_url}/all_discoveries.xml",
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
        link=f"{feeds_base_url}/github_trending.xml",
        items=trending_items,
        out_path=feeds_dir / "github_trending.xml",
    )
    write_rss_feed(
        title="GitHub 定向雷达 & 开发者关注",
        description="低星黑马 / 创作者动态 / Release 追踪精选",
        link=f"{feeds_base_url}/github_radar.xml",
        items=radar_items,
        out_path=feeds_dir / "github_radar.xml",
    )
    write_rss_feed(
        title="开源社区发现（Show HN / HelloGitHub / HuggingFace / Reddit）",
        description="跨社区精选开源项目",
        link=f"{feeds_base_url}/community_picks.xml",
        items=community_items,
        out_path=feeds_dir / "community_picks.xml",
    )

    write_json_feed(
        title="GitHub Radar · 每日开源精选画刊",
        description="全语言聚合 · 反刷星去噪 · 跨源共振 · 中文一句话导读",
        home_page_url=site_base_url,
        feed_url=f"{feeds_base_url}/feed.json",
        items=recent_kept,
        out_path=feeds_dir / "feed.json",
        now_utc=now,
    )

    write_opml(
        site_base_url=site_base_url,
        feeds_base_url=feeds_base_url,
        out_path=feeds_dir / "feeds.opml",
    )

    status_for_web = dict(status_payload)
    status_for_web["feed_total"] = len(recent_kept)
    status_for_web["max_category_share"] = max_cat_share

    sparklines_map = store.load_sparklines([it.canonical_key for it in web_items], days=7)
    write_web_json(
        items=web_items,
        status=status_for_web,
        out_path=feeds_dir / "data.json",
        sparklines_map=sparklines_map,
        now_utc=now,
        biz_tz=eff_tz,
    )

    llms_path = feeds_dir / "llms.txt"
    write_llms_txt(
        site_base_url=site_base_url,
        feeds_base_url=feeds_base_url,
        items=web_items,
        status=status_for_web,
        out_path=llms_path,
        now_utc=now,
        biz_tz=eff_tz,
    )
    if base_dir is not None and (base_dir / "web").is_dir():
        (base_dir / "web" / "llms.txt").write_text(
            llms_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    store.export_state_gzip(feeds_dir / "state_backup.json.gz", now_biz=now_biz)
    return len(recent_kept)


def export_only(cfg: RadarConfig, base_dir: Path) -> Dict[str, Any]:
    """Re-export static JSON, JSON Feed, OPML, llms.txt, and RSS XML directly from existing radar.db."""
    data_dir = base_dir / "data"
    feeds_dir = base_dir / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    biz_tz = resolve_timezone(cfg.output)
    now_utc = datetime.now(timezone.utc)
    now_biz = now_utc.astimezone(biz_tz)
    status_path = data_dir / "status.json"
    status_payload: Dict[str, Any] = {}
    if status_path.exists():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status_payload = {}
    if not status_payload:
        status_payload = {
            "timezone": str(cfg.output.get("timezone") or "Asia/Shanghai"),
            "last_run_at": now_biz.isoformat(timespec="seconds"),
            "last_run_date": now_biz.strftime("%Y-%m-%d"),
            "new_count": 0,
            "feed_total": 0,
        }
    else:
        sh = status_payload.get("source_health")
        reddit_cfg = cfg.sources.get("reddit_rss") or {}
        extra_urls = cfg.sources.get("extra_rss_urls") or []
        if (
            isinstance(sh, dict)
            and "reddit_rss" in sh
            and not reddit_cfg.get("enabled", False)
            and not extra_urls
            and not (sh["reddit_rss"] or {}).get("ok", True)
        ):
            sh.pop("reddit_rss", None)
        if _is_fallback_trend_summary(status_payload.get("trend_summary", "")):
            status_payload["trend_summary"] = ""

    store = RadarStore(data_dir / "radar.db")
    try:
        store.reapply_filters(cfg.filters, now_utc=now_utc)
        store.backfill_missing_categories()
        max_feed = int(cfg.output.get("max_feed_items") or 120)
        feed_total = _write_all_outputs(
            store,
            cfg,
            feeds_dir,
            status_payload,
            max_feed=max_feed,
            base_dir=base_dir,
            now_utc=now_utc,
            biz_tz=biz_tz,
        )
        status_payload["feed_total"] = feed_total
        status_path.write_text(
            json.dumps(status_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return status_payload
    finally:
        store.close()
