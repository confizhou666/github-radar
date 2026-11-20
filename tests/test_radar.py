from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

from radar.config import RadarConfig, resolve_timezone
from radar.feed_builder import apply_category_quota, normalize_macro_category, resolve_site_and_feeds_urls
from radar.filters import compute_breakout_score, is_noisy_repo
from radar.models import RepoItem
from radar.notifier import (
    build_digest_markdown,
    build_webhook_payload,
    build_webhook_payloads,
    filter_digest_items,
)
from radar.pipeline import RadarStore, export_only, restore_state_from_source, run_pipeline
from radar.sources import (
    build_subreddit_rss_url,
    collect_all_sources,
    enrich_github_metadata,
    extract_github_repo_from_url,
    fetch_hackernews_show,
    parse_github_releases_atom,
    parse_github_trending_html,
    parse_huggingface_papers_json,
)
from radar.translator import classify_repo_category, generate_ai_highlights
from run import resolve_runtime_paths


class _FakeHTTPResp:
    def __init__(self, body: str | bytes, status: int = 200, headers: dict | None = None):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResp":
        return self

    def __exit__(self, *args) -> None:
        return None


class TestGitHubRadar(unittest.TestCase):
    def _cfg(self) -> RadarConfig:
        return RadarConfig.load(Path(__file__).resolve().parent.parent / "config.yaml")

    def test_trending_html_parsing_with_svg_path_and_today_stars(self) -> None:
        sample_html = """
        <article class="Box-row">
          <div class="float-right d-flex">
            <a href="/login">
              <svg><path d="M8 .25a.75.75"></path></svg> Star
            </a>
          </div>
          <h2 class="h3 lh-condensed"><a href="/cool/magic-cli">cool / magic-cli</a></h2>
          <p class="col-9 color-fg-muted my-1 pr-4">Turn natural language into safe shell pipelines</p>
          <span itemprop="programmingLanguage">Rust</span>
          <a href="/cool/magic-cli/stargazers">1.2k</a>
          <a href="/cool/magic-cli/forks">88</a>
          <span class="d-inline-block float-sm-right">412 stars today</span>
        </article>
        """
        items = parse_github_trending_html(sample_html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].repo_name, "cool/magic-cli")
        self.assertEqual(items[0].description, "Turn natural language into safe shell pipelines")
        self.assertEqual(items[0].language, "Rust")
        self.assertEqual(items[0].stars, 1200)
        self.assertEqual(items[0].forks, 88)
        self.assertEqual(items[0].stars_delta_24h, 412)

    def test_blacklist_whitelist_and_structural_filters(self) -> None:
        cfg = self._cfg()
        filters = dict(cfg.filters)
        filters["blacklist_owners"] = ["spam-org"]
        filters["whitelist_repos"] = ["vip/no-lang-notes"]
        filters["min_stars"] = 10

        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        noisy_cases = [
            RepoItem(repo_name="foo/awesome-python", url="u", description="A curated list of libs", language="Python", stars=50),
            RepoItem(repo_name="foo/java-interview", url="u", description="Java八股文面试高频题汇总", language="Java", stars=50),
            RepoItem(repo_name="foo/spring-shop", url="u", description="基于SpringBoot的高校毕业设计管理系统", language="Java", stars=50),
            RepoItem(repo_name="foo/chatgpt-prompts", url="u", description="1000 awesome prompts for副业变现", language="JavaScript", stars=50),
            RepoItem(repo_name="foo/free-nodes", url="u", description="每日免费节点clash v2ray订阅", language="Python", stars=50),
            RepoItem(repo_name="foo/only-markdown", url="u", description="Just an essay collection", language=None, stars=50),
            RepoItem(repo_name="alice/alice", url="u", description="My GitHub profile readme", language="HTML", stars=50),
            RepoItem(repo_name="spam-org/good-name", url="u", description="Legit looking tool", language="Go", stars=100),
            RepoItem(repo_name="tiny/too-few-stars", url="u", description="Small CLI utility", language="Go", stars=3),
            RepoItem(
                repo_name="old/zombie-tool",
                url="u",
                description="Ancient utility still decent",
                language="Go",
                stars=100,
                created_at=(now - timedelta(days=365 * 7)).isoformat(),
            ),
            RepoItem(
                repo_name="spam/instant-star",
                url="u",
                description="Brand new overnight hype tool",
                language="Python",
                stars=1200,
                forks=0,
                open_issues=0,
                created_at=(now - timedelta(days=2)).isoformat(),
            ),
        ]
        for case in noisy_cases:
            drop, reason = is_noisy_repo(case, filters, now_utc=now)
            self.assertTrue(drop, f"Expected noisy drop for {case.repo_name}, got ok")
            self.assertNotEqual(reason, "ok")

        whitelisted = RepoItem(repo_name="vip/no-lang-notes", url="u", description="Curated notes", language=None, stars=1)
        drop_wl, reason_wl = is_noisy_repo(whitelisted, filters, now_utc=now)
        self.assertFalse(drop_wl)
        self.assertEqual(reason_wl, "whitelisted")

        good = RepoItem(
            repo_name="indie/terminal-cinema",
            url="https://github.com/indie/terminal-cinema",
            description="Render live shader art in 24-bit ANSI terminals at 60fps",
            language="Rust",
            stars=420,
            forks=25,
            open_issues=6,
            created_at=(now - timedelta(days=20)).isoformat(),
            sources=["GitHub Trending"],
        )
        drop, reason = is_noisy_repo(good, filters, now_utc=now)
        self.assertFalse(drop, f"Unexpected drop for valid repo: {reason}")

    def test_star_farm_shells_topics_and_word_boundary_safety(self) -> None:
        """P0-1 & P0-5: Catch ~383-star poisoned shells without harming firecracker / GC / virtual-list / k8s."""
        cfg = self._cfg()
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)
        recent = (now - timedelta(days=4)).isoformat()

        poison_shells = [
            RepoItem(
                repo_name="nabilaolivia/adobe-indesign-deployment-pipeline",
                url="https://github.com/nabilaolivia/adobe-indesign-deployment-pipeline",
                description="Adobe InDesign Desktop Build: Secure Digital Asset Deployment Pipeline",
                language="HTML",
                stars=384,
                forks=1,
                topics=["adobe-indesign", "indesign-free-soft"],
                created_at=recent,
            ),
            RepoItem(
                repo_name="tiototo/cinebench-2026-desktop-setup",
                url="https://github.com/tiototo/cinebench-2026-desktop-setup",
                description="Cinebench 2026 Standalone Desktop Setup with Pre-Configured Components",
                language="HTML",
                stars=383,
                forks=0,
                topics=["cinebench", "cinebench-crack"],
                created_at=recent,
            ),
            RepoItem(
                repo_name="abhi/ai-app-builder-open",
                url="https://github.com/abhi/ai-app-builder-open",
                description="Open-source AI app builder for full-stack Next.js apps with hosting",
                language="HTML",
                stars=384,
                forks=0,
                created_at=recent,
            ),
            RepoItem(
                repo_name="sketch/daemon-pro-installer",
                url="https://github.com/sketch/daemon-pro-installer",
                description="DAEMON-Pro: Full-Feature Security Installer for Desktop Deployment",
                language="TypeScript",
                stars=140,
                forks=1,
                created_at=recent,
            ),
            RepoItem(
                repo_name="eng/phantom-blade-zero-runtime-toolkit",
                url="https://github.com/eng/phantom-blade-zero-runtime-toolkit",
                description="PhantomBladeZero-Cheat: 54 toggleable runtime options",
                language="HTML",
                stars=382,
                forks=0,
                topics=["phantom-blade-zero-trainer"],
                created_at=recent,
            ),
        ]
        for shell in poison_shells:
            drop, reason = is_noisy_repo(shell, cfg.filters, now_utc=now)
            self.assertTrue(drop, f"Poisoned shell {shell.repo_name} escaped filter!")
            self.assertNotEqual(reason, "ok")

        legit_hardcore_repos = [
            RepoItem(
                repo_name="firecracker-microvm/firecracker",
                url="https://github.com/firecracker-microvm/firecracker",
                description="Secure and fast microVMs for serverless computing",
                language="Rust",
                stars=26000,
                forks=1850,
                created_at=(now - timedelta(days=400)).isoformat(),
            ),
            RepoItem(
                repo_name="runtime/mmtk-core",
                url="https://github.com/runtime/mmtk-core",
                description="High-performance concurrent garbage collection framework in Rust",
                language="Rust",
                stars=980,
                forks=110,
                created_at=(now - timedelta(days=300)).isoformat(),
            ),
            RepoItem(
                repo_name="ui/solid-virtual-list",
                url="https://github.com/ui/solid-virtual-list",
                description="Zero-dependency virtual-list renderer for 100k DOM rows",
                language="TypeScript",
                stars=340,
                forks=19,
                created_at=(now - timedelta(days=60)).isoformat(),
            ),
            RepoItem(
                repo_name="ops/kube-warden",
                url="https://github.com/ops/kube-warden",
                description="Reconcile and audit kubernetes resources with eBPF policies",
                language="Go",
                stars=620,
                forks=48,
                created_at=(now - timedelta(days=90)).isoformat(),
            ),
            RepoItem(
                repo_name="ets-labs/python-dependency-injector",
                url="https://github.com/ets-labs/python-dependency-injector",
                description="Dependency injector framework and IoC container for Python",
                language="Python",
                stars=4100,
                forks=430,
                created_at=(now - timedelta(days=500)).isoformat(),
            ),
        ]
        for good in legit_hardcore_repos:
            drop, reason = is_noisy_repo(good, cfg.filters, now_utc=now)
            self.assertFalse(drop, f"False positive on {good.repo_name}: {reason}")

    def test_dedup_pipeline_translation_ai_and_feeds(self) -> None:
        cfg = self._cfg()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = [
                RepoItem(
                    repo_name="Indie/PocketSynth",
                    url="https://github.com/Indie/PocketSynth",
                    description="Tiny FM synthesizer running in your terminal",
                    language="Rust",
                    stars=310,
                    forks=18,
                    open_issues=4,
                    sources=["GitHub Trending"],
                    stars_delta_24h=85,
                ),
                RepoItem(
                    repo_name="indie/pocketsynth",
                    url="https://github.com/indie/pocketsynth",
                    description="Tiny FM synthesizer running in your terminal over WebAudio",
                    language="Rust",
                    stars=350,
                    forks=18,
                    open_issues=4,
                    sources=["Show HN"],
                    discussion_url="https://news.ycombinator.com/item?id=999",
                    topics=["rust", "audio", "synth"],
                    license_name="MIT",
                ),
                RepoItem(
                    repo_name="disguised/wrapper-spam",
                    url="https://github.com/disguised/wrapper-spam",
                    description="Lightweight tool for devs",
                    language="TypeScript",
                    stars=90,
                    forks=5,
                    sources=["HelloGitHub"],
                ),
            ]

            def fake_ai(_prompt: str) -> str:
                return json.dumps(
                    {
                        "structured_output": {
                            "items": [
                                {
                                    "repo_name": "Indie/PocketSynth",
                                    "keep": True,
                                    "category_tag": "创意玩具",
                                    "highlight": "把FM合成器塞进终端里实时编曲，纯Rust无依赖极客感拉满。",
                                },
                                {
                                    "repo_name": "disguised/wrapper-spam",
                                    "keep": False,
                                    "category_tag": "套壳广告",
                                    "highlight": "实质是引导付费API的空壳模板。",
                                },
                            ]
                        }
                    },
                    ensure_ascii=False,
                )

            stats = run_pipeline(
                cfg,
                base,
                raw_items_override=raw,
                translate_fn=lambda s: f"[译]{s}",
                ai_runner=fake_ai,
                enable_ai=True,
                skip_enrich=True,
                enable_notify=False,
            )
            self.assertEqual(stats["raw_count"], 3)
            self.assertEqual(stats["unique_count"], 2)
            self.assertEqual(stats["new_count"], 1)
            self.assertEqual(stats["dropped_count"], 1)
            self.assertTrue(stats["trend_summary"])

            all_xml = (base / "feeds" / "all_discoveries.xml").read_text(encoding="utf-8")
            root = ET.fromstring(all_xml)
            titles = [el.text or "" for el in root.findall("./channel/item/title")]
            self.assertEqual(len(titles), 1)
            self.assertIn("PocketSynth", titles[0])
            self.assertIn("+85★", titles[0])
            desc_html = root.findtext("./channel/item/description") or ""
            self.assertIn("把FM合成器塞进终端里实时编曲", desc_html)
            self.assertIn("GitHub Trending + Show HN", desc_html)
            self.assertIn("MIT", desc_html)

            tr_xml = ET.fromstring((base / "feeds" / "github_trending.xml").read_text(encoding="utf-8"))
            cm_xml = ET.fromstring((base / "feeds" / "community_picks.xml").read_text(encoding="utf-8"))
            self.assertEqual(len(tr_xml.findall("./channel/item")), 1)
            self.assertEqual(len(cm_xml.findall("./channel/item")), 0)

            web_json = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            item0 = web_json["items"][0]
            self.assertEqual(item0["stars_delta_24h"], 85)
            self.assertEqual(item0["license_name"], "MIT")
            self.assertEqual(item0["topics"], ["rust", "audio", "synth"])
            self.assertTrue(item0["multi_source"])
            self.assertEqual(item0["sparkline"], [265, 350])

            # JSON Feed 1.1, dynamic OPML, llms.txt & compressed state backup all emitted
            self.assertTrue((base / "feeds" / "feed.json").exists())
            self.assertTrue((base / "feeds" / "feeds.opml").exists())
            self.assertTrue((base / "feeds" / "llms.txt").exists())
            self.assertTrue((base / "feeds" / "state_backup.json.gz").exists())

            (base / "feeds" / "data.json").unlink()
            exp_stats = export_only(cfg, base)
            self.assertEqual(exp_stats["feed_total"], 1)
            self.assertTrue((base / "feeds" / "data.json").exists())

            stats2 = run_pipeline(
                cfg,
                base,
                raw_items_override=raw[:1],
                translate_fn=lambda s: f"[译]{s}",
                ai_runner=fake_ai,
                enable_ai=True,
                skip_enrich=True,
                enable_notify=False,
            )
            self.assertEqual(stats2["new_count"], 0)

    def test_huggingface_papers_and_github_releases_parsing(self) -> None:
        hf_json = json.dumps(
            [
                {
                    "title": "Fast Sparse Attention",
                    "paper": {
                        "id": "2611.01234",
                        "githubRepo": "https://github.com/lab/sparse-attn",
                        "upvotes": 42,
                        "githubStars": 180,
                    },
                },
                {
                    "title": "Paper Without Repo",
                    "paper": {"id": "2611.09999", "githubRepo": ""},
                },
            ]
        )
        hf_items = parse_huggingface_papers_json(hf_json)
        self.assertEqual(len(hf_items), 1)
        self.assertEqual(hf_items[0].repo_name, "lab/sparse-attn")
        self.assertEqual(hf_items[0].stars, 180)
        self.assertEqual(hf_items[0].community_score, 42)
        self.assertEqual(hf_items[0].discussion_url, "https://huggingface.co/papers/2611.01234")

        atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <updated>2026-11-18T10:00:00Z</updated>
            <link rel="alternate" type="text/html" href="https://github.com/astral-sh/uv/releases/tag/0.13.9"/>
            <title>0.13.9</title>
            <content type="html">&lt;p&gt;Faster resolver and Python 3.14 wheel support.&lt;/p&gt;</content>
          </entry>
        </feed>
        """
        rel_item = parse_github_releases_atom(atom_xml, "astral-sh/uv")
        self.assertIsNotNone(rel_item)
        assert rel_item is not None
        self.assertEqual(rel_item.release_tag, "0.13.9")
        self.assertIn("Faster resolver", rel_item.description)
        self.assertEqual(rel_item.sources, ["GitHub Releases"])

    def test_graphql_batch_rest_404_resilience_and_community_exemption(self) -> None:
        """P0-2 & P1-3: HN points separated from stars, GraphQL batch enrich, REST skips 404 without aborting, community fallback."""
        hn_payload = json.dumps(
            {
                "hits": [
                    {
                        "title": "Show HN: Zero-copy Parquet viewer",
                        "url": "https://github.com/indie/pq-view",
                        "points": 168,
                        "objectID": "42001",
                    }
                ]
            }
        )
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResp(hn_payload)):
            hn_items = fetch_hackernews_show({"enabled": True, "limit": 10})
        self.assertEqual(len(hn_items), 1)
        self.assertEqual(hn_items[0].stars, 0)
        self.assertEqual(hn_items[0].community_score, 168)

        # GraphQL batch enrichment overwrites 0-star HelloGitHub & Show HN with real stargazerCount
        batch_items = [
            RepoItem(repo_name="hg/zero-star-item", url="https://github.com/hg/zero-star-item", description="HG desc", language="C#", stars=0, sources=["HelloGitHub"]),
            hn_items[0],
        ]
        gql_resp = {
            "data": {
                "r0": {
                    "nameWithOwner": "hg/zero-star-item",
                    "description": "HG desc",
                    "stargazerCount": 2450,
                    "forkCount": 190,
                    "createdAt": "2025-05-01T00:00:00Z",
                    "pushedAt": "2026-11-18T00:00:00Z",
                    "primaryLanguage": {"name": "C#"},
                    "licenseInfo": {"spdxId": "MIT"},
                    "issues": {"totalCount": 12},
                    "repositoryTopics": {"nodes": [{"topic": {"name": "converter"}}]},
                },
                "r1": {
                    "nameWithOwner": "indie/pq-view",
                    "description": "Zero-copy Parquet viewer",
                    "stargazerCount": 530,
                    "forkCount": 31,
                    "createdAt": "2026-11-10T00:00:00Z",
                    "pushedAt": "2026-11-19T00:00:00Z",
                    "primaryLanguage": {"name": "Rust"},
                    "licenseInfo": {"spdxId": "Apache-2.0"},
                    "issues": {"totalCount": 3},
                    "repositoryTopics": {"nodes": []},
                },
            }
        }
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResp(json.dumps(gql_resp))) as mock_open:
            enriched_n = enrich_github_metadata(batch_items, github_token="ghs_dummy_token")
        self.assertEqual(mock_open.call_count, 1)
        self.assertEqual(enriched_n, 2)
        self.assertEqual(batch_items[0].stars, 2450)
        self.assertEqual(batch_items[1].stars, 530)
        self.assertEqual(batch_items[1].language, "Rust")

        # REST mode without token: 404 on first item MUST NOT abort second item; 429 on third stops cleanly
        rest_items = [
            RepoItem(repo_name="dead/deleted-repo", url="u", description="Dead repo", sources=["Show HN"]),
            RepoItem(repo_name="live/good-repo", url="u", description="Good repo", sources=["Show HN"]),
            RepoItem(repo_name="quota/unreached-repo", url="u", description="Valid community tool", sources=["HuggingFace Papers"]),
        ]

        def fake_rest_urlopen(req, timeout=12):
            if "dead/deleted-repo" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
            if "live/good-repo" in req.full_url:
                return _FakeHTTPResp(
                    json.dumps(
                        {
                            "full_name": "live/good-repo",
                            "stargazers_count": 310,
                            "forks_count": 22,
                            "language": "Go",
                            "created_at": "2026-10-01T00:00:00Z",
                        }
                    ),
                    headers={"x-ratelimit-remaining": "10"},
                )
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}"))

        with patch("urllib.request.urlopen", side_effect=fake_rest_urlopen):
            enrich_github_metadata(rest_items, github_token="")

        self.assertEqual(rest_items[1].stars, 310)
        self.assertEqual(rest_items[1].language, "Go")
        self.assertFalse(rest_items[2].metadata_enriched)
        self.assertIsNone(rest_items[2].language)

        # Un-enriched curated community source is exempted from require_programming_language
        cfg = self._cfg()
        drop_unreached, _ = is_noisy_repo(rest_items[2], cfg.filters)
        self.assertFalse(drop_unreached)

        # Source health collection
        health: dict = {}
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResp(hn_payload)):
            collected = collect_all_sources(
                {
                    "github_trending": {"enabled": False},
                    "github_search": {"enabled": False},
                    "hackernews_show": {"enabled": True, "limit": 5},
                    "hellogithub": {"enabled": False},
                    "huggingface_papers": {"enabled": False},
                    "github_releases": {"enabled": False},
                    "reddit_rss": {"enabled": False},
                },
                {},
                health_out=health,
            )
        self.assertEqual(len(collected), 1)
        self.assertTrue(health["hackernews_show"]["ok"])
        self.assertEqual(health["hackernews_show"]["count"], 1)

    def test_ai_chunking_backfill_and_github_models_auto_provider(self) -> None:
        """P0-3 & P1-1: Chunked AI batches (> max_batch_items), DB backfill, and GitHub Models free endpoint."""
        five_items = [
            RepoItem(
                repo_name=f"org/tool-{i}",
                url=f"https://github.com/org/tool-{i}",
                description=f"Developer utility number {i}",
                language="Rust",
                stars=100 + i * 20,
                forks=15,
            )
            for i in range(5)
        ]
        chunk_calls = []

        def fake_chunk_runner(prompt: str) -> str:
            chunk_calls.append(prompt)
            out_items = []
            for r in five_items:
                if r.repo_name in prompt:
                    out_items.append(
                        {
                            "repo_name": r.repo_name,
                            "keep": True,
                            "category_tag": "效率工具",
                            "highlight": f"{r.repo_name} 的亮点描述。",
                        }
                    )
            return json.dumps(
                {
                    "trend_summary": "Rust 命令行与底层工具持续走热，org/tool-4 表现突出。",
                    "items": out_items,
                },
                ensure_ascii=False,
            )

        summary_box: list[str] = []
        res = generate_ai_highlights(
            five_items,
            {"enabled": True, "max_batch_items": 2, "max_chunks_per_run": 5},
            runner=fake_chunk_runner,
            trend_summary_out=summary_box,
        )
        self.assertEqual(len(chunk_calls), 3)
        self.assertEqual(len(res), 5)
        self.assertEqual(summary_box[0], "Rust 命令行与底层工具持续走热，org/tool-4 表现突出。")

        # Retired GitHub Models endpoint must NOT be called even when GITHUB_TOKEN is present without RADAR_AI_API_KEY
        captured_reqs = []

        def fail_if_called(req, timeout=60):
            captured_reqs.append(req.full_url)
            return _FakeHTTPResp("OK\r\n")

        with patch.dict(os.environ, {"RADAR_AI_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
            with patch("radar.translator.shutil.which", return_value=None):
                with patch("urllib.request.urlopen", side_effect=fail_if_called):
                    auto_empty = generate_ai_highlights(
                        five_items[:1],
                        {"enabled": True, "provider": "auto", "openai_model": "gpt-4o-mini"},
                        github_token="ghs_actions_token",
                    )
                    legacy_gh = generate_ai_highlights(
                        five_items[:1],
                        {"enabled": True, "provider": "github_models"},
                        github_token="ghs_actions_token",
                    )
        self.assertEqual(captured_reqs, [])
        self.assertEqual(auto_empty, {})
        self.assertEqual(legacy_gh, {})

        # Pipeline backfill: unhighlighted items get deterministic category_tag immediately, and upgrade highlight on subsequent AI run
        cfg = self._cfg()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_pipeline(
                cfg,
                base,
                raw_items_override=five_items[:2],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
            )
            data1 = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(data1["items"][0]["ai_highlight"], "")
            self.assertTrue(data1["items"][0]["category_tag"])

            run_pipeline(
                cfg,
                base,
                raw_items_override=five_items[2:3],
                translate_fn=lambda s: s,
                ai_runner=fake_chunk_runner,
                enable_ai=True,
                skip_enrich=True,
                enable_notify=False,
            )
            data2 = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            by_key = {it["key"]: it for it in data2["items"]}
            self.assertIn("org/tool-0 的亮点描述", by_key["org/tool-0"]["ai_highlight"])
            self.assertIn("org/tool-2 的亮点描述", by_key["org/tool-2"]["ai_highlight"])

    def test_delta_decay_union_sources_and_release_guid_refresh(self) -> None:
        """P0-4: Stale 24h deltas decay to 0, sources union-merge across days, and old Releases refresh GUID."""
        cfg = self._cfg()
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)

        # 8-year-old repo on GitHub Releases is exempted from max_repo_age_years=5
        old_release = RepoItem(
            repo_name="ollama/ancient-engine",
            url="https://github.com/ollama/ancient-engine",
            description="[Release v2.0.0] Major release notes",
            language="Go",
            stars=45000,
            forks=3200,
            created_at=(now - timedelta(days=365 * 8)).isoformat(),
            sources=["GitHub Releases"],
            release_tag="v2.0.0",
        )
        drop_rel, _ = is_noisy_repo(old_release, cfg.filters, now_utc=now)
        self.assertFalse(drop_rel)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "radar.db"
            store = RadarStore(db_path)
            try:
                # Day 1: spike_repo has +1200 delta; cross_repo seen on Show HN
                spike = RepoItem(
                    repo_name="spike/yesterday-hit",
                    url="https://github.com/spike/yesterday-hit",
                    description="Yesterday hit",
                    language="Python",
                    stars=2000,
                    forks=120,
                    stars_delta_24h=1200,
                    sources=["GitHub Trending"],
                    discovered_at="2026-11-17T08:00:00+00:00",
                )
                cross = RepoItem(
                    repo_name="cross/multi-gem",
                    url="https://github.com/cross/multi-gem",
                    description="Cross source gem",
                    language="Rust",
                    stars=300,
                    forks=24,
                    sources=["Show HN"],
                    discovered_at="2026-11-19T08:00:00+00:00",
                )
                store.upsert_repo(spike, kept=True, seen_date="2026-11-17")
                store.upsert_repo(cross, kept=True, seen_date="2026-11-18")
                store.upsert_repo(old_release, kept=True, seen_date="2026-11-18")

                # Day 2 (2026-11-19): cross_repo also hits GitHub Trending; spike_repo not seen today
                cross_day2 = RepoItem(
                    repo_name="cross/multi-gem",
                    url="https://github.com/cross/multi-gem",
                    description="Cross source gem",
                    language="Rust",
                    stars=480,
                    forks=35,
                    stars_delta_24h=180,
                    sources=["GitHub Trending"],
                    discovered_at="2026-11-19T09:00:00+00:00",
                )
                store.upsert_repo(cross_day2, kept=True, seen_date="2026-11-19")
                store.decay_stale_deltas("2026-11-19")

                loaded = {r.canonical_key: r for r in store.load_recent_kept(limit=20)}
                self.assertEqual(loaded["spike/yesterday-hit"].stars_delta_24h, 0)
                self.assertEqual(loaded["cross/multi-gem"].sources, ["Show HN", "GitHub Trending"])
                self.assertEqual(loaded["cross/multi-gem"].stars_delta_24h, 180)
            finally:
                store.close()

            # Verify release_tag update refreshes GUID in pipeline
            base = Path(tmp) / "pipe"
            rel_v1 = RepoItem(
                repo_name="astral-sh/uv",
                url="https://github.com/astral-sh/uv",
                description="[Release 0.13.8] Fast wheel cache",
                language="Rust",
                stars=50000,
                forks=1500,
                sources=["GitHub Releases"],
                release_tag="0.13.8",
            )
            run_pipeline(cfg, base, raw_items_override=[rel_v1], translate_fn=lambda s: s, enable_ai=False, skip_enrich=True, enable_notify=False)
            xml1 = ET.fromstring((base / "feeds" / "github_radar.xml").read_text(encoding="utf-8"))
            self.assertEqual(xml1.findtext("./channel/item/guid"), "github-radar:astral-sh/uv:0.13.8")

            rel_v2 = RepoItem(
                repo_name="astral-sh/uv",
                url="https://github.com/astral-sh/uv",
                description="[Release 0.13.9] Python 3.14 support",
                language="Rust",
                stars=50200,
                forks=1510,
                sources=["GitHub Releases"],
                release_tag="0.13.9",
            )
            st_v2 = run_pipeline(cfg, base, raw_items_override=[rel_v2], translate_fn=lambda s: s, enable_ai=False, skip_enrich=True, enable_notify=False)
            self.assertEqual(st_v2["new_count"], 1)
            xml2 = ET.fromstring((base / "feeds" / "github_radar.xml").read_text(encoding="utf-8"))
            self.assertEqual(xml2.findtext("./channel/item/guid"), "github-radar:astral-sh/uv:0.13.9")

    def test_sqlite_legacy_migration_and_star_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "radar.db"
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE repos (
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
            conn.close()

            store = RadarStore(db_path)
            try:
                store.record_snapshot("foo/bar", "2026-11-15", 100)
                store.record_snapshot("foo/bar", "2026-11-18", 240)
                d24, d7 = store.compute_deltas("foo/bar", "2026-11-19", 310)
                self.assertEqual(d24, 70)
                self.assertEqual(d7, 210)
                sparks = store.load_sparklines(["foo/bar"], days=7)
                self.assertEqual(sparks["foo/bar"], [100, 240])
            finally:
                store.close()

    def test_openai_compatible_provider_with_fallback(self) -> None:
        items = [
            RepoItem(
                repo_name="acme/fast-grep",
                url="https://github.com/acme/fast-grep",
                description="Parallel regex searcher",
                language="Rust",
                stars=210,
            )
        ]
        ai_cfg = {
            "enabled": True,
            "provider": "openai",
            "base_url": "https://mock-llm.local/v1",
            "api_key": "sk-test",
            "openai_model": "gpt-4o-mini",
        }
        calls = {"count": 0}

        def fake_urlopen(req, timeout=30):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request", {}, io.BytesIO(b"unsupported response_format")
                )
            body = {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"items":[{"repo_name":"acme/fast-grep","keep":true,"category_tag":"效率工具","highlight":"用SIMD加速目录正则检索，百万行代码毫秒级返回。"}]}\n```'
                        }
                    }
                ]
            }
            return _FakeHTTPResp(json.dumps(body, ensure_ascii=False))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = generate_ai_highlights(items, ai_cfg)

        self.assertEqual(calls["count"], 2)
        self.assertIn("acme/fast-grep", res)
        self.assertEqual(res["acme/fast-grep"]["category_tag"], "效率工具")

    def test_notifier_digest_chunking_and_telegram_html(self) -> None:
        """P0-7: Telegram HTML mode safely handles underscores; WeCom/Discord auto-chunk within byte/char limits."""
        items = [
            RepoItem(
                repo_name="one/solo",
                url="https://github.com/one/solo",
                description="Solo source repo",
                language="Python",
                stars=80,
                sources=["GitHub Trending"],
            ),
            RepoItem(
                repo_name="two_org/hot_snake_gem",
                url="https://github.com/two_org/hot_snake_gem",
                description="Multi source gem",
                ai_highlight="多源同时命中的极速终端调试器 <fast & safe>。",
                category_tag="效率工具",
                language="Rust",
                stars=520,
                stars_delta_24h=190,
                sources=["GitHub Trending", "Show HN"],
            ),
        ]
        picked = filter_digest_items(items, {"min_sources": 2, "top_n": 5})
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].repo_name, "two_org/hot_snake_gem")

        status = {
            "last_run_date": "2026-11-19",
            "new_count": 2,
            "trend_summary": "终端与 Rust 工具表现活跃。",
        }
        md = build_digest_markdown(picked, status, {})
        self.assertIn("two_org/hot_snake_gem", md)
        self.assertIn("🔥多源×2", md)
        self.assertIn("+190", md)
        self.assertIn("今日风向标", md)

        feishu = build_webhook_payload("feishu", "Title", md, picked)
        self.assertEqual(feishu["msg_type"], "interactive")
        ding = build_webhook_payload("dingtalk", "Title", md, picked)
        self.assertEqual(ding["msgtype"], "markdown")
        tg = build_webhook_payload("telegram", "Title", md, picked)
        self.assertEqual(tg["parse_mode"], "HTML")
        self.assertIn("<b>two_org/hot_snake_gem</b>", tg["text"])
        self.assertIn("&lt;fast &amp; safe&gt;", tg["text"])

        # 35 verbose CJK items (> 6500 UTF-8 bytes) must split cleanly under WeCom 4096-byte and Discord 2000-char limits
        many_items = [
            RepoItem(
                repo_name=f"team_{i}/project_alpha_{i}",
                url=f"https://github.com/team_{i}/project_alpha_{i}",
                ai_highlight="这是一个把分布式日志索引压缩到单文件里的轻量检索引擎，无外部依赖开箱即用。",
                category_tag="系统底层",
                language="Rust",
                stars=1000 + i,
                stars_delta_24h=50 + i,
                sources=["GitHub Trending", "Show HN"],
            )
            for i in range(35)
        ]
        wecom_payloads = build_webhook_payloads("wecom", "Title", many_items, status)
        self.assertGreaterEqual(len(wecom_payloads), 2)
        for p in wecom_payloads:
            self.assertLessEqual(len(p["markdown"]["content"].encode("utf-8")), 4096)

        discord_payloads = build_webhook_payloads("discord", "Title", many_items, status)
        self.assertGreaterEqual(len(discord_payloads), 3)
        for p in discord_payloads:
            self.assertLessEqual(len(p["content"]), 2000)

    def test_state_gzip_restore_category_quota_breakout_and_ecosystem_outputs(self) -> None:
        """P0-6, P0-8, P1-2, P1-3, P1-5: Category anti-flooding, breakout score, XSS URL sanitization, state restore & SKILL.md."""
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)
        young = RepoItem(
            repo_name="new/rocket",
            url="https://github.com/new/rocket",
            stars=450,
            created_at=(now - timedelta(days=5)).isoformat(),
        )
        old = RepoItem(
            repo_name="old/steady",
            url="https://github.com/old/steady",
            stars=3000,
            created_at=(now - timedelta(days=600)).isoformat(),
        )
        self.assertAlmostEqual(compute_breakout_score(young, now_utc=now), 90.0)
        self.assertAlmostEqual(compute_breakout_score(old, now_utc=now), 5.0)

        # Category quota prevents 8 AI repos from occupying >4 slots in the first 10-item window
        mixed = [
            *[
                RepoItem(repo_name=f"ai/agent-{i}", url="https://github.com/ai/a", category_tag="AI应用", stars=900 - i)
                for i in range(8)
            ],
            *[
                RepoItem(repo_name=f"sys/kernel-{i}", url="https://github.com/sys/k", category_tag="系统底层", stars=200 - i)
                for i in range(6)
            ],
        ]
        balanced = apply_category_quota(mixed, 0.40, strict_drop=False)
        first_ten_ai = sum(1 for r in balanced[:10] if r.category_tag == "AI应用")
        self.assertLessEqual(first_ten_ai, 4)
        self.assertEqual(len(balanced), 14)

        # Pages URL resolution + malicious javascript: URL sanitization + state backup/restore
        cfg = self._cfg()
        with patch.dict(os.environ, {"RADAR_PUBLIC_BASE_URL": "https://demo.github.io/github-radar"}, clear=False):
            site_u, feeds_u = resolve_site_and_feeds_urls(cfg.output)
            self.assertEqual(site_u, "https://demo.github.io/github-radar")
            self.assertEqual(feeds_u, "https://demo.github.io/github-radar/feeds")

            with tempfile.TemporaryDirectory() as tmp:
                base1 = Path(tmp) / "run1"
                malicious_disc_item = RepoItem(
                    repo_name="sec/clean-tool",
                    url="javascript:alert(1)",
                    discussion_url="javascript:evil()",
                    description="Safe systems profiler in Zig",
                    language="Zig",
                    stars=260,
                    forks=19,
                    created_at=(now - timedelta(days=8)).isoformat(),
                    sources=["Show HN"],
                )
                run_pipeline(
                    cfg,
                    base1,
                    raw_items_override=[malicious_disc_item],
                    translate_fn=lambda s: s,
                    enable_ai=False,
                    skip_enrich=True,
                    enable_notify=False,
                )
                data_js = json.loads((base1 / "feeds" / "data.json").read_text(encoding="utf-8"))
                self.assertEqual(data_js["items"][0]["url"], "https://github.com/sec/clean-tool")
                self.assertEqual(data_js["items"][0]["discussion_url"], "")
                self.assertGreater(data_js["items"][0]["breakout_score"], 25.0)

                jfeed = json.loads((base1 / "feeds" / "feed.json").read_text(encoding="utf-8"))
                self.assertEqual(jfeed["version"], "https://jsonfeed.org/version/1.1")
                self.assertEqual(jfeed["feed_url"], "https://demo.github.io/github-radar/feeds/feed.json")
                self.assertNotIn("external_url", jfeed["items"][0])

                llms_txt = (base1 / "feeds" / "llms.txt").read_text(encoding="utf-8")
                self.assertIn("https://demo.github.io/github-radar/feeds/data.json", llms_txt)
                self.assertIn("sec/clean-tool", llms_txt)

                # Simulate cache eviction: restore into fresh base2 from state_backup.json.gz
                backup_gz = base1 / "feeds" / "state_backup.json.gz"
                base2 = Path(tmp) / "run2"
                store2 = RadarStore(base2 / "data" / "radar.db")
                try:
                    imported = restore_state_from_source(store2, str(backup_gz))
                    self.assertEqual(imported, 1)
                finally:
                    store2.close()

                st2 = run_pipeline(
                    cfg,
                    base2,
                    raw_items_override=[malicious_disc_item],
                    translate_fn=lambda s: s,
                    enable_ai=False,
                    skip_enrich=True,
                    enable_notify=False,
                )
                self.assertEqual(st2["new_count"], 0)

        skill_md = Path(__file__).resolve().parent.parent / "skills" / "github-radar" / "SKILL.md"
        self.assertTrue(skill_md.is_file())
        self.assertIn("name: github-radar", skill_md.read_text(encoding="utf-8"))

    def test_review_regressions_flagship_owner_repo_url_extract_notifier_and_eviction(self) -> None:
        """Regression suite for ollama/ollama flagship repos, Markdown URL extraction, oversized/surrogate notifications, GraphQL rate-limit stop, and Day-2 DB eviction."""
        cfg = self._cfg()
        now = datetime(2026, 11, 19, tzinfo=timezone.utc)

        # 1. Flagship org repos (owner == repo) like ollama/ollama and duckdb/duckdb must NOT be dropped as profile READMEs
        ollama_rel = RepoItem(
            repo_name="ollama/ollama",
            url="https://github.com/ollama/ollama",
            description="[Release v0.6.0] New multimodal runner",
            sources=["GitHub Releases"],
            release_tag="v0.6.0",
        )
        self.assertFalse(is_noisy_repo(ollama_rel, cfg.filters, now_utc=now, pre_enrich=True)[0])
        self.assertFalse(is_noisy_repo(ollama_rel, cfg.filters, now_utc=now, pre_enrich=False)[0])

        duckdb = RepoItem(
            repo_name="duckdb/duckdb",
            url="https://github.com/duckdb/duckdb",
            description="DuckDB is an analytical in-process SQL DBMS",
            language="C++",
            stars=28000,
            forks=2200,
            created_at=(now - timedelta(days=900)).isoformat(),
            metadata_enriched=True,
        )
        self.assertFalse(is_noisy_repo(duckdb, cfg.filters, now_utc=now)[0])

        # 2. Markdown links, parenthesized URLs, sentence-ending periods, and reserved paths in extract_github_repo_from_url
        self.assertEqual(
            extract_github_repo_from_url("Check [proj](https://github.com/owner/repo) now!"),
            "owner/repo",
        )
        self.assertEqual(
            extract_github_repo_from_url("Code at (https://www.github.com/owner/repo), MIT."),
            "owner/repo",
        )
        self.assertEqual(
            extract_github_repo_from_url("See https://github.com/owner/repo. Built in Zig."),
            "owner/repo",
        )
        self.assertIsNone(extract_github_repo_from_url("https://github.com/blog/2026-launch"))
        self.assertIsNone(extract_github_repo_from_url("https://github.com/features/copilot"))
        self.assertIsNone(extract_github_repo_from_url("https://github.com/users/alice/projects/1"))

        # 3. Single huge item (> 5400 UTF-8 bytes) & lone surrogates in notifier
        huge_desc = "这是一个零依赖高性能分布式向量检索引擎，" * 90 + " \ud83d <unsafe & raw>"
        huge_item = [
            RepoItem(
                repo_name="acme_corp/super_long_desc_repo",
                url="https://github.com/acme_corp/super_long_desc_repo",
                description=huge_desc,
                desc_cn=huge_desc,
                language="Rust",
                stars=1500,
                sources=["GitHub Trending", "Show HN"],
            )
        ]
        st = {"last_run_date": "2026-11-19", "new_count": 1, "trend_summary": "底层引擎持续升温。"}
        wecom_p = build_webhook_payloads("wecom", "Title", huge_item, st)
        self.assertLessEqual(len(wecom_p[0]["markdown"]["content"].encode("utf-8")), 4096)
        discord_p = build_webhook_payloads("discord", "Title", huge_item, st)
        self.assertLessEqual(len(discord_p[0]["content"]), 2000)
        tg_p = build_webhook_payloads("telegram", "Title", huge_item, st)
        self.assertLessEqual(len(tg_p[0]["text"]), 4096)
        self.assertEqual(tg_p[0]["text"].count("<b>"), tg_p[0]["text"].count("</b>"))
        self.assertEqual(tg_p[0]["text"].count("<a "), tg_p[0]["text"].count("</a>"))

        # 4. GraphQL RATE_LIMITED stops immediately without hammering subsequent 50-item chunks
        many_candidates = [
            RepoItem(repo_name=f"org/r-{i}", url=f"https://github.com/org/r-{i}", stars=0, sources=["Show HN"])
            for i in range(110)
        ]
        gql_calls = []

        def fake_gql_limited(req, timeout=20):
            gql_calls.append(req.full_url)
            return _FakeHTTPResp(
                json.dumps({"data": None, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]})
            )

        with patch("urllib.request.urlopen", side_effect=fake_gql_limited):
            enrich_github_metadata(many_candidates, github_token="ghs_limited")
        self.assertEqual(len(gql_calls), 1)

        # 5. Macro AI category normalization covers 模型训练 / 具身智能 / 端侧AI without matching EMAIL
        self.assertEqual(normalize_macro_category("模型训练"), "AI")
        self.assertEqual(normalize_macro_category("具身智能"), "AI")
        self.assertEqual(normalize_macro_category("端侧AI"), "AI")
        self.assertNotEqual(normalize_macro_category("EMAIL工具"), "AI")

        # 6. Day-1 un-enriched community item evicted when Day-2 enrichment reveals noise; proxy-decoded backup & whitelist revival
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            day1_unverified = RepoItem(
                repo_name="hn/secretly-markdown",
                url="https://github.com/hn/secretly-markdown",
                description="Interesting sounding post on HN",
                language=None,
                stars=0,
                sources=["Show HN"],
                metadata_enriched=False,
            )
            run_pipeline(
                cfg, base, raw_items_override=[day1_unverified], translate_fn=lambda s: s,
                enable_ai=False, skip_enrich=True, enable_notify=False,
            )
            d1 = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(d1["total"], 1)

            day2_enriched_markdown = RepoItem(
                repo_name="hn/secretly-markdown",
                url="https://github.com/hn/secretly-markdown",
                description="Interesting sounding post on HN",
                language=None,
                stars=45,
                sources=["Show HN"],
                metadata_enriched=True,
            )
            run_pipeline(
                cfg, base, raw_items_override=[day2_enriched_markdown], translate_fn=lambda s: s,
                enable_ai=False, skip_enrich=True, enable_notify=False,
            )
            d2 = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(d2["total"], 0)

            # Whitelist revives kept=0 row in reapply_filters, and plain JSON bytes import works
            cfg_wl = self._cfg()
            cfg_wl.filters = dict(cfg_wl.filters)
            cfg_wl.filters["whitelist_repos"] = ["hn/secretly-markdown"]
            exp_wl = export_only(cfg_wl, base)
            self.assertEqual(exp_wl["feed_total"], 1)

            store_plain = RadarStore(base / "plain.db")
            try:
                plain_bytes = json.dumps(
                    {
                        "schema": 1,
                        "repos": [
                            {
                                "canonical_key": "ok/plain-json",
                                "repo_name": "ok/plain-json",
                                "sources_json": "CORRUPTED_NOT_JSON",
                                "kept": 1,
                            }
                        ],
                        "snapshots": [],
                    }
                ).encode("utf-8")
                self.assertEqual(store_plain.import_state_gzip_bytes(plain_bytes), 1)
                self.assertEqual(len(store_plain.load_recent_kept(limit=5)), 1)
            finally:
                store_plain.close()

    def test_stage1_classifier_timezone_negcache_and_rsshub_urls(self) -> None:
        """Stage 1 regressions: deterministic Chinese classifier, Asia/Shanghai cross-day timezone, negative cache API savings, and custom RSSHub domains."""
        cfg = self._cfg()
        self.assertFalse((cfg.sources.get("reddit_rss") or {}).get("enabled", True))
        self.assertEqual(int(cfg.filters.get("min_stars") or 0), 5)

        # 1. Deterministic local classifier covers diverse domains without false-matching substrings like 'email' or 'container'
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="acme/mcp-bridge",
                    url="u",
                    description="Connect Claude Code agents via MCP",
                    topics=["mcp", "claude-code"],
                    language="TypeScript",
                )
            ),
            "AI应用",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="lab/fast-gguf",
                    url="u",
                    description="4-bit quantization and GGUF inference engine",
                    sources=["HuggingFace Papers"],
                    language="C++",
                )
            ),
            "模型推理",
        )
        # Word-boundary check: 'email', 'domain', 'raid', 'container' must NOT false-trigger 'ai'
        non_ai_mail = classify_repo_category(
            RepoItem(
                repo_name="ops/container-mail-relay",
                url="u",
                description="Fast SMTP email relay for custom domain raid storage",
                language="Go",
            )
        )
        self.assertNotIn(non_ai_mail, {"AI应用", "模型推理"})
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="sec/wireguard-mesh",
                    url="u",
                    description="Zero-trust WireGuard VPN tunnel and firewall",
                    language="Go",
                )
            ),
            "安全网络",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="db/parquet-olap",
                    url="u",
                    description="Embedded columnar SQLite and Parquet OLAP engine",
                    language="Rust",
                )
            ),
            "数据存储",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="sys/riscv-hypervisor",
                    url="u",
                    description="Bare-metal RISC-V microvm hypervisor and eBPF verifier",
                    language="C",
                )
            ),
            "系统底层",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="art/retro-synth",
                    url="u",
                    description="8-bit pixel-art synthesizer and MIDI sequencer",
                    language="Rust",
                )
            ),
            "创意玩具",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="mac/menubar-clip",
                    url="u",
                    description="Native macOS menu-bar clipboard and screenshot manager",
                    language="Swift",
                )
            ),
            "桌面工具",
        )
        self.assertEqual(
            classify_repo_category(
                RepoItem(
                    repo_name="web/solid-router",
                    url="u",
                    description="Type-safe UI component-library and state-management for SolidJS",
                    language="TypeScript",
                )
            ),
            "开发框架",
        )

        # 2. Self-hosted RSSHub URL builder supports custom domains without 'rsshub' substring
        self.assertEqual(
            build_subreddit_rss_url("https://www.reddit.com", "opensource"),
            "https://www.reddit.com/r/opensource/hot.rss?limit=20",
        )
        self.assertEqual(
            build_subreddit_rss_url("https://rss.mydomain.com/reddit/subreddit", "SideProject"),
            "https://rss.mydomain.com/reddit/subreddit/SideProject",
        )
        self.assertEqual(
            build_subreddit_rss_url("https://rss.mydomain.com", "selfhosted"),
            "https://rss.mydomain.com/reddit/subreddit/selfhosted",
        )
        self.assertEqual(
            build_subreddit_rss_url("https://feeds.example.org/sub/{subreddit}/rss", "opensource"),
            "https://feeds.example.org/sub/opensource/rss",
        )

        # 3. Unified Asia/Shanghai business timezone at 19:30 UTC (03:30+08:00 next day) + category backfill on legacy empty rows
        tz_sh = resolve_timezone({"timezone": "Asia/Shanghai"})
        utc_1930 = datetime(2026, 11, 19, 19, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(utc_1930.astimezone(tz_sh).strftime("%Y-%m-%d"), "2026-11-20")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Pre-seed a legacy kept=1 row with empty category_tag (simulating online restored state_backup)
            store = RadarStore(base / "data" / "radar.db")
            try:
                legacy_item = RepoItem(
                    repo_name="legacy/ebpf-tracer",
                    url="https://github.com/legacy/ebpf-tracer",
                    description="Linux kernel eBPF syscall profiler",
                    language="C",
                    stars=220,
                    forks=18,
                    sources=["Show HN"],
                    discovered_at="2026-11-19T08:00:00+08:00",
                )
                store.upsert_repo(legacy_item, kept=True, seen_date="2026-11-19")
            finally:
                store.close()

            fresh_cli = RepoItem(
                repo_name="cli/tmux- dash".replace(" ", ""),
                url="https://github.com/cli/tmux-dash",
                description="Fast terminal multiplexer dashboard",
                language="Go",
                stars=85,
                forks=7,
                sources=["GitHub Trending"],
            )
            st = run_pipeline(
                cfg,
                base,
                raw_items_override=[fresh_cli],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=utc_1930,
            )
            self.assertEqual(st["last_run_date"], "2026-11-20")
            self.assertTrue(st["last_run_at"].startswith("2026-11-20T03:30:00+08:00"))
            self.assertEqual(st["run_kept_count"], 1)
            self.assertEqual(st["dropped_count"], 0)

            web_js = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            by_key = {it["key"]: it for it in web_js["items"]}
            self.assertEqual(by_key["legacy/ebpf-tracer"]["category_tag"], "系统底层")
            self.assertEqual(by_key["cli/tmux-dash"]["category_tag"], "效率工具")
            self.assertTrue(
                by_key["cli/tmux-dash"]["discovered_at"].startswith("2026-11-20T03:30:00+08:00")
            )

            # 4. Negative cache: newly enriched 0-star noisy repo is cached with kept=0 so Day-2 skips GraphQL/REST API enrichment
            neg_base = Path(tmp) / "neg"
            hn_raw = [
                RepoItem(
                    repo_name="hn/zero-star-empty",
                    url="https://github.com/hn/zero-star-empty",
                    description="Brand new empty repo posted on HN",
                    stars=0,
                    language=None,
                    sources=["Show HN"],
                )
            ]
            api_calls: list[str] = []

            def fake_collect(_src_cfg, _net_cfg, health_out=None):
                if health_out is not None:
                    health_out["hackernews_show"] = {"ok": True, "count": 1, "duration_ms": 50, "error": ""}
                return [
                    RepoItem(
                        repo_name=hn_raw[0].repo_name,
                        url=hn_raw[0].url,
                        description=hn_raw[0].description,
                        stars=0,
                        language=None,
                        sources=["Show HN"],
                    )
                ]

            def fake_enrich(items_to_enrich, **_kwargs):
                for it in items_to_enrich:
                    api_calls.append(it.canonical_key)
                    it.stars = 1  # Below min_stars=5
                    it.forks = 0
                    it.language = "Python"
                    it.created_at = "2026-11-20T01:00:00Z"
                    it.metadata_enriched = True
                return len(items_to_enrich)

            with patch("radar.pipeline.collect_all_sources", side_effect=fake_collect):
                with patch("radar.pipeline.enrich_github_metadata", side_effect=fake_enrich):
                    st_day1 = run_pipeline(
                        cfg,
                        neg_base,
                        translate_fn=lambda s: s,
                        enable_ai=False,
                        enable_notify=False,
                        now_utc=utc_1930,
                    )
                    self.assertEqual(api_calls, ["hn/zero-star-empty"])
                    self.assertEqual(st_day1["run_kept_count"], 0)
                    self.assertEqual(st_day1["dropped_count"], 1)

                    # Day 2: same un-enriched Show HN item hits negative cache BEFORE enrich_github_metadata
                    api_calls.clear()
                    st_day2 = run_pipeline(
                        cfg,
                        neg_base,
                        translate_fn=lambda s: s,
                        enable_ai=False,
                        enable_notify=False,
                        now_utc=utc_1930 + timedelta(days=1),
                    )
                    self.assertEqual(api_calls, [])
                    self.assertEqual(st_day2["run_kept_count"], 0)
                    self.assertEqual(st_day2["dropped_count"], 1)

            # Day 3: repo blows up on GitHub Trending with 160 stars -> revives cleanly to kept=1!
            surged = RepoItem(
                repo_name="hn/zero-star-empty",
                url="https://github.com/hn/zero-star-empty",
                description="Brand new repo that surged overnight with real community forks",
                language="Python",
                stars=160,
                forks=14,
                sources=["GitHub Trending"],
            )
            st_day3 = run_pipeline(
                cfg,
                neg_base,
                raw_items_override=[surged],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=utc_1930 + timedelta(days=2),
            )
            self.assertEqual(st_day3["run_kept_count"], 1)
            self.assertEqual(st_day3["new_count"], 1)

    def test_review_round_zero_star_reapply_ai_rejected_negcache_and_cli_paths(self) -> None:
        """Regression suite for enriched 0-star purge, ai_rejected vs positive highlight revival, rate-limited neg_cache, fallback trend refresh, and CLI paths."""
        cfg = self._cfg()
        now = datetime(2026, 11, 20, 3, 30, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. Enriched 0-star Show HN / HF Papers repos in radar.db MUST be purged by reapply_filters when min_stars=5
            base_zero = root / "zero_purge"
            store = RadarStore(base_zero / "data" / "radar.db")
            try:
                enriched_zero_hn = RepoItem(
                    repo_name="hn/enriched-zero-star",
                    url="https://github.com/hn/enriched-zero-star",
                    description="Weekend empty scaffold on Show HN",
                    language="Python",
                    stars=0,
                    forks=0,
                    created_at="2026-11-19T12:30:42Z",
                    sources=["Show HN"],
                    metadata_enriched=True,
                    discovered_at="2026-11-19T20:30:00+08:00",
                )
                unverified_zero_hn = RepoItem(
                    repo_name="hn/unverified-quota-exempt",
                    url="https://github.com/hn/unverified-quota-exempt",
                    description="Unenriched community post when API quota ran out",
                    language=None,
                    stars=0,
                    created_at="",
                    sources=["Show HN"],
                    metadata_enriched=False,
                    discovered_at="2026-11-19T20:30:00+08:00",
                )
                store.upsert_repo(enriched_zero_hn, kept=True, seen_date="2026-11-19")
                store.upsert_repo(unverified_zero_hn, kept=True, seen_date="2026-11-19")
                purged_n = store.reapply_filters(cfg.filters, now_utc=now)
                self.assertEqual(purged_n, 1)
                remaining_keys = [x.canonical_key for x in store.load_recent_kept(limit=10)]
                self.assertEqual(remaining_keys, ["hn/unverified-quota-exempt"])
            finally:
                store.close()

            # 2a. Positive ai_highlight repo temporarily dropped by tightened min_stars MUST revive cleanly (not blackholed)
            base_hl = root / "hl_revive"
            good_repo = RepoItem(
                repo_name="org/highlighted-gem",
                url="https://github.com/org/highlighted-gem",
                description="High-speed terminal multiplexer in Rust",
                language="Rust",
                stars=20,
                forks=3,
                created_at="2026-11-15T00:00:00Z",
                sources=["GitHub Trending"],
                metadata_enriched=True,
            )
            run_pipeline(
                cfg,
                base_hl,
                raw_items_override=[good_repo],
                translate_fn=lambda s: s,
                ai_runner=lambda _p: json.dumps(
                    {
                        "items": [
                            {
                                "repo_name": "org/highlighted-gem",
                                "keep": True,
                                "category_tag": "效率工具",
                                "highlight": "单二进制极速终端分屏器，启动耗时低于 3ms。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                enable_ai=True,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now,
            )
            tight_cfg = self._cfg()
            tight_cfg.filters = {**tight_cfg.filters, "min_stars": 100}
            export_only(tight_cfg, base_hl)
            d_tight = json.loads((base_hl / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(d_tight["total"], 0)

            # Repo surges to 260 stars -> revives with its original positive ai_highlight intact!
            surged_good = RepoItem(
                repo_name="org/highlighted-gem",
                url="https://github.com/org/highlighted-gem",
                description="High-speed terminal multiplexer in Rust",
                language="Rust",
                stars=260,
                forks=22,
                sources=["GitHub Trending"],
            )
            st_revived = run_pipeline(
                tight_cfg,
                base_hl,
                raw_items_override=[surged_good],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now + timedelta(days=1),
            )
            self.assertEqual(st_revived["run_kept_count"], 1)
            d_revived = json.loads((base_hl / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(d_revived["total"], 1)
            self.assertIn("单二进制极速终端分屏器", d_revived["items"][0]["ai_highlight"])

            # 2b. Backfill AI rejection (keep=False with empty highlight="") MUST set ai_rejected=1 and stay blocked
            base_bf = root / "bf_spam"
            disguised_spam = RepoItem(
                repo_name="spam/disguised-wrapper",
                url="https://github.com/spam/disguised-wrapper",
                description="A seemingly normal Python tool",
                language="Python",
                stars=80,
                forks=10,
                created_at="2026-11-18T00:00:00Z",
                sources=["GitHub Trending"],
                metadata_enriched=True,
            )
            run_pipeline(
                cfg,
                base_bf,
                raw_items_override=[disguised_spam],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now,
            )

            def reject_backfill_runner(_prompt: str) -> str:
                return json.dumps(
                    {
                        "items": [
                            {
                                "repo_name": "spam/disguised-wrapper",
                                "keep": False,
                                "category_tag": "效率工具",
                                "highlight": "",
                            }
                        ]
                    }
                )

            run_pipeline(
                cfg,
                base_bf,
                raw_items_override=[],
                translate_fn=lambda s: s,
                ai_runner=reject_backfill_runner,
                enable_ai=True,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now + timedelta(hours=1),
            )
            # Next day spam reappears on Trending -> must remain blocked by ai_rejected=1
            st_spam_next = run_pipeline(
                cfg,
                base_bf,
                raw_items_override=[disguised_spam],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now + timedelta(days=1),
            )
            self.assertEqual(st_spam_next["run_kept_count"], 0)
            self.assertEqual(st_spam_next["dropped_count"], 1)

            # 3. Rate-limited un-enriched Trending item on Day 1 MUST NOT poison Day-2 enrichment in neg_cache,
            #    and relaxing min_stars from 5 -> 1 carries forward cached enriched metadata!
            base_rl = root / "rate_limit_neg"
            un_enriched_trending = RepoItem(
                repo_name="sys/no-html-lang-zig",
                url="https://github.com/sys/no-html-lang-zig",
                description="Lock-free ring buffer",
                language=None,
                stars=90,
                forks=8,
                sources=["GitHub Trending"],
                metadata_enriched=False,
            )

            def collect_trending(_s, _n, health_out=None):
                return [
                    RepoItem(
                        repo_name=un_enriched_trending.repo_name,
                        url=un_enriched_trending.url,
                        description=un_enriched_trending.description,
                        language=None,
                        stars=90,
                        forks=8,
                        sources=["GitHub Trending"],
                        metadata_enriched=False,
                    )
                ]

            enrich_attempts: list[str] = []

            def rate_limited_day1(items_list, **_kw):
                enrich_attempts.extend(x.canonical_key for x in items_list)
                return 0  # Simulated 429 rate-limit before enrichment succeeded

            def healthy_day2(items_list, **_kw):
                for x in items_list:
                    enrich_attempts.append(x.canonical_key)
                    x.language = "Zig"
                    x.created_at = "2026-11-18T00:00:00Z"
                    x.metadata_enriched = True
                return len(items_list)

            with patch("radar.pipeline.collect_all_sources", side_effect=collect_trending):
                with patch("radar.pipeline.enrich_github_metadata", side_effect=rate_limited_day1):
                    st_rl1 = run_pipeline(
                        cfg, base_rl, translate_fn=lambda s: s, enable_ai=False, enable_notify=False, now_utc=now
                    )
                    self.assertEqual(st_rl1["run_kept_count"], 0)
                enrich_attempts.clear()
                with patch("radar.pipeline.enrich_github_metadata", side_effect=healthy_day2):
                    st_rl2 = run_pipeline(
                        cfg,
                        base_rl,
                        translate_fn=lambda s: s,
                        enable_ai=False,
                        enable_notify=False,
                        now_utc=now + timedelta(days=1),
                    )
                    self.assertEqual(enrich_attempts, ["sys/no-html-lang-zig"])
                    self.assertEqual(st_rl2["run_kept_count"], 1)

            # Relaxing min_stars from 5 -> 1 hydrates cached 2-star Show HN repo even when skip_enrich=True
            base_relax = root / "relax_min_stars"
            raw_hn_unverified = RepoItem(
                repo_name="hn/two-star-rust-cli",
                url="https://github.com/hn/two-star-rust-cli",
                description="Tiny Rust binary hex viewer",
                language=None,
                stars=0,
                sources=["Show HN"],
                metadata_enriched=False,
            )

            def collect_hn_two_star(_s, _n, health_out=None):
                return [
                    RepoItem(
                        repo_name=raw_hn_unverified.repo_name,
                        url=raw_hn_unverified.url,
                        description=raw_hn_unverified.description,
                        language=None,
                        stars=0,
                        sources=["Show HN"],
                    )
                ]

            def enrich_to_two_stars(items_list, **_kw):
                for x in items_list:
                    x.stars = 2
                    x.forks = 1
                    x.language = "Rust"
                    x.created_at = "2026-11-19T10:00:00Z"
                    x.metadata_enriched = True
                return len(items_list)

            with patch("radar.pipeline.collect_all_sources", side_effect=collect_hn_two_star):
                with patch("radar.pipeline.enrich_github_metadata", side_effect=enrich_to_two_stars):
                    st_drop_d1 = run_pipeline(
                        cfg,
                        base_relax,
                        translate_fn=lambda s: s,
                        enable_ai=False,
                        enable_notify=False,
                        now_utc=now,
                    )
                    self.assertEqual(st_drop_d1["run_kept_count"], 0)

            relaxed_cfg = self._cfg()
            relaxed_cfg.filters = {**relaxed_cfg.filters, "min_stars": 1}
            st_relaxed = run_pipeline(
                relaxed_cfg,
                base_relax,
                raw_items_override=[raw_hn_unverified],
                translate_fn=lambda s: s,
                enable_ai=False,
                skip_enrich=True,
                enable_notify=False,
                now_utc=now + timedelta(days=1),
            )
            self.assertEqual(st_relaxed["run_kept_count"], 1)
            d_relaxed = json.loads((base_relax / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(d_relaxed["items"][0]["stars"], 2)
            self.assertEqual(d_relaxed["items"][0]["language"], "Rust")

            # 4. Stale fallback trend_summary refreshes on export_only after purging <5★ and backfilling categories
            base_ts = root / "trend_refresh"
            store_ts = RadarStore(base_ts / "data" / "radar.db")
            try:
                store_ts.upsert_repo(
                    RepoItem(
                        repo_name="sys/ebpf-top",
                        url="https://github.com/sys/ebpf-top",
                        description="Real-time eBPF kernel monitor",
                        language="C",
                        stars=150,
                        forks=12,
                        created_at="2026-11-18T00:00:00Z",
                        sources=["Show HN"],
                        metadata_enriched=True,
                        discovered_at="2026-11-20T03:30:00+08:00",
                    ),
                    kept=True,
                    seen_date="2026-11-20",
                )
            finally:
                store_ts.close()
            (base_ts / "data" / "status.json").write_text(
                json.dumps(
                    {
                        "last_run_at": "2026-11-20T01:22:31+00:00",
                        "last_run_date": "2026-11-20",
                        "trend_summary": "本期在榜 116 个项目，主力语言集中在 Python（42）。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            st_exp = export_only(cfg, base_ts)
            self.assertIn("本期在榜 1 个项目", st_exp["trend_summary"])
            self.assertIn("系统底层", st_exp["trend_summary"])

            # 5. Packaged CLI resolve_runtime_paths resolves CWD config before site-packages script_dir
            fake_site_packages = root / "site_packages"
            fake_site_packages.mkdir()
            user_workspace = root / "user_workspace"
            user_workspace.mkdir()
            user_cfg = user_workspace / "config.yaml"
            user_cfg.write_text("filters:\n  min_stars: 5\n", encoding="utf-8")
            resolved_base, resolved_cfg = resolve_runtime_paths(
                "config.yaml", cwd=user_workspace, script_dir=fake_site_packages
            )
            self.assertEqual(resolved_base, user_workspace.resolve())
            self.assertEqual(resolved_cfg, user_cfg.resolve())


if __name__ == "__main__":
    unittest.main()
