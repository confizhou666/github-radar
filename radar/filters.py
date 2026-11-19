from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import re
from typing import Any, Dict, List, Optional, Pattern, Tuple

from .models import RepoItem

CURATED_NON_API_PREFIXES = (
    "Show HN",
    "HuggingFace Papers",
    "HelloGitHub",
    "GitHub Releases",
    "Reddit:",
    "自定义RSS",
)

# Suspicious pirated-installer / game-cheat / fake-build shell phrases often used by star-farm clusters
SHELL_BAIT_RE = re.compile(
    r"(?<![a-z0-9])("
    r"standalone\s+(?:desktop\s+)?(?:setup|build|installer)|"
    r"desktop\s+(?:setup|build|installer)|"
    r"full[- ]feature[d]?\s+(?:security\s+)?installer|"
    r"all\s+features\s+enabled|"
    r"all\s+tools\s+pre-installed|"
    r"pre-configured\s+components|"
    r"free[- ]soft|"
    r"cheat|trainer|spoofer|activator"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)

INJECTOR_INSTALLER_RE = re.compile(
    r"(?<![a-z0-9])(installer|injector|loader)(?![a-z0-9])",
    re.IGNORECASE,
)

PROFILE_README_RE = re.compile(
    r"(profile\s+readme|github\s+profile|personal\s+readme|config\s+files\s+for\s+my)",
    re.IGNORECASE,
)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@lru_cache(maxsize=512)
def _compile_keyword_regex(kw_lower: str) -> Optional[Pattern[str]]:
    if not kw_lower or CJK_RE.search(kw_lower):
        return None
    escaped = re.escape(kw_lower)
    prefix = r"(?<![a-z0-9])" if kw_lower[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if kw_lower[-1].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}", re.IGNORECASE)


def _keyword_matches(kw: str, haystack: str) -> bool:
    k = (kw or "").strip().lower()
    if not k:
        return False
    pat = _compile_keyword_regex(k)
    if pat is None:
        return k in haystack
    return pat.search(haystack) is not None


def _is_curated_unverified_language_exempt(item: RepoItem) -> bool:
    """Exempt curated non-GitHub-API community sources when API enrichment quota ran out."""
    if item.metadata_enriched:
        return False
    if not item.sources:
        return False
    return all(
        any(src == p or src.startswith(p) for p in CURATED_NON_API_PREFIXES)
        for src in item.sources
    )


def compute_age_days(created_at: str, now_utc: Optional[datetime] = None) -> Optional[float]:
    if not created_at:
        return None
    now = now_utc or datetime.now(timezone.utc)
    try:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now - created_dt).total_seconds() / 86400.0)
    except ValueError:
        return None


def compute_breakout_score(item: RepoItem, now_utc: Optional[datetime] = None) -> float:
    """Compute velocity score in stars/day: stars / max(age_days, 1) when created_at is known."""
    age_days = compute_age_days(item.created_at, now_utc=now_utc)
    if age_days is not None:
        return round(float(max(0, item.stars)) / max(age_days, 1.0), 2)
    if item.stars_delta_24h > 0:
        return round(float(item.stars_delta_24h), 2)
    return 0.0


def is_noisy_repo(
    item: RepoItem,
    filters_cfg: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
    pre_enrich: bool = False,
) -> Tuple[bool, str]:
    """Return (should_drop, reason) using whitelist, blacklists, and structural quality checks."""
    repo_name = (item.repo_name or "").strip()
    if "/" not in repo_name:
        return True, "invalid_repo_name"
    owner, repo = repo_name.split("/", 1)
    canonical = repo_name.lower()

    whitelist = {
        str(w or "").strip().lower()
        for w in (filters_cfg.get("whitelist_repos") or [])
        if str(w or "").strip()
    }
    if canonical in whitelist:
        return False, "whitelisted"

    blacklist_owners = {
        str(o or "").strip().lower()
        for o in (filters_cfg.get("blacklist_owners") or [])
        if str(o or "").strip()
    }
    if owner.lower() in blacklist_owners:
        return True, f"blacklist_owner:{owner.lower()}"

    is_release = "GitHub Releases" in (item.sources or []) or bool(item.release_tag)

    # 1. Profile README repo (owner == repo), sparing legitimate org flagship repos (e.g. ollama/ollama, duckdb/duckdb)
    if filters_cfg.get("drop_profile_readmes", True) and owner.lower() == repo.lower() and not is_release:
        lang_up = str(item.language or "").strip().upper()
        forks_n = int(item.forks or 0)
        stars_n = int(item.stars or 0)
        if PROFILE_README_RE.search(item.description or ""):
            return True, "profile_readme"
        if not pre_enrich or item.metadata_enriched:
            is_code_flagship = (
                bool(lang_up)
                and lang_up not in {"HTML", "MARKDOWN", "CSS"}
                and (forks_n >= 10 or stars_n >= 150)
            )
            if not is_code_flagship:
                return True, "profile_readme"
    if repo.lower() == "awesome":
        return True, "blacklist:awesome"

    # 2. Pure markdown / non-code repo when language metadata was resolved or explicitly missing
    if not pre_enrich and filters_cfg.get("require_programming_language", True):
        if item.language is None or not str(item.language).strip():
            if not _is_curated_unverified_language_exempt(item):
                return True, "no_programming_language"

    # 3. Minimum stars threshold (deferred during pre_enrich when stars == 0 before API lookup)
    min_stars = int(filters_cfg.get("min_stars") or 0)
    if min_stars > 0 and not (pre_enrich and item.stars == 0):
        if item.stars < min_stars:
            return True, "below_min_stars"

    # 4. Minimum meaningful description (deferred during pre_enrich if empty before API lookup)
    min_desc = int(filters_cfg.get("min_desc_chars") or 0)
    desc_clean = (item.description or "").strip()
    if min_desc > 0 and not (pre_enrich and not desc_clean):
        if len(desc_clean) < min_desc:
            return True, "empty_description"

    # 5. Mandatory blacklist keywords against repo name + description + topics (word-boundary safe)
    topics_str = " ".join(item.topics or [])
    haystack = f"{repo_name} {item.description} {topics_str}".lower()
    for kw in filters_cfg.get("blacklist_keywords") or []:
        if _keyword_matches(str(kw or ""), haystack):
            return True, f"blacklist:{str(kw).strip().lower()}"

    now = now_utc or datetime.now(timezone.utc)
    age_days = compute_age_days(item.created_at, now_utc=now)

    # 6. Ancient zombie repo check (> max_repo_age_years), exempting GitHub Releases
    max_years = int(filters_cfg.get("max_repo_age_years") or 0)
    if max_years > 0 and not is_release and age_days is not None:
        if age_days > max_years * 365:
            return True, "ancient_repo"

    # 7. Upgraded anti-star-farm & poisoned malware shell detection
    if filters_cfg.get("anti_star_farm", True):
        stars = int(item.stars or 0)
        forks = int(item.forks or 0)
        issues = int(item.open_issues or 0)
        lang_upper = str(item.language or "").strip().upper()
        fork_ratio = forks / max(stars, 1)
        is_young = age_days is None or age_days <= 30

        # 7a. High-confidence bot star-cluster signature (>= 250 stars in <= 30d with <= 1 fork / <0.6% fork rate)
        if stars >= 250 and (forks <= 1 or fork_ratio < 0.006) and is_young:
            return True, "star_farm_anomaly"

        # 7b. Medium-star (>= 100) extreme fork deficit combined with HTML shell, zero engagement, or installer bait
        if stars >= 100 and (forks <= 1 or fork_ratio < 0.01):
            if lang_upper == "HTML" and (age_days is None or age_days <= 45):
                return True, "star_farm_html_shell"
            if forks == 0 and issues == 0 and (age_days is None or age_days <= 21):
                return True, "star_farm_anomaly"
            if SHELL_BAIT_RE.search(haystack) or INJECTOR_INSTALLER_RE.search(haystack):
                return True, "star_farm_installer_shell"

        # 7c. Explicit pirated desktop setup / cheat shell bait in HTML or low-fork repos even below 100 stars
        if SHELL_BAIT_RE.search(haystack) and (lang_upper == "HTML" or (stars >= 30 and fork_ratio < 0.02)):
            return True, "star_farm_bait_shell"
        if (
            INJECTOR_INSTALLER_RE.search(haystack)
            and lang_upper == "HTML"
            and "dependency" not in haystack
            and (age_days is None or age_days <= 30)
        ):
            return True, "star_farm_html_installer"

    return False, "ok"


def merge_raw_items(raw_items: List[RepoItem]) -> List[RepoItem]:
    """Merge cross-source duplicates by canonical owner/repo key."""
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
                stars_delta_24h=item.stars_delta_24h,
                stars_delta_7d=item.stars_delta_7d,
                topics=list(dict.fromkeys(item.topics))[:8],
                license_name=item.license_name,
                release_tag=item.release_tag,
                community_score=item.community_score,
                metadata_enriched=item.metadata_enriched,
            )
        else:
            cur = merged_map[key]
            for s in item.sources:
                if s not in cur.sources:
                    cur.sources.append(s)
            if (
                len(item.description or "") > len(cur.description or "")
                and "Sponsor Star " not in (item.description or "")
            ):
                cur.description = item.description
            elif (
                "Sponsor Star " in (cur.description or "")
                and item.description
                and "Sponsor Star " not in item.description
            ):
                cur.description = item.description
            if not cur.language and item.language:
                cur.language = item.language
            cur.stars = max(cur.stars, item.stars)
            cur.forks = max(cur.forks, item.forks)
            cur.open_issues = max(cur.open_issues, item.open_issues)
            cur.stars_delta_24h = max(cur.stars_delta_24h, item.stars_delta_24h)
            cur.stars_delta_7d = max(cur.stars_delta_7d, item.stars_delta_7d)
            cur.community_score = max(cur.community_score, item.community_score)
            cur.metadata_enriched = cur.metadata_enriched or item.metadata_enriched
            if not cur.created_at and item.created_at:
                cur.created_at = item.created_at
            if item.pushed_at and item.pushed_at > (cur.pushed_at or ""):
                cur.pushed_at = item.pushed_at
            if not cur.discussion_url and item.discussion_url:
                cur.discussion_url = item.discussion_url
            if not cur.license_name and item.license_name:
                cur.license_name = item.license_name
            if item.release_tag:
                cur.release_tag = item.release_tag
            if item.topics:
                merged_topics = list(dict.fromkeys([*cur.topics, *item.topics]))
                cur.topics = merged_topics[:8]
    return list(merged_map.values())


def prefilter_merged_items(
    merged_items: List[RepoItem],
    filters_cfg: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
) -> Tuple[List[RepoItem], int]:
    """Run local zero-cost blacklist/structural checks prior to GitHub API enrichment."""
    survivors: List[RepoItem] = []
    dropped = 0
    for repo in merged_items:
        drop, _ = is_noisy_repo(repo, filters_cfg, now_utc=now_utc, pre_enrich=True)
        if drop:
            dropped += 1
        else:
            survivors.append(repo)
    return survivors, dropped


def deduplicate_and_filter(
    raw_items: List[RepoItem],
    filters_cfg: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
) -> Tuple[List[RepoItem], Dict[str, int]]:
    """Merge cross-source duplicates first, then drop noisy repos."""
    merged_list = merge_raw_items(raw_items)
    kept: List[RepoItem] = []
    stats = {"raw_count": len(raw_items), "unique_count": len(merged_list), "dropped_count": 0}
    for repo in merged_list:
        drop, _ = is_noisy_repo(repo, filters_cfg, now_utc=now_utc, pre_enrich=False)
        if drop:
            stats["dropped_count"] += 1
        else:
            kept.append(repo)

    kept.sort(key=lambda r: (len(r.sources), r.stars_delta_24h, r.stars), reverse=True)
    return kept, stats
