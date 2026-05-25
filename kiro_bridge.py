"""Bridge to Kiro CLI and local device operations."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from config import KIRO_BIN, KIRO_CLI_BIN, ProjectRegistry

logger = logging.getLogger(__name__)


class KiroBridge:
    """Full device access: shell, files, directories, Kiro headless chat."""

    def __init__(self):
        self.registry = ProjectRegistry()

    @property
    def cwd(self):
        path = self.registry.cwd
        if not path or not Path(path).is_dir():
            return str(Path.home())
        return path

    def cd(self, path):
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path(self.cwd) / target
        target = target.resolve()
        if not target.is_dir():
            return False, "❌ Not a directory: {}".format(target)
        self.registry.cwd = str(target)
        return True, str(target)

    async def chat(self, prompt, trust_all=True, timeout=300, resume=True):
        """Run kiro-cli headless chat. Returns (ok, response_text)."""
        cmd = [KIRO_CLI_BIN, "chat", "--no-interactive", "--wrap", "never"]
        if trust_all:
            cmd.append("--trust-all-tools")
        if resume:
            cmd.append("--resume")
        cmd.append(prompt)

        logger.info("kiro-cli chat in %s: %s", self.cwd, prompt[:100])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env={
                    **os.environ,
                    "PATH": "{}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{}".format(
                        str(Path.home()), os.environ.get("PATH", "")
                    ),
                },
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            raw = stdout.decode(errors="replace")
            err_raw = stderr.decode(errors="replace")
            response = self._clean_response(raw)

            # Check for rate limit in stderr even on success exit
            if "limit reached" in err_raw.lower() or "monthly request limit" in err_raw.lower():
                return False, "⚠️ Kiro CLI rate limit reached. Limits reset on the 1st of next month."

            if proc.returncode == 0:
                return True, response if response.strip() else "(no response)"
            else:
                if response.strip():
                    return True, response
                err = self._clean_response(err_raw)
                return False, "Exit {}: {}".format(proc.returncode, (err or err_raw.strip())[:500])

        except asyncio.TimeoutError:
            return False, "⏰ Timed out after {}s".format(timeout)
        except FileNotFoundError:
            return False, "❌ kiro-cli not found at: {}\nInstall: curl -fsSL https://cli.kiro.dev/install | bash".format(KIRO_CLI_BIN)
        except Exception as e:
            return False, "❌ {}".format(e)

    async def send_to_ide(self, prompt, mode="agent"):
        """Dispatch a prompt to Kiro IDE (fire-and-forget)."""
        cmd = [KIRO_BIN, "chat", "--mode", mode, "--reuse-window", prompt]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return True, "Sent to Kiro IDE"
            return False, stderr.decode(errors="replace").strip()
        except Exception as e:
            return False, str(e)

    async def run(self, command, timeout=120):
        """Run a shell command in cwd."""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env={
                **os.environ,
                "PATH": "{}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{}".format(
                    str(Path.home()), os.environ.get("PATH", "")
                ),
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "", "Timed out after {}s".format(timeout), -1
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode,
        )

    async def read_file(self, filepath):
        p = Path(filepath).expanduser()
        if not p.is_absolute():
            p = Path(self.cwd) / p
        p = p.resolve()
        if not p.exists():
            return None, "❌ Not found: {}".format(p)
        if not p.is_file():
            return None, "❌ Not a file: {}".format(p)
        try:
            return p.read_text(encoding="utf-8", errors="replace"), str(p)
        except Exception as e:
            return None, "❌ {}".format(e)

    async def write_file(self, filepath, content):
        p = Path(filepath).expanduser()
        if not p.is_absolute():
            p = Path(self.cwd) / p
        p = p.resolve()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return True, str(p)
        except Exception as e:
            return False, "❌ {}".format(e)

    async def list_dir(self, directory="."):
        p = Path(directory).expanduser()
        if not p.is_absolute():
            p = Path(self.cwd) / p
        p = p.resolve()
        if not p.exists():
            return None, "❌ Not found: {}".format(p)
        if not p.is_dir():
            return None, "❌ Not a directory: {}".format(p)
        entries = []
        try:
            for entry in sorted(p.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    entries.append("📁 {}/".format(entry.name))
                else:
                    size = entry.stat().st_size
                    if size < 1024:
                        sz = "{}B".format(size)
                    elif size < 1048576:
                        sz = "{:.1f}K".format(size / 1024)
                    else:
                        sz = "{:.1f}M".format(size / 1048576)
                    entries.append("📄 {} ({})".format(entry.name, sz))
        except PermissionError:
            return None, "❌ Permission denied"
        return entries, str(p)

    async def screenshot(self, window_name="Kiro"):
        """Take a screenshot of the Kiro IDE window (or full screen)."""
        screenshot_path = Path("/tmp/kiro_screenshot.png")

        # Try to capture the Kiro window specifically
        script = '''
        tell application "System Events"
            set kiroWindows to (every window of every process whose name contains "{}")
        end tell
        '''.format(window_name)

        # Use screencapture with window selection via AppleScript
        # First try to find and capture the specific window
        cmd = 'screencapture -x -o "{}"'.format(screenshot_path)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        if screenshot_path.exists():
            return True, str(screenshot_path)
        return False, "❌ Failed to take screenshot"

    async def screenshot_window(self, app_name="Kiro"):
        """Take a screenshot of a specific app window using osascript."""
        screenshot_path = Path("/tmp/kiro_screenshot.png")

        # Bring the app to front and capture
        script = '''
        tell application "{}" to activate
        delay 0.5
        do shell script "screencapture -x -o /tmp/kiro_screenshot.png"
        '''.format(app_name)

        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

        if screenshot_path.exists():
            return True, str(screenshot_path)
        return False, "❌ Failed to capture {} window".format(app_name)

    async def get_info(self):
        cwd = Path(self.cwd)
        project = self.registry.active_name
        lines = []
        if project:
            lines.append("📂 Project: **{}**".format(project))
        lines.append("📍 CWD: `{}`".format(cwd))
        markers = {
            "package.json": "Node.js", "requirements.txt": "Python",
            "pyproject.toml": "Python", "Cargo.toml": "Rust",
            "go.mod": "Go", "pom.xml": "Java", ".git": "Git", ".kiro": "Kiro",
        }
        found = [v for k, v in markers.items() if (cwd / k).exists()]
        if found:
            lines.append("🔧 Stack: {}".format(", ".join(found)))
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            out, _ = await proc.communicate()
            branch = out.decode().strip()
            if branch:
                lines.append("🌿 Branch: `{}`".format(branch))
        except Exception:
            pass
        return "\n".join(lines)

    def _clean_response(self, raw):
        """Strip kiro-cli chrome and ANSI codes."""
        raw = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw)
        raw = re.sub(r'\x1b\].*?\x07', '', raw)
        lines = raw.split("\n")
        cleaned = []
        skip_patterns = [
            "All tools are now trusted",
            "Agents can sometimes do unexpected",
            "Learn more at https://kiro.dev",
            "Credits:",
            "Time:",
        ]
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append("")
                continue
            if any(p in stripped for p in skip_patterns):
                continue
            if stripped.startswith("> "):
                cleaned.append(stripped[2:])
            elif stripped == ">":
                cleaned.append("")
            else:
                cleaned.append(line.rstrip())
        return "\n".join(cleaned).strip()
