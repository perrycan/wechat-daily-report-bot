# 微信群日报自动化机器人

> 自动化微信工作群的每日日报：**发起接龙 → 催办未交 → AI 汇总私发领导**，工作日全程无人值守，并支持随时用一句中文口令临时触发。

[![CI](https://github.com/perrycan/wechat-daily-report-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/perrycan/wechat-daily-report-bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)](#)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

English: [README.md](README.md)

> **说明** 本仓库是一个**真实在用项目的脱敏作品版**。公司、科室、人名、工厂、客户、并购项目名均已替换为虚构占位，所有密钥已移除。


## 演示

![DRY-RUN 演示](docs/assets/dry-run-demo.gif)

*`BOT_DRY_RUN=true` 模式：完整跑通 **start → remind → summarize**，全程不操作真实微信（数据已脱敏）。*

---

## 解决的问题

某团队每个工作日用微信原生「接龙」功能提交日报。以下三件事过去每天靠人工完成：

1. 固定时间**发起**接龙；
2. **催办**未提交的人（领导、机器人、已请假者除外）；
3. 把 10 个人的零散日报**汇总**成管理口径的报告，私发给领导。

本机器人把这三件事自动化——准点、准人、准内容，且由一套可热加载的规则驱动 AI 汇总。

## 功能特性

- 🚀 **微信原生接龙**：驱动微信真实的 `#接龙 → 编辑接龙表格 → 发起接龙` 流程（非伪造文本），保留自动编号与姓名。
- ⏰ **工作日定时**：`16:30` 发接龙 · `17:10` 催办 · `17:35` 汇总，用 `chinese-calendar` 跳过法定节假日。
- 🎯 **精准识别**：按当天日期搜索定位接龙（规避折叠卡片与历史旧消息），解析参与者并与花名册做差集。
- 🙋 **请假豁免催办**：所有未交者合并为一条多人 `@` 消息；领导、机器人、截止前已请假者永不催。
- 🧠 **规则热加载的 LLM 汇总**：汇总 Prompt 外置在规则文件，**每次汇总前重新读取**，改规则即时生效，无需重启/发版。
- 🔁 **模型回退链**：`gpt-5.4 → gpt-5.4-mini → deepseek-chat-v3`，含瞬态错误重试与区域不可用回退。
- 🔌 **三种触发**：定时、HTTP API（`/jielong /cuiban /huizong /status`）、聊天口令（经可选网关）。
- 🧪 **DRY-RUN 模式**：不操作真实微信即可联调 API 与逻辑。

## 架构

清晰的五层职责分离：

```mermaid
flowchart TD
    T1["Timer - workdays 16:30 / 17:10 / 17:35"]
    T2["Chat command - start / remind / summarize / status"]
    T3["HTTP API"]
    R["openclaw_command_router.py"]
    A["api_server.py - FastAPI + scheduler"]
    C["bot_core.py - start / remind / summarize"]
    W["wxauto / wxautox4 - WeChat GUI automation"]
    M["llm_summary.py - hot-reload rules to LLM"]
    RULES[("summary rules: md + txt")]
    LLM[("OpenRouter / DeepSeek")]
    WX[("PC WeChat - group + manager DM")]
    CFG[("config.py - roster / times / copy")]
    T1 --> A
    T2 --> R
    T3 --> A
    R --> A
    A --> C
    C --> W
    C --> M
    M -->|reads each run| RULES
    M --> LLM
    W --> WX
    CFG -->|config| C
    classDef trig fill:#1f6feb,color:#ffffff,stroke:#1f6feb;
    class T1,T2,T3 trig;
```

### 每日流程

```mermaid
sequenceDiagram
    autonumber
    participant S as Trigger
    participant B as bot_core
    participant W as WeChat
    participant L as LLM
    participant M as Manager
    S->>B: 16:30 start
    B->>W: post native roll-call and at-everyone
    S->>B: 17:10 remind
    B->>W: read roll-call, diff vs roster
    B->>W: at-mention pending, merged and leave-aware
    S->>B: 17:35 summarize
    B->>W: extract latest roll-call
    B->>L: roll-call plus hot-reloaded rules
    L-->>B: management-grade summary
    B-->>M: send summary privately
    B->>W: post done receipt to group
```

各层对应文件：`openclaw_command_router.py`（指令）· `api_server.py`（接口 + 定时）· `bot_core.py`（业务）· `llm_summary.py`（模型）· `wxauto`/`wxautox4`（微信）。完整说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 工程亮点

本项目真正有含金量的是 **GUI 自动化背后的健壮性设计**（完整清单见 [docs/PITFALLS.md](docs/PITFALLS.md)）：

- **强制微信前台**：用 `AttachThreadInput` 绕过 Windows 前台锁定，否则热键/点击会落到错误窗口。
- **读取折叠接龙卡片**：打开聊天记录搜索 → 按日期查询 → 双击复制全文，替代脆弱的滑屏抓取。
- **@所有人四级降级**：原生 `AtAll` → 模拟输入选候选 → `SendMsg(at=...)` → 纯文本兜底，兼容多版本微信。
- **多人 @ 修复**：人多时文案会被插到人名中间——改为先用占位符 @，再单独发文案。
- **私发防误发**：汇总发送强制带 `who=` 目标，会话切换异常也不会误发到群。
- **数据驱动的 Prompt 工程**：汇总规则是用「领导人工汇总（标准答案）vs 模型产出」逐日对照提炼出来的，见 [docs/SUMMARY_RULES.md](docs/SUMMARY_RULES.md)。

## 技术栈

Python 3.10+ · FastAPI · uvicorn · `schedule` · `chinese-calendar` · pyautogui · pyperclip · uiautomation · OpenRouter/DeepSeek（LLM）· wxauto/wxautox4（微信后端）。

## 目录结构

```
wechat-daily-report-bot/
├── api_server.py                # 入口：后端初始化、FastAPI、定时线程
├── bot_core.py                  # 核心：发接龙/催办/汇总 + 全部微信交互与健壮性逻辑
├── llm_summary.py               # LLM 汇总：provider 解析、规则热加载、模型回退
├── config.py                    # 配置：群、花名册、姓名映射、时间、文案
├── openclaw_command_router.py   # CLI：中文口令 → 核心函数 → JSON
├── summary_rules.txt            # 补充规则（可热更新）
├── rules/
│   └── summary_fixed_rules.md   # 固定汇总规则（作为 LLM system prompt 热加载）
├── scripts/                     # 可选的聊天网关补丁（口令集成）
├── docs/                        # 架构、避坑、规则方法论
├── tests/                       # 纯解析/比对函数的单元测试
├── requirements.txt
└── .env.example
```

## 快速开始

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 配置（切勿提交真实 .env）
copy .env.example .env      # Linux/macOS: cp .env.example .env
#   填入 OPENROUTER_API_KEY=... ；先保持 BOT_DRY_RUN=true 以免操作真实微信

# 3) 启动
python api_server.py
#   → 接口文档 http://localhost:8000/docs
```

真实运行前先登录 PC 微信并保持窗口可见（本质是 GUI 自动化）。微信 4.x 建议用 `wxautox4` 后端以获得完整功能。

### 触发方式

| 方式 | 用法 |
|---|---|
| **定时** | 工作日自动：`16:30` 发接龙 · `17:10` 催办 · `17:35` 汇总 |
| **HTTP API** | `GET /jielong` · `/cuiban` · `/huizong` · `/status`；`POST /send_msg`；`GET/POST /rules` |
| **CLI** | `python openclaw_command_router.py start\|remind\|summarize\|status [--json]` |
| **聊天口令** | 微信内发 `start/remind/summarize/status`（经 `scripts/` 中的可选网关补丁） |

## 更新汇总规则

编辑 `rules/summary_fixed_rules.md`（或 `summary_rules.txt`），下次汇总自动生效，无需重启。可用 `LLM_FIXED_RULES_FILE` 指向任意本地 `.md/.txt/.docx`。

## 测试

```bash
pip install pytest
pytest        # 解析 / 花名册差集 / 消息按日截断 的单元测试（无需微信）
```

## 安全与免责声明

- 仓库不含任何密钥，请自行通过 `.env`（已 git-ignore）提供。
- 本项目为 GUI 自动化（RPA），仅供个人学习使用。自动化 IM 客户端可能与平台服务条款冲突，请谨慎并自担风险。
- 仓库中所有业务标识均为**虚构**（由原项目脱敏而来）。

## 许可

[MIT](LICENSE)
