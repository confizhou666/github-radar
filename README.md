# GitHub Radar · 每日开源精选画刊

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/Dependencies-PyYAML%20Only-4C8E3B)](#本地快速开始与-docker-部署)
[![Frontend](https://img.shields.io/badge/Frontend-Zero--Build%20HTML%2FCSS%2FJS-E66A43)](#效果展示)
[![GitHub Pages](https://img.shields.io/badge/Deploy-GitHub%20Actions%20%2B%20Pages-2088FF)](#零服务器部署github-actions--pages)
[![License](https://img.shields.io/badge/License-MIT-black)](./LICENSE)

**GitHub Radar** 是一个轻量级、支持**零服务器部署**的开源项目发现与中文导读工具。它每天自动聚合 **GitHub Trending、GitHub 定向检索（低星黑马）、关注开发者 Star 动态、Show HN、HelloGitHub、HuggingFace Daily Papers、GitHub Releases、Reddit 及自定义 RSS** 等多路来源，完成跨源共振加权、黑名单与反刷星过滤、**24h/7d Star 增速追踪**、免费中文翻译及可选大模型一句话导读，最终输出 **杂志风静态 Web 画刊**、**标准 RSS 2.0 订阅源** 与 **多平台 Webhook 每日简报**。

---

## 核心特性

- **多源雷达聚合与共振**：
  - 覆盖 GitHub Trending（精准解析仓库简介与今日新增 Star）、GitHub Search（新锐黑马 / 冷门宝藏）、指定开发者 Star 追踪、Hacker News `Show HN`、HelloGitHub、**HuggingFace Daily Papers**（附 GitHub 仓库的论文项目）、**GitHub Releases Atom 追踪**、Reddit（支持自定义 RSSHub 前缀）及自定义 RSS/Atom。
  - 跨源按 `owner/repo` 合并去重，标出 `多源×N` 共振权重，并按 `(多源命中数, 24h 增速, 总 Star)` 优先排序；支持可选 `GITHUB_TOKEN` 与 `RADAR_PROXY` 代理配置，优先补全缺语言的非 API 源与 `topics` / `license` / `open_issues` / `pushed_at`。
- **硬核去噪 + 白名单/作者黑名单**：
  - 自动过滤 Profile README（`owner == repo`）、纯 Markdown 仓库、过老僵尸仓库与异常刷星项目（`anti_star_farm`）。
  - 支持 `whitelist_repos`（特例放行）、`blacklist_owners`（屏蔽营销号/高频刷榜账号）、`min_stars` 与可配置关键词黑名单（拦截 `awesome-*` 合集、八股面试、毕设模板、引流卖课与灰产脚本）。
- **SQLite 增量缓存与 Star 增速追踪**：
  - `data/radar.db` 兼容旧库平滑迁移，通过 `repo_snapshots` 每日快照自动计算 `stars_delta_24h` 与 `stars_delta_7d`，精准捕捉突然飙升的黑马项目。
- **免费中文翻译 + GitHub Models / 通用大模型 / `agy` 三引擎**：
  - 内置免费 Google `gtx` + MyMemory 多线程并发翻译，统一支持 `network.proxy` / `RADAR_PROXY` 代理，自动跳过已是中文的项目。
  - AI 分块全量导读、存量无标回填与「今日开源风向标」支持 **GitHub Models 免费大模型**（Fork 后直接复用内置 `GITHUB_TOKEN`，零密钥开箱即用）、标准 **OpenAI 兼容接口**（DeepSeek / OpenAI / Gemini / Ollama）与本地 `agy` CLI。
- **Web 画刊、RSS 2.0、JSON Feed 1.1 与 AI Agent 生态**：
  - **Web 画刊**：零构建纯静态前端，含刊首「今日风向标」、信源健康度监控、分类防霸榜配额（`max_category_share`）、7 日原生 SVG 迷你趋势图（Sparkline）、`🚀 黑马爆发度` 排序、URL 状态深链分享、详情抽屉（含 GitHub OpenGraph 预览卡、零限流 CDN README 预览、一键 `git clone`）及本地阅读备份。
  - **多制式订阅与 Agent 直读**：同时输出 4 份标准 RSS 2.0 分榜 XML、动态 `feeds.opml`、**JSON Feed 1.1 (`feeds/feed.json`)**、站点根目录 **`/llms.txt`**，并内置零后端 [`skills/github-radar/SKILL.md`](skills/github-radar/SKILL.md) 供 Claude Code / Cursor / Antigravity 直接查询。
  - **消息推送**：支持通用 Webhook、飞书交互卡片、钉钉、企业微信（4096 字节自动分片）、Discord（2000 字符自动分片）、Telegram（HTML 安全转义模式）每日简报推送。

---

## 效果展示

### 浅色画刊视图（时间轴分组 · 多源共振 · 24h Star 增速）

![GitHub Radar 浅色首页效果](docs/screenshots/home-light.png)

### 今日精选 Markdown 简报弹窗

![GitHub Radar 今日简报弹窗](docs/screenshots/digest-modal.png)

### 深色阅读模式 & 增强详情抽屉

![GitHub Radar 深色详情抽屉](docs/screenshots/detail-drawer.png)

---

## 工作流程

```text
多源并发采集 (Trending / Search / Starred / Show HN / HelloGitHub / HF Papers / Releases / Reddit & RSS)
   │
   ▼
跨源合并去重 + 本地零成本黑白名单预筛 (merge_raw_items + prefilter_merged_items)
   │
   ▼
GitHub 元信息批量补充 (GraphQL v4 50仓/批 + REST 容错回退，补全 0★ / 语言 / Topics / 创仓时间)
   │
   ▼
完整反刷星与结构去噪过滤 (拦截 ~383★ 盗版安装器壳仓 · 词边界防误伤 · Releases 年龄豁免)
   │
   ▼
SQLite 增量比对 & 每日 Star 快照 (data/radar.db) ──► 计算 24h / 7d 增速 + 非活跃增速衰减清零
   │
   ▼
多线程并发中文翻译 & 分块 AI 全量导读 + 存量回填 (OpenAI 兼容 / GitHub Models / agy CLI)
   │
   ▼
分类防霸榜配额 ──► 生成 data.json + feed.json + 4 路 RSS + feeds.opml + llms.txt + 状态压缩备份 + 分片通知
```

---

## 目录结构

```text
github-radar/
├── .github/workflows/
│   └── radar.yml           # GitHub Actions 定时采集（含 models:read 与双重状态持久化）与 Pages 部署
├── skills/github-radar/
│   └── SKILL.md            # 零后端 AI Agent Skill（直接读取线上 Pages JSON / llms.txt）
├── run.py                  # CLI 入口（--no-ai / --export-only / --restore-state / --notify-dry-run / --no-notify）
├── config.yaml             # 网络代理、数据源、黑白名单、翻译、AI 引擎、分类配额与推送配置
├── requirements.txt        # Python 依赖（PyYAML>=6.0）
├── docker-compose.yml      # Nginx 静态画刊与 Feeds 服务编排
├── nginx.conf              # Nginx 站点、/llms.txt、/feeds.opml 与 /feeds/ 跨域缓存配置
├── feeds.opml              # RSS 分榜 OPML 导入模板（流水线同时动态生成 feeds/feeds.opml）
├── radar/
│   ├── config.py           # YAML 配置加载
│   ├── models.py           # RepoItem 数据模型
│   ├── sources.py          # 多线程多源抓取、信源健康度与 GraphQL/REST 元信息补全
│   ├── filters.py          # 跨源去重、词边界黑白名单、反刷星与黑马爆发度计算
│   ├── translator.py       # 并发中文翻译、三引擎分块 AI 导读与「今日开源风向标」
│   ├── feed_builder.py     # 分类防霸榜配额、RSS 2.0、JSON Feed 1.1、OPML、llms.txt 与 data.json
│   ├── notifier.py         # 飞书 / 钉钉 / 企微 / Discord / Telegram(HTML) 按字节安全分片推送
│   └── pipeline.py         # SQLite 持久化、增速衰减、存量回填、状态备份恢复与主流水线编排
├── web/
│   ├── index.html          # 画刊页面结构（含今日风向标、信源健康度、黑马排序）
│   ├── styles.css          # 浅色/深色主题、Sparkline、OpenGraph 预览卡与响应式样式
│   └── app.js              # 筛选、URL 深链同步、抽屉、零限流 CDN README 预览与阅读备份
└── tests/
    └── test_radar.py       # 反刷星、词边界、GraphQL、AI 分块回填、增速衰减、分片通知与状态恢复测试
```

---

## 零服务器部署（GitHub Actions + Pages）

仓库已自带 [.github/workflows/radar.yml](.github/workflows/radar.yml)，Fork 本仓库即可零成本托管：

1. 进入仓库 **Settings → Pages**，将 **Source** 设置为 **GitHub Actions**。
2. 工作流默认已声明 `models: read` 权限，**无需配置任何 API Key** 即可通过内置 `GITHUB_TOKEN` 自动调用 **GitHub Models (`openai/gpt-4o-mini`)** 生成中文亮点与今日风向标，并通过 GitHub GraphQL 批量补全仓库元信息；同时随 `_site` 自动备份压缩状态快照，防止 Actions Cache 过期导致重复推送。
3. 如需自定义外部模型或消息通知，可在 **Settings → Secrets and variables → Actions** 中添加可选变量：
   - `RADAR_AI_API_KEY`：自定义 OpenAI 兼容大模型 API Key（配置后优先于 GitHub Models）
   - `RADAR_AI_BASE_URL`：接口地址（如 DeepSeek、SiliconFlow 或自建网关，默认 `https://api.openai.com/v1`）
   - `RADAR_AI_MODEL`：模型名（默认 `gpt-4o-mini` / GitHub Models 自动映射 `openai/gpt-4o-mini`）
   - `RADAR_WEBHOOK_URL`：开启 `notifications.enabled: true` 后用于接收飞书 / 钉钉 / 企微 / Discord 简报
   - `RADAR_TG_BOT_TOKEN` / `RADAR_TG_CHAT_ID`：Telegram 机器人推送配置
4. 进入 **Actions → GitHub Radar Daily Build & Pages → Run workflow** 手动触发首次构建；之后每天北京时间 `03:30` 自动采集更新并发布画刊、RSS、`feed.json` 与 `/llms.txt`。

---

## 本地快速开始与 Docker 部署

```bash
git clone https://github.com/confizhou666/github-radar.git
cd github-radar
pip install -r requirements.txt

# 仅使用内置免费翻译（不调用 AI 亮点生成）
python run.py --no-ai

# 启用 AI 导读并终端预览每日简报（不触发外部 Webhook）
python run.py --notify-dry-run

# 不抓取网络，仅从已有 data/radar.db 重新导出 RSS 与 data.json
python run.py --export-only
```

Docker Compose 部署静态画刊：

```bash
docker compose up -d
# 访问 http://localhost:8088/
```

---

## 配置说明（`config.yaml`）

| 配置项 | 说明 |
| --- | --- |
| `network` | 可选 `proxy`（或 `RADAR_PROXY`）与 `github_token_env`（默认读取 `GITHUB_TOKEN`），提升 API 配额 |
| `sources.github_trending` | 抓取语言列表（`""` 为全语言总榜）、时间维度（`daily`/`weekly`/`monthly`）与条数上限 |
| `sources.github_search.queries` | 自定义多组雷达检索策略（创建天数、活跃天数、Star 区间等） |
| `sources.starred_users` | 填入你想追踪的 GitHub 用户名列表，收集他们最近 Star 的项目 |
| `sources.huggingface_papers` | 抓取 HuggingFace Daily Papers 中附带开源仓库的前沿 AI 项目 |
| `sources.github_releases` | 通过 Atom 订阅追踪指定仓库的最新 Release 动态（零 API 配额消耗） |
| `sources.reddit_rss` / `extra_rss_urls` | Subreddit 订阅（支持配置 `rss_base_url` 为自建 RSSHub）与任意自定义 RSS/Atom |
| `filters` | 配置 `whitelist_repos`、`blacklist_owners`、`min_stars`、`anti_star_farm` 与关键词黑名单 |
| `translation` | 启用/禁用免费中文翻译（默认 `google_gtx` + MyMemory 备用） |
| `ai_highlight` | 支持 `provider: auto \| openai \| github_models \| agy`、分块导读、存量回填与 `base_url`/`openai_model` |
| `notifications` | 配置推送开关、`provider`（`generic`/`feishu`/`dingtalk`/`wecom`/`discord`/`telegram`）、分类配额与自动分片 |
| `output` | 控制 RSS/Web 最大展示条数、`max_category_share` 防霸榜配额及 `public_base_url` |

---

## Web 快捷键一览

| 快捷键 | 功能 |
| --- | --- |
| `/` | 聚焦顶部搜索框 |
| `D` | 切换浅色 / 深色阅读模式 |
| `←` / `→` | 在详情抽屉中切换上一篇 / 下一篇 |
| `S` | 在详情抽屉中收藏 / 取消收藏当前项目 |
| `Esc` | 关闭详情抽屉、弹窗或退出搜索框聚焦 |

---

## 运行测试

```bash
python -m unittest discover -s tests -v
```

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 开源。
