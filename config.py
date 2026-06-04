# -*- coding: utf-8 -*-
# ============================================================
#  财务科 - 接龙机器人配置
# ============================================================

GROUP_NAME = "财务科"

# 员工名单（必须和 wxauto 的 sender_remark 完全一致！）
GROUP_MEMBERS = [
    "张三",
    "李四",
    "王五",
    "赵六",
    "钱七",
    "孙八",
    "周九",
    "吴十",
    "郑华",
    "冯明",
    "孙莉",
    "AI助手",
]

# 接龙文本中的名字 → sender_remark 的映射
# 昵称已统一为身份证姓名，无需强盛xxx别名
JIELONG_NAME_MAP = {
    "张三": "张三",
    "李四": "李四",
    "王五": "王五",
    "赵六": "赵六",
    "钱七": "钱七",
    "孙八": "孙八",
    "周九": "周九",
    "吴十": "吴十",
    "郑华": "郑华",
    "冯明": "冯明",
    "孙莉": "孙莉",
    "AI助手": "AI助手",
}

# 排除检查的人
EXCLUDE_MEMBERS = ["孙莉", "AI助手"]

# 请假识别（固定格式：@AI助手 请假）
LEAVE_REQUIRE_HELPER_MENTION = True
LEAVE_HELPER_KEYWORDS = ["@AI助手", "AI助手"]

# 汇总发给谁
REPORT_TO = "孙莉"

# 接龙模板（自动带当天日期）
def get_jielong_template():
    from datetime import datetime
    today = datetime.now().strftime("%Y.%m.%d")
    return (
        f"#接龙   {today}日报\n"
        f"注：每人姓名仅可出现1次，工作内容以1）格式进行编号\n\n"
        f"1. AI助手"
    )

# 定时任务
JIELONG_TIME = "16:30"
CUIBAN_TIME = "17:10"
HUIZONG_TIME = "17:35"

# 指令关键词 / command keywords
CMD_JIELONG = "start"
CMD_CUIBAN = "remind"
CMD_HUIZONG = "summarize"
CMD_STATUS = "status"

# 文案配置
JIELONG_AT_ALL_TEXT = "@所有人"
CUIBAN_AT_TEXT = "写日报"
HUIZONG_GROUP_ACK_TEXT = "日报已汇整发送，又是美好的一天"

# 安全延迟（秒）
MIN_DELAY = 1.5
MAX_DELAY = 3.5
WORKDAY_ONLY = True


def get_effective_members():
    """返回需要纳入接龙统计/催办的人。"""
    excluded = set(EXCLUDE_MEMBERS)
    return [m for m in GROUP_MEMBERS if m not in excluded]
