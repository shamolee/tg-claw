---
name: setup
description: TeleClaw first-time setup — install dependencies, create config.yaml, test the bot, and start the service.
---

# /setup — TeleClaw first-time setup

Walk the user through setting up TeleClaw one step at a time. Wait for their answer before moving on.

## Step 1: Check prerequisites

Run:
- `python --version` → needs 3.11+
- `claude --version` → Claude Code must be installed and logged in

If either is missing, tell the user how to install it and stop.

## Step 2: Install

From the repository root:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e .
```

## Step 3: Create config.yaml

If `config.yaml` already exists, show its project names and ask whether to add to it or start over. Never print bot tokens back to the user.

Ask for:
1. **Bot token**: "Create a bot with @BotFather on Telegram and paste its token."
2. **Your Telegram user ID**: "Message @userinfobot on Telegram and paste the ID it replies with."
3. **Project folder**: "Which folder should Claude Code work in?" Check that it exists.
4. **Project name**: "A short name for this project, e.g. MyApp."
5. **Language**: "en (English), ko (Korean), az (Azerbaijani), ru (Russian) or tr (Turkish)?"

Copy `config.example.yaml` to `config.yaml` and fill in the values:

```yaml
lang: "{language}"
chat_id: "{user_id}"
allowed_users: ""

projects:
  {project_name}:
    cwd: "{project_path}"
    bot_token: "{bot_token}"
```

## Step 4: Test the bot

From inside the project folder, so the helper picks that project's bot:

```bash
python -m src.telegram_helper text "TeleClaw setup complete!"
```

This works from any folder because Step 2 installed the package; keep the venv active. If no message arrives, have the user check the token and user ID, and make sure they've pressed **Start** in the chat with their bot.

## Step 5: Start as a service

- **Linux / macOS:** `teleclaw install`
- **Windows:** offer two options:
  - `teleclaw install`: starts when the user logs in.
  - `win-scripts\install_service_admin.ps1`, in an elevated PowerShell: starts at boot and keeps running after logout. Recommended for an always-on machine.

## Step 6: More projects (optional)

Ask: "Add another project? Each one needs its own bot from @BotFather."

For each, repeat the questions for token, folder and name, and add it under `projects:`. Restart the service afterwards (`win-scripts\restart.ps1` on Windows, or `teleclaw uninstall` + `teleclaw install`).

## Done

Show:

```
TeleClaw is running.

Message your bot on Telegram to talk to Claude Code in that project.

Useful commands:
  /status          session health
  /help            all commands
  teleclaw status  service status
  teleclaw logs    recent logs
```
