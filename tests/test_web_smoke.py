from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def _build_clean_browser_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def _build_smoke_fixture() -> dict:
    items = [
        {
            "key": "astral-sh/uv",
            "repo_name": "astral-sh/uv",
            "owner": "astral-sh",
            "name": "uv",
            "url": "https://github.com/astral-sh/uv",
            "description": "An extremely fast Python package and project manager, written in Rust.",
            "desc_cn": "用 Rust 编写的极速 Python 包与项目管理器。",
            "ai_highlight": "把 pip、venv 和锁文件合并成一个极速单二进制工具。",
            "category_tag": "效率工具",
            "language": "Rust",
            "stars": 52000,
            "forks": 1520,
            "open_issues": 310,
            "stars_delta_24h": 420,
            "stars_delta_7d": 2100,
            "breakout_score": 85.4,
            "age_days": 609.0,
            "community_score": 0,
            "sparkline": [49900, 50500, 51580, 52000],
            "topics": ["python", "rust", "packaging"],
            "license_name": "MIT",
            "release_tag": "0.14.0",
            "created_at": "2025-03-20T00:00:00Z",
            "pushed_at": "2026-11-20T01:00:00Z",
            "sources": ["GitHub Trending", "GitHub Releases"],
            "multi_source": True,
            "bucket": "trending",
            "discussion_url": "https://github.com/astral-sh/uv/releases/tag/0.14.0",
            "discovered_at": "2026-11-20T03:30:00+08:00",
        },
        {
            "key": "acme/mcp-orchestrator",
            "repo_name": "acme/mcp-orchestrator",
            "owner": "acme",
            "name": "mcp-orchestrator",
            "url": "https://github.com/acme/mcp-orchestrator",
            "description": "Multi-agent MCP router for local coding workflows",
            "desc_cn": "面向本地编码工作流的多智能体 MCP 路由器",
            "ai_highlight": "零配置串联多个本地 MCP 工具服务。",
            "category_tag": "AI应用",
            "language": "TypeScript",
            "stars": 640,
            "forks": 45,
            "open_issues": 8,
            "stars_delta_24h": 210,
            "stars_delta_7d": 640,
            "breakout_score": 160.0,
            "age_days": 4.0,
            "community_score": 96,
            "sparkline": [120, 430, 640],
            "topics": ["mcp", "agent"],
            "license_name": "Apache-2.0",
            "release_tag": "",
            "created_at": "2026-11-16T00:00:00Z",
            "pushed_at": "2026-11-20T02:00:00Z",
            "sources": ["GitHub Trending", "Show HN"],
            "multi_source": True,
            "bucket": "trending",
            "discussion_url": "https://news.ycombinator.com/item?id=88888",
            "discovered_at": "2026-11-20T03:30:00+08:00",
        },
        {
            "key": "kernel-lab/ebpf-guard",
            "repo_name": "kernel-lab/ebpf-guard",
            "owner": "kernel-lab",
            "name": "ebpf-guard",
            "url": "https://github.com/kernel-lab/ebpf-guard",
            "description": "Zero-overhead Linux syscall firewall built on eBPF",
            "desc_cn": "基于 eBPF 的零开销 Linux 系统调用防火墙",
            "ai_highlight": "",
            "category_tag": "系统底层",
            "language": "C",
            "stars": 390,
            "forks": 28,
            "open_issues": 5,
            "stars_delta_24h": 95,
            "stars_delta_7d": 390,
            "breakout_score": 78.0,
            "age_days": 5.0,
            "community_score": 0,
            "sparkline": [295, 390],
            "topics": ["ebpf", "linux-kernel"],
            "license_name": "GPL-2.0",
            "release_tag": "",
            "created_at": "2026-11-15T00:00:00Z",
            "pushed_at": "2026-11-19T22:00:00Z",
            "sources": ["关注开发者:torvalds"],
            "multi_source": False,
            "bucket": "radar",
            "discussion_url": "",
            "discovered_at": "2026-11-20T03:30:00+08:00",
        },
        {
            "key": "indie/custom-rss-gem",
            "repo_name": "indie/custom-rss-gem",
            "owner": "indie",
            "name": "custom-rss-gem",
            "url": "https://github.com/indie/custom-rss-gem",
            "description": "Self-hosted terminal RSS reader in Go",
            "desc_cn": "Go 编写的自托管终端 RSS 阅读器",
            "ai_highlight": "",
            "category_tag": "效率工具",
            "language": "Go",
            "stars": 280,
            "forks": 19,
            "open_issues": 3,
            "stars_delta_24h": 40,
            "stars_delta_7d": 120,
            "breakout_score": 28.0,
            "age_days": 10.0,
            "community_score": 0,
            "sparkline": [240, 280],
            "topics": ["rss", "tui"],
            "license_name": "MIT",
            "release_tag": "",
            "created_at": "2026-11-10T00:00:00Z",
            "pushed_at": "2026-11-19T18:00:00Z",
            "sources": ["自定义RSS"],
            "multi_source": False,
            "bucket": "community",
            "discussion_url": "https://blog.example.com/post-1",
            "discovered_at": "2026-11-20T03:30:00+08:00",
        },
        {
            "key": "oss/reddit-pick",
            "repo_name": "oss/reddit-pick",
            "owner": "oss",
            "name": "reddit-pick",
            "url": "https://github.com/oss/reddit-pick",
            "description": "Lightweight Docker log viewer",
            "desc_cn": "轻量级 Docker 容器日志查看器",
            "ai_highlight": "",
            "category_tag": "效率工具",
            "language": "Go",
            "stars": 510,
            "forks": 33,
            "open_issues": 6,
            "stars_delta_24h": 0,
            "stars_delta_7d": 80,
            "breakout_score": 17.0,
            "age_days": 30.0,
            "community_score": 0,
            "sparkline": [490, 510],
            "topics": ["docker"],
            "license_name": "MIT",
            "release_tag": "",
            "created_at": "2026-10-21T00:00:00Z",
            "pushed_at": "2026-11-18T12:00:00Z",
            "sources": ["Reddit:r/selfhosted"],
            "multi_source": False,
            "bucket": "community",
            "discussion_url": "",
            "discovered_at": "2026-11-19T03:30:00+08:00",
        },
        {
            "key": "prev/trending-day1-a",
            "repo_name": "prev/trending-day1-a",
            "owner": "prev",
            "name": "trending-day1-a",
            "url": "https://github.com/prev/trending-day1-a",
            "description": "Yesterday trending shader playground",
            "desc_cn": "昨日上榜的着色器实时演练场",
            "ai_highlight": "",
            "category_tag": "系统底层",
            "language": "Rust",
            "stars": 920,
            "forks": 64,
            "open_issues": 9,
            "stars_delta_24h": 0,
            "stars_delta_7d": 410,
            "breakout_score": 46.0,
            "age_days": 20.0,
            "community_score": 0,
            "sparkline": [700, 920],
            "topics": ["vulkan", "shader"],
            "license_name": "MIT",
            "release_tag": "",
            "created_at": "2026-10-31T00:00:00Z",
            "pushed_at": "2026-11-18T09:00:00Z",
            "sources": ["GitHub Trending"],
            "multi_source": False,
            "bucket": "trending",
            "discussion_url": "",
            "discovered_at": "2026-11-19T03:30:00+08:00",
        },
        {
            "key": "prev/trending-day1-b",
            "repo_name": "prev/trending-day1-b",
            "owner": "prev",
            "name": "trending-day1-b",
            "url": "https://github.com/prev/trending-day1-b",
            "description": "Yesterday trending AI prompt router",
            "desc_cn": "昨日上榜的本地模型网关",
            "ai_highlight": "",
            "category_tag": "AI应用",
            "language": "Python",
            "stars": 1100,
            "forks": 85,
            "open_issues": 11,
            "stars_delta_24h": 0,
            "stars_delta_7d": 500,
            "breakout_score": 55.0,
            "age_days": 20.0,
            "community_score": 0,
            "sparkline": [800, 1100],
            "topics": ["llm"],
            "license_name": "MIT",
            "release_tag": "",
            "created_at": "2026-10-31T00:00:00Z",
            "pushed_at": "2026-11-18T09:00:00Z",
            "sources": ["GitHub Trending"],
            "multi_source": False,
            "bucket": "trending",
            "discussion_url": "",
            "discovered_at": "2026-11-19T03:30:00+08:00",
        },
        {
            "key": "radar/dark-horse-db",
            "repo_name": "radar/dark-horse-db",
            "owner": "radar",
            "name": "dark-horse-db",
            "url": "https://github.com/radar/dark-horse-db",
            "description": "Embedded MVCC key-value store in 2000 lines of Zig",
            "desc_cn": "2000 行 Zig 实现的嵌入式 MVCC 键值存储",
            "ai_highlight": "",
            "category_tag": "系统底层",
            "language": "Zig",
            "stars": 310,
            "forks": 21,
            "open_issues": 2,
            "stars_delta_24h": 65,
            "stars_delta_7d": 310,
            "breakout_score": 103.3,
            "age_days": 3.0,
            "community_score": 0,
            "sparkline": [245, 310],
            "topics": ["zig", "database"],
            "license_name": "MIT",
            "release_tag": "",
            "created_at": "2026-11-17T00:00:00Z",
            "pushed_at": "2026-11-20T01:30:00Z",
            "sources": ["雷达:新锐黑马"],
            "multi_source": False,
            "bucket": "radar",
            "discussion_url": "",
            "discovered_at": "2026-11-20T03:30:00+08:00",
        },
    ]

    # Single-run stats intentionally differ from cumulative 8-item multi-day pool to test Bug 1 & Bug 2 fixes
    status = {
        "timezone": "Asia/Shanghai",
        "last_run_at": "2026-11-20T03:30:00+08:00",
        "last_run_date": "2026-11-20",
        "raw_count": 20,
        "unique_count": 18,
        "run_kept_count": 13,
        "dropped_count": 5,
        "new_count": 4,
        "feed_total": 8,
        "max_category_share": 0.4,
        "trend_summary": "Rust 与 eBPF 底层工具领跑本期热度，astral-sh/uv 与 acme/mcp-orchestrator 同时命中多路信源。",
        "source_health": {
            "github_trending": {"ok": True, "count": 2, "duration_ms": 420, "error": ""},
            "github_search": {"ok": True, "count": 6, "duration_ms": 610, "error": ""},
            "starred_users": {"ok": True, "count": 2, "duration_ms": 310, "error": ""},
            "hackernews_show": {"ok": True, "count": 3, "duration_ms": 280, "error": ""},
            "reddit_rss": {"ok": True, "count": 3, "duration_ms": 350, "error": ""},
        },
    }
    return {
        "generated_at": "2026-11-20T03:30:00+08:00",
        "status": status,
        "total": len(items),
        "items": items,
    }


class TestWebFrontendSmoke(unittest.TestCase):
    def test_v7_frontend_headless_smoke_and_regressions(self) -> None:
        require_browser = os.environ.get("RADAR_REQUIRE_BROWSER_SMOKE", "").strip() == "1"
        if not HAS_PLAYWRIGHT:
            if require_browser:
                self.fail("RADAR_REQUIRE_BROWSER_SMOKE=1 is set, but playwright is not installed.")
            self.skipTest("playwright not installed; install .[dev] to run browser smoke tests.")

        repo_root = Path(__file__).resolve().parent.parent
        web_src = repo_root / "web"

        with tempfile.TemporaryDirectory() as tmp:
            site_dir = Path(tmp)
            shutil.copytree(web_src, site_dir, dirs_exist_ok=True)
            feeds_dir = site_dir / "feeds"
            feeds_dir.mkdir(parents=True, exist_ok=True)

            fixture = _build_smoke_fixture()
            (feeds_dir / "data.json").write_text(
                json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (feeds_dir / "feed.json").write_text(
                json.dumps({"version": "https://jsonfeed.org/version/1.1", "items": []}),
                encoding="utf-8",
            )
            (feeds_dir / "all_discoveries.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Test</title></channel></rss>',
                encoding="utf-8",
            )
            (site_dir / "llms.txt").write_text("# GitHub Radar\n", encoding="utf-8")

            handler = partial(_QuietHandler, directory=str(site_dir))
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            port = httpd.server_address[1]
            server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            server_thread.start()

            page_errors: list[str] = []
            console_errors: list[str] = []

            try:
                clean_env = _build_clean_browser_env()
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=["--no-proxy-server"],
                        env=clean_env,
                    )
                    try:
                        context = browser.new_context(viewport={"width": 1440, "height": 900})

                        def _mock_external(route):
                            url = route.request.url
                            if "raw.githubusercontent.com" in url:
                                malicious_md = (
                                    "# UV Fast Manager\n\n"
                                    "<script>window.__xss_executed = true;</script>\n\n"
                                    "- Safe **bold** item with [bad link](javascript:alert(1)) and [docs](docs/guide.md)\n"
                                )
                                route.fulfill(
                                    status=200,
                                    content_type="text/plain; charset=utf-8",
                                    body=malicious_md,
                                )
                                return
                            if "opengraph.githubassets.com" in url:
                                route.fulfill(status=404, body="")
                                return
                            route.continue_()

                        context.route("**/*", _mock_external)
                        page = context.new_page()
                        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                        page.on(
                            "console",
                            lambda msg: console_errors.append(msg.text)
                            if msg.type == "error" and "404" not in msg.text
                            else None,
                        )

                        base_url = f"http://127.0.0.1:{port}"
                        page.goto(base_url, wait_until="networkidle")
                        page.wait_for_selector(".signal-entry")

                        # 1. SEO & OpenGraph metadata checks
                        og_title = page.locator('meta[property="og:title"]').get_attribute("content")
                        og_img = page.locator('meta[property="og:image"]').get_attribute("content")
                        tw_card = page.locator('meta[name="twitter:card"]').get_attribute("content")
                        self.assertIn("GitHub Radar", og_title or "")
                        self.assertTrue((og_img or "").endswith("/og-preview.png"))
                        self.assertEqual(tw_card, "summary_large_image")

                        # 2. Bug 1 regression: single-run vernier gauge vs multi-day cumulative pool
                        self.assertEqual(page.locator("#rivalryKeptVal").inner_text().strip(), "13")
                        self.assertEqual(page.locator("#rivalryDroppedVal").inner_text().strip(), "5")
                        raw_meta = page.locator("#rivalryRawMeta").inner_text().strip()
                        self.assertIn("本轮捕获 20 仓（去重 18）", raw_meta)
                        self.assertIn("留存率 72%", raw_meta)
                        self.assertIn("累计在榜 8", raw_meta)
                        self.assertEqual(
                            page.locator('[data-count="all"]').inner_text().strip(), "8"
                        )

                        # 3. Bug 2 regression: channel pill tooltip never shows inverted "原始捕获 2 → 去噪留存 4"
                        trending_btn = page.locator('#sourceHealthPill button[data-source="github_trending"]')
                        trending_tip = trending_btn.get_attribute("title") or ""
                        self.assertIn("本轮抓取 2 仓 · 当前在榜 4 仓", trending_tip)
                        self.assertNotIn("原始捕获", trending_tip)

                        # 4. Bug 3 regression: starred_users channel button displays Chinese label & filters accurately
                        starred_btn = page.locator('#sourceHealthPill button[data-source="starred_users"]')
                        self.assertIn("关注开发者", starred_btn.inner_text())
                        starred_btn.click()
                        self.assertEqual(page.locator(".signal-entry").count(), 1)
                        self.assertIn(
                            "ebpf-guard", page.locator(".signal-entry").first.inner_text()
                        )
                        starred_btn.click()
                        self.assertEqual(page.locator(".signal-entry").count(), 8)

                        # 5. Bug 4 regression: reddit_rss matches both "Reddit:..." and "自定义RSS"
                        rss_btn = page.locator('#sourceHealthPill button[data-source="reddit_rss"]')
                        rss_btn.click()
                        self.assertEqual(page.locator(".signal-entry").count(), 2)
                        rss_texts = page.locator("#signalStream").inner_text()
                        self.assertIn("custom-rss-gem", rss_texts)
                        self.assertIn("reddit-pick", rss_texts)
                        rss_btn.click()

                        # 6. Category ribbon, multi-source toggle, sort, and dark mode toggle
                        cat_chips = page.locator("#categoryRibbon .cat-chip")
                        self.assertGreaterEqual(cat_chips.count(), 4)
                        page.locator('#categoryRibbon .cat-chip[data-cat="系统底层"]').click()
                        self.assertEqual(page.locator(".signal-entry").count(), 3)
                        page.locator("#btnResetFilters").click()
                        self.assertEqual(page.locator(".signal-entry").count(), 8)

                        page.keyboard.press("d")
                        self.assertEqual(
                            page.locator("html").get_attribute("data-theme"), "dark"
                        )
                        self.assertTrue(page.locator("#themeIconMoon").is_visible())
                        self.assertTrue(page.locator("#themeIconSun").is_hidden())
                        page.keyboard.press("d")
                        self.assertEqual(
                            page.locator("html").get_attribute("data-theme"), "light"
                        )
                        self.assertTrue(page.locator("#themeIconSun").is_visible())
                        self.assertTrue(page.locator("#themeIconMoon").is_hidden())

                        # 7. Detail drawer + safe Markdown README preview (XSS stripped) + keyboard save & close
                        page.locator('.signal-entry[data-key="astral-sh/uv"]').click()
                        page.wait_for_selector("#detailDrawer.is-open")
                        page.locator("#btnLoadReadme").click()
                        page.wait_for_function(
                            "() => document.getElementById('readmeContent')?.innerText.includes('UV Fast Manager')"
                        )
                        xss_flag = page.evaluate("() => window.__xss_executed || false")
                        self.assertFalse(xss_flag)
                        readme_html = page.locator("#readmeContent").inner_html()
                        self.assertNotIn("<script", readme_html.lower())
                        self.assertNotIn("javascript:", readme_html.lower())
                        self.assertIn("https://github.com/astral-sh/uv/blob/HEAD/docs/guide.md", readme_html)

                        page.keyboard.press("s")
                        self.assertEqual(
                            page.locator('[data-count="saved"]').inner_text().strip(), "1"
                        )
                        page.keyboard.press("Escape")
                        page.wait_for_function(
                            "() => document.getElementById('detailDrawer')?.getAttribute('aria-hidden') === 'true'"
                        )

                        # 8. Daily Digest modal & RSS modal
                        page.locator("#btnDigestModal").click()
                        self.assertFalse(page.locator("#digestBackdrop").is_hidden())
                        digest_val = page.locator("#digestTextarea").input_value()
                        self.assertIn("astral-sh/uv", digest_val)
                        page.keyboard.press("Escape")
                        self.assertTrue(page.locator("#digestBackdrop").is_hidden())

                        # 9. Legacy ?source=starred_developers deep-link alias compatibility
                        page.goto(f"{base_url}/?source=starred_developers", wait_until="networkidle")
                        self.assertEqual(page.locator(".signal-entry").count(), 1)

                        # 10. Mobile 390x844 responsive check: zero horizontal overflow + filter drawer toggle
                        page.set_viewport_size({"width": 390, "height": 844})
                        page.goto(base_url, wait_until="networkidle")
                        overflow_px = page.evaluate(
                            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                        )
                        self.assertLessEqual(overflow_px, 0)
                        page.locator("#btnMobileFilters").click()
                        self.assertEqual(
                            page.locator("#btnMobileFilters").get_attribute("aria-expanded"),
                            "true",
                        )

                        self.assertEqual(page_errors, [])
                        self.assertEqual(console_errors, [])
                        context.close()

                        # 11. Overseas visitor timezone (America/Los_Angeles) + legacy/zero-fetch status edge cases
                        us_ctx = browser.new_context(
                            viewport={"width": 1280, "height": 800},
                            timezone_id="America/Los_Angeles",
                        )
                        us_ctx.route("**/*", _mock_external)
                        us_page = us_ctx.new_page()
                        us_page.goto(base_url, wait_until="networkidle")
                        us_page.wait_for_selector(".issue-divider-title")
                        lead_divider = us_page.locator(".issue-divider-title").first.inner_text()
                        self.assertIn("2026-11-20", lead_divider)

                        # Legacy status without run_kept_count where dropped_count > raw_count
                        legacy_fixture = json.loads(json.dumps(fixture, ensure_ascii=False))
                        legacy_fixture["status"].pop("run_kept_count", None)
                        legacy_fixture["status"]["raw_count"] = 30
                        legacy_fixture["status"]["unique_count"] = 25
                        legacy_fixture["status"]["dropped_count"] = 40
                        (feeds_dir / "data.json").write_text(
                            json.dumps(legacy_fixture, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        us_page.reload(wait_until="networkidle")
                        legacy_meta = us_page.locator("#rivalryRawMeta").inner_text().strip()
                        self.assertIn("本轮捕获 40 仓", legacy_meta)
                        self.assertIn("留存率 0%", legacy_meta)

                        # Zero-fetch run with historical items falls back cleanly to pool count
                        zero_fetch_fixture = json.loads(json.dumps(fixture, ensure_ascii=False))
                        zero_fetch_fixture["status"]["raw_count"] = 0
                        zero_fetch_fixture["status"]["unique_count"] = 0
                        zero_fetch_fixture["status"]["run_kept_count"] = 0
                        zero_fetch_fixture["status"]["dropped_count"] = 0
                        (feeds_dir / "data.json").write_text(
                            json.dumps(zero_fetch_fixture, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        us_page.reload(wait_until="networkidle")
                        self.assertEqual(
                            us_page.locator("#rivalryKeptVal").inner_text().strip(), "8"
                        )
                        us_ctx.close()
                    finally:
                        browser.close()
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
