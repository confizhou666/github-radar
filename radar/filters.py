from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import RepoItem


def is_noisy_repo(item: RepoItem, filters_cfg: Dict[str, Any], *, now_utc: Optional[datetime] = None) -> Tuple[bool, str]:
    """Return (should_drop, reason) using mandatory keyword blacklists + structural quality checks."""
    repo_name = (item.repo_name or "").strip()
    if "/" not in repo_name:
        return True, "invalid_repo_name"
    owner, repo = repo_name.split("/", 1)

    # 1. Profile README repo (owner == repo)
    if filters_cfg.get("drop_profile_readmes", True) and owner.lower() == repo.lower():
        return True, "profile_readme"

    # 2. Pure markdown / non-code repo when language metadata was resolved or explicitly missing
    if filters_cfg.get("require_programming_language", True):
        if item.language is None or not str(item.language).strip():
            # Allow HelloGitHub/ShowHN only if enrichment failed AND description has substance?
            # Strict rule requested by user: drop language == null to block 70%+ link lists & essays.
            return True, "no_programming_language"

    # 3. Minimum meaningful description
    min_desc = int(filters_cfg.get("min_desc_chars") or 0)
    if min_desc > 0 and len((item.description or "").strip()) < min_desc:
        return True, "empty_description"

    # 4. Mandatory blacklist keywords against repo name + description
    haystack = f"{repo_name} {item.description}".lower()
    for kw in filters_cfg.get("blacklist_keywords") or []:
        k = str(kw or "").strip().lower()
        if k and k in haystack:
            return True, f"blacklist:{k}"

    now = now_utc or datetime.now(timezone.utc)
    # 5. Ancient zombie repo check (> max_repo_age_years)
    max_years = int(filters_cfg.get("max_repo_age_years") or 0)
    age_days: Optional[float] = None
    if item.created_at:
        try:
            created_dt = datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))
            age_days = (now - created_dt).total_seconds() / 86400.0
            if max_years > 0 and age_days > max_years * 365:
                return True, "ancient_repo"
        except ValueError:
            pass

    # 6. Sudden artificial star-farm spam check
    if filters_cfg.get("anti_star_farm", True):
        if item.stars >= 500 and item.forks == 0 and item.open_issues == 0:
            if age_days is None or age_days <= 14:
                return True, "star_farm_anomaly"

    return False, "ok"


def deduplicate_and_filter(
    raw_items: List[RepoItem],
    filters_cfg: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
) -> Tuple[List[RepoItem], Dict[str, int]]:
    """Merge cross-source duplicates first, then drop noisy repos."""
    merged_map: Dict[str, RepoItem] = {}
    for item in raw_items:
        key = item.canonical_key
        if not key or "/" not in key:
            continue
        if key not in merged_map:
            merged_map[key] = RepoItem(
                repo_name=item.repo_name,
                url=item.url,
                description=item.description,
                language=item.language,
                stars=item.stars,
                forks=item.forks,
                open_issues=item.open_issues,
                created_at=item.created_at,
                pushed_at=item.pushed_at,
                sources=list(dict.fromkeys(item.sources)),
                discussion_url=item.discussion_url,
            )
        else:
            cur = merged_map[key]
            for s in item.sources:
                if s not in cur.sources:
                    cur.sources.append(s)
            if len(item.description or "") > len(cur.description or ""):
                cur.description = item.description
            if not cur.language and item.language:
                cur.language = item.language
            cur.stars = max(cur.stars, item.stars)
            cur.forks = max(cur.forks, item.forks)
            cur.open_issues = max(cur.open_issues, item.open_issues)
            if not cur.created_at and item.created_at:
                cur.created_at = item.created_at
            if not cur.pushed_at and item.pushed_at:
                cur.pushed_at = item.pushed_at
            if not cur.discussion_url and item.discussion_url:
                cur.discussion_url = item.discussion_url

    kept: List[RepoItem] = []
    stats = {"raw_count": len(raw_items), "unique_count": len(merged_map), "dropped_count": 0}
    for repo in merged_map.values():
        drop, _ = is_noisy_repo(repo, filters_cfg, now_utc=now_utc)
        if drop:
            stats["dropped_count"] += 1
        else:
            kept.append(repo)

    # Sort multi-source discoveries & high stars first
    kept.sort(key=lambda r: (len(r.sources), r.stars), reverse=True)
    return kept, stats
