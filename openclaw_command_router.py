# -*- coding: utf-8 -*-
"""
OpenClaw 指令路由器（本地直连版）

用法：
  python openclaw_command_router.py 发接龙
  python openclaw_command_router.py 催办
  python openclaw_command_router.py 汇总
  python openclaw_command_router.py 状态
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
    "发接龙": "jielong",
    "接龙": "jielong",
    "jielong": "jielong",
    "催办": "cuiban",
    "cuiban": "cuiban",
    "汇总": "huizong",
    "huizong": "huizong",
    "状态": "status",
    "status": "status",
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
            "error": "缺少指令。可用：发接龙/催办/汇总/状态"
        })
        raise SystemExit(2)

    action = ALIASES.get(cmd_raw)
    if not action:
        _print_json({
            "success": False,
            "error": f"不支持的指令: {cmd_raw}",
            "supported": ["发接龙", "催办", "汇总", "状态"],
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
