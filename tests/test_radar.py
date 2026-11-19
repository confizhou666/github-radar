from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from radar.config import RadarConfig
from radar.filters import deduplicate_and_filter, is_noisy_repo
from radar.models import RepoItem
from radar.pipeline import export_only, run_pipeline
from radar.sources import parse_github_trending_html


class TestGitHubRadar(unittest.TestCase):
    def _cfg(self) -> RadarConfig:
        return RadarConfig.load(Path(__file__).resolve().parent.parent / "config.yaml")

    def test_trending_html_parsing(self) -> None:
        sample_html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed"><a href="/cool/magic-cli">cool / magic-cli</a></h2>
          <p class="col-9 color-fg-muted my-1 pr-4">Turn natural language into safe shell pipelines</p>
          <span itemprop="programmingLanguage">Rust</span>
          <a href="/cool/magic-cli/stargazers">1.2k</a>
          <a href="/cool/magic-cli/forks">88</a>
        </article>
        """
        items = parse_github_trending_html(sample_html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].repo_name, "cool/magic-cli")
        self.assertEqual(items[0].language, "Rust")
        self.assertEqual(items[0].stars, 1200)
        self.assertEqual(items[0].forks, 88)

    def test_blacklist_and_structural_filters(self) -> None:
        cfg = self._cfg()
        now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        noisy_cases = [
            RepoItem(repo_name="foo/awesome-python", url="u", description="A curated list of libs", language="Python"),
            RepoItem(repo_name="foo/java-interview", url="u", description="Java八股文面试高频题汇总", language="Java"),
            RepoItem(repo_name="foo/spring-shop", url="u", description="基于SpringBoot的高校毕业设计管理系统", language="Java"),
            RepoItem(repo_name="foo/chatgpt-prompts", url="u", description="1000 awesome prompts for副业变现", language="JavaScript"),
            RepoItem(repo_name="foo/free-nodes", url="u", description="每日免费节点clash v2ray订阅", language="Python"),
            RepoItem(repo_name="foo/only-markdown", url="u", description="Just an essay collection", language=None),
            RepoItem(repo_name="alice/alice", url="u", description="My GitHub profile readme", language="HTML"),
            RepoItem(
                repo_name="old/zombie-tool",
                url="u",
                description="Ancient utility still decent",
                language="Go",
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
            drop, reason = is_noisy_repo(case, cfg.filters, now_utc=now)
            self.assertTrue(drop, f"Expected noisy drop for {case.repo_name}, got ok")
            self.assertNotEqual(reason, "ok")

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
        drop, reason = is_noisy_repo(good, cfg.filters, now_utc=now)
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
            self.assertIn("创意玩具", titles[0])
            desc_html = root.findtext("./channel/item/description") or ""
            self.assertIn("把FM合成器塞进终端里实时编曲", desc_html)
            self.assertIn("GitHub Trending + Show HN", desc_html)
            self.assertIn("https://news.ycombinator.com/item?id=999", desc_html)

            # Trending + Show HN merged repo must exist ONLY in github_trending.xml (no cross-feed dupe)
            tr_xml = ET.fromstring((base / "feeds" / "github_trending.xml").read_text(encoding="utf-8"))
            cm_xml = ET.fromstring((base / "feeds" / "community_picks.xml").read_text(encoding="utf-8"))
            self.assertEqual(len(tr_xml.findall("./channel/item")), 1)
            self.assertEqual(len(cm_xml.findall("./channel/item")), 0)

            # Verify static web JSON payload and export_only path
            web_json = json.loads((base / "feeds" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(web_json["total"], 1)
            self.assertEqual(web_json["items"][0]["repo_name"], "Indie/PocketSynth")
            self.assertEqual(web_json["items"][0]["owner"], "Indie")
            self.assertEqual(web_json["items"][0]["name"], "PocketSynth")
            self.assertEqual(web_json["items"][0]["bucket"], "trending")
            self.assertEqual(web_json["items"][0]["category_tag"], "创意玩具")

            (base / "feeds" / "data.json").unlink()
            exp_stats = export_only(cfg, base)
            self.assertEqual(exp_stats["feed_total"], 1)
            self.assertTrue((base / "feeds" / "data.json").exists())

            # Second run with same repo yields new_count == 0
            stats2 = run_pipeline(
                cfg,
                base,
                raw_items_override=raw[:1],
                translate_fn=lambda s: f"[译]{s}",
                ai_runner=fake_ai,
                enable_ai=True,
                skip_enrich=True,
            )
            self.assertEqual(stats2["new_count"], 0)


if __name__ == "__main__":
    unittest.main()
