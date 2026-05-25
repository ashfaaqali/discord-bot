# Kiro Discord Bot

Remote Mac/Kiro control via Discord. Run shell commands, browse files, chat with Kiro AI, dispatch tasks to the IDE, and approve/deny actions — all from your phone.

## Setup

### 1. Create the Discord Bot

1. Go to https://discord.com/developers/applications
2. Click "New Application" → name it (e.g. "Kiro Controller")
3. Go to **Bot** section → click "Reset Token" → copy the token
4. Enable **Message Content Intent** under Privileged Gateway Intents
5. Go to **OAuth2** → URL Generator:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands`
6. Copy the generated URL → open it → select your server → authorize

### 2. Get Your User ID

1. Open Discord → Settings → Advanced → enable Developer Mode
2. Click your profile → "Copy User ID"

### 3. Configure

Edit `.env`:
```
DISCORD_BOT_TOKEN=your_token_here
DISCORD_OWNER_ID=your_user_id_here
```

### 4. Install & Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start.sh
```

## Commands

### Shell & Files
- `/run <command>` — execute shell command
- `/git <args>` — git shorthand
- `/diff [file]` — git diff
- `/ls [dir]` — list directory
- `/cd <path>` — change directory
- `/pwd` — current directory
- `/file <path>` — read a file
- `/write <path> <content>` — write to a file
- `/find <pattern>` — find files
- `/grep <pattern> [path]` — search file contents

### Kiro AI
- `/kiro <prompt>` — chat with Kiro (headless, full response)
- `/ask <question>` — ask Kiro a question
- `/ide <prompt>` — send task to Kiro IDE (fire-and-forget)

### Visual
- `/screen` — screenshot full desktop
- `/screen <app>` — screenshot a specific app window

### Projects
- `/projects` — list all
- `/switch <name>` — switch active project
- `/addproject <name> <path>` — register
- `/rmproject <name>` — remove
- `/scan <dir>` — auto-discover

### Info
- `/info` — current state

### Also
- **DM the bot** or **@mention** it with any message → routes to Kiro CLI chat
- Approval gate sends buttons for approve/deny decisions

## Approval Gate (for Kiro Hooks)

```bash
python gate.py "Delete the migrations folder"
```

Sends a message to Discord with Approve/Deny buttons. Prints `APPROVED` or `DENIED` to stdout.

Requires `DISCORD_APPROVAL_CHANNEL_ID` in `.env`.

## Notifications (for Kiro Hooks)

```bash
python notify.py "Build complete ✅"
```

Requires `DISCORD_NOTIFICATION_CHANNEL_ID` in `.env`.
