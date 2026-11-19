from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import subprocess

from radar.config import RadarConfig
from radar.pipeline import RadarStore, export_only, restore_state_from_source, run_pipeline
from radar.sources import resolve_proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("github-radar")


def refresh_freshrss(user: str = "admin") -> None:
    try:
        res = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "www-data",
                "github-radar-freshrss",
                "php",
                "./cli/actualize-user.php",
                "--user",
                user,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode == 0:
            logger.info("已触发 FreshRSS (%s) 立即抓取最新订阅", user)
        else:
            logger.debug("FreshRSS 刷新返回 %s: %s", res.returncode, res.stderr.strip())
    except Exception as exc:
        logger.debug("跳过 FreshRSS 立即刷新: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub Discovery & Translation Radar")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--no-ai", action="store_true", help="Disable batched AI highlights")
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Rebuild feeds, JSON Feed, llms.txt, and web JSON from existing SQLite DB only",
    )
    parser.add_argument(
        "--restore-state",
        default="",
        help="Optional local path or URL to state_backup.json.gz to import before running",
    )
    parser.add_argument("--no-notify", action="store_true", help="Disable webhook notifications")
    parser.add_argument(
        "--notify-dry-run", action="store_true", help="Print notification digest without sending"
    )
    parser.add_argument(
        "--freshrss-user", default="admin", help="FreshRSS username to refresh if container is running"
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    cfg = RadarConfig.load(base_dir / args.config)

    if args.restore_state:
        store = RadarStore(base_dir / "data" / "radar.db")
        try:
            restored = restore_state_from_source(
                store, args.restore_state, proxy=resolve_proxy(cfg.network)
            )
            logger.info("从 %s 导入了 %s 条状态记录", args.restore_state, restored)
        finally:
            store.close()

    if args.export_only:
        stats = export_only(cfg, base_dir)
        logger.info("仅导出完成: %s", json.dumps(stats, ensure_ascii=False))
        return

    stats = run_pipeline(
        cfg,
        base_dir,
        enable_ai=not args.no_ai,
        enable_notify=not args.no_notify,
        notify_dry_run=args.notify_dry_run,
    )
    logger.info("执行完成: %s", json.dumps(stats, ensure_ascii=False))
    refresh_freshrss(user=args.freshrss_user)


if __name__ == "__main__":
    main()
