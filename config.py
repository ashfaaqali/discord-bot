"""Configuration for Kiro Discord Bot."""
from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Discord
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0"))

# Kiro
KIRO_BIN = os.getenv("KIRO_BIN", "/usr/local/bin/kiro")
KIRO_CLI_BIN = os.getenv("KIRO_CLI_BIN", "") or str(Path.home() / ".local" / "bin" / "kiro-cli")
KIRO_WORKSPACE_PATH = os.getenv("KIRO_WORKSPACE_PATH", "") or str(Path.home())

# Paths
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PROJECTS_FILE = DATA_DIR / "projects.json"


class ProjectRegistry:
    """Manages named project paths, persisted as JSON."""

    def __init__(self, filepath=None):
        self.filepath = filepath or PROJECTS_FILE
        self._data = self._load()

    def _load(self):
        if self.filepath.exists():
            try:
                return json.loads(self.filepath.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"projects": {}, "active": None, "cwd": str(Path.home())}

    def _save(self):
        self.filepath.write_text(json.dumps(self._data, indent=2))

    @property
    def projects(self):
        return self._data.get("projects", {})

    @property
    def active_name(self):
        return self._data.get("active")

    @property
    def cwd(self):
        return self._data.get("cwd", str(Path.home()))

    @cwd.setter
    def cwd(self, path):
        self._data["cwd"] = path
        self._save()

    @property
    def active_path(self):
        name = self.active_name
        if name and name in self.projects:
            return self.projects[name]
        return KIRO_WORKSPACE_PATH

    def add(self, name, path):
        resolved = str(Path(path).expanduser().resolve())
        if not Path(resolved).is_dir():
            return "❌ Not a valid directory: {}".format(resolved)
        self._data["projects"][name] = resolved
        if len(self._data["projects"]) == 1:
            self._data["active"] = name
            self._data["cwd"] = resolved
        self._save()
        return "✅ Registered '{}' → {}".format(name, resolved)

    def remove(self, name):
        if name not in self.projects:
            return "❌ Unknown project: {}".format(name)
        del self._data["projects"][name]
        if self._data["active"] == name:
            self._data["active"] = None
        self._save()
        return "🗑️ Removed '{}'".format(name)

    def switch(self, name):
        if name not in self.projects:
            return "❌ Unknown project: {}\nUse /projects to see list.".format(name)
        self._data["active"] = name
        self._data["cwd"] = self.projects[name]
        self._save()
        return "🔀 Switched to '{}' → {}".format(name, self.projects[name])

    def list_all(self):
        if not self.projects:
            return "📂 No projects registered.\nAdd: /addproject name path\nScan: /scan ~/code"
        lines = ["📂 **Registered Projects:**\n"]
        for name, path in sorted(self.projects.items()):
            marker = " ✅" if name == self.active_name else ""
            lines.append("  `{}`{}\n  ↳ {}".format(name, marker, path))
        lines.append("\nSwitch: /switch <name>")
        return "\n".join(lines)

    def scan_directory(self, parent_dir):
        parent = Path(parent_dir).expanduser().resolve()
        if not parent.is_dir():
            return []
        markers = {
            ".git", "package.json", "requirements.txt", "Cargo.toml",
            "go.mod", "pom.xml", "Makefile", "pyproject.toml",
            "Gemfile", ".kiro",
        }
        found = []
        for entry in sorted(parent.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if any((entry / m).exists() for m in markers):
                found.append((entry.name, str(entry)))
        return found
