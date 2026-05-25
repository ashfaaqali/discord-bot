"""
Kiro Discord Bot — Remote Mac/Kiro control via Discord.

Per-project categories with context-aware channels.
Rich embed responses. Approval gate.
"""

import asyncio
import json
import logging
import os
import ssl
import time
from pathlib import Path

import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

import discord
from discord.ext import commands

from config import BOT_TOKEN, OWNER_ID, DATA_DIR
from kiro_bridge import KiroBridge
from gate import is_trusted, add_trusted, remove_trusted, list_trusted, request_approval

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True

bot = discord.Bot(intents=intents)
bridge = KiroBridge()

# Channel mappings: channel_id -> {"project": name, "type": "chat"|"terminal"|"approvals"|"files"}
CHANNELS_FILE = DATA_DIR / "channels.json"
channel_map: dict[str, dict] = {}

# Pending approvals are managed in gate.py


# ── Channel Mapping ─────────────────────────────────────────────────────────

def load_channels():
    global channel_map
    if CHANNELS_FILE.exists():
        try:
            channel_map = json.loads(CHANNELS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            channel_map = {}


def save_channels():
    CHANNELS_FILE.write_text(json.dumps(channel_map, indent=2))


def get_channel_context(channel_id: int) -> dict | None:
    return channel_map.get(str(channel_id))


# ── Auth ────────────────────────────────────────────────────────────────────

def owner_only():
    async def predicate(ctx):
        if ctx.author.id != OWNER_ID:
            await ctx.respond("🔒 Unauthorized.", ephemeral=True)
            return False
        return True
    return commands.check(predicate)


# ── Embed Helpers ───────────────────────────────────────────────────────────

def embed_success(title, description="", **fields):
    em = discord.Embed(title=title, description=description, color=0x2ECC71)
    for k, v in fields.items():
        em.add_field(name=k, value=str(v)[:1024], inline=False)
    return em


def embed_error(title, description=""):
    return discord.Embed(title="❌ " + title, description=description, color=0xE74C3C)


def embed_info(title, description="", **fields):
    em = discord.Embed(title=title, description=description, color=0x3498DB)
    for k, v in fields.items():
        em.add_field(name=k, value=str(v)[:1024], inline=False)
    return em


def embed_command(cmd, stdout, stderr, code, cwd, elapsed=None):
    color = 0x2ECC71 if code == 0 else 0xE74C3C
    em = discord.Embed(color=color)
    em.add_field(name="🖥️ Command", value=f"```{cmd[:200]}```", inline=False)
    em.add_field(name="📍 Directory", value=f"`{cwd}`", inline=True)
    em.add_field(name="Exit", value=str(code), inline=True)
    if elapsed:
        em.add_field(name="⏱️ Time", value=f"{elapsed:.1f}s", inline=True)
    if stdout.strip():
        out = stdout.strip()[:1000]
        em.add_field(name="Output", value=f"```\n{out}\n```", inline=False)
    if stderr.strip():
        err = stderr.strip()[:1000]
        em.add_field(name="⚠️ Stderr", value=f"```\n{err}\n```", inline=False)
    if not stdout.strip() and not stderr.strip():
        em.add_field(name="Output", value="(no output)", inline=False)
    return em


def embed_kiro(response, project="~"):
    em = discord.Embed(description=response[:4000], color=0x9B59B6)
    em.set_author(name=f"Kiro • {project}")
    return em


def format_kiro_response(response: str) -> str:
    """Format kiro response for Discord rendering.

    Discord natively supports markdown (bold, italic, bullets, code blocks),
    so we only need to handle tables and avoid breaking existing formatting.
    """
    lines = response.split("\n")
    result = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Detect markdown table rows (start and end with |)
        is_table_line = stripped.startswith("|") and stripped.endswith("|")

        if is_table_line and not in_table:
            in_table = True
            result.append("```")
            result.append(line)
        elif is_table_line and in_table:
            result.append(line)
        elif not is_table_line and in_table:
            in_table = False
            result.append("```")
            result.append(line)
        else:
            result.append(line)

    if in_table:
        result.append("```")

    return "\n".join(result)


async def send_kiro_response(target, response: str, project: str = "~"):
    """Send a Kiro response, handling long messages and formatting."""
    formatted = format_kiro_response(response)
    header = f"**Kiro • {project}**\n\n"
    full = header + formatted

    if len(full) <= 2000:
        await target.reply(full)
    else:
        # Split into chunks
        chunks = []
        remaining = full
        while remaining:
            if len(remaining) <= 2000:
                chunks.append(remaining)
                break
            # Try to split on newline
            split_at = remaining.rfind("\n", 0, 2000)
            if split_at < 500:
                split_at = 2000
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

        await target.reply(chunks[0])
        for chunk in chunks[1:]:
            await target.channel.send(chunk)


def truncate(text: str, max_len: int = 1900) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... (truncated)"


# ── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    load_channels()
    await bot.sync_commands()
    logger.info("Bot ready: %s (ID: %s)", bot.user.name, bot.user.id)
    print("🚀 Kiro Discord Bot online: {}".format(bot.user.name))
    print("📍 CWD: {}".format(bridge.cwd))
    if bridge.registry.projects:
        print("📂 Projects: {}".format(", ".join(bridge.registry.projects.keys())))
    if OWNER_ID:
        print("🔒 Owner: {}".format(OWNER_ID))
    print("📡 Channel mappings: {}".format(len(channel_map)))
    print("✅ Commands synced")


# ── Project Setup Command ───────────────────────────────────────────────────

@bot.slash_command(name="setup", description="Create channels for a project")
@owner_only()
@discord.option("name", description="Project name")
@discord.option("path", description="Project path on your machine")
async def cmd_setup(ctx, name: str, path: str):
    await ctx.defer()

    guild = ctx.guild
    if not guild:
        await ctx.respond(embed=embed_error("Must be used in a server"))
        return

    result = bridge.registry.add(name, path)
    if "❌" in result:
        await ctx.respond(embed=embed_error("Setup Failed", result))
        return

    category = await guild.create_category(name.upper())
    chat_channel = await category.create_text_channel("chat")
    terminal_channel = await category.create_text_channel("terminal")
    files_channel = await category.create_text_channel("files")
    approvals_channel = await category.create_text_channel("approvals")

    channel_map[str(chat_channel.id)] = {"project": name, "type": "chat"}
    channel_map[str(terminal_channel.id)] = {"project": name, "type": "terminal"}
    channel_map[str(files_channel.id)] = {"project": name, "type": "files"}
    channel_map[str(approvals_channel.id)] = {"project": name, "type": "approvals"}
    save_channels()

    bridge.registry.switch(name)

    em = embed_success(f"📂 Project '{name}' Ready", "Category and channels created.")
    em.add_field(name="💬 Chat", value=chat_channel.mention, inline=True)
    em.add_field(name="🖥️ Terminal", value=terminal_channel.mention, inline=True)
    em.add_field(name="📁 Files", value=files_channel.mention, inline=True)
    em.add_field(name="🔐 Approvals", value=approvals_channel.mention, inline=True)
    em.add_field(name="📍 Path", value=f"`{bridge.registry.projects[name]}`", inline=False)
    await ctx.respond(embed=em)

    await chat_channel.send(embed=embed_info(
        f"🤖 Kiro Chat — {name}",
        "Type anything here to chat with Kiro AI about this project.",
    ))
    await terminal_channel.send(embed=embed_info(
        f"🖥️ Terminal — {name}",
        "Type any command here and it runs in this project's directory.\n"
        "Example: `git status`, `ls -la`, `npm run build`",
    ))
    await files_channel.send(embed=embed_info(
        f"📁 Files — {name}",
        "Use `/file <path>` to read files and `/write <path> <content>` to write.\n"
        "Messages here are treated as file read requests.",
    ))
    await approvals_channel.send(embed=embed_info(
        f"🔐 Approvals — {name}",
        "Approval requests for this project appear here.\n"
        "Tap ✅ to approve or ❌ to deny.",
    ))


# ── Upgrade existing projects ───────────────────────────────────────────────

@bot.slash_command(name="upgrade", description="Add missing files channel to an existing project")
@owner_only()
@discord.option("name", description="Project name to upgrade")
async def cmd_upgrade(ctx, name: str):
    await ctx.defer()

    guild = ctx.guild
    if not guild:
        await ctx.respond(embed=embed_error("Must be used in a server"))
        return

    if name not in bridge.registry.projects:
        await ctx.respond(embed=embed_error("Unknown project", f"Use /projects to see registered projects."))
        return

    # Find the existing category by name
    category = discord.utils.get(guild.categories, name=name.upper())
    if not category:
        await ctx.respond(embed=embed_error("Category not found", f"No category named **{name.upper()}** in this server."))
        return

    added = []

    # Add files channel if missing
    has_files = any(d["project"] == name and d["type"] == "files" for d in channel_map.values())
    if not has_files:
        files_ch = await category.create_text_channel("files")
        channel_map[str(files_ch.id)] = {"project": name, "type": "files"}
        await files_ch.send(embed=embed_info(
            f"📁 Files — {name}",
            "Use `/file <path>` to read files and `/write <path> <content>` to write.\n"
            "Messages here are treated as file read requests.",
        ))
        added.append(files_ch.mention)

    save_channels()

    if added:
        await ctx.respond(embed=embed_success(f"✅ Upgraded '{name}'", "Added: " + ", ".join(added)))
    else:
        await ctx.respond(embed=embed_info(f"'{name}' already up to date", "No channels were missing."))


# ── Shell Commands ──────────────────────────────────────────────────────────

@bot.slash_command(name="run", description="Execute a shell command")
@owner_only()
@discord.option("command", description="Shell command to execute")
async def cmd_run(ctx, command: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    project = bridge.registry.active_name or "~"
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]
        project = context["project"]

    t0 = time.time()
    stdout, stderr, code = await bridge.run(command)
    elapsed = time.time() - t0
    await ctx.respond(embed=embed_command(command, stdout, stderr, code, bridge.cwd, elapsed))


@bot.slash_command(name="git", description="Run a git command")
@owner_only()
@discord.option("args", description="Git arguments (e.g. status, log --oneline)")
async def cmd_git(ctx, args: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    stdout, stderr, code = await bridge.run("git " + args)
    await ctx.respond(embed=embed_command("git " + args, stdout, stderr, code, bridge.cwd))


@bot.slash_command(name="diff", description="Show git diff")
@owner_only()
@discord.option("file", description="File to diff (optional)", required=False, default="")
async def cmd_diff(ctx, file: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    cmd = "git diff " + file if file else "git diff"
    stdout, stderr, code = await bridge.run(cmd)
    if not stdout.strip() and code == 0:
        await ctx.respond(embed=embed_success("Git Diff", "No changes (working tree clean)"))
    else:
        await ctx.respond(embed=embed_command(cmd, stdout, stderr, code, bridge.cwd))


# ── Filesystem ──────────────────────────────────────────────────────────────

@bot.slash_command(name="ls", description="List directory contents")
@owner_only()
@discord.option("directory", description="Directory to list", required=False, default=".")
async def cmd_ls(ctx, directory: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    entries, resolved = await bridge.list_dir(directory)
    if entries is None:
        await ctx.respond(embed=embed_error("Not Found", resolved))
        return
    content = "\n".join(entries) if entries else "(empty)"
    em = embed_info(f"📂 {resolved}", f"```\n{truncate(content, 3500)}\n```")
    await ctx.respond(embed=em)


@bot.slash_command(name="cd", description="Change working directory")
@owner_only()
@discord.option("path", description="Directory path")
async def cmd_cd(ctx, path: str):
    ok, result = bridge.cd(path)
    if ok:
        await ctx.respond(embed=embed_success("Directory Changed", f"`{result}`"))
    else:
        await ctx.respond(embed=embed_error("Failed", result))


@bot.slash_command(name="pwd", description="Show current directory")
@owner_only()
async def cmd_pwd(ctx):
    await ctx.respond(embed=embed_info("📍 Current Directory", f"`{bridge.cwd}`"))


@bot.slash_command(name="file", description="Read a file")
@owner_only()
@discord.option("path", description="File path to read")
async def cmd_file(ctx, path: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    content, info = await bridge.read_file(path)
    if content is None:
        await ctx.respond(embed=embed_error("File Error", info))
    else:
        em = embed_info(f"📄 {Path(info).name}", f"```\n{truncate(content, 3500)}\n```")
        em.set_footer(text=info)
        await ctx.respond(embed=em)


@bot.slash_command(name="write", description="Write content to a file")
@owner_only()
@discord.option("path", description="File path to write")
@discord.option("content", description="Content to write")
async def cmd_write(ctx, path: str, content: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    ok, info = await bridge.write_file(path, content)
    if ok:
        await ctx.respond(embed=embed_success("File Written", f"`{info}`\n{len(content)} bytes"))
    else:
        await ctx.respond(embed=embed_error("Write Failed", info))


# ── Kiro CLI ────────────────────────────────────────────────────────────────

@bot.slash_command(name="kiro", description="Chat with Kiro AI")
@owner_only()
@discord.option("prompt", description="Prompt for Kiro")
async def cmd_kiro(ctx, prompt: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    project = "~"
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]
        project = context["project"]

    ok, response = await bridge.chat(prompt)
    if ok:
        formatted = format_kiro_response(response)
        header = f"**Kiro • {project}**\n\n"
        full = header + formatted
        if len(full) <= 2000:
            await ctx.respond(full)
        else:
            await ctx.respond(full[:2000])
            remaining = full[2000:]
            while remaining:
                chunk = remaining[:2000]
                remaining = remaining[2000:]
                await ctx.followup.send(chunk)
    else:
        await ctx.respond(embed=embed_error("Kiro Error", response))


# ── Kiro IDE ────────────────────────────────────────────────────────────────

@bot.slash_command(name="ide", description="Send a task to Kiro IDE (fire-and-forget)")
@owner_only()
@discord.option("prompt", description="Task for Kiro IDE")
async def cmd_ide(ctx, prompt: str):
    await ctx.defer()
    context = get_channel_context(ctx.channel_id)
    if context and context["project"] in bridge.registry.projects:
        bridge.registry.cwd = bridge.registry.projects[context["project"]]

    ok, msg = await bridge.send_to_ide(prompt, mode="agent")
    if ok:
        em = embed_success("📨 Sent to Kiro IDE", f"Task: `{prompt[:200]}`")
        em.set_footer(text="Check the IDE on your Mac when ready")
        await ctx.respond(embed=em)
    else:
        await ctx.respond(embed=embed_error("IDE Error", msg))


@bot.slash_command(name="screen", description="Screenshot your Mac")
@owner_only()
@discord.option("app", description="App to capture (default: full screen)", required=False, default="")
async def cmd_screen(ctx, app: str):
    await ctx.defer()
    if app:
        ok, path = await bridge.screenshot_window(app)
    else:
        ok, path = await bridge.screenshot()
    if ok:
        await ctx.respond("📸", file=discord.File(path))
    else:
        await ctx.respond(embed=embed_error("Screenshot Failed", path))


# ── Projects ────────────────────────────────────────────────────────────────

@bot.slash_command(name="projects", description="List registered projects")
@owner_only()
async def cmd_projects(ctx):
    await ctx.defer()
    projects = bridge.registry.projects
    if not projects:
        await ctx.followup.send(embed=embed_info("📂 Projects", "No projects registered.\nUse `/setup <name> <path>` to add one."))
        return
    lines = []
    for name, path in sorted(projects.items()):
        marker = " ✅" if name == bridge.registry.active_name else ""
        lines.append(f"**{name}**{marker}\n↳ `{path}`")
    em = embed_info("📂 Projects", "\n\n".join(lines))
    await ctx.followup.send(embed=em)


@bot.slash_command(name="switch", description="Switch active project")
@owner_only()
@discord.option("name", description="Project name to switch to")
async def cmd_switch(ctx, name: str):
    result = bridge.registry.switch(name)
    if "❌" in result:
        await ctx.respond(embed=embed_error("Switch Failed", result))
    else:
        await ctx.respond(embed=embed_success("Project Switched", result))


@bot.slash_command(name="rename", description="Rename a registered project")
@owner_only()
@discord.option("old_name", description="Current project name")
@discord.option("new_name", description="New project name")
async def cmd_rename(ctx, old_name: str, new_name: str):
    await ctx.defer()
    if old_name not in bridge.registry.projects:
        await ctx.followup.send(embed=embed_error("Unknown project", old_name))
        return
    if new_name in bridge.registry.projects:
        await ctx.followup.send(embed=embed_error("Name taken", new_name))
        return

    # Update registry
    path = bridge.registry.projects[old_name]
    bridge.registry._data["projects"][new_name] = path
    del bridge.registry._data["projects"][old_name]
    if bridge.registry._data.get("active") == old_name:
        bridge.registry._data["active"] = new_name
    bridge.registry._save()

    # Update channel mappings
    for cid, data in channel_map.items():
        if data.get("project") == old_name:
            data["project"] = new_name
    save_channels()

    # Rename Discord category if found
    guild = ctx.guild
    if guild:
        cat = discord.utils.get(guild.categories, name=old_name.upper())
        if cat:
            await cat.edit(name=new_name.upper())

    await ctx.followup.send(embed=embed_success("Renamed", f"`{old_name}` → `{new_name}`"))


@bot.slash_command(name="scan", description="Auto-discover projects in a directory")
@owner_only()
@discord.option("directory", description="Directory to scan (e.g. ~/code)")
async def cmd_scan(ctx, directory: str):
    await ctx.defer()
    found = bridge.registry.scan_directory(directory)
    if not found:
        await ctx.respond(embed=embed_info("🔍 Scan", f"No projects found in {directory}"))
        return
    already = set(bridge.registry.projects.keys())
    new = [(n, p) for n, p in found if n not in already]
    if not new:
        await ctx.respond(embed=embed_info("🔍 Scan", f"All {len(found)} projects already registered."))
        return
    results = [bridge.registry.add(n, p) for n, p in new]
    em = embed_success(f"🔍 Found {len(new)} Projects", "\n".join(results))
    em.set_footer(text="Use /setup to create channels for each project")
    await ctx.respond(embed=em)


# ── Info ────────────────────────────────────────────────────────────────────

@bot.slash_command(name="info", description="Show current bot state")
@owner_only()
async def cmd_info(ctx):
    info = await bridge.get_info()
    context = get_channel_context(ctx.channel_id)
    if context:
        info += f"\n📡 Channel context: **{context['project']}** ({context['type']})"
    await ctx.respond(embed=embed_info("ℹ️ Status", info))


# ── Approval Gate Commands ───────────────────────────────────────────────────

@bot.slash_command(name="trust", description="Add a command prefix to the trusted list")
@owner_only()
@discord.option("prefix", description="Command prefix to trust (e.g. 'git push')")
async def cmd_trust(ctx, prefix: str):
    ok, msg = add_trusted(prefix)
    if ok:
        await ctx.respond(embed=embed_success("Trusted", msg))
    else:
        await ctx.respond(embed=embed_info("Already Trusted", msg))


@bot.slash_command(name="untrust", description="Remove a command prefix from the trusted list")
@owner_only()
@discord.option("prefix", description="Command prefix to untrust")
async def cmd_untrust(ctx, prefix: str):
    ok, msg = remove_trusted(prefix)
    if ok:
        await ctx.respond(embed=embed_success("Untrusted", msg))
    else:
        await ctx.respond(embed=embed_error("Not Found", msg))


@bot.slash_command(name="trusted", description="Show all trusted command prefixes")
@owner_only()
async def cmd_trusted(ctx):
    trusted = list_trusted()
    if not trusted:
        await ctx.respond(embed=embed_info("Trusted Commands", "None — all commands require approval."))
        return
    lines = [f"`{t}`" for t in sorted(trusted)]
    await ctx.respond(embed=embed_info("Trusted Commands", "\n".join(lines)))


# ── Message Handler ─────────────────────────────────────────────────────────

_processed_messages: set[int] = set()


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.author.id != OWNER_ID:
        return
    if message.id in _processed_messages:
        return
    _processed_messages.add(message.id)
    # Keep set from growing forever
    if len(_processed_messages) > 100:
        _processed_messages.clear()

    text = message.content
    if not text or text.startswith("/"):
        return

    # Strip bot mention if present
    if bot.user in (message.mentions or []):
        text = text.replace(f"<@{bot.user.id}>", "").strip()
    if not text:
        return

    # Check channel context
    context = get_channel_context(message.channel.id)

    if context:
        project = context["project"]
        ch_type = context["type"]

        if project in bridge.registry.projects:
            bridge.registry.cwd = bridge.registry.projects[project]

        if ch_type == "terminal":
            async with message.channel.typing():
                t0 = time.time()
                stdout, stderr, code = await bridge.run(text)
                elapsed = time.time() - t0
            await message.reply(embed=embed_command(text, stdout, stderr, code, bridge.cwd, elapsed))
            return

        elif ch_type == "files":
            async with message.channel.typing():
                content, info = await bridge.read_file(text)
            if content is None:
                await message.reply(embed=embed_error("File Error", info))
            else:
                em = embed_info(f"📄 {Path(info).name}", f"```\n{truncate(content, 3500)}\n```")
                em.set_footer(text=info)
                await message.reply(embed=em)
            return

        elif ch_type == "chat":
            async with message.channel.typing():
                ok, response = await bridge.chat(text)
            if ok:
                await send_kiro_response(message, response, project)
            else:
                await message.reply(embed=embed_error("Kiro Error", response))
            return

    # Default: not in a mapped channel — treat as Kiro chat
    async with message.channel.typing():
        project = bridge.registry.active_name or "~"
        ok, response = await bridge.chat(text)

    if ok:
        await send_kiro_response(message, response, project)
    else:
        await message.reply(embed=embed_error("Error", response))


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("❌ Set DISCORD_BOT_TOKEN in .env")
        return
    if not OWNER_ID:
        print("⚠️  No DISCORD_OWNER_ID set — bot won't respond to anyone")

    print("🚀 Starting Kiro Discord Bot...")
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
