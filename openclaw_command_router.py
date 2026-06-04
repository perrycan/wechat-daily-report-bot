# -*- coding: utf-8 -*-
"""
OpenClaw 指令路由器（本地直连版）

用法 / usage:
  python openclaw_command_router.py start       # 发起接龙 / start roll-call
  python openclaw_command_router.py remind      # 催办未交 / remind non-submitters
  python openclaw_command_router.py summarize   # 汇总私发 / summarize to manager
  python openclaw_command_router.py status      # 查看状态 / show status
"""
import json
import sys
import io
import contextlib
from datetime import datetime

import config
import api_server
from bot_core import send_jielong, cuiban, huizong, get_participated, get_leave_members


ALIASES = {
    # English commands (canonical)
    "start": "jielong",
    "rollcall": "jielong",
    "remind": "cuiban",
    "chase": "cuiban",
    "summarize": "huizong",
    "summary": "huizong",
    "status": "status",
    # pinyin aliases (backward compatible)
    "jielong": "jielong",
    "cuiban": "cuiban",
    "huizong": "huizong",
}


def get_status(wx):
    members = config.get_effective_members()
    done = get_participated(wx)
    leave = get_leave_members(wx)
    not_done = [m for m in members if m not in done and m not in leave]
    return {
        "group": config.GROUP_NAME,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(members),
        "done": done,
        "not_done": not_done,
        "leave": leave,
    }


def _print_json(payload):
    # 直接输出中文，减少上层模型对 \uXXXX 转义再解释带来的误差。
    print(json.dumps(payload, ensure_ascii=False))


def _run(func, json_only):
    if not json_only:
        return func()
    with contextlib.redirect_stdout(io.StringIO()):
        return func()


def _parse_args(argv):
    cmd = None
    json_only = False
    for token in argv[1:]:
        value = str(token).strip()
        if value in {"--json", "--json-only"}:
            json_only = True
            continue
        if not cmd:
            cmd = value
    return cmd, json_only


def main():
    cmd_raw, json_only = _parse_args(sys.argv)
    if not cmd_raw:
        _print_json({
            "success": False,
            "error": "missing command. Available: start / remind / summarize / status"
        })
        raise SystemExit(2)

    action = ALIASES.get(cmd_raw)
    if not action:
        _print_json({
            "success": False,
            "error": f"unsupported command: {cmd_raw}",
            "supported": ["start", "remind", "summarize", "status"],
        })
        raise SystemExit(2)

    wx = _run(api_server.init_wx, json_only)
    if not wx:
        err_detail = getattr(api_server, "wx_init_error", None) or "微信初始化失败，请确认微信已登录且窗口可见。"
        _print_json({
            "success": False,
            "action": action,
            "error": str(err_detail),
        })
        raise SystemExit(1)

    if action == "jielong":
        ok = _run(lambda: send_jielong(wx), json_only)
        data = _run(lambda: get_status(wx), json_only)
        _print_json({"success": ok, "action": action, "data": data})
        raise SystemExit(0 if ok else 1)

    if action == "cuiban":
        ok = _run(lambda: cuiban(wx), json_only)
        data = _run(lambda: get_status(wx), json_only)
        _print_json({"success": ok, "action": action, "data": data})
        raise SystemExit(0 if ok else 1)

    if action == "huizong":
        ok = _run(lambda: huizong(wx), json_only)
        data = _run(lambda: get_status(wx), json_only)
        _print_json({"success": ok, "action": action, "data": data})
        raise SystemExit(0 if ok else 1)

    status = _run(lambda: get_status(wx), json_only)
    _print_json({"success": True, "action": action, "data": status})
    raise SystemExit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _print_json({
            "success": False,
            "error": str(e),
        })
        raise SystemExit(1)
