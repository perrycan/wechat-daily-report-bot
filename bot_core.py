# -*- coding: utf-8 -*-
"""
bot_core.py —— 核心业务逻辑：发接龙、催办、汇总
"""
import re
import time
import random
import sys
from datetime import datetime
import config

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pass


def delay():
    time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))


def log(message):
    """避免 Windows GBK 控制台因 emoji 输出报错影响主流程。"""
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = text.encode(enc, errors="ignore").decode(enc, errors="ignore")
        print(safe)


def call_with_retry(fn, action_name, retries=3, wait_seconds=1.0):
    last_err = None
    for i in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i < retries:
                log(f"  [WARN] {action_name} 失败，第{i}次重试: {e}")
                time.sleep(wait_seconds)
            else:
                log(f"  [ERR] {action_name} 最终失败: {e}")
                raise
    raise last_err


def _chatwith_compat(wx, target, exact=True, force=False, force_wait=0.5):
    """
    兼容不同 WeChat SDK 的 ChatWith 参数签名。
    新版支持 force/force_wait，旧版不支持。
    """
    try:
        return wx.ChatWith(target, exact=exact, force=force, force_wait=force_wait)
    except TypeError:
        try:
            return wx.ChatWith(target, exact=exact)
        except TypeError:
            return wx.ChatWith(target)


def chat_with(wx, target):
    """更稳妥地切换聊天对象：先尝试切回聊天页，再切会话。"""
    target_norm = normalize_name(target)

    def _is_on_target():
        try:
            chatbox = getattr(wx, "ChatBox", None)
            current = str(getattr(chatbox, "who", "") or "").strip()
        except Exception:
            # ChatBox.who 是 property，可能抛 KeyError 等非 AttributeError
            return False
        if not current:
            return False
        cur_norm = normalize_name(current)
        return (
            cur_norm == target_norm
            or (target_norm and target_norm in cur_norm)
            or (cur_norm and cur_norm in target_norm)
        )

    # 快捷路径：已在目标会话则直接返回，避免不必要的窗口操作
    if _is_on_target():
        return True

    def _recover_chat_surface():
        _ensure_wechat_foreground(wx)
        try:
            if hasattr(wx, "SwitchToChat"):
                wx.SwitchToChat()
                time.sleep(0.2)
        except Exception:
            pass

    def _switch():
        # 优先走更稳的策略：force=True 可减少"找不到 EditControl"瞬时超时。
        strategies = [
            {"exact": True, "force": True, "force_wait": 1.0, "post_wait": 0.35},
            {"exact": False, "force": True, "force_wait": 1.0, "post_wait": 0.35},
            {"exact": True, "force": False, "force_wait": 0.5, "post_wait": 0.25},
            {"exact": False, "force": False, "force_wait": 0.5, "post_wait": 0.25},
        ]

        last_err = None
        for _ in range(2):
            _recover_chat_surface()
            for st in strategies:
                try:
                    _chatwith_compat(
                        wx,
                        target,
                        exact=st["exact"],
                        force=st["force"],
                        force_wait=st["force_wait"],
                    )
                    time.sleep(st["post_wait"])
                    if _is_on_target():
                        return True
                except Exception as e:
                    last_err = e
                    msg = str(e or "")
                    # 该错误大多是 UI 尚未稳定，拉回聊天页后继续下一策略。
                    if "Find Control Timeout" in msg and "EditControl" in msg:
                        _recover_chat_surface()
                        continue
                    continue

        # 最后兜底：直接调用 SessionBox.switch_chat
        try:
            sb = getattr(wx, "SessionBox", None)
            if sb is not None and hasattr(sb, "switch_chat"):
                try:
                    sb.switch_chat(target, exact=True, force=True, force_wait=1.0)
                except TypeError:
                    try:
                        sb.switch_chat(target, exact=True)
                    except TypeError:
                        sb.switch_chat(target)
                time.sleep(0.35)
                if _is_on_target():
                    return True
        except Exception as e:
            last_err = e

        if last_err:
            raise last_err
        current = str(getattr(getattr(wx, "ChatBox", None), "who", "") or "")
        raise RuntimeError(f"会话切换校验失败，target={target}, current={current}")

    return call_with_retry(_switch, f"切换会话({target})", retries=3, wait_seconds=0.8)


def send_at(wx, name, text):
    """兼容不同 WeChat SDK 的@发送写法。"""
    msg = str(text or "").strip()
    # 避免重复 @：如果文本已经以"@姓名"开头，先去掉再走 at 参数
    msg = re.sub(rf"^(?:@\s*{re.escape(name)}(?:\u2005|\s)*)+", "", msg).strip()
    if not msg:
        msg = config.CUIBAN_AT_TEXT
    try:
        call_with_retry(lambda: wx.SendMsg(msg, who=config.GROUP_NAME, at=name), f"@发送({name})")
    except TypeError:
        # 兜底（旧接口不支持 at 参数）时，手动补一个 @前缀
        call_with_retry(lambda: wx.SendMsg(f"@{name} {msg}", who=config.GROUP_NAME), f"发送({name})")


def send_multi_at(wx, names, text):
    """一次性 @ 多人并发送催办文案。

    wxautox4 的 SendMsg(msg, at=list) 在人数较多时会将 msg 文本插到
    @ 列表中间而非末尾，导致催办文案夹在人名之间。
    修复：msg 只传一个不可见占位符，@完后再单独发一条文案消息。
    """
    uniq_names = []
    for n in names or []:
        n = str(n or "").strip()
        if n and n not in uniq_names:
            uniq_names.append(n)
    if not uniq_names:
        return

    msg = str(text or "").strip() or config.CUIBAN_AT_TEXT
    msg = re.sub(r"^(?:@\s*[^\s\u2005]+(?:\u2005|\s)*)+", "", msg).strip()
    if not msg:
        msg = config.CUIBAN_AT_TEXT

    # 第一条：@所有人，msg 用文案本身（人少时不会出现插入中间的问题）
    # 第二条：如果人多（>3人），改为 msg 传换行占位，@完后再发文案
    if len(uniq_names) <= 3:
        call_with_retry(
            lambda: wx.SendMsg(msg, who=config.GROUP_NAME, at=uniq_names),
            f"@多人发送({len(uniq_names)}人)",
        )
    else:
        # @所有人时 msg 只放换行占位
        call_with_retry(
            lambda: wx.SendMsg("\n", who=config.GROUP_NAME, at=uniq_names),
            f"@多人发送({len(uniq_names)}人)",
        )
        time.sleep(0.3)
        # 紧跟发送文案
        call_with_retry(
            lambda: wx.SendMsg(msg, who=config.GROUP_NAME),
            f"发送催办文案",
        )


def send_at_all(wx, text):
    """尽最大可能实现 @所有人。"""
    at_msg = str(text or "").strip()
    mention_only = at_msg in {"@所有人", "所有人"}
    # 非"纯提及"场景下，去掉手工重复前缀，避免"@所有人 @所有人 ..."
    if not mention_only:
        at_msg = re.sub(r"^(?:@\s*所有人(?:\u2005|\s)*)+", "", at_msg).strip()

    # 1) 优先调用 wxauto 的原生 AtAll（微信菜单选择"所有人"）
    try:
        if hasattr(wx, "AtAll") and not mention_only:
            wx.AtAll(at_msg, who=config.GROUP_NAME)
            log("  @所有人发送方式: wx.AtAll(who=GROUP_NAME)")
            return True
    except TypeError:
        try:
            # 某些实现为 AtAll(msg) 且当前已在目标会话。
            # 纯提及场景不走这个分支（该分支在部分版本会发出"@"）。
            if not mention_only:
                chat_with(wx, config.GROUP_NAME)
                delay()
                wx.AtAll(at_msg)
                log("  @所有人发送方式: wx.AtAll(current_chat)")
                return True
        except Exception:
            pass
    except Exception:
        pass

    # 2) 纯提及"@所有人"：使用微信输入框原生选择并发送，避免发成单个"@"。
    if mention_only:
        try:
            import pyautogui
            import wxautox4.uia as uia

            chat_with(wx, config.GROUP_NAME)
            delay()
            chatbox = getattr(wx, "ChatBox", None)
            editbox = getattr(chatbox, "editbox", None) if chatbox is not None else None
            if editbox is None:
                raise RuntimeError("当前后端未暴露 ChatBox.editbox")

            rect = editbox.BoundingRectangle
            cx = int(rect.left + rect.width() / 2)
            cy = int(rect.top + rect.height() / 2)
            pyautogui.click(cx, cy)
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.press("delete")
            pyautogui.typewrite("@", interval=0.02)
            time.sleep(0.35)

            at_all_item = uia.ListItemControl(Name="所有人", searchDepth=60)
            if at_all_item.Exists(0.6):
                at_all_item.Click(simulateMove=False)
            else:
                pyautogui.press("down")
                time.sleep(0.1)
                pyautogui.press("enter")

            time.sleep(0.2)
            pyautogui.press("enter")
            log("  @所有人发送方式: native_input_mention")
            return True
        except Exception as e:
            log(f"  [WARN] @所有人原生输入发送失败: {e}")

    # 3) 兼容没有 AtAll 的实现
    candidates = [("所有人", at_msg), ("all", at_msg)]
    for at_target, candidate_msg in candidates:
        if mention_only and not candidate_msg:
            # 空消息在部分版本会退化成"@"，因此跳过。
            continue
        try:
            call_with_retry(lambda: wx.SendMsg(candidate_msg, who=config.GROUP_NAME, at=at_target), f"@所有人发送(at={at_target})")
            log(f"  @所有人发送方式: SendMsg(at={at_target})")
            return True
        except Exception:
            pass

    # 4) 最后兜底：普通文本（保留可用性）
    try:
        fallback = "@所有人" if (mention_only or not at_msg) else f"@所有人 {at_msg}"
        call_with_retry(lambda: wx.SendMsg(fallback, who=config.GROUP_NAME), "@所有人发送(文本兜底)")
        log("  @所有人发送方式: plain_text_fallback")
        return True
    except Exception:
        pass
    return False

def parse_clock_time(text):
    """从文本中提取 HH:MM。"""
    if not text:
        return None
    m = re.search(r'([01]?\d|2[0-3]):([0-5]\d)', str(text))
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_mmdd_date(text):
    """
    从微信时间分隔文本中提取"月日"，如：
    - 3月18日 10:46
    - 3月18日
    """
    if not text:
        return None
    m = re.search(r'([1-9]|1[0-2])月([1-9]|[12]\d|3[01])日', str(text))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def normalize_name(text):
    """统一昵称格式，去掉空白/符号/emoji，便于映射。"""
    if text is None:
        return ""
    s = str(text).strip().lower()
    # 保留中文、英文、数字
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", s)


def _ensure_wechat_foreground(wx):
    """尽量把微信主窗口拉到前台，降低 Ctrl+F 误打到其他窗口的概率。"""
    try:
        if hasattr(wx, "Show"):
            wx.Show()
    except Exception:
        pass
    try:
        chatbox = getattr(wx, "ChatBox", None)
        ctl = getattr(chatbox, "control", None) if chatbox is not None else None
        if ctl is not None and hasattr(ctl, "SetFocus"):
            ctl.SetFocus()
    except Exception:
        pass
    # 通过 Win32 API 强制将微信窗口激活到前台
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "微信")  # "微信"
        if not hwnd:
            hwnd = user32.FindWindowW("WeChat MainWndForPC", None)
        if hwnd:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            # AttachThreadInput 绕过 Windows 前台锁定限制
            fg_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
            if fg_tid != tgt_tid:
                user32.AttachThreadInput(fg_tid, tgt_tid, True)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            if fg_tid != tgt_tid:
                user32.AttachThreadInput(fg_tid, tgt_tid, False)
    except Exception:
        pass


def _native_jielong_keyword():
    # 避免运行环境编码问题，使用 Unicode 码点构造"#接龙"。
    return "#" + chr(0x63A5) + chr(0x9F99)


def _latest_jielong_via_wechat_search(wx, timeout_seconds=8.0):
    """
    通过微信群聊天记录按钮提取"最新未折叠接龙"：
    1) 点击群聊顶部的"聊天记录"按钮打开聊天记录搜索窗口；
    2) 输入当天日期（如 2026.03.29）；
    3) 在搜索结果中找到 #接龙 + 当天日期的条目并提取全文。
    """
    import pyautogui
    import pyperclip
    import wxautox4.uia as uia

    date_tag = datetime.now().strftime("%Y.%m.%d")
    keyword = _native_jielong_keyword()
    # 的聊天记录 = "的聊天记录"
    dlg_keyword = "的聊天记录"

    _ensure_wechat_foreground(wx)
    chat_with(wx, config.GROUP_NAME)
    time.sleep(0.3)

    # 点击群聊顶部的"聊天记录"按钮
    # 聊天记录 = "聊天记录"
    chat_history_btn = uia.ButtonControl(Name="聊天记录", searchDepth=30)
    if not chat_history_btn.Exists(0.5):
        raise RuntimeError("未找到群聊“聊天记录”按钮。")
    chat_history_btn.Click(simulateMove=False)
    time.sleep(0.8)

    # 定位聊天记录窗口（名称形如 "财务科"的聊天记录(N)）
    dialog = None
    drect = None
    for idx in range(1, 8):
        w = uia.WindowControl(foundIndex=idx, searchDepth=30)
        if not w.Exists(0.1):
            continue
        wname = str(getattr(w, "Name", "") or "")
        if dlg_keyword in wname:
            dialog = w
            drect = w.BoundingRectangle
            break
    if dialog is None or drect is None:
        raise RuntimeError("未打开聊天记录搜索窗口。")

    # 定位搜索输入框（窗口内第一个 EditControl）
    dialog_edit = None
    for idx in range(1, 10):
        e = uia.EditControl(foundIndex=idx, searchDepth=100)
        if not e.Exists(0.05):
            continue
        try:
            er = e.BoundingRectangle
            if drect.left <= er.left <= drect.right and drect.top <= er.top <= drect.bottom:
                dialog_edit = e
                break
        except Exception:
            continue
    if dialog_edit is None:
        raise RuntimeError("未在聊天记录窗口中定位到搜索输入框。")

    # 输入日期查询
    er = dialog_edit.BoundingRectangle
    pyautogui.click(int(er.left + 20), int(er.top + 10))
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.06)
    pyautogui.press("delete")
    time.sleep(0.06)
    pyperclip.copy(date_tag)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.0)

    def _scan_items():
        cands = []
        fb = []
        miss_streak = 0
        for idx in range(1, 25):
            item = uia.ListItemControl(foundIndex=idx, searchDepth=60)
            if not item.Exists(0.02):
                miss_streak += 1
                if miss_streak >= 3:
                    break
                continue
            miss_streak = 0
            try:
                cls = str(getattr(item, "ClassName", "") or "")
                if "ChatTextItemView" not in cls:
                    continue
                text = str(getattr(item, "Name", "") or "")
                r = item.BoundingRectangle
                # 约束在聊天记录窗口内部
                if not (drect.left <= r.left <= drect.right and drect.top <= r.top <= drect.bottom):
                    continue
                packed = (int(r.top), text, (int(r.left), int(r.top), int(r.right), int(r.bottom)))
                fb.append(packed)
                if keyword in text and date_tag in text:
                    cands.append(packed)
                    break
            except Exception:
                continue
        return cands, fb

    candidates, fallback_items = _scan_items()

    if not candidates:
        candidates = [x for x in fallback_items if date_tag in x[1]]
    if not candidates:
        candidates = fallback_items
    if not candidates:
        # 关闭聊天记录窗口
        pyautogui.press("escape")
        time.sleep(0.15)
        raise RuntimeError("聊天记录窗口中未找到当天接龙正文。")
    candidates.sort(key=lambda x: x[0])
    latest_text = candidates[0][1]

    # 判断 Name 文本是否已足够完整（需覆盖大部分成员才算完整）
    participant_lines = re.findall(r'^\d+\.\s*.+', latest_text, re.MULTILINE)
    effective_count = len(config.get_effective_members())
    # 预览文本至少需包含一半以上成员的编号行才视为完整，否则必须双击复制全文
    text_sufficient = (
        len(participant_lines) >= max(effective_count // 2, 5)
        and keyword in latest_text
    )

    # 始终尝试双击复制完整文本，仅在复制失败时回退到预览文本
    l, t, r, b = candidates[0][2]
    pyautogui.doubleClick(int((l + r) / 2), int((t + b) / 2))
    time.sleep(0.3)
    try:
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.15)
        clip = str(pyperclip.paste() or "")
        if keyword in clip and date_tag in clip:
            latest_text = clip
    except Exception:
        pass
    time.sleep(0.15)
    pyautogui.press("escape")
    time.sleep(0.15)

    # 恢复 SDK 会话状态
    try:
        _chatwith_compat(wx, config.GROUP_NAME, exact=True, force=False, force_wait=0.3)
        time.sleep(0.15)
    except Exception:
        pass

    return latest_text


def _native_jielong_text_lines():
    """
    从配置模板中提取原始接龙文案：
    1) #接龙   YYYY.MM.DD日报
    2) 注：每人姓名仅可出现1次
    """
    tpl = str(config.get_jielong_template() or "")
    lines = [ln.strip() for ln in tpl.splitlines() if ln.strip()]
    title = lines[0] if lines else _native_jielong_keyword()
    note = ""
    if len(lines) >= 2:
        for ln in lines[1:]:
            if ln.startswith("注"):
                note = ln
                break
        if not note:
            note = lines[1]
    return title, note


def _collect_native_jielong_dialog_edits(timeout_seconds=6.0):
    """
    采集"编辑接龙表格"弹窗里的文本输入框：
    - 第1个：接龙标题
    - 第2个：注释
    """
    import wxautox4.uia as uia

    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        # 在当前微信 4.1.x 弹窗中，foundIndex=1/2 分别为标题与注释输入框。
        e1 = uia.EditControl(foundIndex=1, searchDepth=70)
        e2 = uia.EditControl(foundIndex=2, searchDepth=70)
        if e1.Exists(0.15) and e2.Exists(0.15):
            c1 = str(getattr(e1, "ClassName", "") or "")
            c2 = str(getattr(e2, "ClassName", "") or "")
            if "XValidatorTextEdit" in c1 and "XValidatorTextEdit" in c2:
                return [e1, e2]
        time.sleep(0.2)
    return []


def _set_edit_control_text(edit_ctrl, text):
    import pyautogui
    import pyperclip

    rect = edit_ctrl.BoundingRectangle
    cx = int(rect.left + rect.width() / 2)
    cy = int(rect.top + rect.height() / 2)
    pyautogui.click(cx, cy)
    time.sleep(0.12)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.08)
    pyautogui.press("delete")
    pyperclip.copy(str(text or ""))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)


def _fill_native_jielong_dialog_text():
    """
    在"编辑接龙表格"弹窗中写入接龙标题与注释文案。
    优先直接定位两个输入框，避免 Tab 焦点错位导致标题丢失。
    """
    import pyautogui
    import pyperclip

    title, note = _native_jielong_text_lines()

    edits = _collect_native_jielong_dialog_edits(timeout_seconds=6.0)
    if len(edits) >= 2:
        _set_edit_control_text(edits[0], title)
        if note:
            _set_edit_control_text(edits[1], note)
        return True

    # 兜底：保留旧方案（依赖默认焦点 + Tab）
    # 第1个输入框：接龙标题
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    pyperclip.copy(title)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)

    # 跳到第2个输入框：注释/格式提示
    pyautogui.press("tab")
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    if note:
        pyperclip.copy(note)
        pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    return True


def _find_and_click_launch_jielong_button(timeout_seconds=6.0):
    """
    在微信"编辑接龙表格"弹窗中点击"发起接龙"按钮。
    """
    import wxautox4.uia as uia

    button_name = "".join(chr(c) for c in [0x53D1, 0x8D77, 0x63A5, 0x9F99])  # 发起接龙
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        btn = uia.ButtonControl(Name=button_name, searchDepth=40)
        if btn.Exists(0.2):
            btn.Click(simulateMove=False)
            return True
        time.sleep(0.15)
    return False


def _send_jielong_by_wechat_native(wx):
    """
    使用微信原生"#接龙 -> 编辑接龙表格 -> 发起接龙"流程发起接龙。
    """
    import pyautogui
    import pyperclip

    chat_with(wx, config.GROUP_NAME)
    delay()

    chatbox = getattr(wx, "ChatBox", None)
    editbox = getattr(chatbox, "editbox", None) if chatbox is not None else None
    if editbox is None:
        raise RuntimeError("当前后端未暴露 ChatBox.editbox，无法走微信原生接龙流程。")

    rect = editbox.BoundingRectangle
    cx = int(rect.left + rect.width() / 2)
    cy = int(rect.top + rect.height() / 2)

    # 聚焦输入框并粘贴"#接龙"
    pyautogui.click(cx, cy)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")
    pyperclip.copy(_native_jielong_keyword())
    pyautogui.hotkey("ctrl", "v")

    # 选择弹出的"编辑接龙表格"
    time.sleep(0.8)
    pyautogui.press("down")
    time.sleep(0.2)
    pyautogui.press("enter")

    # 写回原始文案（标题 + 注释）
    time.sleep(0.6)
    _fill_native_jielong_dialog_text()

    # 点击"发起接龙"
    time.sleep(0.5)
    if not _find_and_click_launch_jielong_button(timeout_seconds=6.0):
        raise RuntimeError("未找到“发起接龙”按钮，微信原生接龙发起失败。")

    time.sleep(1.0)
    return True


def send_jielong(wx):
    """16:00 发起接龙"""
    try:
        call_with_retry(lambda: _send_jielong_by_wechat_native(wx), "微信原生发接龙")
        delay()
        if send_at_all(wx, config.JIELONG_AT_ALL_TEXT):
            log("  已发送 @所有人 提醒")
        else:
            log("  [WARN] @所有人发送失败，已降级为普通文本/忽略")
        log(f"[{datetime.now():%H:%M:%S}] 接龙已发送")
        return True
    except Exception as e:
        log(f"[ERR] 发接龙失败: {e}")
        return False


def _normalize_msgs(raw):
    results = []
    for msg in raw or []:
        t = getattr(msg, 'type', '')
        if t == 'sys':
            continue
        sender = getattr(msg, 'sender_remark', '') or getattr(msg, 'sender', '')
        content = getattr(msg, 'content', '') or ''
        is_self = (t == 'self')
        results.append({
            'type': t,
            'sender': sender,
            'content': content,
            'is_self': is_self,
        })
    return results


def _slice_msgs_since_today(msgs):
    """
    利用微信群"日期分隔标识"（如 3月18日 10:46）截断旧历史，
    仅保留今天附近的消息，避免向前遍历整年数据。
    """
    if not msgs:
        return []
    today = datetime.now()
    start = None
    first_today_idx = None
    has_time_marker = False
    # 从最新向前找最近一个"非今天日期"分隔线。
    for idx in range(len(msgs) - 1, -1, -1):
        m = msgs[idx]
        if m.get("type") != "time":
            continue
        has_time_marker = True
        md = parse_mmdd_date(m.get("content", ""))
        if not md:
            continue
        if md == (today.month, today.day):
            first_today_idx = idx
            continue
        if first_today_idx is not None:
            start = idx + 1
            break
        # 已遇到非今天且还没遇到今天，说明当前视图不在当天区间
        # （例如聊天窗口停在更早日期），直接返回空，避免误读旧消息。
        start = len(msgs)
        break

    if start is not None:
        return msgs[start:]
    if first_today_idx is not None:
        return msgs[first_today_idx:]

    # 没有任何日期分隔标识时，最多只看尾部少量消息，避免扫全量历史。
    if not has_time_marker:
        return msgs[-80:]
    return []


def get_all_messages(wx, prefer_history=False, history_n=220):
    """读取群消息并返回结构化列表。prefer_history=True 时优先读取更多历史。"""
    chat_with(wx, config.GROUP_NAME)
    time.sleep(0.3)
    if prefer_history:
        try:
            chatbox = getattr(wx, "ChatBox", None)
            get_hist = getattr(chatbox, "get_msgs_from_history", None) if chatbox is not None else None
            if callable(get_hist):
                raw_hist = call_with_retry(lambda: get_hist(int(history_n)), f"读取群历史消息({history_n})", retries=2)
                normalized = _normalize_msgs(raw_hist)
                if normalized:
                    return normalized
        except Exception as e:
            log(f"  [WARN] 读取历史消息失败，回退 GetAllMessage: {e}")

    raw = call_with_retry(lambda: wx.GetAllMessage(), "读取群消息")
    return _normalize_msgs(raw)


def parse_jielong_participants(content):
    """
    从接龙消息正文中提取参与者名单
    参与者格式：N. 姓名（微信接龙自动编号）
    注：姓名下方工作内容的序号分隔（1、/1,/1.）不参与姓名识别
    """
    names = []
    known_alias = sorted(config.JIELONG_NAME_MAP.keys(), key=len, reverse=True)
    alias_keys = sorted(config.JIELONG_NAME_MAP.keys(), key=len, reverse=True)
    for line in content.split('\n'):
        line = line.strip()
        # 姓名行严格按"数字 + 英文点 + 空格/姓名"识别，避免误把工作内容当姓名。
        m_name = re.match(r'^\d+\.\s*(.+)$', line)
        if not m_name:
            continue
        body = m_name.group(1).strip()
        body_norm = normalize_name(body)
        # 先按已知别名定位，要求"以该姓名开头"，避免内容里提到他人姓名造成误识别
        picked = None
        for alias in alias_keys:
            alias_norm = normalize_name(alias)
            if alias_norm and body_norm.startswith(alias_norm):
                picked = alias
                break
        if picked:
            names.append(picked)
            continue

        # 回退：提取首个词，并要求能匹配到姓名库，避免把工作内容误识别为姓名。
        raw = re.split(r'[\s:：,，;；\(\)（）\[]', body, maxsplit=1)[0].strip()
        if raw and any(normalize_name(a) == normalize_name(raw) for a in known_alias):
            names.append(raw)
    return names


def collect_today_jielong_messages(msgs):
    """
    收集当天接龙相关消息（包含折叠态"展开"文本）。
    说明：微信接龙卡片在 UI 折叠时，GetAllMessage 可能只返回片段，
    因此这里会聚合"当天所有含 #接龙 的消息快照"。
    """
    keyword = _native_jielong_keyword()
    today_tag = datetime.now().strftime("%Y.%m.%d")
    picked = []
    for m in msgs:
        content = m.get('content', '') or ''
        if keyword not in content:
            continue
        if today_tag not in content:
            continue
        picked.append(m)
    return picked


def find_last_jielong(msgs):
    """
    找到最后一条真实的接龙消息
    跳过机器人自己发的空模板
    """
    candidates = []
    for m in collect_today_jielong_messages(msgs):
        content = m.get('content', '') or ''
        participant_lines = re.findall(r'^\d+\.\s*.+', content, re.MULTILINE)
        score = (len(participant_lines), len(content))
        candidates.append((score, m))
    if not candidates:
        return None
    # 优先非机器人自己发出的快照；同分时取内容更长的。
    non_self = [x for x in candidates if not x[1].get('is_self')]
    pool = non_self or candidates
    pool.sort(key=lambda x: x[0], reverse=True)
    return pool[0][1].get('content', '')


def get_participated(wx, use_search=True, return_jielong_text=False):
    """获取已接龙的人；可选返回提取到的最新接龙正文。"""
    participated = set()
    members = set(config.get_effective_members())

    raw_names = []
    latest_full = ""
    if use_search:
        # 可选：按"顶部搜索 -> 搜索聊天记录"拿最新未折叠接龙（会操作微信搜索框）
        try:
            latest_full = _latest_jielong_via_wechat_search(wx) or ""
            raw_names.extend(parse_jielong_participants(latest_full))
        except Exception as e:
            log(f"  [WARN] 搜索窗口提取接龙失败，回退历史解析: {e}")

    # 仅在搜索提取失败/为空时才重拉历史，减少耗时。
    msgs = []
    jielong_msgs = []
    try:
        if raw_names:
            msgs = get_all_messages(wx, prefer_history=False)
            msgs = _slice_msgs_since_today(msgs)
            jielong_msgs = collect_today_jielong_messages(msgs)
        else:
            msgs = get_all_messages(wx, prefer_history=True, history_n=40)
            msgs = _slice_msgs_since_today(msgs)
            jielong_msgs = collect_today_jielong_messages(msgs)
            if jielong_msgs:
                for m in jielong_msgs:
                    raw_names.extend(parse_jielong_participants(m.get('content', '') or ''))
            else:
                log("  [WARN] 未找到接龙消息，尝试按发送人兜底识别")
    except Exception as e:
        log(f"  [WARN] 获取群消息失败，跳过消息回退解析: {e}")
        jielong_msgs = []

    # 保序去重
    dedup_names = []
    for n in raw_names:
        if n not in dedup_names:
            dedup_names.append(n)
    raw_names = dedup_names
    if raw_names:
        log(f"  接龙中的名字: {raw_names}")

    normalized_alias_map = []
    for alias, remark in config.JIELONG_NAME_MAP.items():
        normalized_alias_map.append((normalize_name(alias), remark, alias))
    normalized_alias_map.sort(key=lambda x: len(x[0]), reverse=True)

    for name in raw_names:
        remark = config.JIELONG_NAME_MAP.get(name)
        if not remark:
            n = normalize_name(name)
            for alias_norm, mapped_remark, _ in normalized_alias_map:
                if not alias_norm:
                    continue
                if n == alias_norm or alias_norm in n or n in alias_norm:
                    remark = mapped_remark
                    break
        if remark and remark in members:
            participated.add(remark)

    # 兜底：按"当日接龙窗口内发送 #接龙 的发送人"计入已接龙，降低正文格式不规范导致的漏识别。
    keyword = _native_jielong_keyword()
    today_tag = datetime.now().strftime("%Y.%m.%d")
    for m in msgs:
        sender = m.get("sender", "")
        c = m.get("content", "")
        # 折叠卡片场景：按"发送人 + 当天 #接龙"兜底识别为已接龙。
        if sender in members and keyword in c and today_tag in c:
            participated.add(sender)

    participated_list = list(participated)
    if not return_jielong_text:
        return participated_list

    jielong_text = str(latest_full or "").strip()
    if not jielong_text:
        try:
            jielong_text = str(find_last_jielong(msgs) or "").strip()
        except Exception:
            jielong_text = ""
    return participated_list, jielong_text


def get_leave_members(wx, cutoff_time=None, msgs=None):
    """
    识别请假人员：
    1) 默认要求消息包含"请假"；
    2) 若 LEAVE_REQUIRE_HELPER_MENTION=True，则还需出现"小助手/AI助手"等关键词；
    3) 若能识别到时间分隔消息，则仅统计 cutoff_time 之前（默认 CUIBAN_TIME）。
    可通过 msgs 参数传入已读取的消息列表，避免重复读取。
    """
    if msgs is None:
        try:
            msgs = get_all_messages(wx)
            msgs = _slice_msgs_since_today(msgs)
        except Exception as e:
            log(f"  [WARN] 读取请假消息失败: {e}")
            return []
    leave = set()
    members = set(config.get_effective_members())
    helper_keywords = tuple(config.LEAVE_HELPER_KEYWORDS)
    require_helper = bool(config.LEAVE_REQUIRE_HELPER_MENTION)
    cutoff = cutoff_time or config.CUIBAN_TIME
    current_clock = None

    for m in msgs:
        m_type = m.get('type')
        content = m['content']
        sender = m['sender']

        # 时间分隔消息：用于"17:00 前请假不催办"的判定
        if m_type == 'time':
            parsed = parse_clock_time(content)
            if parsed:
                current_clock = parsed
            continue

        if sender not in members:
            continue
        if '请假' not in content:
            continue
        if require_helper and not any(k in content for k in helper_keywords):
            continue
        if current_clock and cutoff and current_clock > cutoff:
            continue

        leave.add(sender)
    return list(leave)


def cuiban(wx):
    """17:00 催办"""
    try:
        log(f"[{datetime.now():%H:%M:%S}] 执行催办...")
        members = config.get_effective_members()
        participated = get_participated(wx, use_search=True)
        # 复用 get_participated 已读取的消息，避免重复读取和窗口操作
        msgs = get_all_messages(wx)
        msgs = _slice_msgs_since_today(msgs)
        leave = get_leave_members(wx, msgs=msgs)
        log(f"  已接龙: {participated}")
        log(f"  请假: {leave}")

        not_done = [m for m in members
                    if m not in participated and m not in leave]
        log(f"  未接龙: {not_done}")

        chat_with(wx, config.GROUP_NAME)
        delay()

        if not not_done:
            call_with_retry(lambda: wx.SendMsg("所有人已完成接龙！", who=config.GROUP_NAME), "发送催办结果")
            return True

        # 一次性 @ 所有未接龙人员
        send_multi_at(wx, not_done, config.CUIBAN_AT_TEXT)
        delay()

        log(f"[{datetime.now():%H:%M:%S}] 催办完成，提醒了 {len(not_done)} 人")
        return True
    except Exception as e:
        log(f"[ERR] 催办失败: {e}")
        return False


def huizong(wx):
    """17:35 汇总（含 LLM 智能总结）"""
    try:
        log(f"[{datetime.now():%H:%M:%S}] 执行汇总...")
        members = config.get_effective_members()
        participated, jielong_text = get_participated(wx, use_search=True, return_jielong_text=True)
        # 向上加载最多40条消息（覆盖当天典型消息量），再按日期截断只保留当天
        msgs = get_all_messages(wx, prefer_history=True, history_n=40)
        msgs = _slice_msgs_since_today(msgs)
        leave = get_leave_members(wx, msgs=msgs)
        not_done = [m for m in members
                    if m not in participated and m not in leave]

        # 汇总复用上一步提取结果，避免重复搜索；仅在空文本时回退历史解析。
        if not jielong_text:
            jielong_text = find_last_jielong(msgs)

        jl_lines = re.findall(r'^\d+\.\s*.+', jielong_text or "", re.MULTILINE)
        log(f"  接龙文本长度={len(jielong_text or '')}字, 识别编号行={len(jl_lines)}行, "
            f"已接龙={len(participated)}人({','.join(participated)}), "
            f"未接龙={len(not_done)}人({','.join(not_done)}), 请假={len(leave)}人")

        today = datetime.now().strftime("%Y-%m-%d")

        # LLM 智能总结
        full_report = ""
        try:
            from llm_summary import summarize
            log(f"  正在调用 LLM 生成总结...")
            summary = summarize(
                jielong_content=jielong_text or "",
                date=today,
                participated=participated,
                not_done=not_done,
                leave=leave,
            )
            if summary:
                full_report = summary
                log(f"  LLM 总结完成")
        except ImportError:
            log(f"  [INFO] 未找到 llm_summary 模块，跳过 AI 总结")
        except Exception as e:
            log(f"  [WARN] LLM 总结异常: {e}")

        if not full_report:
            full_report = f"【{today} {config.GROUP_NAME} 日报汇总】\n（总结生成失败，请查看原始接龙内容）"
        def _send_text_in_chunks(text, who, chunk_size=1800):
            remaining = str(text or "")
            while remaining:
                part = remaining[:chunk_size]
                if len(remaining) > chunk_size:
                    cut = part.rfind("\n")
                    if cut > 200:
                        part = part[:cut]
                call_with_retry(lambda p=part: wx.SendMsg(p, who=who), f"发送消息分片({who})")
                remaining = remaining[len(part):].lstrip("\n")
                if remaining:
                    time.sleep(0.4)

        # 发给领导：发送时强制 who，避免因会话切换异常误发到群里。
        # 会话切换仅作为"尽量提升成功率"的前置步骤，失败不阻塞后续 who 发送。
        try:
            chat_with(wx, config.REPORT_TO)
            delay()
        except Exception as e:
            log(f"  [WARN] 切换领导会话失败，改用 who 直发: {e}")
        _send_text_in_chunks(full_report, config.REPORT_TO)
        delay()

        # 回到群内发送"已汇总"确认文案（需求 3.3）
        chat_with(wx, config.GROUP_NAME)
        delay()
        call_with_retry(lambda: wx.SendMsg(config.HUIZONG_GROUP_ACK_TEXT, who=config.GROUP_NAME), "发送汇总回执")

        log(f"[{datetime.now():%H:%M:%S}] 汇总已发送给 {config.REPORT_TO}")
        return True
    except Exception as e:
        log(f"[ERR] 汇总失败: {e}")
        return False




