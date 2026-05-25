#!/bin/bash
# Start the Kiro Discord Bot
# Usage: ./start.sh          (foreground)
#        ./start.sh daemon    (background)
#        ./start.sh stop      (stop background)
#        ./start.sh status    (check if running)
#        ./start.sh logs      (tail logs)

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv/bin/python3"
BOT="$DIR/bot.py"
LOG="$DIR/bot.log"
PID_FILE="$DIR/bot.pid"

case "${1:-}" in
  daemon)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "⚠️  Bot already running (PID $(cat "$PID_FILE"))"
      echo "   Use: ./start.sh stop"
      exit 1
    fi
    echo "🚀 Starting bot in background..."
    nohup "$VENV" "$BOT" >> "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    echo "✅ Bot started (PID $!)"
    echo "📄 Logs: tail -f $LOG"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm "$PID_FILE"
        echo "🛑 Bot stopped (PID $PID)"
      else
        rm "$PID_FILE"
        echo "ℹ️  Bot was not running (stale PID file removed)"
      fi
    else
      echo "ℹ️  No PID file found. Bot not running."
    fi
    ;;
  logs)
    tail -f "$LOG"
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "✅ Bot is running (PID $(cat "$PID_FILE"))"
    else
      echo "❌ Bot is not running"
    fi
    ;;
  *)
    echo "🚀 Starting bot (foreground, Ctrl+C to stop)..."
    "$VENV" "$BOT"
    ;;
esac
