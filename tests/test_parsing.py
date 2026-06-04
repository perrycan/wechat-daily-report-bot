# -*- coding: utf-8 -*-
"""
纯函数单元测试（无需微信 / 无需网络）。
覆盖：姓名归一化、接龙参与者解析、按日期截断消息、花名册差集、时间解析。
Run:  pytest
"""
from datetime import datetime, timedelta

import config
import bot_core as bc


# ---------- normalize_name ----------
def test_normalize_name_strips_symbols_and_emoji():
    assert bc.normalize_name("  张三 ") == "张三"
    assert bc.normalize_name("张三😀") == "张三"
    assert bc.normalize_name("AI助手") == "ai助手"        # 英文转小写
    assert bc.normalize_name(None) == ""


# ---------- parse_jielong_participants ----------
def test_parse_jielong_participants_picks_names_not_content():
    text = (
        "#接龙   2026.01.02日报\n"
        "注：每人姓名仅可出现1次\n"
        "1. AI助手\n"
        "2. 张三\n"
        "1）做了一件事\n"           # 工作内容编号（无英文点）不应被当成姓名
        "3. 李四\n"
        "1、又做了一件事\n"
    )
    names = bc.parse_jielong_participants(text)
    assert "张三" in names
    assert "李四" in names
    # 工作内容不应被识别为参与者
    assert "做了一件事" not in names


def test_parse_jielong_participants_ignores_unknown_names():
    text = "1. 路人甲\n2. 王五\n"
    names = bc.parse_jielong_participants(text)
    assert "王五" in names
    assert "路人甲" not in names      # 不在花名册，应被过滤


# ---------- get_effective_members ----------
def test_effective_members_excludes_leader_and_bot():
    eff = config.get_effective_members()
    for excluded in config.EXCLUDE_MEMBERS:
        assert excluded not in eff
    assert "张三" in eff
    assert "AI助手" not in eff


# ---------- parse_clock_time / parse_mmdd_date ----------
def test_parse_clock_time():
    assert bc.parse_clock_time("今天 17:35 提醒") == "17:35"
    assert bc.parse_clock_time("9:05") == "09:05"
    assert bc.parse_clock_time("无时间") is None


def test_parse_mmdd_date():
    assert bc.parse_mmdd_date("3月18日 10:46") == (3, 18)
    assert bc.parse_mmdd_date("12月1日") == (12, 1)
    assert bc.parse_mmdd_date("没有日期") is None


# ---------- _slice_msgs_since_today ----------
def _t(content):
    return {"type": "time", "content": content, "sender": ""}


def _m(sender, content):
    return {"type": "friend", "sender": sender, "content": content}


def test_slice_msgs_since_today_drops_old_history():
    today = datetime.now()
    yest = today - timedelta(days=1)
    old = today - timedelta(days=40)
    msgs = [
        _t(f"{old.month}月{old.day}日 09:00"),
        _m("张三", "OLD_X"),
        _t(f"{yest.month}月{yest.day}日 18:00"),
        _m("李四", "YEST_Y"),
        _t(f"{today.month}月{today.day}日 17:00"),
        _m("王五", "TODAY_Z"),
    ]
    sliced = bc._slice_msgs_since_today(msgs)
    contents = [m.get("content") for m in sliced]
    assert "TODAY_Z" in contents       # 今天的消息保留
    assert "OLD_X" not in contents     # 40 天前的旧历史被丢弃


def test_slice_msgs_no_time_marker_returns_tail():
    msgs = [_m("张三", f"msg{i}") for i in range(200)]
    sliced = bc._slice_msgs_since_today(msgs)
    assert len(sliced) <= 80           # 无日期标识时只看尾部少量消息
