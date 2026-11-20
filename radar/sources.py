from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import html
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .models import RepoItem

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
GITHUB_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[/?#\"'\s<>)\],;:!]|$)"
)
SAFE_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RESERVED_GITHUB_OWNERS = {
    "about",
    "apps",
    "blog",
    "codespaces",
    "collections",
    "copilot",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "issues",
    "login",
    "marketplace",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "readme",
    "search",
    "security",
    "settings",
    "signup",
    "sponsors",
    "topics",
    "trending",
    "users",
}
TRENDING_ARTICLE_RE = re.compile(
    r"<article\b[^>]*class=\"[^\"]*Box-row[^\"]*\"[^>]*>(.*?)</article>",
    re.DOTALL | re.IGNORECASE,
)


def sanitize_http_url(url: str) -> str:
    """Allow only http:// or https:// URLs to prevent javascript:/data: injection from external feeds."""
    s = (url or "").strip()
    if not s:
        return ""
    try:
        parsed = urllib.parse.urlsplit(s)
    except Exception:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return s


def resolve_github_token(network_cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = network_cfg or {}
    env_name = str(cfg.get("github_token_env") or "GITHUB_TOKEN").strip()
    return str(cfg.get("github_token") or os.environ.get(env_name, "") or "").strip()


def resolve_proxy(network_cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = network_cfg or {}
    return str(cfg.get("proxy") or os.environ.get("RADAR_PROXY", "") or "").strip()


def _open_url(req: urllib.request.Request, *, timeout: int = 12, proxy: str = ""):
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _http_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 12,
    proxy: str = "",
    github_token: str = "",
) -> Tuple[int, str, Dict[str, str]]:
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    if github_token and "api.github.com" in url and "Authorization" not in req_headers:
        req_headers["Authorization"] = f"Bearer {github_token}"

    req = urllib.request.Request(url, headers=req_headers)
    with _open_url(req, timeout=timeout, proxy=proxy) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        resp_hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return int(resp.status), raw, resp_hdrs


def _http_post_json(
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    proxy: str = "",
    github_token: str = "",
) -> Tuple[int, str, Dict[str, str]]:
    req_headers = {"User-Agent": UA, "Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if github_token and "Authorization" not in req_headers:
        req_headers["Authorization"] = f"Bearer {github_token}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    with _open_url(req, timeout=timeout, proxy=proxy) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        resp_hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return int(resp.status), raw, resp_hdrs


def _clean_html_text(fragment: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", fragment)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _parse_int_loose(text: str) -> int:
    s = (text or "").strip().lower().replace(",", "")
    if not s:
        return 0
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except ValueError:
        return 0


def parse_github_trending_html(
    html_text: str,
    *,
    source_label: str = "GitHub Trending",
    since: str = "daily",
) -> List[RepoItem]:
    items: List[RepoItem] = []
    for match in TRENDING_ARTICLE_RE.finditer(html_text or ""):
        block = match.group(1)
        m_href = re.search(
            r"<h2[^>]*>.*?<a[^>]*href=\"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\"",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not m_href:
            continue
        repo_name = m_href.group(1).strip("/")

        # Match <p ...> strictly with word boundary so <path ...> SVGs never match <p[^>]*>.
        m_desc = re.search(
            r"<p\b[^>]*class=\"[^\"]*col-9[^\"]*\"[^>]*>(.*?)</p>",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not m_desc:
            m_desc = re.search(r"<p\b[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)
        desc = _clean_html_text(m_desc.group(1)) if m_desc else ""

        m_lang = re.search(
            r'itemprop="programmingLanguage"[^>]*>(.*?)</span>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        lang = _clean_html_text(m_lang.group(1)) if m_lang else None
        m_stars = re.search(
            r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        stars = _parse_int_loose(_clean_html_text(m_stars.group(1))) if m_stars else 0
        m_forks = re.search(
            r'href="/[^"]+/forks"[^>]*>(.*?)</a>',
            block,
            re.DOTALL | re.IGNORECASE,
        )
        forks = _parse_int_loose(_clean_html_text(m_forks.group(1))) if m_forks else 0

        delta_24h = 0
        m_today = re.search(r"([\d,]+)\s+stars\s+today", block, re.IGNORECASE)
        if m_today:
            delta_24h = _parse_int_loose(m_today.group(1))
        elif since == "daily":
            m_period = re.search(r"([\d,]+)\s+stars\s+this", block, re.IGNORECASE)
            if m_period:
                delta_24h = _parse_int_loose(m_period.group(1))

        items.append(
            RepoItem(
                repo_name=repo_name,
                url=f"https://github.com/{repo_name}",
                description=desc,
                language=lang or None,
                stars=stars,
                forks=forks,
                sources=[source_label],
                stars_delta_24h=delta_24h,
            )
        )
    return items


def extract_github_repo_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = GITHUB_REPO_RE.search(url)
    if not m:
        return None
    full = m.group(1).rstrip("/.")
    if full.lower().endswith(".git"):
        full = full[:-4].rstrip("/.")
    parts = full.split("/")
    if len(parts) != 2:
        return None
    owner, repo = parts[0], parts[1].rstrip(".")
    if not owner or not repo or owner in {".", ".."} or repo in {".", ".."}:
        return None
    if owner.lower() in RESERVED_GITHUB_OWNERS:
        return None
    return f"{owner}/{repo}"


def _repo_from_gh_api(raw: Dict[str, Any], source_label: str) -> Optional[RepoItem]:
    full_name = str(raw.get("full_name") or "").strip()
    if not full_name or "/" not in full_name:
        return None
    lic = raw.get("license")
    license_name = ""
    if isinstance(lic, dict):
        spdx = str(lic.get("spdx_id") or "").strip()
        if spdx and spdx.upper() != "NOASSERTION":
            license_name = spdx
    topics_raw = raw.get("topics") or []
    topics = [str(t).strip() for t in topics_raw if str(t).strip()][:8]
    raw_url = sanitize_http_url(str(raw.get("html_url") or "")) or f"https://github.com/{full_name}"
    return RepoItem(
        repo_name=full_name,
        url=raw_url,
        description=str(raw.get("description") or "").strip(),
        language=raw.get("language") or None,
        stars=int(raw.get("stargazers_count") or 0),
        forks=int(raw.get("forks_count") or 0),
        open_issues=int(raw.get("open_issues_count") or 0),
        created_at=str(raw.get("created_at") or ""),
        pushed_at=str(raw.get("pushed_at") or ""),
        sources=[source_label],
        topics=topics,
        license_name=license_name,
        metadata_enriched=True,
    )


def fetch_github_trending(
    cfg: Dict[str, Any], *, proxy: str = "", errors_out: Optional[List[str]] = None
) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    since = str(cfg.get("since") or "daily")
    max_items = int(cfg.get("max_items") or 30)
    langs = cfg.get("languages") or [""]
    out: List[RepoItem] = []
    for lang in langs:
        lang_slug = urllib.parse.quote(str(lang or "").strip())
        path = (
            f"https://github.com/trending/{lang_slug}?since={since}"
            if lang_slug
            else f"https://github.com/trending?since={since}"
        )
        label = f"GitHub Trending({lang})" if lang_slug else "GitHub Trending"
        try:
            _, html_text, _ = _http_get(path, proxy=proxy)
            parsed = parse_github_trending_html(html_text, source_label=label, since=since)
            out.extend(parsed[:max_items])
        except Exception as exc:
            logger.warning("抓取 %s 失败: %s", label, exc)
            if errors_out is not None:
                errors_out.append(f"{label}: {exc}")
    return out


def fetch_github_search(
    cfg: Dict[str, Any],
    *,
    proxy: str = "",
    github_token: str = "",
    errors_out: Optional[List[str]] = None,
) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    now_utc = datetime.now(timezone.utc)
    out: List[RepoItem] = []
    for qcfg in cfg.get("queries") or []:
        if not isinstance(qcfg, dict):
            continue
        name = str(qcfg.get("name") or "GitHub Search")
        days_created = int(qcfg.get("days_created") or 21)
        days_pushed = int(qcfg.get("days_pushed") or 7)
        min_stars = int(qcfg.get("min_stars") or 30)
        max_stars = int(qcfg.get("max_stars") or 5000)
        per_page = min(50, int(qcfg.get("per_page") or 25))
        d_created = (now_utc - timedelta(days=days_created)).strftime("%Y-%m-%d")
        d_pushed = (now_utc - timedelta(days=days_pushed)).strftime("%Y-%m-%d")
        query = f"stars:{min_stars}..{max_stars} created:>={d_created} pushed:>={d_pushed}"
        extra = str(qcfg.get("extra_query") or "").strip()
        if extra:
            query = f"{query} {extra}"
        url = (
            "https://api.github.com/search/repositories?q="
            + urllib.parse.quote(query)
            + f"&sort=stars&order=desc&per_page={per_page}"
        )
        try:
            _, raw, _ = _http_get(
                url,
                headers={"Accept": "application/vnd.github+json"},
                proxy=proxy,
                github_token=github_token,
            )
            js = json.loads(raw)
            for item in js.get("items") or []:
                repo = _repo_from_gh_api(item, f"雷达:{name}")
                if repo:
                    out.append(repo)
        except Exception as exc:
            logger.warning("抓取 GitHub Search(%s) 失败: %s", name, exc)
            if errors_out is not None:
                errors_out.append(f"{name}: {exc}")
    return out


def fetch_starred_developers(
    users: List[str],
    *,
    proxy: str = "",
    github_token: str = "",
    errors_out: Optional[List[str]] = None,
) -> List[RepoItem]:
    out: List[RepoItem] = []
    for u in users or []:
        uname = str(u).strip()
        if not uname:
            continue
        url = f"https://api.github.com/users/{urllib.parse.quote(uname)}/starred?per_page=15"
        try:
            _, raw, _ = _http_get(
                url,
                headers={"Accept": "application/vnd.github+json"},
                proxy=proxy,
                github_token=github_token,
            )
            items = json.loads(raw)
            if isinstance(items, list):
                for item in items:
                    repo = _repo_from_gh_api(item, f"关注开发者:{uname}")
                    if repo:
                        out.append(repo)
        except Exception as exc:
            logger.warning("抓取开发者 %s Star 动态失败: %s", uname, exc)
            if errors_out is not None:
                errors_out.append(f"{uname}: {exc}")
    return out


def fetch_hackernews_show(
    cfg: Dict[str, Any], *, proxy: str = "", errors_out: Optional[List[str]] = None
) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    limit = min(50, int(cfg.get("limit") or 30))
    url = f"https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage={limit}"
    out: List[RepoItem] = []
    try:
        _, raw, _ = _http_get(url, proxy=proxy)
        hits = (json.loads(raw) or {}).get("hits") or []
        for h in hits:
            if not isinstance(h, dict):
                continue
            target_url = str(h.get("url") or "")
            repo_name = extract_github_repo_from_url(target_url)
            if not repo_name:
                repo_name = extract_github_repo_from_url(str(h.get("story_text") or ""))
            if not repo_name:
                continue
            title = re.sub(r"^Show HN:\s*", "", str(h.get("title") or ""), flags=re.IGNORECASE).strip()
            oid = str(h.get("objectID") or "")
            points = int(h.get("points") or 0)
            out.append(
                RepoItem(
                    repo_name=repo_name,
                    url=f"https://github.com/{repo_name}",
                    description=title,
                    stars=0,
                    community_score=points,
                    sources=["Show HN"],
                    discussion_url=f"https://news.ycombinator.com/item?id={oid}" if oid else "",
                )
            )
    except Exception as exc:
        logger.warning("抓取 Show HN 失败: %s", exc)
        if errors_out is not None:
            errors_out.append(str(exc))
    return out


def fetch_hellogithub(
    cfg: Dict[str, Any], *, proxy: str = "", errors_out: Optional[List[str]] = None
) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    limit = int(cfg.get("limit") or 25)
    url = "https://api.hellogithub.com/v1/?sort_by=featured&tid=&page=1"
    out: List[RepoItem] = []
    try:
        _, raw, _ = _http_get(url, proxy=proxy)
        rows = (json.loads(raw) or {}).get("data") or []
        for r in rows[:limit]:
            if not isinstance(r, dict):
                continue
            full_name = str(r.get("full_name") or "").strip()
            if not full_name or "/" not in full_name:
                continue
            desc = str(r.get("summary") or r.get("title") or "").strip()
            out.append(
                RepoItem(
                    repo_name=full_name,
                    url=f"https://github.com/{full_name}",
                    description=desc,
                    language=r.get("primary_lang") or None,
                    stars=int(r.get("stars") or 0),
                    community_score=int(r.get("clicks_total") or 0),
                    sources=["HelloGitHub"],
                )
            )
    except Exception as exc:
        logger.warning("抓取 HelloGitHub 失败: %s", exc)
        if errors_out is not None:
            errors_out.append(str(exc))
    return out


def parse_huggingface_papers_json(raw_json: str, *, limit: int = 20) -> List[RepoItem]:
    rows = json.loads(raw_json)
    if not isinstance(rows, list):
        return []
    out: List[RepoItem] = []
    for entry in rows[:limit]:
        if not isinstance(entry, dict):
            continue
        paper = entry.get("paper") or {}
        if not isinstance(paper, dict):
            continue
        gh_url = str(paper.get("githubRepo") or "").strip()
        repo_name = extract_github_repo_from_url(gh_url)
        if not repo_name:
            continue
        title = str(entry.get("title") or paper.get("title") or "").strip()
        paper_id = str(paper.get("id") or "").strip()
        upvotes = int(paper.get("upvotes") or entry.get("numUpvotes") or 0)
        stars_gh = int(paper.get("githubStars") or 0)
        out.append(
            RepoItem(
                repo_name=repo_name,
                url=f"https://github.com/{repo_name}",
                description=title,
                stars=stars_gh,
                community_score=upvotes,
                sources=["HuggingFace Papers"],
                discussion_url=f"https://huggingface.co/papers/{paper_id}" if paper_id else "",
            )
        )
    return out


def fetch_huggingface_papers(
    cfg: Dict[str, Any], *, proxy: str = "", errors_out: Optional[List[str]] = None
) -> List[RepoItem]:
    if not cfg.get("enabled", False):
        return []
    limit = min(50, int(cfg.get("limit") or 20))
    url = f"https://huggingface.co/api/daily_papers?limit={limit}"
    try:
        _, raw, _ = _http_get(url, proxy=proxy)
        return parse_huggingface_papers_json(raw, limit=limit)
    except Exception as exc:
        logger.warning("抓取 HuggingFace Daily Papers 失败: %s", exc)
        if errors_out is not None:
            errors_out.append(str(exc))
        return []


def parse_github_releases_atom(xml_text: str, repo_name: str) -> Optional[RepoItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
    link_el = entry.find("a:link", ns)
    rel_url = sanitize_http_url(link_el.attrib.get("href", "") if link_el is not None else "")
    updated = (entry.findtext("a:updated", default="", namespaces=ns) or "").strip()
    tag = rel_url.rstrip("/").split("/")[-1] if "/releases/tag/" in rel_url else title
    summary = _clean_html_text(entry.findtext("a:content", default="", namespaces=ns) or "")
    desc = f"[Release {tag}] {title}" + (f" — {summary[:140]}" if summary else "")
    return RepoItem(
        repo_name=repo_name,
        url=f"https://github.com/{repo_name}",
        description=desc.strip(),
        pushed_at=updated,
        sources=["GitHub Releases"],
        discussion_url=rel_url,
        release_tag=tag[:40],
    )


def fetch_github_releases(
    cfg: Dict[str, Any], *, proxy: str = "", errors_out: Optional[List[str]] = None
) -> List[RepoItem]:
    if not cfg.get("enabled", False):
        return []
    repos = cfg.get("repos") or []
    out: List[RepoItem] = []
    for raw_repo in repos:
        repo_name = str(raw_repo or "").strip().strip("/")
        if "/" not in repo_name:
            continue
        url = f"https://github.com/{repo_name}/releases.atom"
        try:
            _, xml_text, _ = _http_get(url, proxy=proxy)
            item = parse_github_releases_atom(xml_text, repo_name)
            if item:
                out.append(item)
        except Exception as exc:
            logger.warning("抓取 %s Release 动态失败: %s", repo_name, exc)
            if errors_out is not None:
                errors_out.append(f"{repo_name}: {exc}")
    return out


def _parse_rss_or_atom(xml_text: str, source_label: str) -> List[RepoItem]:
    out: List[RepoItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    ns_atom = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item")
    if entries:
        for item in entries:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = _clean_html_text(item.findtext("description") or "")
            repo_name = extract_github_repo_from_url(link) or extract_github_repo_from_url(desc)
            if repo_name:
                safe_disc = sanitize_http_url(link) if "github.com" not in link else ""
                out.append(
                    RepoItem(
                        repo_name=repo_name,
                        url=f"https://github.com/{repo_name}",
                        description=title or desc[:200],
                        sources=[source_label],
                        discussion_url=safe_disc,
                    )
                )
        return out

    for entry in root.findall(".//a:entry", ns_atom):
        title = (entry.findtext("a:title", default="", namespaces=ns_atom) or "").strip()
        link_el = entry.find("a:link", ns_atom)
        link = link_el.attrib.get("href", "") if link_el is not None else ""
        content = _clean_html_text(
            entry.findtext("a:content", default="", namespaces=ns_atom)
            or entry.findtext("a:summary", default="", namespaces=ns_atom)
            or ""
        )
        repo_name = extract_github_repo_from_url(link) or extract_github_repo_from_url(content)
        if repo_name:
            safe_disc = sanitize_http_url(link) if "github.com" not in link else ""
            out.append(
                RepoItem(
                    repo_name=repo_name,
                    url=f"https://github.com/{repo_name}",
                    description=title or content[:200],
                    sources=[source_label],
                    discussion_url=safe_disc,
                )
            )
    return out


def build_subreddit_rss_url(base_url: str, subreddit: str) -> str:
    """Build subreddit feed URL compatible with official Reddit, custom-domain RSSHub, or URL templates."""
    base = (base_url or "https://www.reddit.com").strip().rstrip("/")
    sub_q = urllib.parse.quote((subreddit or "").strip().strip("/"))
    if "{subreddit}" in base:
        return base.replace("{subreddit}", sub_q)

    parsed = urllib.parse.urlsplit(base)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    if host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it":
        return f"{base}/r/{sub_q}/hot.rss?limit=20"
    if path:
        return f"{base}/{sub_q}"
    return f"{base}/reddit/subreddit/{sub_q}"


def fetch_reddit_and_extra_rss(
    reddit_cfg: Dict[str, Any],
    extra_urls: List[str],
    *,
    proxy: str = "",
    errors_out: Optional[List[str]] = None,
) -> List[RepoItem]:
    out: List[RepoItem] = []
    if reddit_cfg.get("enabled", False):
        base_url = str(reddit_cfg.get("rss_base_url") or "https://www.reddit.com").rstrip("/")
        for sub in reddit_cfg.get("subreddits") or []:
            s = str(sub).strip()
            if not s:
                continue
            url = build_subreddit_rss_url(base_url, s)
            try:
                _, xml_text, _ = _http_get(
                    url,
                    headers={"User-Agent": "linux:github-radar:v1.0 (by /u/radar_bot)"},
                    proxy=proxy,
                )
                out.extend(_parse_rss_or_atom(xml_text, f"Reddit:r/{s}"))
            except Exception as exc:
                logger.info("Reddit r/%s 跳过（可能受网络或限流限制）: %s", s, exc)
                if errors_out is not None:
                    errors_out.append(f"r/{s}: {exc}")

    for raw_url in extra_urls or []:
        u = sanitize_http_url(str(raw_url).strip())
        if not u:
            continue
        try:
            _, xml_text, _ = _http_get(u, proxy=proxy)
            out.extend(_parse_rss_or_atom(xml_text, "自定义RSS"))
        except Exception as exc:
            logger.warning("抓取自定义 RSS %s 失败: %s", u, exc)
            if errors_out is not None:
                errors_out.append(f"{u}: {exc}")
    return out


def _needs_enrichment(item: RepoItem) -> bool:
    if item.metadata_enriched and item.created_at:
        return False
    if item.stars == 0:
        return True
    if not item.language or not item.description or "Sponsor Star " in item.description:
        return True
    if not item.created_at:
        return True
    if not item.topics and not item.license_name:
        return True
    return False


def _enrich_priority_key(item: RepoItem) -> Tuple[int, int, int]:
    missing_core = 0 if (item.stars == 0 or not item.language or not item.description or "Sponsor Star " in item.description) else 1
    missing_age = 0 if not item.created_at else 1
    return (missing_core, missing_age, -len(item.sources))


def _apply_graphql_node(item: RepoItem, node: Dict[str, Any]) -> None:
    nwo = str(node.get("nameWithOwner") or "").strip()
    if nwo and "/" in nwo:
        item.repo_name = nwo
        item.url = f"https://github.com/{nwo}"
    desc = str(node.get("description") or "").strip()
    if desc and (not item.description or "Sponsor Star " in item.description):
        item.description = desc
    lang_obj = node.get("primaryLanguage")
    if isinstance(lang_obj, dict) and lang_obj.get("name"):
        item.language = str(lang_obj["name"])
    if node.get("stargazerCount") is not None:
        item.stars = int(node.get("stargazerCount") or 0)
    if node.get("forkCount") is not None:
        item.forks = int(node.get("forkCount") or 0)
    issues_obj = node.get("issues")
    if isinstance(issues_obj, dict) and issues_obj.get("totalCount") is not None:
        item.open_issues = int(issues_obj.get("totalCount") or 0)
    if node.get("createdAt"):
        item.created_at = str(node["createdAt"])
    if node.get("pushedAt"):
        item.pushed_at = str(node["pushedAt"])
    lic_obj = node.get("licenseInfo")
    if not item.license_name and isinstance(lic_obj, dict):
        spdx = str(lic_obj.get("spdxId") or "").strip()
        if spdx and spdx.upper() != "NOASSERTION":
            item.license_name = spdx
    topics_conn = (node.get("repositoryTopics") or {}).get("nodes")
    if not item.topics and isinstance(topics_conn, list):
        extracted = []
        for tn in topics_conn:
            if isinstance(tn, dict) and isinstance(tn.get("topic"), dict):
                tname = str(tn["topic"].get("name") or "").strip()
                if tname:
                    extracted.append(tname)
        if extracted:
            item.topics = extracted[:8]
    item.metadata_enriched = True


def _enrich_via_graphql_batch(
    batch: List[RepoItem], *, proxy: str = "", github_token: str = ""
) -> Tuple[List[RepoItem], bool, bool]:
    """Batch-enrich up to 50 repos in a single GraphQL request.

    Returns (items_needing_rest_fallback, stop_graphql_batches, stop_all_api).
    """
    valid_pairs: List[Tuple[str, RepoItem, str, str]] = []
    fallback: List[RepoItem] = []
    for idx, item in enumerate(batch):
        slug = item.repo_name.strip()
        if not SAFE_REPO_SLUG_RE.match(slug):
            fallback.append(item)
            continue
        owner, name = slug.split("/", 1)
        valid_pairs.append((f"r{idx}", item, owner, name))

    if not valid_pairs:
        return fallback, False, False

    fields = (
        "nameWithOwner description stargazerCount forkCount createdAt pushedAt "
        "primaryLanguage { name } licenseInfo { spdxId } "
        "issues(states: OPEN) { totalCount } "
        "repositoryTopics(first: 8) { nodes { topic { name } } }"
    )
    query_parts = [
        f"{alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }}"
        for alias, _, owner, name in valid_pairs
    ]
    query = "query {\n  " + "\n  ".join(query_parts) + "\n}"

    try:
        _, raw, _ = _http_post_json(
            "https://api.github.com/graphql",
            {"query": query},
            proxy=proxy,
            github_token=github_token,
            timeout=20,
        )
        js = json.loads(raw)
        errors = js.get("errors") or []
        quota_or_auth_err = any(
            isinstance(e, dict)
            and (
                str(e.get("type") or "").upper()
                in {"RATE_LIMITED", "FORBIDDEN", "INSUFFICIENT_SCOPES"}
                or "rate limit" in str(e.get("message") or "").lower()
            )
            for e in errors
        )
        data = js.get("data")
        if not isinstance(data, dict):
            return batch, True, quota_or_auth_err
        not_found_aliases = set()
        for err in errors:
            if isinstance(err, dict) and err.get("type") == "NOT_FOUND":
                path = err.get("path") or []
                if path:
                    not_found_aliases.add(str(path[0]))
        for alias, item, _, _ in valid_pairs:
            node = data.get(alias)
            if isinstance(node, dict):
                _apply_graphql_node(item, node)
            elif alias not in not_found_aliases:
                # Could be a renamed repository or transient partial error; allow REST redirect lookup
                fallback.append(item)
        return fallback, quota_or_auth_err, False
    except urllib.error.HTTPError as exc:
        stop_all = exc.code in (401, 429)
        logger.debug("GraphQL 批量补充元信息 HTTP %s，停止后续 GraphQL 批次", exc.code)
        return batch, True, stop_all
    except Exception as exc:
        logger.debug("GraphQL 批量补充元信息回退至 REST: %s", exc)
        return batch, True, False


def enrich_github_metadata(
    items: List[RepoItem],
    *,
    max_enrich: Optional[int] = None,
    proxy: str = "",
    github_token: str = "",
) -> int:
    """Fill missing stars / language / created_at / open_issues / topics / license within rate limits."""
    candidates = [r for r in items if _needs_enrichment(r)]
    if not candidates:
        return 0

    limit = max_enrich if max_enrich is not None else (120 if github_token else 25)
    prioritized = sorted(candidates, key=_enrich_priority_key)[:limit]
    enriched = 0
    stop_all_api = False

    remaining_for_rest = prioritized
    if github_token and len(prioritized) >= 2:
        remaining_for_rest = []
        for start in range(0, len(prioritized), 50):
            chunk = prioritized[start : start + 50]
            before_done = sum(1 for x in chunk if x.metadata_enriched)
            still_needed, stop_gql, stop_all = _enrich_via_graphql_batch(
                chunk, proxy=proxy, github_token=github_token
            )
            after_done = sum(1 for x in chunk if x.metadata_enriched)
            enriched += max(0, after_done - before_done)
            remaining_for_rest.extend(still_needed)
            if stop_all:
                stop_all_api = True
                break
            if stop_gql:
                remaining_for_rest.extend(prioritized[start + 50 :])
                break

    if stop_all_api:
        return enriched

    consecutive_net_errors = 0
    for item in remaining_for_rest:
        if enriched >= limit:
            break
        if not _needs_enrichment(item):
            continue
        if not SAFE_REPO_SLUG_RE.match(item.repo_name.strip()):
            continue
        url = f"https://api.github.com/repos/{item.repo_name.strip()}"
        try:
            _, raw, hdrs = _http_get(
                url,
                headers={"Accept": "application/vnd.github+json"},
                proxy=proxy,
                github_token=github_token,
            )
            consecutive_net_errors = 0
            js = json.loads(raw)
            if isinstance(js, dict) and js.get("full_name"):
                item.repo_name = str(js["full_name"])
                item.url = f"https://github.com/{item.repo_name}"
                if js.get("description") and (not item.description or "Sponsor Star " in item.description):
                    item.description = str(js["description"]).strip()
                if js.get("language"):
                    item.language = str(js["language"])
                if js.get("stargazers_count") is not None:
                    item.stars = int(js.get("stargazers_count") or 0)
                if js.get("forks_count") is not None:
                    item.forks = int(js.get("forks_count") or 0)
                item.open_issues = int(js.get("open_issues_count") or item.open_issues)
                item.created_at = str(js.get("created_at") or item.created_at)
                item.pushed_at = str(js.get("pushed_at") or item.pushed_at)
                if not item.topics and isinstance(js.get("topics"), list):
                    item.topics = [str(t).strip() for t in js["topics"] if str(t).strip()][:8]
                if not item.license_name and isinstance(js.get("license"), dict):
                    spdx = str(js["license"].get("spdx_id") or "").strip()
                    if spdx and spdx.upper() != "NOASSERTION":
                        item.license_name = spdx
                item.metadata_enriched = True
                enriched += 1
            rem = int(hdrs.get("x-ratelimit-remaining", "999") or 999)
            if rem <= 3:
                logger.info("GitHub API 剩余配额较低 (%s)，停止补充元信息", rem)
                break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                logger.info("GitHub API 触发鉴权或限流 (HTTP %s)，停止补充元信息", exc.code)
                break
            logger.debug("跳过不可访问仓库 %s (HTTP %s)", item.repo_name, exc.code)
            continue
        except Exception as exc:
            consecutive_net_errors += 1
            logger.debug("补充仓库 %s 元信息异常: %s", item.repo_name, exc)
            if consecutive_net_errors >= 3:
                break
    return enriched


def collect_all_sources(
    sources_cfg: Dict[str, Any],
    network_cfg: Optional[Dict[str, Any]] = None,
    *,
    health_out: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[RepoItem]:
    proxy = resolve_proxy(network_cfg)
    github_token = resolve_github_token(network_cfg)
    max_workers = max(1, int((network_cfg or {}).get("max_workers") or 6))

    tasks: List[Tuple[str, Callable[[List[str]], List[RepoItem]]]] = []

    trending_cfg = sources_cfg.get("github_trending") or {}
    if trending_cfg.get("enabled", True):
        tasks.append(
            ("github_trending", lambda errs: fetch_github_trending(trending_cfg, proxy=proxy, errors_out=errs))
        )

    search_cfg = sources_cfg.get("github_search") or {}
    if search_cfg.get("enabled", True):
        tasks.append(
            (
                "github_search",
                lambda errs: fetch_github_search(
                    search_cfg, proxy=proxy, github_token=github_token, errors_out=errs
                ),
            )
        )

    starred_users = sources_cfg.get("starred_users") or []
    if starred_users:
        tasks.append(
            (
                "starred_users",
                lambda errs: fetch_starred_developers(
                    starred_users, proxy=proxy, github_token=github_token, errors_out=errs
                ),
            )
        )

    hn_cfg = sources_cfg.get("hackernews_show") or {}
    if hn_cfg.get("enabled", True):
        tasks.append(
            ("hackernews_show", lambda errs: fetch_hackernews_show(hn_cfg, proxy=proxy, errors_out=errs))
        )

    hg_cfg = sources_cfg.get("hellogithub") or {}
    if hg_cfg.get("enabled", True):
        tasks.append(
            ("hellogithub", lambda errs: fetch_hellogithub(hg_cfg, proxy=proxy, errors_out=errs))
        )

    hf_cfg = sources_cfg.get("huggingface_papers") or {}
    if hf_cfg.get("enabled", False):
        tasks.append(
            (
                "huggingface_papers",
                lambda errs: fetch_huggingface_papers(hf_cfg, proxy=proxy, errors_out=errs),
            )
        )

    rel_cfg = sources_cfg.get("github_releases") or {}
    if rel_cfg.get("enabled", False):
        tasks.append(
            ("github_releases", lambda errs: fetch_github_releases(rel_cfg, proxy=proxy, errors_out=errs))
        )

    reddit_cfg = sources_cfg.get("reddit_rss") or {}
    extra_urls = sources_cfg.get("extra_rss_urls") or []
    if reddit_cfg.get("enabled", False) or extra_urls:
        tasks.append(
            (
                "reddit_rss",
                lambda errs: fetch_reddit_and_extra_rss(
                    reddit_cfg, extra_urls, proxy=proxy, errors_out=errs
                ),
            )
        )

    results_by_name: Dict[str, List[RepoItem]] = {}
    health_map: Dict[str, Dict[str, Any]] = {}

    def _run_task(name: str, fn: Callable[[List[str]], List[RepoItem]]):
        t0 = time.monotonic()
        errs: List[str] = []
        try:
            res = fn(errs)
        except Exception as exc:
            errs.append(str(exc))
            res = []
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        ok = len(res) > 0 or len(errs) == 0
        return name, res, {
            "ok": ok,
            "count": len(res),
            "duration_ms": elapsed_ms,
            "error": "; ".join(errs)[:240] if errs else "",
        }

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(tasks)))) as pool:
        futures = [pool.submit(_run_task, name, fn) for name, fn in tasks]
        for fut in as_completed(futures):
            name, res, hinfo = fut.result()
            results_by_name[name] = res
            health_map[name] = hinfo

    # Reassemble in deterministic config order
    ordered_items: List[RepoItem] = []
    for name, _ in tasks:
        ordered_items.extend(results_by_name.get(name) or [])
        if health_out is not None and name in health_map:
            health_out[name] = health_map[name]

    return ordered_items
