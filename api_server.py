# -*- coding: utf-8 -*-
"""
api_server.py —— 主程序入口
同时运行：定时任务 + HTTP API（给 OpenClaw 调用）

启动方式：python api_server.py
API 文档：http://localhost:8000/docs
"""
import threading
import time
import os
import socket
import sys
import importlib
from datetime import datetime
from typing import Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import schedule

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # python-dotenv is optional; env vars can still come from system settings.
    pass

WECHAT_BACKEND = None
WECHAT_IMPORT_ERROR = ""
WeChatClient = None

import config
from bot_core import send_jielong, cuiban, huizong, get_participated, get_leave_members

try:
    from chinese_calendar import is_workday as cn_is_workday
except Exception:
    cn_is_workday = None

# ============================================================
#  微信连接
# ============================================================
def _is_truthy(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


DRY_RUN = _is_truthy(os.getenv("BOT_DRY_RUN", "false"))
wx: Optional[Any] = None
wx_init_error: Optional[str] = None


class DryRunWeChat:
    """用于本地联调 API，不操作真实微信。

    方法签名统一吃掉 *args/**kwargs，以兼容业务层使用的
    who= / exact= / force= / at= 等关键字参数，避免 DRY_RUN 下因
    签名不匹配而抛 TypeError。"""
    def __init__(self):
        self.current_chat = ""

    def ChatWith(self, who, *args, **kwargs):
        self.current_chat = who
        print(f"[DRY_RUN] ChatWith({who})")
        return True

    def SendMsg(self, msg, *args, **kwargs):
        who = kwargs.get("who", self.current_chat)
        at = kwargs.get("at")
        if at:
            print(f"[DRY_RUN] SendMsg(to={who}, at={at}): {msg}")
        else:
            print(f"[DRY_RUN] SendMsg(to={who}): {msg}")

    def GetAllMessage(self, *args, **kwargs):
        print(f"[DRY_RUN] GetAllMessage(from={self.current_chat})")
        return []

    # 业务层偶尔会探测以下方法/属性；提供无害默认值即可。
    def AtAll(self, *args, **kwargs):
        print("[DRY_RUN] AtAll()")
        return True

    def Show(self, *args, **kwargs):
        pass

    class _DryChatBox:
        """最小化的 ChatBox 桩：让 chat_with 的会话校验 (ChatBox.who) 通过，
        从而 DRY_RUN 下流程能顺畅走完，而不会反复重试切换。"""
        def __init__(self, owner):
            self._owner = owner
        @property
        def who(self):
            return self._owner.current_chat
        editbox = None  # 无原生输入框 → 发接龙会优雅降级为失败，而非崩溃

    @property
    def ChatBox(self):
        return DryRunWeChat._DryChatBox(self)


def load_wechat_client():
    global WECHAT_BACKEND
    global WECHAT_IMPORT_ERROR
    global WeChatClient

    if WeChatClient is not None or WECHAT_BACKEND == "unavailable":
        return WeChatClient

    def _wechat_major_version_hint():
        # 仅用于后端选择提示：检测是否已安装 4.x（Weixin）。
        candidates = [
            r"C:\Program Files\Tencent\Weixin\Weixin.exe",
            r"C:\Program Files\Tencent\Weixin\4.1.8.67\Weixin.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return 4
        legacy = r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"
        if os.path.exists(legacy):
            return 3
        return 0

    preferred = os.getenv("BOT_WECHAT_BACKEND", "").strip().lower()
    if preferred not in {"wxauto", "wxautox", "wxautox4"}:
        # 自动选择：检测到 4.x 微信时优先 wxautox4，否则优先 3.9 线 wxauto。
        preferred = "wxautox4" if _wechat_major_version_hint() >= 4 else "wxauto"

    if preferred == "wxautox4":
        backends = ["wxautox4", "wxauto", "wxautox"]
    elif preferred == "wxautox":
        backends = ["wxautox", "wxauto", "wxautox4"]
    else:
        backends = ["wxauto", "wxautox", "wxautox4"]

    # 优先使用项目内 vendored wxauto（目录结构：./wxauto/wxauto）
    vendored_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wxauto")
    vendored_pkg = os.path.join(vendored_root, "wxauto")
    if os.path.isdir(vendored_pkg) and vendored_root not in sys.path:
        sys.path.insert(0, vendored_root)

    errors = {}
    for backend in backends:
        try:
            if backend == "wxauto":
                wxauto_module = importlib.import_module("wxauto")
                WxClient = getattr(wxauto_module, "WeChat")
            elif backend == "wxautox4":
                from wxautox4 import WeChat as WxClient
            else:
                from wxautox import WeChat as WxClient
            WeChatClient = WxClient
            WECHAT_BACKEND = backend
            return WeChatClient
        except BaseException as e:
            errors[backend] = str(e)

    WECHAT_BACKEND = "unavailable"
    WECHAT_IMPORT_ERROR = f"wxautox: {errors.get('wxautox', '')}; wxauto: {errors.get('wxauto', '')}"
    WeChatClient = None
    return None


def init_wx():
    global wx
    global wx_init_error

    if wx is not None:
        return wx

    if DRY_RUN:
        wx = DryRunWeChat()
        print("running with BOT_DRY_RUN=true, WeChat actions are mocked.")
        return wx

    client_cls = load_wechat_client()

    if client_cls is None:
        wx_init_error = (
            f"未找到可用 WeChat SDK。请安装 wxautox 或放置 wxauto 源码。"
            f" import_error=({WECHAT_IMPORT_ERROR})"
        )
        print(f"[ERR] {wx_init_error}")
        return None

    try:
        wx = client_cls()
        print(f"connected - group: {config.GROUP_NAME} backend={WECHAT_BACKEND}")
        return wx
    except BaseException as e:
        raw_err = str(e or "").strip() or repr(e)
        if isinstance(e, SystemExit) and WECHAT_BACKEND == "wxautox4":
            raw_err = (
                "wxautox4 初始化被终止（可能未激活）。请运行 `wxautox4 --export` 获取机器码，"
                "并用 `wxautox4 -a <激活码>` 激活后重试。"
            )
        if "无效的窗口句柄" in raw_err or "invalid window handle" in raw_err.lower():
            wx_init_error = f"{raw_err}；检测到微信可能停留在登录窗口，请先在 PC 微信扫码登录并进入主界面。"
        else:
            wx_init_error = raw_err
        print(f"[ERR] 微信初始化失败: {wx_init_error}")
        return None


def require_wx():
    client = init_wx()
    if client is None:
        detail = (
            f"WeChat 不可用: {wx_init_error}. "
            f"请确认微信已登录并前台可见；仅调试 API 可设置 BOT_DRY_RUN=true。"
        )
        raise HTTPException(status_code=503, detail=detail)
    return client

app = FastAPI(title="WeChat Bot API", version="1.0")

# ============================================================
#  核心接口
# ============================================================

@app.get("/jielong")
def api_jielong():
    """发起接龙"""
    client = require_wx()
    return {"action": "jielong", "success": send_jielong(client)}

@app.get("/cuiban")
def api_cuiban():
    """催办未接龙人员"""
    client = require_wx()
    return {"action": "cuiban", "success": cuiban(client)}

@app.get("/huizong")
def api_huizong():
    """汇总并发给领导（含 AI 总结）"""
    client = require_wx()
    return {"action": "huizong", "success": huizong(client)}

@app.get("/status")
def api_status():
    """查看当前接龙状态"""
    try:
        client = require_wx()
        members = config.get_effective_members()
        participated = get_participated(client)
        leave = get_leave_members(client)
        not_done = [m for m in members
                    if m not in participated and m not in leave]
        return {
            "group": config.GROUP_NAME,
            "total": len(members),
            "done": participated,
            "not_done": not_done,
            "leave": leave,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/huizong_data")
def api_huizong_data():
    """
    返回原始汇总数据，不发消息，不调 LLM
    OpenClaw 拿到后自己用模型总结，再调 /send_msg 发给领导
    """
    try:
        from bot_core import get_all_messages, find_last_jielong
        client = require_wx()
        members = config.get_effective_members()
        participated = get_participated(client)
        leave = get_leave_members(client)
        not_done = [m for m in members
                    if m not in participated and m not in leave]
        msgs = get_all_messages(client)
        jielong_text = find_last_jielong(msgs) or ""
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "group": config.GROUP_NAME,
            "total": len(members),
            "done": participated,
            "not_done": not_done,
            "leave": leave,
            "jielong_content": jielong_text,
            "report_to": config.REPORT_TO,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
#  通用微信操作接口
# ============================================================

class SendMsgRequest(BaseModel):
    who: str
    msg: str

@app.post("/send_msg")
def api_send_msg(req: SendMsgRequest):
    """发送任意消息给任意人/群"""
    try:
        client = require_wx()
        client.ChatWith(req.who)
        time.sleep(1)
        client.SendMsg(req.msg)
        return {"success": True, "who": req.who}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/read_msg")
def api_read_msg(who: str = None):
    """读取某个群/人的最近消息"""
    try:
        client = require_wx()
        target = who or config.GROUP_NAME
        client.ChatWith(target)
        time.sleep(1)
        msgs = client.GetAllMessage()
        result = []
        for msg in msgs[-10:]:
            sender = getattr(msg, 'sender_remark', '') or getattr(msg, 'sender', '')
            content = getattr(msg, 'content', '') or ''
            result.append({"sender": sender, "content": content[:200]})
        return {"who": target, "messages": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
#  规则管理接口（OpenClaw 改写 summary_rules.txt 补充规则）
# ============================================================
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summary_rules.txt")

@app.get("/rules")
def api_get_rules():
    """读取当前补充规则（固定 docx 规则在 llm_summary.py 中配置）"""
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return {"rules": f.read()}
    except FileNotFoundError:
        return {"rules": "(规则文件不存在)"}

class UpdateRulesRequest(BaseModel):
    rules: str

@app.post("/rules")
def api_update_rules(req: UpdateRulesRequest):
    """更新补充规则（OpenClaw 调用）"""
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        f.write(req.rules)
    return {"success": True, "message": "规则已更新，下次汇总生效"}

# ============================================================
#  定时任务
# ============================================================
def is_workday():
    today = datetime.now().date()
    if cn_is_workday:
        return bool(cn_is_workday(today))
    return datetime.now().weekday() < 5

def job(fn):
    if config.WORKDAY_ONLY and not is_workday():
        return
    client = init_wx()
    if client is None:
        print(f"[WARN] 跳过任务 {fn.__name__}，原因：{wx_init_error}")
        return
    fn(client)

schedule.every().day.at(config.JIELONG_TIME).do(job, send_jielong)
schedule.every().day.at(config.CUIBAN_TIME).do(job, cuiban)
schedule.every().day.at(config.HUIZONG_TIME).do(job, huizong)

def schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(30)


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred_port: int) -> int:
    # 用户显式指定端口时，保持原样；若占用则让 uvicorn 报错，避免“悄悄换端口”。
    if os.getenv("BOT_API_PORT"):
        return preferred_port

    if is_port_available(host, preferred_port):
        return preferred_port

    for p in range(preferred_port + 1, preferred_port + 20):
        if is_port_available(host, p):
            print(f"[WARN] 端口 {preferred_port} 已占用，自动切换到 {p}")
            return p
    return preferred_port

# ============================================================
#  启动
# ============================================================
if __name__ == "__main__":
    print(f"jielong@{config.JIELONG_TIME} cuiban@{config.CUIBAN_TIME} huizong@{config.HUIZONG_TIME}")
    host = os.getenv("BOT_API_HOST", "0.0.0.0")
    preferred_port = int(os.getenv("BOT_API_PORT", "8000"))
    port = pick_port(host, preferred_port)
    print(f"API: http://localhost:{port}/docs")
    if DRY_RUN:
        print("BOT_DRY_RUN=true (当前不会操作真实微信)")
    else:
        print("首次调用业务接口时会初始化微信客户端。")

    t = threading.Thread(target=schedule_loop, daemon=True)
    t.start()

    uvicorn.run(app, host=host, port=port)
