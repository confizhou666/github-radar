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
- **免费中文翻译 + 通用大模型 API / `agy` 双引擎**：
  - 内置免费 Google `gtx` + MyMemory 翻译兜底，自动跳过已是中文的项目。
  - AI 一句话导读与语义去噪支持标准 **OpenAI 兼容接口**（DeepSeek / OpenAI / Gemini / Ollama，纯标准库零额外依赖）与本地 `agy` CLI；未配置密钥时自动降级。
- **Web 画刊、RSS 2.0 与消息推送**：
  - **Web 画刊**：零构建纯静态前端，支持按发现日期时间轴分组、`多源共振` 筛选、`24h 增速最快` 排序、主题胶囊与语言搜索、滑出式详情抽屉（含 `Topics`、开源协议、Issues、最近提交、在线 README 预览、一键 `git clone`）、`今日简报` Markdown 弹窗、收藏夹导出 Markdown 以及本地阅读记录 JSON 导入/导出备份。
  - **RSS 2.0**：输出全源聚合及 3 个互不重复分榜 XML（`all_discoveries.xml`、`github_trending.xml`、`github_radar.xml`、`community_picks.xml`）及 `feeds.opml`，订阅地址自动适配 GitHub Pages 子路径。
  - **消息推送**：支持通用 Webhook、飞书交互卡片、钉钉、企业微信、Discord、Telegram 每日简报推送（支持 `--notify-dry-run` 终端预览）。

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
多源采集 (Trending / Search / Starred / Show HN / HelloGitHub / HF Papers / Releases / Reddit & RSS)
   │
   ▼
GitHub 元信息补充 (enrich_github_metadata，支持 GITHUB_TOKEN 与未认证配额保护)
   │
   ▼
跨源合并去重 + 白名单 / 作者黑名单 / 关键词 / 反刷星过滤 (radar/filters.py)
   │
   ▼
SQLite 增量比对 & 每日 Star 快照 (data/radar.db) ──► 计算 24h / 7d 增速
   │
   ▼
仅对新仓库执行中文翻译 & 批量 AI 亮点提炼 (OpenAI 兼容 API / agy CLI)
   │
   ▼
生成 feeds/data.json + 4 份 RSS 2.0 XML ──► GitHub Pages / Nginx 画刊 + Webhook 简报推送
```

---

## 目录结构

```text
github-radar/
├── .github/workflows/
│   └── radar.yml           # GitHub Actions 定时采集与 GitHub Pages 自动部署
├── run.py                  # CLI 入口（--no-ai / --export-only / --notify-dry-run / --no-notify）
├── config.yaml             # 网络代理、数据源、黑白名单、翻译、AI 引擎与推送配置
├── requirements.txt        # Python 依赖（PyYAML>=6.0）
├── docker-compose.yml      # Nginx 静态画刊与 Feeds 服务编排
├── nginx.conf              # Nginx 站点与 /feeds/ 跨域、缓存配置
├── feeds.opml              # RSS 分榜 OPML 导入模板（可按需替换服务地址）
├── radar/
│   ├── config.py           # YAML 配置加载
│   ├── models.py           # RepoItem 数据模型
│   ├── sources.py          # 多源抓取与 GitHub 元信息补充
│   ├── filters.py          # 跨源去重与黑白名单去噪
│   ├── translator.py       # 免费中文翻译与双引擎 AI 亮点生成
│   ├── feed_builder.py     # RSS 2.0 XML 与 Web data.json 生成
│   ├── notifier.py         # 飞书 / 钉钉 / 企微 / Discord / Telegram / 通用 Webhook 推送
│   └── pipeline.py         # SQLite 持久化、快照增速计算与主流水线编排
├── web/
│   ├── index.html          # 画刊页面结构
│   ├── styles.css          # 浅色/深色主题、时间轴与弹窗样式
│   └── app.js              # 筛选、抽屉、README 预览、简报生成、备份恢复与快捷键
└── tests/
    └── test_radar.py       # 解析、去噪、迁移快照、OpenAI 兼容接口与推送单元测试
```

---

## 零服务器部署（GitHub Actions + Pages）

仓库已自带 [.github/workflows/radar.yml](.github/workflows/radar.yml)，Fork 本仓库即可零成本托管：

1. 进入仓库 **Settings → Pages**，将 **Source** 设置为 **GitHub Actions**。
2. 按需在 **Settings → Secrets and variables → Actions** 中添加环境变量（全部可选）：
   - `RADAR_AI_API_KEY`：OpenAI 兼容大模型 API Key（不填则自动跳过 AI 导读，保留免费中文翻译）
   - `RADAR_AI_BASE_URL`：接口地址（如 DeepSeek、SiliconFlow 或自建网关，默认 `https://api.openai.com/v1`）
   - `RADAR_AI_MODEL`：模型名（默认 `gpt-4o-mini`）
   - `RADAR_WEBHOOK_URL`：开启 `notifications.enabled: true` 后用于接收飞书 / 钉钉 / 企微 / Discord 简报
   - `RADAR_TG_BOT_TOKEN` / `RADAR_TG_CHAT_ID`：Telegram 机器人推送配置
3. 进入 **Actions → GitHub Radar Daily Build & Pages → Run workflow** 手动触发首次构建；之后每天北京时间 `03:30` 自动采集更新并发布站点与 RSS。

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
| `ai_highlight` | 支持 `provider: auto \| openai \| agy`、兼容接口 `base_url`/`openai_model` 与 `agy` 路径 |
| `notifications` | 配置推送开关、`provider`（`generic`/`feishu`/`dingtalk`/`wecom`/`discord`/`telegram`）与筛选条件 |
| `output` | 控制 RSS/Web 最大展示条数及 RSS `<link>` 使用的 `public_base_url` |

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
