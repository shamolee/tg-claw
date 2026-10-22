# TeleClaw

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab.svg)](https://python.org)

**Run and steer Claude Code sessions from Telegram.**

TeleClaw keeps Claude Code working on your projects while you're away from your desk. Each project gets its own Telegram bot: you send instructions, watch the answer stream in, and restart or pause sessions from your phone.

> A fork of [TeleClaw](https://github.com/gbongk/teleclaw) by gbongk.

## Contents

- [How it works](#how-it-works)
- [Security](#security)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Languages](#languages)
- [Running as a service](#running-as-a-service)
- [Telegram commands](#telegram-commands)
- [CLI](#cli)
- [Auto-recovery](#auto-recovery)
- [Files and storage](#files-and-storage)
- [Project structure](#project-structure)
- [Development](#development)

## How it works

TeleClaw starts each project as a long-lived Claude Code session through the [Claude Code SDK](https://docs.anthropic.com/en/docs/claude-code/sdk), so Claude keeps its context between your messages. Events stream back over the SDK and are written into Telegram with `editMessage`, so you see the answer and tool calls as they happen.

```
You (Telegram) ──► TeleClaw ──► Claude Code SDK ──► Claude Code session (one per project)
       ▲                              │
       └──── live editMessage ◄───────┘  text, tool use, results
```

```
┌──────────────┐
│   Telegram   │   one bot per project
└──────┬───────┘
       │ long poll
┌──────▼───────────────────────────────────┐
│            TeleClaw (asyncio)            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐     │
│  │ Project1│ │ Project2│ │ Project3│ ... │
│  └────┬────┘ └────┬────┘ └────┬────┘     │
│  health check (2 min) · watchdog (5 min) │
└───────┼───────────┼───────────┼──────────┘
┌───────▼───────────▼───────────▼──────────┐
│   Claude Code sessions (persistent)      │
└──────────────────────────────────────────┘
        ▲
        └── teleclaw_daemon: restarts TeleClaw if it crashes
```

What you get:

- **Several projects at once**, each in its own session with its own bot.
- **Live output**: answers and tool activity (which files Claude reads or edits) update in place.
- **Recovery without you**: stuck or dead sessions restart and pick up where they left off; a wrapper restarts the whole app if it crashes.
- **Claude Code commands from Telegram**: `/compact`, `/context`, `/cost` and other slash commands are passed straight to the session.
- **Five UI languages**: English, Korean, Azerbaijani, Russian and Turkish.
- **Windows, Linux and macOS**, with a system service for each.

## Security

TeleClaw gives whoever controls the bot a shell on your machine. Read this before you run it.

- **No permission prompts.** Sessions run with `bypassPermissions`, so Claude runs commands and edits files without asking. Only use it on machines and projects you trust.
- **The bot token is a key to your machine.** Keep it in `config.yaml`, which is gitignored, and never commit it.
- **Restrict who can talk to it.** Set `chat_id` to your own Telegram ID. Messages from anyone else are ignored, and if `chat_id` is empty, everything is rejected. Add other trusted IDs with `allowed_users`.
- **Use a private chat** with the bot, not a group.

## Quick start

### Prerequisites

- Python 3.11+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code), installed and logged in
- A Telegram bot token for each project, from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID, for example from [@userinfobot](https://t.me/userinfobot)

### Install

```bash
git clone https://github.com/shamolee/teleclaw.git
cd teleclaw
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e .
```

### Configure

```bash
cp config.example.yaml config.yaml
```

Fill in at least `chat_id` and one project:

```yaml
lang: "en"
chat_id: "123456789"

projects:
  MyApp:
    cwd: "/path/to/myapp"
    bot_token: "123456:ABC..."
```

### Run

```bash
teleclaw                      # run in the foreground (no auto-restart)
python -m src.teleclaw_daemon # run with the auto-restart wrapper
```

Send your bot a message; it goes to Claude Code in that project's folder. To keep it running after reboots, see [Running as a service](#running-as-a-service).

## Configuration

All settings live in `config.yaml`. TeleClaw looks for it in this order:

1. the path in the `TELECLAW_CONFIG` environment variable
2. `config.yaml` in the repository root
3. `~/.teleclaw/config.yaml`

| Key | Default | Description |
|---|---|---|
| `lang` | `en` | UI language: `en`, `ko`, `az`, `ru` or `tr`. See [Languages](#languages). |
| `chat_id` | *(required)* | Your Telegram user ID. Only this user, plus `allowed_users`, can use the bots. |
| `allowed_users` | `""` | Extra Telegram user IDs, comma-separated. |
| `projects` | | One entry per project, each with `cwd` (the folder Claude works in) and `bot_token`. |
| `output_level` | `normal` | `minimal` shows text only, `normal` adds tool summaries. Change it at runtime with `/mode`. |
| `icons` | | Optional `thinking` and `done` icons for status messages. |
| `storage_dir` | `storage` | Where logs, locks, state and uploads go. A relative path is resolved from the folder containing `config.yaml`. |
| `upload_retention_days` | `7` | Delete images and files received from Telegram after this many days. `0` keeps them forever. |
| `service_name` | `TeleClaw` | Name of the Windows scheduled task. |

Several projects, each with its own bot:

```yaml
projects:
  Frontend:
    cwd: "/home/me/frontend"
    bot_token: "111:AAA..."
  Backend:
    cwd: "/home/me/backend"
    bot_token: "222:BBB..."
```

## Languages

Set `lang` in `config.yaml` to `en`, `ko`, `az`, `ru` or `tr`. The language applies to every Telegram message, the bot's `/` command menu, CLI output and notifications. It also applies to the prompts TeleClaw sends to Claude, such as "please check this image" or the auto-resume prompt, so Claude answers in the same language.

Translations are plain JSON files in [`src/resources/lang/`](src/resources/lang/), one per language. To add a language:

1. Copy `en.json` to `<code>.json`, for example `de.json`.
2. Translate the values. Keep every `{placeholder}`, line break and leading space as it is.
3. Run `pytest tests/test_locales.py`. It checks that your file has the same keys and placeholders as `en.json`.

Keys missing from a language fall back to English.

## Running as a service

### Linux and macOS (systemd user service)

```bash
teleclaw install     # register and start; starts again on boot
teleclaw status
teleclaw logs [N]
teleclaw uninstall
```

### Windows

There are two options.

**Simple: `teleclaw install`.** Registers a scheduled task that starts the wrapper when you log in.

**Recommended for an always-on machine: [`win-scripts/`](win-scripts/).** Starts at boot and keeps running after you log out, without storing your password. In an elevated PowerShell:

```powershell
win-scripts\install_service_admin.ps1
```

The installer uses the task name from `service_name`. It finds `claude` on your PATH and passes its folder to the task, so Claude Code must be installed and on PATH when you run it. Run it again whenever you move the folder or change `service_name`.

Day-to-day scripts:

| Script | Does |
|---|---|
| `win-scripts\start.ps1` | Start the task and show status |
| `win-scripts\stop.ps1` | Stop the task and every TeleClaw process. Use an elevated shell if the task was started at boot. |
| `win-scripts\restart.ps1` | Stop, then start |
| `win-scripts\status.ps1` | Task state, running processes, bot status and the last log lines |

The task runs [`run_teleclaw.cmd`](run_teleclaw.cmd). It uses `.venv` if one exists and writes the wrapper's output to `storage/logs/task_out.log`, keeping one previous copy once the log passes 5 MB.

## Telegram commands

Send any normal message to give Claude an instruction. Photos and files are saved and handed to Claude together with their caption.

**Status and info**

| Command | Description |
|---|---|
| `/status` (`/s`) | Session health: OK, DEAD or STUCK |
| `/usage` (`/u`) | Claude usage against the 5-hour and 7-day limits |
| `/ctx` | Context window usage per session |
| `/sys` | CPU, memory and processes |
| `/log [N]` (`/l`) | Last N log lines (default 20) |
| `/mode [minimal\|normal]` | Show or change the output level |
| `/help` (`/h`) | Command list |

**Session control**

| Command | Description |
|---|---|
| `/esc [name]` (`/interrupt`) | Interrupt the current task |
| `/pause [name]` (`/p`) | Disconnect the session until you `/restart` it |
| `/restart [name]` (`/r`) | Restart and keep context, then resume what it was doing |
| `/reset [name]` | Restart with a fresh context |
| `/ask <question>` | Quick question in a separate session, without disturbing the main one |

**Claude Code commands.** Any other `/` command, such as `/compact`, `/context`, `/cost` or `/review`, is forwarded to the Claude Code session as-is.

## CLI

`teleclaw_ctl` does the same management from a terminal:

```bash
python -m src.teleclaw_ctl <command> [args]
```

| Command | Short | Description |
|---|---|---|
| `ps` | `s` | Sessions and processes: PID, memory, queue/errors/restarts |
| `sys` | | CPU and memory |
| `ctx` | `c` | Context window usage per session |
| `usage` | `u` | Claude usage (5h / 7d) |
| `restart [name]` | `r` | Restart a session |
| `reset [name]` | | Restart a session with a fresh context |
| `pause [name]` | `p` | Pause a session |
| `tc` | | Restart TeleClaw itself |
| `log [N]` | `l` | Last N log lines |
| `help` | `h` | Command list |

Commands reach the running app through its SQLite state database, so there are no flag files.

To let Claude itself message you, for example when a long build finishes, use the Telegram helper from inside a project folder. It picks the bot whose `cwd` matches your current folder.

```bash
python -m src.telegram_helper text "Build finished"
python -m src.telegram_helper photo screenshot.png "Latest UI"
python -m src.telegram_helper file report.csv "Results"
```

## Auto-recovery

Recovery works at three levels:

1. **Session health check, every 2 minutes.** A session whose client is gone is **DEAD**, and one busy for 30+ minutes is **STUCK**. Both are restarted automatically. After a restart, Claude is asked to continue its unfinished work. A session is restarted at most 3 times in 30 minutes.
2. **Watchdog.** If the app's event loop stops responding for 5 minutes, the process exits so the wrapper can start it again.
3. **Wrapper (`teleclaw_daemon`).** Restarts the app when it exits. A clean run that lasted 30 seconds or more restarts after 3 s. Repeated quick crashes back off exponentially (3 s, 6 s, 12 s … up to 30 min) and you get a Telegram alert. While it waits, the wrapper still answers emergency commands: `/status`, `/log`, `/restart`, `/kill`, `/ask`, `/help`.

The system service restarts the wrapper itself after a reboot.

## Files and storage

Everything TeleClaw writes at runtime goes under `storage/`, or the folder set by `storage_dir`:

```
storage/
  logs/      teleclaw.log (+ daily archives, kept 7 days), wrapper.log,
             teleclaw_stderr.log, task_out.log
  locks/     single-instance locks for the app and the wrapper
  state/     SQLite state, Claude session IDs, Telegram offsets, status
  uploads/   images and files received from Telegram
```

Only `state/` needs to survive. It holds each project's Claude session ID, so context survives restarts, and each bot's Telegram position, so old messages aren't processed again. Everything else is safe to delete while TeleClaw is stopped.

Upgrading from a version that used `logs/` and `data/` in the repository root? Those folders are moved into `storage/` automatically the first time the new version starts.

## Project structure

```
teleclaw/
├── src/
│   ├── teleclaw.py           # core: sessions, polling, streaming, health checks
│   ├── teleclaw_daemon.py    # auto-restart wrapper
│   ├── teleclaw_ctl.py       # terminal CLI
│   ├── telegram_helper.py    # send text/photo/file to Telegram from a shell
│   ├── telegram_api.py       # Telegram Bot API calls and text formatting
│   ├── channel.py            # abstract chat channel
│   ├── channel_telegram.py   # Telegram implementation of the channel
│   ├── commands.py           # Telegram command handlers
│   ├── messages.py           # loads translations, msg()
│   ├── resources/lang/       # en/ko/az/ru/tr translations (JSON)
│   ├── config.py             # config.yaml loading and paths
│   ├── storage.py            # storage/ layout, migration, upload cleanup
│   ├── state_db.py           # SQLite state
│   ├── session.py            # per-session state
│   ├── service.py            # systemd / Task Scheduler install
│   ├── process_utils.py      # cross-platform process helpers
│   ├── usage_fmt.py          # usage formatting
│   └── logging_utils.py      # logging and single-instance lock
├── win-scripts/              # Windows service install and control
├── run_teleclaw.cmd          # launcher used by the Windows task
├── tests/                    # unit tests + live smoke test
└── config.example.yaml
```

## Development

```bash
pip install -e . pytest pytest-asyncio
make test                 # python -m pytest tests/ -x -q
```

`tests/test_smoke.py` checks a live installation (its logs, status file and running processes), so it's excluded from the normal test run. Run it directly with `python tests/test_smoke.py` on a machine where TeleClaw is running.

## Credits

This project is a fork of [TeleClaw](https://github.com/gbongk/teleclaw) by gbongk, released under the MIT License.

## License

MIT. See [LICENSE](LICENSE).
