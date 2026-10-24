"""ShortSmash MCP server: let AI agents drive the video factory.

Exposes the pipeline as Model Context Protocol tools over stdio, so
Claude Code, Cursor, or any MCP client can generate videos, check
trends, and inspect projects without touching the CLI.

Add to your MCP client config:
    {"mcpServers": {"shortsmash": {
        "command": "/path/to/shortsmash/.venv/bin/python",
        "args": ["-m", "shortsmash.mcp_server"]}}}
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import Config
from . import pipeline
from . import trends as trends_mod

mcp = FastMCP("shortsmash")


@mcp.tool()
def generate_video(topic: str, duration: int = 30) -> str:
    """Create a finished vertical video from a topic prompt.

    Runs the full pipeline: script → voice → verified footage →
    karaoke captions → music → render. Returns the project slug.
    """
    cfg = Config()
    workdir = pipeline.create_project(topic, duration, cfg)
    return f"Video ready: {workdir.name} (publish with `shortsmash publish {workdir.name}`)"


@mcp.tool()
def get_trends(niche: str = "general", limit: int = 10) -> str:
    """Harvest what's trending right now in a niche. Free, no API keys."""
    trends = trends_mod.harvest(niche, limit)
    if not trends:
        return "No trends found."
    lines = [f"{i}. {t.title} [{t.source}, score {t.score}]"
             for i, t in enumerate(trends, 1)]
    return "\n".join(lines)


@mcp.tool()
def list_projects() -> str:
    """List video projects and their render state."""
    projects = pipeline.list_projects()
    if not projects:
        return "No projects yet."
    return "\n".join(
        f"{'🎬' if p['has_video'] else '📝'} {p['slug']}" for p in projects
    )


@mcp.tool()
def project_status(slug: str) -> str:
    """Check a project's stage: does it have script, audio, clips, video?"""
    from pathlib import Path
    from .config import PROJECTS_DIR
    d = Path(PROJECTS_DIR) / slug
    if not d.exists():
        return f"Unknown project: {slug}"
    parts = []
    for name, label in [("plan.json", "script"), ("captions.ass", "captions"),
                        ("final.mp4", "video")]:
        parts.append(f"{label}: {'✓' if (d / name).exists() else '—'}")
    clips = list((d / "clips").glob("*.mp4")) if (d / "clips").exists() else []
    parts.append(f"clips: {len(clips)}")
    return f"{slug}: " + ", ".join(parts)


@mcp.tool()
def list_social_packs() -> str:
    """List ShortSmash Social draft packs and their approval status.

    Preview only. There is deliberately no approve or publish tool here:
    an agent must not approve its own post. Approval is a human action
    in the Studio social page (/social) or the CLI.
    """
    from . import approval
    packs = approval.list_packs()
    if not packs:
        return "No social packs yet."
    lines = []
    for p in packs:
        statuses: dict[str, int] = {}
        for d in p["drafts"]:
            statuses[d["status"]] = statuses.get(d["status"], 0) + 1
        lines.append(f"{p['id']} {p['topic']} {statuses}")
    return "\n".join(lines)


@mcp.tool()
def social_queue() -> str:
    """Show the ShortSmash Social approval queue (packs and draft statuses).

    Preview only, same intentional limit as list_social_packs: no
    approve or live-publish tool exists in this MCP server.
    """
    return list_social_packs()


@mcp.tool()
def social_draft_preview(topic: str, notes: str = "") -> str:
    """Generate draft previews for a topic, for human review only.

    Uses offline template drafts by default (source=template) so this
    tool never spends money or calls a paid API. Drafts are NOT approved
    and NOT published by this tool. A human must approve them in Studio
    or the CLI before any dry-run or live publishing step.
    """
    from . import posts, profile as prof
    result = posts.generate_drafts(topic, prof.load_profile(), notes=notes,
                                   use_llm=False)
    lines = []
    for platform, drafts in result["platforms"].items():
        lines.append(f"{platform}: {drafts[0]['text'][:180]} "
                     f"(source={drafts[0]['source']})")
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
