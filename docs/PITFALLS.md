# GUI 自动化避坑设计（工程经验）

← 返回 [README](../README.md) ｜ 另见 [ARCHITECTURE.md](ARCHITECTURE.md)

> 做 PC 微信 UI 自动化（RPA）一定会踩的坑，以及本项目验证过的解法。每条都对应 `bot_core.py` / `api_server.py` 里的实现，可迁移到任何 wxauto 类项目。

### 坑 1 · 微信窗口不在前台，热键/点击打到别的窗口
多重保险拉前台（`_ensure_wechat_foreground`）：`wx.Show()` + 控件 `SetFocus()`；Win32 `FindWindowW` 定位并 `SW_RESTORE`；**关键**用 `AttachThreadInput` 挂接前台线程后再 `SetForegroundWindow`，**绕过 Windows 前台锁定**（否则常常只闪任务栏不生效）。

### 坑 2 · 切会话不稳定 / SDK 参数签名不一致
`_chatwith_compat` 逐级退化参数兼容多版本；`chat_with` 用 `exact×force` 多策略轮询，每次先拉前台 + `SwitchToChat`；**切换后回读 `ChatBox.who` 校验**真的切过去了，没切成功就重试；最终兜底 `SessionBox.switch_chat`。教训：UI 自动化里"调了 API"≠"成功"，必须回读状态。

### 坑 3 · @所有人 经常失败 / 退化成单个"@"
四级降级（`send_at_all`）：① 原生 `AtAll` → ② 模拟输入"@"+ 选候选列表"所有人" → ③ `SendMsg(at="所有人")` → ④ 纯文本兜底。每级打日志标明用了哪种方式。

### 坑 4 · 重复 @前缀堆叠（"@所有人 @所有人 …"）
发送前用正则剥掉文本已有的 `@姓名`/`@所有人` 前缀（注意微信用全角分隔符），再交给 `at` 参数。

### 坑 5 · 多人 @ 时文案被插到人名中间
`SendMsg(msg, at=[多人])` 人数较多时会把文案插到 @ 列表中间。解法（`send_multi_at`）：≤3 人直接发；**>3 人先用换行占位符完成 @，再单独发一条文案消息**。

### 坑 6 · 接龙折叠卡片读不全
折叠时 `GetAllMessage()` 只返回片段。解法（`_latest_jielong_via_wechat_search`）：开「聊天记录」搜索窗 → 输入当天日期定位 → **双击复制全文**并校验剪贴板含关键词+日期；还会判断预览文本是否足够完整（编号行数过半）决定是否必须复制。

### 坑 7 · 向前扫描读到一整年历史
`_slice_msgs_since_today` 利用群"日期分隔标识"，从最新往前找到第一个"非今天"分隔线，**只截取今天**；无任何标识时最多看尾部 80 条。

### 坑 8 · 姓名识别误判（把工作内容当姓名）
`parse_jielong_participants` 严格匹配 `^\d+\.\s*姓名`（英文点+空格）且要求命中花名册（先别名前缀、再回退首词）；`normalize_name` 去符号统一；并以"当天发过 `#接龙` 的发送人"兜底，降低正文不规范导致的漏识别。

### 坑 9 · Windows GBK 控制台 emoji 报错 / 源码编码
`log()` 捕获 `UnicodeEncodeError` 安全降级打印；**用 Unicode 码点构造中文关键字**（`#接龙` = `"#"+chr(0x63A5)+chr(0x9F99)`）规避运行环境源码编码差异；口令包装器里设 `chcp 65001` / `PYTHONUTF8=1` / `python -X utf8`。

### 坑 10 · 汇总误发到群（而非私发领导）
发送时**强制带 `who=领导`**；`chat_with(领导)` 仅作提升成功率的前置，切换失败也不阻塞、继续 who 直发。切换 + who 双保险。

### 坑 11 · 超长汇总消息发送失败
`_send_text_in_chunks` 按 1800 字分片，且优先在换行处切，片间短暂 sleep。

### 坑 12 · 编辑接龙弹窗 Tab 焦点错位丢标题
直接用 `EditControl(foundIndex=1/2)` 定位两个输入框逐个写入，仅在定位失败时才回退 Tab 方案。

### 坑 13 · LLM 网络抖动 / 模型区域不可用
模型回退链 `gpt-5.4 → mini → deepseek`；HTTP 400/403/404 且含 region/not available/model not found → 换下一模型；5xx 与瞬态网络错误（ssl/eof/connection reset/timeout）→ 重试 3 次、间隔 5s；全失败时业务层发兜底提示，不让汇总整体崩。

### 坑 14 · 通用重试封装
`call_with_retry(fn, name, retries=3)` 包裹几乎所有微信交互并打 WARN/ERR 日志。UI 自动化瞬时失败极多，重试是标配。

### 坑 15 · 端口被占用
`pick_port` 默认 8000 被占用自动 +1 试探；但显式设了 `BOT_API_PORT` 就不偷偷换，让 uvicorn 直接报错。

### 坑 16 · 后端不可用时的可读诊断
`init_wx` 把底层异常翻译成可执行的中文指引（未激活→怎么激活；无效窗口句柄→去登录），而非抛原始堆栈。

---

## 通用经验小结（可迁移到任何 wxauto 项目）
1. 每个 UI 动作后**回读状态校验**，别信"调用即成功"。
2. **多版本 SDK 兼容**：try 全参逐级退化签名。
3. 前台激活必须用 **`AttachThreadInput` 绕过前台锁定**。
4. 关键中文用 **Unicode 码点构造**，躲编码坑。
5. 折叠内容靠**搜索 + 双击复制全文**，别靠滑屏。
6. 私密消息**强制 `who`**，切换失败也不阻塞。
7. **重试 + 可读日志 + 多级降级** 是 RPA 稳定性的三件套。
