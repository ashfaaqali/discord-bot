#!/usr/bin/env python3
"""
Send a notification to Discord from the command line.

Usage:
    ./notify.py "Kiro finished the task"
    echo "some message" | ./notify.py -

Designed to be called from Kiro hooks (runCommand).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

script_dir = Path(__file__).parent
env_file = script_dir / ".env"

if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("DISCORD_NOTIFICATION_CHANNEL_ID", "")
API = "https://discord.com/api/v10"


def send(message: str) -> bool:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("Error: BOT_TOKEN or CHANNEL_ID not configured", file=sys.stderr)
        return False

    url = "{}/channels/{}/messages".format(API, CHANNEL_ID)
    payload = json.dumps({"content": message}).encode()
    headers = {
        "Authorization": "Bot {}".format(BOT_TOKEN),
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print("Failed to send: {}".format(e), file=sys.stderr)
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: notify.py <message>")
        print("       notify.py -    (read from stdin)")
        sys.exit(1)

    if sys.argv[1] == "-":
        message = sys.stdin.read().strip()
    else:
        message = " ".join(sys.argv[1:])

    if not message:
        print("Empty message, skipping.")
        sys.exit(0)

    ok = send(message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
