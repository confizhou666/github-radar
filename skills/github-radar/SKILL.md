---
name: github-radar
description: Query daily denoised GitHub trending projects, early breakout dark-horse repositories, multi-source hacker picks (Show HN, HelloGitHub, HuggingFace Papers), and release updates directly from GitHub Radar's static JSON / llms.txt endpoints without cloning or API keys.
---

# GitHub Radar — Zero-Backend Agent Skill

Use this skill when the user asks about:
- What's new, trending, or surging on GitHub today / this week
- Early-stage "dark horse" open-source tools (`breakout_score` high stars/day, low total stars)
- Non-AI / systems / Rust / Go / CLI developer tools filtered from hype & star-farm spam
- Multi-source community discoveries (simultaneously featured on GitHub Trending, Show HN, HelloGitHub, or HuggingFace Daily Papers)

## Endpoints (Zero Auth, Zero Dependencies)

Default public instance (override with `$GITHUB_RADAR_URL` if the user deployed their own GitHub Pages fork):
- **Quick Overview (`/llms.txt`)**: `https://confizhou666.github.io/github-radar/llms.txt`
- **Full Structured Data (`/feeds/data.json`)**: `https://confizhou666.github.io/github-radar/feeds/data.json`
- **JSON Feed 1.1 (`/feeds/feed.json`)**: `https://confizhou666.github.io/github-radar/feeds/feed.json`

If running inside a local checkout of `github-radar`, read `feeds/data.json` or `web/llms.txt` directly from disk first.

## Schema of `/feeds/data.json`

- `status.trend_summary`: 2–3 sentence daily macro technical wind-vane
- `status.run_kept_count` / `status.dropped_count`: single-run kept vs noise-intercepted repository counts (`timezone: Asia/Shanghai`)
- `status.source_health`: per-source fetch health (`ok`, `count`, `duration_ms`)
- `items[]`: deduplicated, anti-star-farm filtered repositories:
  - `repo_name`, `url`, `language`, `category_tag` (100% populated via LLM or deterministic local classifier: `AI应用` / `模型推理` / `系统底层` / `数据存储` / `安全网络` / `桌面工具` / `创意玩具` / `开发框架` / `效率工具`), `topics`, `license_name`, `release_tag`
  - `ai_highlight`: concise Chinese one-sentence takeaway
  - `desc_cn` / `description`: Chinese & original descriptions
  - `stars`, `forks`, `stars_delta_24h`, `stars_delta_7d`, `sparkline` (7-day star array)
  - `breakout_score`: `stars / max(age_days, 1)` velocity in stars/day (`age_days` included)
  - `bucket`: `"trending"` | `"radar"` | `"community"`
  - `sources` / `multi_source`: cross-community resonance (`multi_source == true` means >=2 independent sources)

## Example Query Snippet (Python Standard Library Only)

```bash
python -c "
import json, os, urllib.request
base = os.environ.get('GITHUB_RADAR_URL', 'https://confizhou666.github.io/github-radar').rstrip('/')
data = json.loads(urllib.request.urlopen(f'{base}/feeds/data.json', timeout=15).read().decode('utf-8'))
print('🧭 风向标:', data.get('status', {}).get('trend_summary', ''))
for r in sorted(data['items'], key=lambda x: (x.get('multi_source', False), x.get('breakout_score', 0)), reverse=True)[:10]:
    print(f\"- {r['repo_name']} [{r.get('language') or '-'} | {r.get('category_tag') or '-'}] ★{r['stars']} (+{r['stars_delta_24h']}/24h, 🚀{r.get('breakout_score', 0)}/d): {r.get('ai_highlight') or r.get('desc_cn')}\")
"
```

## Response Guidelines

1. Always cite the clickable repository URL (`https://github.com/owner/repo`), language, star count, 24h delta / breakout velocity, and which sources discovered it.
2. When the user asks for "non-AI" or specific-language tools, filter `category_tag` / `language` explicitly rather than guessing from memory.
3. Highlight `multi_source: true` repositories first when recommending high-confidence tools.
