from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple
import urllib.error
import urllib.parse
import urllib.request

from .models import RepoItem

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HIGHLIGHT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "trend_summary": {
            "type": "string",
            "description": "可选：基于本批高热项目提炼2句中文技术风向导语（45~110字），点名具体技术方向与代表项目，禁套话",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "keep": {
                        "type": "boolean",
                        "description": "True if genuinely interesting/useful software; False if disguised spam, pirated installer, SEO wrapper, prompt dump, or pure tutorial",
                    },
                    "category_tag": {
                        "type": "string",
                        "description": "从以下9个标准中文分类中选择最贴切的1个：效率工具、AI应用、模型推理、系统底层、数据存储、安全网络、创意玩具、开发框架、桌面工具",
                    },
                    "highlight": {
                        "type": "string",
                        "description": "1句中文亮点（20~55字）：直白说清这个项目好玩或有用在哪里，禁止套话",
                    },
                },
                "required": ["repo_name", "keep", "category_tag", "highlight"],
            },
        },
    },
    "required": ["items"],
}

# Deterministic zero-cost local category rules: (category_tag, english_tokens, cjk_phrases)
_CATEGORY_RULES: List[Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = [
    (
        "模型推理",
        (
            "vllm",
            "gguf",
            "llama-cpp",
            "llama.cpp",
            "quantization",
            "fine-tuning",
            "finetuning",
            "lora",
            "qlora",
            "pytorch",
            "tensorrt",
            "onnx",
            "mlx",
            "diffusion",
            "stable-diffusion",
            "text-to-speech",
            "speech-to-text",
            "speech-recognition",
            "tts",
            "asr",
            "whisper",
            "vision-language",
            "vlm",
            "multimodal",
            "transformer",
            "neural-network",
            "deep-learning",
            "machine-learning",
            "reinforcement-learning",
            "rlhf",
            "grpo",
            "embedding",
            "reranker",
            "triton",
            "inference-engine",
            "llm-inference",
        ),
        (
            "推理引擎",
            "模型推理",
            "本地推理",
            "量化",
            "微调",
            "模型训练",
            "预训练",
            "语音合成",
            "语音识别",
            "多模态",
            "扩散模型",
            "注意力机制",
            "神经网络",
            "强化学习",
        ),
    ),
    (
        "AI应用",
        (
            "llm",
            "llms",
            "gpt",
            "chatgpt",
            "claude",
            "claude-code",
            "anthropic",
            "gemini",
            "openai",
            "deepseek",
            "qwen",
            "ollama",
            "agent",
            "agents",
            "multi-agent",
            "agentic",
            "mcp",
            "model-context-protocol",
            "rag",
            "langchain",
            "llamaindex",
            "dify",
            "comfyui",
            "copilot",
            "cursor",
            "codex",
            "aigc",
            "chatbot",
            "ai-agent",
            "ai-assistant",
            "ai-coding",
            "coding-agent",
            "ai",
        ),
        (
            "智能体",
            "大模型",
            "知识库",
            "对话机器人",
            "编程助手",
            "AI助手",
            "AI代理",
            "检索增强",
            "文生图",
            "文生视频",
        ),
    ),
    (
        "安全网络",
        (
            "security",
            "cybersecurity",
            "pentest",
            "pentesting",
            "vulnerability",
            "cve",
            "exploit",
            "fuzzing",
            "fuzzer",
            "reverse-engineering",
            "decompiler",
            "disassembler",
            "forensics",
            "waf",
            "firewall",
            "vpn",
            "wireguard",
            "socks5",
            "tunnel",
            "intranet",
            "dns",
            "tls",
            "mtls",
            "oauth",
            "oidc",
            "passkey",
            "zero-trust",
            "encryption",
            "cryptography",
            "packet",
        ),
        (
            "网络安全",
            "漏洞",
            "渗透",
            "逆向",
            "防火墙",
            "内网穿透",
            "加密",
            "鉴权",
            "零信任",
            "抓包",
            "审计",
        ),
    ),
    (
        "数据存储",
        (
            "database",
            "sql",
            "sqlite",
            "postgres",
            "postgresql",
            "mysql",
            "duckdb",
            "clickhouse",
            "parquet",
            "arrow",
            "apache-arrow",
            "olap",
            "oltp",
            "vector-db",
            "vector-database",
            "qdrant",
            "milvus",
            "lancedb",
            "kv-store",
            "rocksdb",
            "lsm-tree",
            "redis",
            "valkey",
            "s3",
            "object-storage",
            "distributed-storage",
            "kafka",
            "message-queue",
            "etl",
            "cdc",
            "dataframe",
            "polars",
            "search-engine",
            "full-text-search",
        ),
        (
            "数据库",
            "向量检索",
            "向量库",
            "存储引擎",
            "对象存储",
            "消息队列",
            "数据仓库",
            "列式存储",
            "全文检索",
            "检索引擎",
        ),
    ),
    (
        "系统底层",
        (
            "kernel",
            "linux-kernel",
            "operating-system",
            "osdev",
            "unikernel",
            "rtos",
            "embedded",
            "firmware",
            "stm32",
            "esp32",
            "riscv",
            "risc-v",
            "hypervisor",
            "kvm",
            "qemu",
            "microvm",
            "virtualization",
            "sandbox",
            "container-runtime",
            "ebpf",
            "bpf",
            "wasm",
            "webassembly",
            "wasi",
            "compiler",
            "llvm",
            "jit",
            "garbage-collection",
            "allocator",
            "syscall",
            "driver",
            "filesystem",
            "fuse",
            "cuda",
            "vulkan",
            "opengl",
            "webgpu",
        ),
        (
            "操作系统",
            "内核",
            "编译器",
            "虚拟机",
            "嵌入式",
            "固件",
            "文件系统",
            "字节码",
            "内存分配",
            "垃圾回收",
            "显卡",
            "驱动",
        ),
    ),
    (
        "创意玩具",
        (
            "game",
            "gamedev",
            "game-engine",
            "godot",
            "bevy",
            "raylib",
            "retro",
            "emulator",
            "gameboy",
            "nes",
            "pixel-art",
            "voxel",
            "shader",
            "generative-art",
            "synth",
            "synthesizer",
            "midi",
            "daw",
            "webaudio",
            "ascii-art",
            "animation",
            "historical-atlas",
            "atlas",
            "interactive-map",
            "astronomy",
            "planetarium",
            "chess",
            "puzzle",
            "simulation",
        ),
        (
            "游戏",
            "合成器",
            "编曲",
            "像素",
            "着色器",
            "动画",
            "历史地图",
            "交互地图",
            "模拟器",
            "天文",
            "创意",
        ),
    ),
    (
        "桌面工具",
        (
            "desktop",
            "desktop-app",
            "macos",
            "menubar",
            "menu-bar",
            "windows-app",
            "wpf",
            "winui",
            "tauri",
            "electron",
            "wails",
            "flutter",
            "swiftui",
            "appkit",
            "gtk",
            "qt",
            "wayland",
            "hyprland",
            "gui",
            "clipboard",
            "screenshot",
            "screen-recorder",
            "video-editor",
            "image-editor",
            "pdf-viewer",
            "ebook",
            "epub",
            "markdown-editor",
            "note-taking",
            "launcher",
            "wallpaper",
            "media-player",
        ),
        (
            "桌面端",
            "桌面应用",
            "客户端",
            "菜单栏",
            "状态栏",
            "剪贴板",
            "截图",
            "录屏",
            "阅读器",
            "笔记",
            "播放器",
            "壁纸",
        ),
    ),
    (
        "开发框架",
        (
            "framework",
            "web-framework",
            "ui-library",
            "component-library",
            "design-system",
            "sdk",
            "react",
            "nextjs",
            "vue",
            "nuxt",
            "svelte",
            "solidjs",
            "angular",
            "astro",
            "htmx",
            "tailwindcss",
            "orm",
            "graphql",
            "grpc",
            "rpc",
            "router",
            "middleware",
            "state-management",
            "bundler",
            "vite",
            "linter",
            "formatter",
            "type-checker",
            "lsp",
            "parser",
            "virtual-list",
            "ioc",
        ),
        (
            "前端框架",
            "组件库",
            "开发框架",
            "状态管理",
            "中间件",
            "打包器",
            "语法解析",
            "依赖注入",
        ),
    ),
    (
        "效率工具",
        (
            "cli",
            "command-line",
            "tui",
            "terminal",
            "shell",
            "zsh",
            "bash",
            "tmux",
            "neovim",
            "vim",
            "emacs",
            "dotfiles",
            "git",
            "github-actions",
            "ci-cd",
            "devops",
            "docker",
            "kubernetes",
            "k8s",
            "helm",
            "terraform",
            "monitoring",
            "observability",
            "profiler",
            "benchmark",
            "package-manager",
            "resolver",
            "automation",
            "scraper",
            "crawler",
            "self-hosted",
            "selfhosted",
            "homelab",
            "converter",
            "downloader",
        ),
        (
            "命令行",
            "终端",
            "包管理",
            "自动化",
            "自托管",
            "监控",
            "性能分析",
            "下载器",
            "转换器",
        ),
    ),
]

_LANG_CATEGORY_FALLBACK: Dict[str, str] = {
    "C": "系统底层",
    "Assembly": "系统底层",
    "Zig": "系统底层",
    "Verilog": "系统底层",
    "SystemVerilog": "系统底层",
    "Swift": "桌面工具",
    "Kotlin": "桌面工具",
    "Dart": "桌面工具",
    "Objective-C": "桌面工具",
    "C#": "桌面工具",
    "Vue": "开发框架",
    "Svelte": "开发框架",
    "CSS": "开发框架",
    "HTML": "开发框架",
    "TypeScript": "开发框架",
    "JavaScript": "开发框架",
    "SQL": "数据存储",
    "Jupyter Notebook": "模型推理",
}


@lru_cache(maxsize=512)
def _compile_token_pattern(token: str) -> Pattern[str]:
    escaped = re.escape(token.lower())
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def classify_repo_category(item: RepoItem) -> str:
    """Deterministically classify a repository into a concise Chinese category tag without LLM calls."""
    existing = (item.category_tag or "").strip()
    if existing:
        return existing[:16]

    topics_lower = [str(t or "").strip().lower() for t in (item.topics or []) if str(t or "").strip()]
    topics_blob = " ".join(topics_lower)
    slug_words = re.sub(r"[/._-]+", " ", (item.repo_name or "").lower())
    desc_en = (item.description or "").lower()
    desc_zh = item.desc_cn or ""
    en_haystack = f"{slug_words} {topics_blob} {desc_en}"
    zh_haystack = f"{desc_zh} {item.description or ''}"

    scores: Dict[str, float] = {cat: 0.0 for cat, _, _ in _CATEGORY_RULES}

    if "HuggingFace Papers" in (item.sources or []):
        scores["模型推理"] += 2.2

    for cat, en_tokens, zh_phrases in _CATEGORY_RULES:
        for tok in en_tokens:
            if tok in topics_lower:
                scores[cat] += 3.0
            elif _compile_token_pattern(tok).search(en_haystack):
                scores[cat] += 1.6
        for phrase in zh_phrases:
            if phrase in zh_haystack:
                scores[cat] += 1.8

    best_cat, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score > 0.0:
        return best_cat

    lang = (item.language or "").strip()
    if lang in _LANG_CATEGORY_FALLBACK:
        return _LANG_CATEGORY_FALLBACK[lang]
    return "效率工具"


def _open_url(req: urllib.request.Request, *, timeout: int = 12, proxy: str = ""):
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _contains_mostly_cjk(text: str) -> bool:
    if not text:
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk >= max(4, len(text) * 0.25)


def translate_free_zh(text: str, *, proxy: str = "") -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    if _contains_mostly_cjk(clean):
        return clean

    eff_proxy = proxy or os.environ.get("RADAR_PROXY", "").strip()

    # 1. Free Google Translate gtx endpoint
    try:
        q = urllib.parse.quote(clean[:800])
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _open_url(req, timeout=8, proxy=eff_proxy) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(
                    str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0]
                )
                if translated.strip():
                    return translated.strip()
    except Exception as exc:
        logger.debug("google_gtx 翻译失败，尝试备用接口: %s", exc)

    # 2. Free MyMemory fallback
    try:
        q = urllib.parse.quote(clean[:450])
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _open_url(req, timeout=8, proxy=eff_proxy) as resp:
            js = json.loads(resp.read().decode("utf-8", errors="replace"))
            txt = str((js.get("responseData") or {}).get("translatedText") or "").strip()
            if txt:
                return txt
    except Exception as exc:
        logger.debug("mymemory 翻译失败: %s", exc)

    return clean


def _resolve_openai_settings(ai_cfg: Dict[str, Any]) -> Tuple[str, str, str]:
    provider = str(ai_cfg.get("provider") or "auto").strip().lower()
    api_key_env = str(ai_cfg.get("api_key_env") or "RADAR_AI_API_KEY").strip()
    api_key = str(
        ai_cfg.get("api_key")
        or os.environ.get(api_key_env, "")
        or os.environ.get("RADAR_AI_API_KEY", "")
        or (os.environ.get("OPENAI_API_KEY", "") if provider == "openai" else "")
        or ""
    ).strip()
    base_url = str(
        os.environ.get("RADAR_AI_BASE_URL", "")
        or ai_cfg.get("base_url")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")
    model = str(
        os.environ.get("RADAR_AI_MODEL", "")
        or ai_cfg.get("openai_model")
        or ai_cfg.get("model")
        or "gpt-4o-mini"
    ).strip()
    return base_url, api_key, model


def _call_chat_completions(
    prompt: str,
    *,
    endpoint: str,
    api_key: str,
    model: str,
    timeout: int = 90,
    proxy: str = "",
    include_trend_summary: bool = False,
) -> str:
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    schema_hint = (
        '{"trend_summary": "2句中文宏观风向（点名具体方向与项目，禁套话）", "items": [{"repo_name": "owner/repo", "keep": true, "category_tag": "效率工具", "highlight": "..."}]}'
        if include_trend_summary
        else '{"items": [{"repo_name": "owner/repo", "keep": true, "category_tag": "效率工具", "highlight": "..."}]}'
    )
    json_prompt = (
        prompt
        + f"\n\n严格返回合法 JSON 对象，格式为：{schema_hint}，不要输出多余解释。"
    )
    body: Dict[str, Any] = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": "你是资深开源工具发掘编辑，只输出合法 JSON。"},
            {"role": "user", "content": json_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    for attempt in range(2):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with _open_url(req, timeout=timeout, proxy=proxy) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                outer = json.loads(raw)
                if isinstance(outer, dict):
                    choices = outer.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        msg = choices[0].get("message") or {}
                        return str(msg.get("content") or "")
                    if "error" in outer:
                        logger.warning(
                            "大模型接口 (%s) 返回错误载荷: %s",
                            endpoint,
                            str(outer["error"])[:200],
                        )
                        return ""
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 400 and attempt == 0 and "response_format" in body:
                body.pop("response_format", None)
                continue
            logger.warning("大模型接口 (%s) 返回 HTTP %s: %s", endpoint, exc.code, exc.reason)
            return ""
        except json.JSONDecodeError:
            logger.warning("大模型接口 (%s) 返回非 JSON 文本: %r", endpoint, raw[:80])
            return ""
        except Exception as exc:
            logger.warning("大模型接口 (%s) 调用异常: %s", endpoint, exc)
            return ""
    return ""


def _run_openai_compatible(
    prompt: str,
    ai_cfg: Dict[str, Any],
    *,
    proxy: str = "",
    include_trend_summary: bool = False,
) -> str:
    base_url, api_key, model = _resolve_openai_settings(ai_cfg)
    if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
        logger.info("未配置 OpenAI 兼容 API Key，跳过 OpenAI 接口调用")
        return ""
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    timeout = int(ai_cfg.get("timeout_seconds") or 90)
    return _call_chat_completions(
        prompt,
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout=timeout,
        proxy=proxy,
        include_trend_summary=include_trend_summary,
    )


def _run_agy_cli(prompt: str, ai_cfg: Dict[str, Any]) -> str:
    exe = str(ai_cfg.get("agy_executable") or "agy")
    resolved_exe = shutil.which(exe)
    if not resolved_exe:
        logger.info("未找到 %s，跳过 AI 一句话亮点生成", exe)
        return ""
    model = str(ai_cfg.get("model") or "gemini-4-hydrogen-medium")
    timeout = int(ai_cfg.get("timeout_seconds") or 90)
    cmd = [
        resolved_exe,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--disable-slash-commands",
        "--json-schema",
        json.dumps(HIGHLIGHT_SCHEMA, ensure_ascii=False),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        if proc.returncode != 0:
            logger.warning(
                "agy 亮点生成返回非0 (%s): %s", proc.returncode, (proc.stderr or "")[-200:]
            )
            return ""
        return proc.stdout or ""
    except Exception as exc:
        logger.warning("agy 亮点生成异常: %s", exc)
        return ""


def _build_batch_prompt(batch: List[RepoItem], *, want_trend_summary: bool = False) -> str:
    payload_lines = [
        f"- {r.repo_name} | lang={r.language or 'unknown'} | stars={r.stars} | forks={r.forks} | desc={(r.desc_cn or r.description)[:200]}"
        for r in batch
    ]
    trend_rule = (
        "4. `trend_summary`: 用 2 句简练中文（50~110字）概括今日这批项目的真实技术动向，直接点出具体技术方向与 1~2 个代表仓库名；严禁使用“赋能、生态、闭环、重磅、总而言之、不仅…更…”等套话。\n\n"
        if want_trend_summary
        else "\n"
    )
    return (
        "你是资深开源工具发掘编辑。下面是一批已初步去噪的 GitHub 项目列表。\n"
        "请逐条评估并输出 JSON：\n"
        "1. `keep`: 如果实质是八股面试、纯教程、资源链接合集、Prompt 文本堆砌、盗版安装器/外挂壳仓、引流壳站或灰产脚本，设为 false；真正有代码创意或实用价值设为 true。\n"
        "2. `category_tag`: 优先从以下 9 个标准中文分类中选 1 个：效率工具 / AI应用 / 模型推理 / 系统底层 / 数据存储 / 安全网络 / 创意玩具 / 开发框架 / 桌面工具。\n"
        "3. `highlight`: 用一句大白话（20~55字）直指核心：它到底解决什么痛点、或有什么有趣脑洞？严禁“功能强大/值得收藏/赋能/重磅”等空话。\n"
        + trend_rule
        + "\n".join(payload_lines)
    )


def generate_ai_highlights(
    items: List[RepoItem],
    ai_cfg: Dict[str, Any],
    *,
    runner: Optional[Callable[[str], str]] = None,
    proxy: str = "",
    github_token: str = "",
    trend_summary_out: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run chunked LLM calls via configured provider (auto / openai / agy) to generate highlights."""
    _ = github_token
    if not items or not ai_cfg.get("enabled", True):
        return {}

    eff_proxy = proxy or os.environ.get("RADAR_PROXY", "").strip()
    provider = str(ai_cfg.get("provider") or "auto").strip().lower()
    base_url, api_key, _ = _resolve_openai_settings(ai_cfg)
    has_openai = bool(api_key or "localhost" in base_url or "127.0.0.1" in base_url)
    has_agy = bool(shutil.which(str(ai_cfg.get("agy_executable") or "agy")))

    if provider == "github_models":
        logger.warning(
            "GitHub Models (models.github.ai) 官方推理接口已下线退役，跳过调用并交由本地确定性规则分类兜底；如需 AI 一句话导读请配置 RADAR_AI_API_KEY"
        )
        return {}

    candidate_modes: List[str] = []
    if runner is not None:
        candidate_modes = ["runner"]
    elif provider == "openai":
        if has_openai:
            candidate_modes = ["openai"]
    elif provider == "agy":
        if has_agy:
            candidate_modes = ["agy"]
    else:
        if has_openai:
            candidate_modes.append("openai")
        if has_agy:
            candidate_modes.append("agy")

    if not candidate_modes:
        logger.info("未检测到可用大模型 Provider（RADAR_AI_API_KEY / 本地 agy CLI），跳过 AI 导读并启用本地规则分类兜底")
        return {}

    max_batch = max(1, int(ai_cfg.get("max_batch_items") or 25))
    max_chunks = max(1, int(ai_cfg.get("max_chunks_per_run") or 8))

    def _invoke_mode(mode: str, prompt: str, *, want_summary: bool) -> str:
        if mode == "runner" and runner is not None:
            return runner(prompt)
        if mode == "openai":
            return _run_openai_compatible(
                prompt, ai_cfg, proxy=eff_proxy, include_trend_summary=want_summary
            )
        if mode == "agy":
            return _run_agy_cli(prompt, ai_cfg)
        return ""

    active_mode = candidate_modes[0]
    combined: Dict[str, Dict[str, Any]] = {}
    cursor = 0
    chunk_idx = 0

    while cursor < len(items) and chunk_idx < max_chunks:
        batch = items[cursor : cursor + max_batch]
        want_summary = chunk_idx == 0 and trend_summary_out is not None and not trend_summary_out
        prompt = _build_batch_prompt(batch, want_trend_summary=want_summary)

        raw_stdout = _invoke_mode(active_mode, prompt, want_summary=want_summary)
        batch_map, batch_summary = (
            _parse_highlight_output_with_summary(raw_stdout) if raw_stdout else ({}, "")
        )
        # On the first chunk in auto mode, cascade cleanly if higher-priority provider rejected credentials/quota or returned unparseable output
        if not batch_map and chunk_idx == 0 and len(candidate_modes) > 1:
            for fallback_mode in candidate_modes[1:]:
                logger.info("AI Provider (%s) 未返回有效导读，自动回退尝试 %s", active_mode, fallback_mode)
                raw_stdout = _invoke_mode(fallback_mode, prompt, want_summary=want_summary)
                fb_map, fb_summary = (
                    _parse_highlight_output_with_summary(raw_stdout) if raw_stdout else ({}, "")
                )
                if fb_map:
                    active_mode = fallback_mode
                    batch_map, batch_summary = fb_map, fb_summary
                    break

        if not batch_map:
            if chunk_idx == 0:
                break
            cursor += max_batch
            chunk_idx += 1
            continue

        combined.update(batch_map)
        if batch_summary and trend_summary_out is not None and not trend_summary_out:
            trend_summary_out.append(batch_summary)

        cursor += len(batch)
        chunk_idx += 1

    return combined


def _extract_parsed_dict(raw_stdout: str) -> Optional[Dict[str, Any]]:
    text = (raw_stdout or "").strip()
    if not text:
        return None

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    parsed: Any = None
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            parsed = outer.get("structured_output")
            if parsed is None and isinstance(outer.get("items"), list):
                parsed = outer
            elif parsed is None and isinstance(outer.get("response"), str):
                parsed = json.loads(outer["response"])
    except Exception:
        pass

    if not isinstance(parsed, dict):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None

    return parsed if isinstance(parsed, dict) else None


def _parse_highlight_output_with_summary(
    raw_stdout: str,
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    parsed = _extract_parsed_dict(raw_stdout)
    if not parsed or not isinstance(parsed.get("items"), list):
        return {}, ""

    summary = str(parsed.get("trend_summary") or "").strip()[:240]
    result: Dict[str, Dict[str, Any]] = {}
    for entry in parsed["items"]:
        if not isinstance(entry, dict):
            continue
        rname = str(entry.get("repo_name") or "").strip().lower()
        if not rname:
            continue
        result[rname] = {
            "keep": bool(entry.get("keep", True)),
            "category_tag": str(entry.get("category_tag") or "").strip()[:16],
            "highlight": str(entry.get("highlight") or "").strip()[:120],
        }
    return result, summary


def _parse_highlight_output(raw_stdout: str) -> Dict[str, Dict[str, Any]]:
    res, _ = _parse_highlight_output_with_summary(raw_stdout)
    return res


def build_fallback_trend_summary(items: List[RepoItem], *, new_count: int = 0) -> str:
    """Build a concrete, zero-cliche editorial lead from actual repository metrics when LLM summary is absent."""
    if not items:
        return ""

    lang_counts = Counter(r.language for r in items if r.language)
    cat_counts = Counter(r.category_tag for r in items if r.category_tag)
    top_langs = [f"{lang}（{cnt}）" for lang, cnt in lang_counts.most_common(3)]
    top_cats = [cat for cat, _ in cat_counts.most_common(3)]

    multi_hits = [r for r in items if len(r.sources) >= 2]
    surging = sorted(items, key=lambda r: (r.stars_delta_24h, r.stars), reverse=True)
    leader = surging[0] if surging else None

    part1_bits = [f"本期在榜 {len(items)} 个项目"]
    if new_count > 0:
        part1_bits.append(f"新收录 {new_count} 项")
    if top_langs:
        part1_bits.append(f"主力语言集中在 {'、'.join(top_langs)}")
    if top_cats:
        part1_bits.append(f"高频方向为 {' / '.join(top_cats)}")
    sent1 = "，".join(part1_bits) + "。"

    sent2_bits = []
    if leader and leader.stars_delta_24h > 0:
        sent2_bits.append(f"{leader.repo_name} 单日新增 {leader.stars_delta_24h:,} Star 领跑热度")
    elif leader:
        sent2_bits.append(f"{leader.repo_name}（★{leader.stars:,}）位居榜首")
    if multi_hits:
        names = "、".join(r.repo_name for r in multi_hits[:2])
        suffix = f" 等 {len(multi_hits)} 项" if len(multi_hits) > 2 else ""
        sent2_bits.append(f"{names}{suffix}同时命中多路社区信源")
    sent2 = "；".join(sent2_bits) + "。" if sent2_bits else ""
    return (sent1 + sent2).strip()
