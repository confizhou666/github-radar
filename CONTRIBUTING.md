# 贡献指南 (Contributing to GitHub Radar)

感谢你对 **GitHub Radar** 的关注与贡献！本项目致力于维持**极简依赖、零服务器可部署、高信噪比去噪与安全原生前端**的工程克制度。在提交 Issue 或 Pull Request 前，请阅读以下约定。

---

## 核心架构红线

1. **后端零膨胀依赖**：生产运行时仅依赖 Python 3.9+ 标准库与 `PyYAML>=6.0`，请勿引入重量级第三方网络或 ORM 框架（开发/测试工具放入 `pyproject.toml` 的 `[project.optional-dependencies] dev`）。
2. **前端零构建**：`web/` 目录采用原生 HTML / CSS / JavaScript，无需 Node 打包构建即可直接托管在 GitHub Pages 或 Nginx。
3. **默认安全防 XSS**：任何来自外部信源（GitHub 简介、RSS、README Markdown）的文本与 URL 必须经过 `sanitize_http_url` / `safeHttpUrl` 白名单校验与 HTML 转义，严禁直接拼接未清洗 HTML。

---

## 本地开发环境搭建

```bash
git clone https://github.com/confizhou666/github-radar.git
cd github-radar

# 安装可编辑模式包及开发测试工具（ruff + playwright）
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

常用调试命令：

```bash
# 仅使用免费并发翻译 + 本地确定性规则分类（无需任何 API Key）
python run.py --no-ai

# 从现有 SQLite 数据库直接重新导出静态产物（data.json / RSS / JSON Feed / llms.txt）
python run.py --export-only

# 终端预览通知简报而不实际发送外部 Webhook
python run.py --notify-dry-run
```

---

## 提交前验证清单

所有 Pull Request 都会触发 `.github/workflows/ci.yml` 自动门禁，提交前请在本地运行：

```bash
# 1. Python 静态检查
ruff check .

# 2. 前端脚本语法校验
node --check web/app.js

# 3. 后端单元/回归测试 + 无头浏览器前端冒烟测试（注意绕过本机回环代理）
NO_PROXY=127.0.0.1,localhost python -m unittest discover -s tests -v
```

---

## 常见贡献方向

- **改进去噪与反刷星规则**：在 `config.yaml` 或 `radar/filters.py` 补充新型刷星套壳特征，并在 `tests/test_radar.py` 添加正反例回归测试（确保不误伤合法硬核项目）。
- **扩展确定性分类词典**：在 `radar/translator.py` 的 `_CATEGORY_RULES` 补充高区分度技术词条。
- **新增采集信源或通知渠道**：分别在 `radar/sources.py` 或 `radar/notifier.py` 实现，并同步补齐 `web/app.js` 的 `CHANNEL_DEFS` 与自动化测试。
