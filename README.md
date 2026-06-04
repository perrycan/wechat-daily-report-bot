# WeChat Daily-Report Bot

> Automated daily work-report collection for a WeChat work group — **post the roll-call → chase non-submitters → AI-summarize for the manager**, fully unattended on workdays, and triggerable on demand by a chat command.

[![CI](https://github.com/perrycan/wechat-daily-report-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/perrycan/wechat-daily-report-bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)](#)
[![LLM](https://img.shields.io/badge/LLM-OpenRouter%20%7C%20DeepSeek-purple)](#)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

中文文档见 [README.zh-CN.md](README.zh-CN.md)。

> **Note** This is an **anonymized portfolio version** of a real, in-production project. All company names, department names, people, factories, customers and M&A project names have been replaced with fictional placeholders, and all secrets removed.

---

## The problem

A team posts a daily work report every workday using WeChat's native **"接龙" (roll-call / chain sign-up)** feature. Three chores were done by hand every day:

1. **Start** the roll-call at a fixed time.
2. **Chase** whoever hasn't submitted (excluding the manager, the bot, and people on leave).
3. **Summarize** ten people's raw entries into a clean, management-grade report and send it privately to the manager.

This bot automates all three — accurately, on time, and with a hot-reloadable rule set driving the AI summary.

## Features

- 🚀 **Native roll-call** — drives WeChat's real `#接龙 → edit table → launch` flow (not a fake text message), so entries keep their auto-numbering and names.
- ⏰ **Workday scheduling** — `16:30` start · `17:10` chase · `17:35` summarize, skipping public holidays via `chinese-calendar`.
- 🎯 **Accurate roll-call parsing** — locates the day's roll-call by searching its date (avoids collapsed cards & stale history), parses participants, diffs against the roster.
- 🙋 **Leave-aware chasing** — merges all non-submitters into a single multi-`@` message; never chases the manager, the bot, or anyone who declared leave before the cutoff.
- 🧠 **LLM summary with hot-reloadable rules** — the summarization prompt lives in an external rules file that is **re-read on every run**; edit the rules and the next summary uses them, no restart/redeploy.
- 🔁 **Model fallback chain** — `gpt-5.4 → gpt-5.4-mini → deepseek-chat-v3` with transient-error retries and region-unavailable fallback.
- 🔌 **Three trigger paths** — timer, HTTP API (`/jielong /cuiban /huizong /status`), and a Chinese chat command via an optional ops gateway.
- 🧪 **DRY-RUN mode** — exercise the API/logic without touching real WeChat.

## Architecture

A clean five-layer separation of concerns:

```
┌──────────────────────────────────────────────────────────────────┐
│ Command layer   chat command "start/remind/summarize/status" → CLI/API        │  openclaw_command_router.py
├──────────────────────────────────────────────────────────────────┤
│ API layer       FastAPI :8000  +  schedule timer thread           │  api_server.py
├──────────────────────────────────────────────────────────────────┤
│ Business layer  start / chase / summarize; parse, diff, send      │  bot_core.py
├──────────────────────────────────────────────────────────────────┤
│ Model layer     hot-reload rules → call LLM → management summary   │  llm_summary.py
├──────────────────────────────────────────────────────────────────┤
│ WeChat layer    window switch, native roll-call, @, history read   │  wxauto / wxautox4
└──────────────────────────────────────────────────────────────────┘
        config.py (group/roster/times/copy)   rules/summary_fixed_rules.md (hot-reloaded prompt)
```

Full write-up: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Engineering highlights

The interesting part of this project is the **robustness work behind GUI automation**. A few examples (full list in [docs/PITFALLS.md](docs/PITFALLS.md)):

- **Force WeChat to the foreground** using `AttachThreadInput` to bypass Windows' foreground-lock — otherwise hotkeys/clicks land on the wrong window.
- **Read collapsed roll-call cards** by opening the chat-history search, querying the date, and double-clicking to copy the full text — instead of fragile scroll-and-scrape.
- **Four-level `@everyone` fallback** (native `AtAll` → simulated input + candidate pick → `SendMsg(at=...)` → plain text) across WeChat versions.
- **Multi-`@` message fix**: when `@`-ing many people, the text would get inserted *between* names — fixed by sending the mentions with a placeholder, then the message separately.
- **Private-send safety**: the summary is sent with a forced `who=` target so a session-switch glitch can never leak it to the group.
- **Data-driven prompt engineering**: the summary rules were distilled by comparing the manager's manual summary (ground truth) against the model's output, day after day — see [docs/SUMMARY_RULES.md](docs/SUMMARY_RULES.md).

## Tech stack

Python 3.10+ · FastAPI · uvicorn · `schedule` · `chinese-calendar` · pyautogui · pyperclip · uiautomation · OpenRouter/DeepSeek (LLM) · wxauto/wxautox4 (WeChat backend).

## Project structure

```
wechat-daily-report-bot/
├── api_server.py                # entry point: backend init, FastAPI, timer thread
├── bot_core.py                  # core: start / chase / summarize + all WeChat I/O & robustness
├── llm_summary.py               # LLM summary: provider resolve, rule hot-reload, model fallback
├── config.py                    # config: group, roster, name map, times, copy
├── openclaw_command_router.py   # CLI: Chinese command → core functions → JSON
├── summary_rules.txt            # supplementary rules (hot-updatable)
├── rules/
│   └── summary_fixed_rules.md   # fixed summary rules (hot-reloaded as the LLM system prompt)
├── scripts/                     # optional ops-gateway patch (chat-command integration)
├── docs/                        # architecture, pitfalls, summary-rule methodology
├── tests/                       # unit tests for the pure parsing/diff functions
├── requirements.txt
└── .env.example
```

## Quick start

```bash
# 1) install
pip install -r requirements.txt

# 2) configure (never commit your real .env)
cp .env.example .env      # Windows: copy .env.example .env
#   set OPENROUTER_API_KEY=...   ; keep BOT_DRY_RUN=true to test without real WeChat

# 3) run
python api_server.py
#   → API docs at http://localhost:8000/docs
```

On a real run, log into PC WeChat first and keep its window visible (this is GUI automation). For full functionality on WeChat 4.x, use the `wxautox4` backend.

### Triggering

| Method | How |
|---|---|
| **Timer** | Auto on workdays: `16:30` start · `17:10` chase · `17:35` summarize |
| **HTTP API** | `GET /jielong` · `/cuiban` · `/huizong` · `/status` ; `POST /send_msg` ; `GET/POST /rules` |
| **CLI** | `python openclaw_command_router.py start\|remind\|summarize\|status [--json]` |
| **Chat command** | Send `start/remind/summarize/status` in WeChat (via the optional ops gateway in `scripts/`) |

## Updating the summary rules

Edit `rules/summary_fixed_rules.md` (or `summary_rules.txt`) — the next summary picks it up automatically; no restart. Point `LLM_FIXED_RULES_FILE` at any local `.md/.txt/.docx`.

## Tests

```bash
pip install pytest
pytest        # unit tests for parsing / roster-diff / message-slicing (no WeChat needed)
```

## Security & disclaimer

- No secrets are stored in the repo; provide your own via `.env` (git-ignored).
- This is GUI automation (RPA) for personal/educational use. Automating IM clients may conflict with the platform's Terms of Service — use responsibly and at your own risk.
- All business identifiers in this repo are **fictional** (anonymized from the original project).

## License

[MIT](LICENSE)
