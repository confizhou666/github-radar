from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple
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
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "keep": {
                        "type": "boolean",
                        "description": "True if genuinely interesting/useful software; False if disguised spam, SEO wrapper, prompt dump, or pure tutorial",
                    },
                    "category_tag": {
                        "type": "string",
                        "description": "2~6字中文分类标签，如：效率工具、AI应用、系统底层、创意玩具、开发框架",
                    },
                    "highlight": {
                        "type": "string",
                        "description": "1句中文亮点（20~55字）：直白说清这个项目好玩或有用在哪里，禁止套话",
                    },
                },
                "required": ["repo_name", "keep", "category_tag", "highlight"],
            },
        }
    },
    "required": ["items"],
}


def _contains_mostly_cjk(text: str) -> bool:
    if not text:
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk >= max(4, len(text) * 0.25)


def translate_free_zh(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    if _contains_mostly_cjk(clean):
        return clean

    # 1. Free Google Translate gtx endpoint
    try:
        q = urllib.parse.quote(clean[:800])
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0])
                if translated.strip():
                    return translated.strip()
    except Exception as exc:
        logger.debug("google_gtx 翻译失败，尝试备用接口: %s", exc)

    # 2. Free MyMemory fallback
    try:
        q = urllib.parse.quote(clean[:450])
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
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


def _run_openai_compatible(prompt: str, ai_cfg: Dict[str, Any]) -> str:
    base_url, api_key, model = _resolve_openai_settings(ai_cfg)
    if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
        logger.info("未配置 OpenAI 兼容 API Key，跳过 AI 一句话亮点生成")
        return ""

    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    timeout = int(ai_cfg.get("timeout_seconds") or 90)
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    json_prompt = (
        prompt
        + '\n\n严格返回合法 JSON 对象，格式为：{"items": [{"repo_name": "owner/repo", "keep": true, "category_tag": "效率工具", "highlight": "..."}]}，不要输出多余解释。'
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                outer = json.loads(raw)
                choices = outer.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    return str(msg.get("content") or "")
                return raw
        except urllib.error.HTTPError as exc:
            # Some OpenAI-compatible endpoints reject response_format=json_object
            if exc.code == 400 and attempt == 0 and "response_format" in body:
                body.pop("response_format", None)
                continue
            logger.warning("OpenAI 兼容接口返回 HTTP %s: %s", exc.code, exc.reason)
            return ""
        except Exception as exc:
            logger.warning("OpenAI 兼容接口调用异常: %s", exc)
            return ""
    return ""


def _run_agy_cli(prompt: str, ai_cfg: Dict[str, Any]) -> str:
    exe = str(ai_cfg.get("agy_executable") or "agy")
    if not shutil.which(exe):
        logger.info("未找到 %s，跳过 AI 一句话亮点生成", exe)
        return ""
    model = str(ai_cfg.get("model") or "gemini-4-hydrogen-medium")
    timeout = int(ai_cfg.get("timeout_seconds") or 90)
    cmd = [
        exe,
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
            logger.warning("agy 亮点生成返回非0 (%s): %s", proc.returncode, (proc.stderr or "")[-200:])
            return ""
        return proc.stdout or ""
    except Exception as exc:
        logger.warning("agy 亮点生成异常: %s", exc)
        return ""


def generate_ai_highlights(
    items: List[RepoItem],
    ai_cfg: Dict[str, Any],
    *,
    runner: Optional[Callable[[str], str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run one batched LLM call via configured provider (auto / openai / agy) to generate highlights."""
    if not items or not ai_cfg.get("enabled", True):
        return {}

    max_batch = max(1, int(ai_cfg.get("max_batch_items") or 25))
    batch = items[:max_batch]
    payload_lines = [
        f"- {r.repo_name} | lang={r.language or 'unknown'} | stars={r.stars} | desc={(r.desc_cn or r.description)[:200]}"
        for r in batch
    ]
    prompt = (
        "你是资深开源工具发掘编辑。下面是一批已初步去噪的 GitHub 项目列表。\n"
        "请逐条评估并输出 JSON：\n"
        "1. `keep`: 如果实质是八股面试、纯教程、资源链接合集、Prompt 文本堆砌、引流壳站或灰产脚本，设为 false；真正有代码创意或实用价值设为 true。\n"
        "2. `category_tag`: 给一个 2~6 字中文分类（如 效率工具 / AI应用 / 系统底层 / 好玩创意 / 开发框架 / 桌面神器）。\n"
        "3. `highlight`: 用一句大白话（20~55字）直指核心：它到底解决什么痛点、或有什么有趣脑洞？严禁“功能强大/值得收藏/赋能/重磅”等空话。\n\n"
        + "\n".join(payload_lines)
    )

    raw_stdout = ""
    if runner is not None:
        raw_stdout = runner(prompt)
    else:
        provider = str(ai_cfg.get("provider") or "auto").strip().lower()
        if provider == "openai":
            raw_stdout = _run_openai_compatible(prompt, ai_cfg)
        elif provider == "agy":
            raw_stdout = _run_agy_cli(prompt, ai_cfg)
        else:
            # auto: prefer configured OpenAI key/local endpoint, otherwise fall back to agy CLI
            base_url, api_key, _ = _resolve_openai_settings(ai_cfg)
            if api_key or ("localhost" in base_url or "127.0.0.1" in base_url):
                raw_stdout = _run_openai_compatible(prompt, ai_cfg)
            elif shutil.which(str(ai_cfg.get("agy_executable") or "agy")):
                raw_stdout = _run_agy_cli(prompt, ai_cfg)
            else:
                logger.info("未检测到 OpenAI API Key 或 agy CLI，跳过 AI 一句话亮点生成")
                return {}

    return _parse_highlight_output(raw_stdout)


def _parse_highlight_output(raw_stdout: str) -> Dict[str, Dict[str, Any]]:
    text = (raw_stdout or "").strip()
    if not text:
        return {}

    # Strip optional markdown code fences
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

    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        return {}

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
    return result
