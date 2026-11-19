from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import xml.etree.ElementTree as ET

from radar.config import RadarConfig
from radar.filters import deduplicate_and_filter, is_noisy_repo
from radar.models import RepoItem
from radar.notifier import build_digest_markdown, build_webhook_payload, filter_digest_items
from radar.pipeline import RadarStore, export_only, run_pipeline
from radar.sources import (
    parse_github_releases_atom,
    parse_github_trending_html,
    parse_huggingface_papers_json,
)
from radar.translator import generate_ai_highlights


class _FakeHTTPResp:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status
        self.headers = {}

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

    def test_notifier_digest_and_payloads(self) -> None:
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
                repo_name="two/hot-gem",
                url="https://github.com/two/hot-gem",
                description="Multi source gem",
                ai_highlight="多源同时命中的极速终端调试器。",
                category_tag="效率工具",
                language="Rust",
                stars=520,
                stars_delta_24h=190,
                sources=["GitHub Trending", "Show HN"],
            ),
        ]
        picked = filter_digest_items(items, {"min_sources": 2, "top_n": 5})
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].repo_name, "two/hot-gem")

        md = build_digest_markdown(picked, {"last_run_date": "2026-11-19", "new_count": 2}, {})
        self.assertIn("two/hot-gem", md)
        self.assertIn("🔥多源×2", md)
        self.assertIn("+190", md)

        feishu = build_webhook_payload("feishu", "Title", md, picked)
        self.assertEqual(feishu["msg_type"], "interactive")
        ding = build_webhook_payload("dingtalk", "Title", md, picked)
        self.assertEqual(ding["msgtype"], "markdown")
        tg = build_webhook_payload("telegram", "Title", md, picked)
        self.assertEqual(tg["parse_mode"], "Markdown")


if __name__ == "__main__":
    unittest.main()
