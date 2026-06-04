# -*- coding: utf-8 -*-
"""
llm_summary.py —— LLM 汇总模块
支持：
1) 固定 docx 规则（每次汇总必带）
2) summary_rules.txt 补充规则
3) OpenRouter / DeepSeek 双提供商（默认优先 OpenRouter）
"""
import json
import logging
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
import time as _time

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv is optional; env vars can still come from system settings.
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(BASE_DIR, "summary_rules.txt")
# 固定规则文件：支持 .md / .txt / .docx 三种格式。
# 默认使用仓库自带的脱敏示例规则；可用环境变量 LLM_FIXED_RULES_FILE（推荐）
# 或兼容旧名 LLM_FIXED_RULES_DOCX 覆盖为任意本地路径。
FIXED_RULES_FILE = (
    os.getenv("LLM_FIXED_RULES_FILE")
    or os.getenv("LLM_FIXED_RULES_DOCX")
    or os.path.join(BASE_DIR, "rules", "summary_fixed_rules.md")
).strip()
DEFAULT_SYSTEM_RULES = "你是一个团队日报汇总助手，请对日报内容进行精炼总结。"

_LLM_INFO_LOGGED = False

# ── 日志：同时输出到控制台和文件 ──
_LOG_DIR = os.path.join(BASE_DIR, "wxauto_logs")
os.makedirs(_LOG_DIR, exist_ok=True)

def _llm_log(msg):
    """写入 wxauto_logs/llm_YYYYMMDD.log 同时 print 到控制台。"""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        log_path = os.path.join(_LOG_DIR, f"llm_{datetime.now():%Y%m%d}.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _extract_docx_text(docx_path):
    """读取 docx 正文文本（不依赖 python-docx）。"""
    if not docx_path or not os.path.exists(docx_path):
        return ""
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            xml_data = z.read("word/document.xml")
        root = ET.fromstring(xml_data)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = []
        for para in root.findall(".//w:p", ns):
            chunks = []
            for node in para.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "t" and node.text:
                    chunks.append(node.text)
                elif tag == "tab":
                    chunks.append("\t")
            line = "".join(chunks).strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()
    except Exception as e:
        _llm_log(f"[LLM] 读取固定规则 docx 失败: {e}")
        return ""


def _extract_rules_text(path):
    """读取固定规则正文，按扩展名分派：.docx 走 XML 解析，.md/.txt 直接读取。"""
    if not path or not os.path.exists(path):
        return ""
    if path.lower().endswith(".docx"):
        return _extract_docx_text(path)
    return _read_text(path)


def _resolve_fixed_rules_path():
    candidates = []
    if FIXED_RULES_FILE:
        candidates.append(FIXED_RULES_FILE)
        if not os.path.isabs(FIXED_RULES_FILE):
            candidates.append(os.path.join(BASE_DIR, FIXED_RULES_FILE))
    # 仓库自带的默认示例规则（多种可能位置/格式）
    candidates.append(os.path.join(BASE_DIR, "rules", "summary_fixed_rules.md"))
    candidates.append(os.path.join(BASE_DIR, "rules", "summary_fixed_rules.txt"))
    candidates.append(os.path.join(BASE_DIR, "rules", "summary_fixed_rules.docx"))

    seen = set()
    for path in candidates:
        norm = os.path.normpath(path)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.exists(norm):
            return norm
    return ""


# ── 飞书 API 获取规则文档 ──
_FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "").strip()
_FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "").strip()
_FEISHU_DOC_ID = os.getenv("FEISHU_DOC_ID", "").strip()

_feishu_token_cache = {"token": "", "expires_at": 0}


def _feishu_get_token():
    """获取飞书 tenant_access_token，带缓存（2小时有效期，提前5分钟刷新）。"""
    now = _time.time()
    if _feishu_token_cache["token"] and now < _feishu_token_cache["expires_at"]:
        return _feishu_token_cache["token"]

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = json.dumps({
        "app_id": _FEISHU_APP_ID,
        "app_secret": _FEISHU_APP_SECRET,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"飞书token获取失败: {data}")
    token = data["tenant_access_token"]
    expire = data.get("expire", 7200)
    _feishu_token_cache["token"] = token
    _feishu_token_cache["expires_at"] = now + expire - 300
    return token


def _fetch_feishu_rules():
    """从飞书文档获取固定规则文本。失败返回空字符串。"""
    if not (_FEISHU_APP_ID and _FEISHU_APP_SECRET and _FEISHU_DOC_ID):
        return ""
    try:
        token = _feishu_get_token()
        url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{_FEISHU_DOC_ID}/raw_content"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        })
        resp = urllib.request.urlopen(req, timeout=20)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            _llm_log(f"[LLM] 飞书文档获取失败: code={data.get('code')}, msg={data.get('msg')}")
            return ""
        content = data.get("data", {}).get("content", "").strip()
        if content:
            _llm_log(f"[LLM] 从飞书获取固定规则成功 ({len(content)} 字符)")
        return content
    except Exception as e:
        _llm_log(f"[LLM] 飞书API调用失败: {e}")
        return ""


def load_rules():
    """
    每次汇总时重新读取规则：
    - 固定规则从本地 docx 读取
    - summary_rules.txt：作为补充规则，可被 OpenClaw 动态更新
    """
    fixed_rules_path = _resolve_fixed_rules_path()
    fixed_rules = _extract_rules_text(fixed_rules_path)
    extra_rules = _read_text(RULES_FILE)

    if fixed_rules and extra_rules:
        return (
            f"{fixed_rules}\n\n"
            f"【补充规则（可通过 /rules 更新）】\n{extra_rules}\n\n"
            "若补充规则与固定规则冲突，以固定规则为准。"
        )
    if fixed_rules:
        return fixed_rules
    if extra_rules:
        return extra_rules
    return DEFAULT_SYSTEM_RULES


def _resolve_provider():
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            provider = "openrouter"
        elif os.getenv("DEEPSEEK_API_KEY", "").strip():
            provider = "deepseek"
        else:
            provider = "openrouter"

    if provider == "openrouter":
        return {
            "provider": "openrouter",
            "api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
            "api_url": os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions").strip(),
            "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-5.4").strip(),
            "fallback_model": os.getenv("OPENROUTER_FALLBACK_MODEL", "openai/gpt-5.4-mini").strip(),
            "second_fallback_model": os.getenv("OPENROUTER_SECOND_FALLBACK_MODEL", "deepseek/deepseek-chat-v3-0324").strip(),
            "site_url": os.getenv("OPENROUTER_SITE_URL", "").strip(),
            "app_name": os.getenv("OPENROUTER_APP_NAME", "").strip(),
        }

    if provider == "deepseek":
        return {
            "provider": "deepseek",
            "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
            "api_url": os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip(),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            "fallback_model": "",
            "second_fallback_model": "",
            "site_url": "",
            "app_name": "",
        }

    # 通用兜底
    return {
        "provider": provider or "custom",
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "api_url": os.getenv("LLM_API_URL", "").strip(),
        "model": os.getenv("LLM_MODEL", "").strip(),
        "fallback_model": "",
        "second_fallback_model": "",
        "site_url": "",
        "app_name": "",
    }


USER_PROMPT_TEMPLATE = """以下是 {date} 的团队日报接龙内容，请进行总结：
{content}

已完成接龙：{done_count}人（{done_names}）；未完成接龙：{not_done_count}人（{not_done_names}）；请假：{leave_count}人（{leave_names}）。"""


def _extract_content(result):
    try:
        content = result["choices"][0]["message"]["content"]
    except Exception:
        return None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text")
                if txt:
                    parts.append(str(txt))
        return "".join(parts).strip() if parts else None
    return None


def summarize(jielong_content, date, participated, not_done, leave):
    global _LLM_INFO_LOGGED

    cfg = _resolve_provider()
    api_key = cfg["api_key"]
    api_url = cfg["api_url"]
    model = cfg["model"]
    fallback_model = cfg.get("fallback_model", "")
    second_fallback_model = cfg.get("second_fallback_model", "")
    provider = cfg["provider"]

    if not api_key:
        _llm_log(f"[LLM] 未配置 {provider} API Key，跳过 AI 总结")
        return None
    if not api_url or not model:
        _llm_log(f"[LLM] 提供商配置不完整 provider={provider} api_url/model 不能为空")
        return None

    if not _LLM_INFO_LOGGED:
        fixed_rules_path = _resolve_fixed_rules_path() or "(未找到固定规则文件)"
        _llm_log(f"[LLM] provider={provider} model={model} api_url={api_url}")
        _llm_log(f"[LLM] fixed_rules_file={fixed_rules_path}")
        _LLM_INFO_LOGGED = True

    # 记录传入的接龙原文长度和参与人数，便于排查截断问题
    _llm_log(f"[LLM] 接龙原文长度={len(jielong_content)}字, 已接龙={len(participated)}人, 未接龙={len(not_done)}人, 请假={len(leave)}人")
    if jielong_content:
        # 打印前300字用于调试确认内容是否完整
        preview = jielong_content[:300].replace('\n', '\\n')
        _llm_log(f"[LLM] 接龙原文前300字: {preview}")

    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date,
        content=jielong_content,
        done_count=len(participated),
        done_names="、".join(participated) if participated else "无",
        not_done_count=len(not_done),
        not_done_names="、".join(not_done) if not_done else "无",
        leave_count=len(leave),
        leave_names="、".join(leave) if leave else "无",
    )

    base_payload = {
        "messages": [
            {"role": "system", "content": load_rules()},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.2,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if provider == "openrouter":
        if cfg["site_url"]:
            headers["HTTP-Referer"] = cfg["site_url"]
        if cfg["app_name"]:
            headers["X-Title"] = cfg["app_name"]

    def _do_call(call_model):
        _llm_log(f"[LLM] 调用模型: {call_model}")
        payload = dict(base_payload)
        payload["model"] = call_model
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = _extract_content(result)
            if content:
                _llm_log(f"[LLM] 汇总结果({len(content)}字):\n{content}")
                return content
            _llm_log(f"[LLM] 响应缺少文本内容: {str(result)[:240]}")
            return None

    model_candidates = []
    for m in [model, fallback_model, second_fallback_model]:
        m = str(m or "").strip()
        if m and m not in model_candidates:
            model_candidates.append(m)

    MAX_NETWORK_RETRIES = 3          # 网络瞬态错误最多重试次数
    NETWORK_RETRY_DELAY = 5          # 重试间隔（秒）

    def _is_transient_network_error(exc):
        msg = str(exc).lower()
        keywords = ["ssl", "eof occurred", "connection reset",
                     "connection aborted", "timed out", "timeout",
                     "temporary failure", "name resolution"]
        return any(k in msg for k in keywords)

    last_http = None
    for i, call_model in enumerate(model_candidates, start=1):
        for retry in range(MAX_NETWORK_RETRIES):
            try:
                if i > 1 and retry == 0:
                    _llm_log(f"[LLM] 主模型不可用，自动回退 {call_model}")
                if retry > 0:
                    _llm_log(f"[LLM] 网络重试 {retry}/{MAX_NETWORK_RETRIES - 1}，模型 {call_model}")
                return _do_call(call_model)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="ignore")
                last_http = (e.code, body)
                bad = body.lower()
                can_fallback = (
                    provider == "openrouter"
                    and e.code in (400, 403, 404)
                    and any(k in bad for k in ["region", "not available", "does not support", "model not found"])
                )
                if can_fallback and i < len(model_candidates):
                    break  # 跳出 retry 循环，进入下一个模型
                # 服务端 5xx 也值得重试
                if e.code >= 500 and retry < MAX_NETWORK_RETRIES - 1:
                    _llm_log(f"[LLM] HTTP {e.code}，{NETWORK_RETRY_DELAY}秒后重试...")
                    _time.sleep(NETWORK_RETRY_DELAY)
                    continue
                _llm_log(f"[LLM] HTTP {e.code}: {body[:300]}")
                return None
            except Exception as e:
                if _is_transient_network_error(e) and retry < MAX_NETWORK_RETRIES - 1:
                    _llm_log(f"[LLM] 网络异常: {e}，{NETWORK_RETRY_DELAY}秒后重试...")
                    _time.sleep(NETWORK_RETRY_DELAY)
                    continue
                _llm_log(f"[LLM] 调用失败: {e}")
                return None

    if last_http:
        _llm_log(f"[LLM] HTTP {last_http[0]}: {last_http[1][:300]}")
    return None


if __name__ == "__main__":
    test = summarize(
        "1. 李四\n1、车载审核\n2. 张三\n1、IT单测试",
        "2026-03-21",
        ["李四", "张三"],
        ["赵六"],
        [],
    )
    print(test or "调用失败")
