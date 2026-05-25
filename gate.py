"""
Approval gate and trusted commands management.

Whitelist approach: only commands matching a trusted prefix run without
approval. Everything else gets routed to the project's approvals channel.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import discord

from config import DATA_DIR, OWNER_ID

# ── Trusted Commands ────────────────────────────────────────────────────────

TRUSTED_COMMANDS_FILE = DATA_DIR / "trusted_commands.json"

DEFAULT_TRUSTED = [
    # Git (read-only)
    "git status", "git log", "git diff", "git branch", "git show",
    "git stash list", "git remote", "git tag", "git fetch",
    # Filesystem (read-only)
    "ls", "cat ", "head ", "tail ", "find ", "tree", "pwd", "wc ",
    "file ", "stat ", "du ", "df ",
    # Build & dev
    "npm run", "npm install", "npm test", "npm start", "npm ci",
    "yarn ", "npx ", "pnpm ",
    "python ", "pip ", "poetry ",
    "gradle", "gradlew", "./gradlew",
    "cargo build", "cargo run", "cargo test", "cargo check",
    "make ", "cmake ",
    "flutter ", "dart ",
    # Info & text processing
    "echo ", "which ", "env", "whoami", "date", "uptime",
    "grep ", "awk ", "sed ", "sort ", "uniq ", "cut ", "xargs ",
    "curl ", "wget ",
    "open ", "code ", "kiro ",
    # Package info
    "brew list", "brew info",
    "adb ", "fastboot ",
]


def load_trusted() -> list[str]:
    """Load trusted command prefixes from file, or use defaults."""
    if TRUSTED_COMMANDS_FILE.exists():
        try:
            return json.loads(TRUSTED_COMMANDS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return list(DEFAULT_TRUSTED)


def save_trusted(commands: list[str]):
    """Persist trusted command list."""
    TRUSTED_COMMANDS_FILE.write_text(json.dumps(sorted(commands), indent=2))


def is_trusted(command: str) -> bool:
    """Check if a command matches any trusted prefix."""
    cmd = command.strip()
    trusted = load_trusted()
    for prefix in trusted:
        # Exact prefix match or prefix with trailing space
        if cmd == prefix or cmd.startswith(prefix):
            return True
        # Handle ./prefix (e.g. ./gradlew)
        if cmd.startswith("./" + prefix):
            return True
    return False


def add_trusted(prefix: str) -> tuple[bool, str]:
    """Add a prefix to trusted list. Returns (success, message)."""
    prefix = prefix.strip()
    if not prefix:
        return False, "Empty prefix."
    trusted = load_trusted()
    if prefix in trusted:
        return False, f"`{prefix}` is already trusted."
    trusted.append(prefix)
    save_trusted(trusted)
    return True, f"Added `{prefix}` to trusted commands."


def remove_trusted(prefix: str) -> tuple[bool, str]:
    """Remove a prefix from trusted list. Returns (success, message)."""
    prefix = prefix.strip()
    trusted = load_trusted()
    if prefix not in trusted:
        return False, f"`{prefix}` is not in the trusted list."
    trusted.remove(prefix)
    save_trusted(trusted)
    return True, f"Removed `{prefix}` from trusted commands."


def list_trusted() -> list[str]:
    """Return current trusted list."""
    return load_trusted()


# ── Approval View ───────────────────────────────────────────────────────────

# Shared dict for pending approval futures: nonce -> Future
pending_approvals: dict[str, asyncio.Future] = {}


class ApprovalView(discord.ui.View):
    def __init__(self, nonce: str):
        super().__init__(timeout=120)
        self.nonce = nonce

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, button, interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("🔒 Not your call.", ephemeral=True)
            return
        future = pending_approvals.get(self.nonce)
        if future and not future.done():
            future.set_result("APPROVED")
        em = discord.Embed(title="✅ APPROVED", color=0x2ECC71)
        await interaction.response.edit_message(embed=em, view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, emoji="❌")
    async def deny(self, button, interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("🔒 Not your call.", ephemeral=True)
            return
        future = pending_approvals.get(self.nonce)
        if future and not future.done():
            future.set_result("DENIED")
        em = discord.Embed(title="❌ DENIED", color=0xE74C3C)
        await interaction.response.edit_message(embed=em, view=None)


# ── Request Approval ────────────────────────────────────────────────────────

async def request_approval(
    guild,
    project: str,
    command: str,
    source_channel,
    cwd: str,
    channel_map: dict,
) -> bool:
    """
    Post an approval request to the project's approvals channel.
    Returns True if approved, False if denied or timed out.
    """
    # Find approval channel for this project
    approval_channel_id = None
    for cid, data in channel_map.items():
        if data.get("project") == project and data.get("type") == "approvals":
            approval_channel_id = int(cid)
            break

    approval_channel = guild.get_channel(approval_channel_id) if approval_channel_id else None
    target = approval_channel or source_channel

    nonce = str(uuid.uuid4())[:8]
    future = asyncio.get_event_loop().create_future()
    pending_approvals[nonce] = future

    em = discord.Embed(
        title="⚠️ Approval Required",
        description=f"```{command[:500]}```",
        color=0xF39C12,
    )
    em.add_field(name="Project", value=project, inline=True)
    em.add_field(name="Directory", value=f"`{cwd}`", inline=True)
    em.set_footer(text="This command is not in the trusted list. Use /trust to add it.")

    view = ApprovalView(nonce)
    await target.send(embed=em, view=view)

    # Notify source channel if approval went to a different channel
    if target != source_channel:
        notify_em = discord.Embed(
            title="⏳ Awaiting Approval",
            description=f"Sent to {target.mention}",
            color=0x3498DB,
        )
        await source_channel.send(embed=notify_em)

    try:
        result = await asyncio.wait_for(future, timeout=120)
    except asyncio.TimeoutError:
        result = "DENIED"
        timeout_em = discord.Embed(
            title="⏰ Timed Out",
            description="Approval request expired.",
            color=0x95A5A6,
        )
        await target.send(embed=timeout_em)
    finally:
        pending_approvals.pop(nonce, None)

    return result == "APPROVED"
