# 安全策略 (Security Policy)

## 受支持版本 (Supported Versions)

| 版本 | 安全更新支持 |
| :--- | :---: |
| `0.2.x`（当前主分支与最新发布） | ✅ |
| `< 0.2.0` | ❌ |

---

## 安全边界说明

1. **防跨站脚本 (XSS) 与恶意协议注入**：
   - 后端 `radar/sources.py`（`sanitize_http_url`）与前端 `web/app.js`（`safeHttpUrl`）严格执行 `http:` / `https:` 协议白名单，拦截 `javascript:`、`data:`、`vbscript:` 等伪协议。
   - 在线 README 预览器（`renderSafeMarkdown`）采用反向占位符与全量实体转义，过滤原始内联 HTML 标签。
2. **密钥与公开状态快照隔离**：
   - API 密钥（`RADAR_AI_API_KEY`）与推送凭据（`RADAR_WEBHOOK_URL`、`RADAR_TG_BOT_TOKEN`）应仅通过 GitHub Actions Secrets 或本地环境变量注入，切勿硬编码提交至 `config.yaml`。
   - 随 GitHub Pages 发布的 `feeds/state_backup.json.gz` 仅包含公开 GitHub 仓库元数据与历史星标快照，不包含任何环境变量或私密配置。

---

## 报告安全漏洞 (Reporting a Vulnerability)

如果你在本项目中发现了潜在的安全问题（例如前端 DOM XSS 绕过、RSS/XML 注入、命令执行或敏感信息泄露风险）：

1. **请勿在公开 Issue 中直接披露可利用的攻击载荷（PoC）**。
2. 请优先通过本仓库的 **[GitHub Private Vulnerability Reporting](https://github.com/confizhou666/github-radar/security/advisories/new)** 私密提交报告，并附上复现步骤与受影响代码位置。
3. 维护者将在 **72 小时内**响应确认，并在完成修复与回归测试后发布安全补丁及致谢说明。
