from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
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
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:[/?#\"'\s<>]|$)"
)
TRENDING_ARTICLE_RE = re.compile(
    r"<article\b[^>]*class=\"[^\"]*Box-row[^\"]*\"[^>]*>(.*?)</article>",
    re.DOTALL | re.IGNORECASE,
)


def _http_get(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 12) -> Tuple[int, str, Dict[str, str]]:
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def parse_github_trending_html(html_text: str, *, source_label: str = "GitHub Trending") -> List[RepoItem]:
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
        m_desc = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)
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

        items.append(
            RepoItem(
                repo_name=repo_name,
                url=f"https://github.com/{repo_name}",
                description=desc,
                language=lang or None,
                stars=stars,
                forks=forks,
                sources=[source_label],
            )
        )
    return items


def extract_github_repo_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = GITHUB_REPO_RE.search(url)
    if not m:
        return None
    full = m.group(1).rstrip("/")
    if full.endswith(".git"):
        full = full[:-4]
    parts = full.split("/")
    if len(parts) != 2:
        return None
    if parts[0].lower() in {"topics", "trending", "explore", "search", "marketplace", "sponsors", "orgs"}:
        return None
    return full


def _repo_from_gh_api(raw: Dict[str, Any], source_label: str) -> Optional[RepoItem]:
    full_name = str(raw.get("full_name") or "").strip()
    if not full_name or "/" not in full_name:
        return None
    return RepoItem(
        repo_name=full_name,
        url=str(raw.get("html_url") or f"https://github.com/{full_name}"),
        description=str(raw.get("description") or "").strip(),
        language=raw.get("language") or None,
        stars=int(raw.get("stargazers_count") or 0),
        forks=int(raw.get("forks_count") or 0),
        open_issues=int(raw.get("open_issues_count") or 0),
        created_at=str(raw.get("created_at") or ""),
        pushed_at=str(raw.get("pushed_at") or ""),
        sources=[source_label],
    )


def fetch_github_trending(cfg: Dict[str, Any]) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    since = str(cfg.get("since") or "daily")
    max_items = int(cfg.get("max_items") or 30)
    langs = cfg.get("languages") or [""]
    out: List[RepoItem] = []
    for lang in langs:
        lang_slug = urllib.parse.quote(str(lang or "").strip())
        path = f"https://github.com/trending/{lang_slug}?since={since}" if lang_slug else f"https://github.com/trending?since={since}"
        label = f"GitHub Trending({lang})" if lang_slug else "GitHub Trending"
        try:
            _, html_text, _ = _http_get(path)
            parsed = parse_github_trending_html(html_text, source_label=label)
            out.extend(parsed[:max_items])
        except Exception as exc:
            logger.warning("抓取 %s 失败: %s", label, exc)
    return out


def fetch_github_search(cfg: Dict[str, Any]) -> List[RepoItem]:
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
            _, raw, _ = _http_get(url, headers={"Accept": "application/vnd.github+json"})
            js = json.loads(raw)
            for item in js.get("items") or []:
                repo = _repo_from_gh_api(item, f"雷达:{name}")
                if repo:
                    out.append(repo)
        except Exception as exc:
            logger.warning("抓取 GitHub Search(%s) 失败: %s", name, exc)
    return out


def fetch_starred_developers(users: List[str]) -> List[RepoItem]:
    out: List[RepoItem] = []
    for u in users or []:
        uname = str(u).strip()
        if not uname:
            continue
        url = f"https://api.github.com/users/{urllib.parse.quote(uname)}/starred?per_page=15"
        try:
            _, raw, _ = _http_get(url, headers={"Accept": "application/vnd.github+json"})
            items = json.loads(raw)
            if isinstance(items, list):
                for item in items:
                    repo = _repo_from_gh_api(item, f"关注开发者:{uname}")
                    if repo:
                        out.append(repo)
        except Exception as exc:
            logger.warning("抓取开发者 %s Star 动态失败: %s", uname, exc)
    return out


def fetch_hackernews_show(cfg: Dict[str, Any]) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    limit = min(50, int(cfg.get("limit") or 30))
    url = f"https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage={limit}"
    out: List[RepoItem] = []
    try:
        _, raw, _ = _http_get(url)
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
                    stars=points,
                    sources=["Show HN"],
                    discussion_url=f"https://news.ycombinator.com/item?id={oid}" if oid else "",
                )
            )
    except Exception as exc:
        logger.warning("抓取 Show HN 失败: %s", exc)
    return out


def fetch_hellogithub(cfg: Dict[str, Any]) -> List[RepoItem]:
    if not cfg.get("enabled", True):
        return []
    limit = int(cfg.get("limit") or 25)
    url = "https://api.hellogithub.com/v1/?sort_by=featured&tid=&page=1"
    out: List[RepoItem] = []
    try:
        _, raw, _ = _http_get(url)
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
                    sources=["HelloGitHub"],
                )
            )
    except Exception as exc:
        logger.warning("抓取 HelloGitHub 失败: %s", exc)
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
                out.append(
                    RepoItem(
                        repo_name=repo_name,
                        url=f"https://github.com/{repo_name}",
                        description=title or desc[:200],
                        sources=[source_label],
                        discussion_url=link if "github.com" not in link else "",
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
            out.append(
                RepoItem(
                    repo_name=repo_name,
                    url=f"https://github.com/{repo_name}",
                    description=title or content[:200],
                    sources=[source_label],
                    discussion_url=link if "github.com" not in link else "",
                )
            )
    return out


def fetch_reddit_and_extra_rss(reddit_cfg: Dict[str, Any], extra_urls: List[str]) -> List[RepoItem]:
    out: List[RepoItem] = []
    if reddit_cfg.get("enabled", True):
        for sub in reddit_cfg.get("subreddits") or []:
            s = str(sub).strip()
            if not s:
                continue
            url = f"https://www.reddit.com/r/{urllib.parse.quote(s)}/hot.rss?limit=20"
            try:
                _, xml_text, _ = _http_get(
                    url, headers={"User-Agent": "linux:github-radar:v1.0 (by /u/radar_bot)"}
                )
                out.extend(_parse_rss_or_atom(xml_text, f"Reddit:r/{s}"))
            except Exception as exc:
                logger.info("Reddit r/%s 跳过（可能受机房访问限制）: %s", s, exc)

    for raw_url in extra_urls or []:
        u = str(raw_url).strip()
        if not u:
            continue
        try:
            _, xml_text, _ = _http_get(u)
            out.extend(_parse_rss_or_atom(xml_text, "自定义RSS"))
        except Exception as exc:
            logger.warning("抓取自定义 RSS %s 失败: %s", u, exc)
    return out


def enrich_github_metadata(items: List[RepoItem], *, max_enrich: int = 20) -> None:
    """Fill missing created_at / open_issues / language for non-API sources within unauthenticated rate limit."""
    enriched = 0
    for item in items:
        if enriched >= max_enrich:
            break
        if item.created_at and item.language is not None:
            continue
        url = f"https://api.github.com/repos/{item.repo_name}"
        try:
            _, raw, hdrs = _http_get(url, headers={"Accept": "application/vnd.github+json"})
            js = json.loads(raw)
            if isinstance(js, dict) and js.get("full_name"):
                item.repo_name = str(js["full_name"])
                if not item.description and js.get("description"):
                    item.description = str(js["description"]).strip()
                if js.get("language"):
                    item.language = str(js["language"])
                item.stars = max(item.stars, int(js.get("stargazers_count") or 0))
                item.forks = max(item.forks, int(js.get("forks_count") or 0))
                item.open_issues = int(js.get("open_issues_count") or item.open_issues)
                item.created_at = str(js.get("created_at") or item.created_at)
                item.pushed_at = str(js.get("pushed_at") or item.pushed_at)
                enriched += 1
            rem = int(hdrs.get("x-ratelimit-remaining") or 10)
            if rem <= 5:
                logger.info("GitHub API 剩余配额较低 (%s)，停止补充元信息", rem)
                break
        except Exception as exc:
            logger.debug("补充仓库 %s 元信息失败: %s", item.repo_name, exc)
            break


def collect_all_sources(sources_cfg: Dict[str, Any]) -> List[RepoItem]:
    items: List[RepoItem] = []
    items.extend(fetch_github_trending(sources_cfg.get("github_trending") or {}))
    items.extend(fetch_github_search(sources_cfg.get("github_search") or {}))
    items.extend(fetch_starred_developers(sources_cfg.get("starred_users") or []))
    items.extend(fetch_hackernews_show(sources_cfg.get("hackernews_show") or {}))
    items.extend(fetch_hellogithub(sources_cfg.get("hellogithub") or {}))
    items.extend(
        fetch_reddit_and_extra_rss(
            sources_cfg.get("reddit_rss") or {},
            sources_cfg.get("extra_rss_urls") or [],
        )
    )
    return items
