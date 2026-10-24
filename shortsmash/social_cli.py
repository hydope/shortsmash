"""ShortSmash Social CLI: `shortsmash social ...`

Local MVP only. Publishing here is dry-run only; real live publishing is
not wired up. Image generation defaults to the offline Pillow template;
paid providers fail clearly and spend nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import approval, images, posts, profile as prof, repurpose
from .config import Config

app = typer.Typer(help="ShortSmash Social 0.4 (local MVP, dry-run publishing).",
                  no_args_is_help=True)
console = Console()


@app.command("profile-set")
def profile_set(name: str = typer.Option("", help="Name/handle"),
                does: str = typer.Option("", help="What you do, in your own words"),
                company: str = typer.Option("", help="Company (optional)"),
                niche: str = typer.Option("", help="Niche"),
                tone: str = typer.Option("", help="Tone"),
                audience: str = typer.Option("", help="Audience"),
                language: str = typer.Option("en", help="Language"),
                style: str = typer.Option("", help="Style sentence / palette"),
                links: str = typer.Option("", help="Links/CTA"),
                do_not_say: str = typer.Option("", help="Comma-separated phrases to never use"),
                employer_opt_in: bool = typer.Option(False, help="Allow mentioning the company")):
    data = prof.set_profile(
        name=name or None, does=does or None, company=company or None,
        niche=niche or None, tone=tone or None, audience=audience or None,
        language=language, style=style or None, links=links or None,
        do_not_say=do_not_say or None, employer_opt_in=employer_opt_in)
    console.print(f"[green]✓[/green] Profile saved. employer_opt_in={data['employer_opt_in']}")
    console.print(json.dumps(data, indent=2))


@app.command("profile-show")
def profile_show():
    data = prof.load_profile()
    if not data:
        console.print("[yellow]No profile yet. Run: shortsmash social profile-set --name ...[/yellow]")
        raise typer.Exit(1)
    console.print(json.dumps(data, indent=2))


@app.command("profile-delete")
def profile_delete():
    if prof.delete_profile():
        console.print("[green]✓[/green] Profile deleted. Future generation will not use it.")
    else:
        console.print("[yellow]No profile to delete.[/yellow]")


@app.command("post-draft")
def post_draft(topic: str, notes: str = typer.Option(""),
               platforms: str = typer.Option("x,threads,bluesky,instagram,youtube,linkedin"),
               use_llm: bool = typer.Option(False, help="Try the real LLM hook; default is offline template")):
    plats = tuple(p.strip() for p in platforms.split(",") if p.strip())
    result = posts.generate_drafts(topic, prof.load_profile(), notes=notes,
                                   cfg=Config(), use_llm=use_llm, platforms=plats)
    console.print(json.dumps(result, indent=2))


@app.command("image")
def image_cmd(prompt: str,
              provider: str = typer.Option("template", help="template|stock|gemini|pollinations"),
              size: str = typer.Option("feed", help="feed|story|link"),
              cap: float = typer.Option(5.0, help="Daily cap in USD")):
    preview = images.cost_preview(provider, 4, cap)
    console.print(f"Cost preview: {preview}")
    result = images.generate(prompt, provider=provider, size=size,
                             profile=prof.load_profile(), daily_cap_usd=cap)
    for c in result["candidates"]:
        console.print(f"  candidate {c['candidate']}: {c['path']} ({c['dimensions']}) - {c['note']}")


@app.command("repurpose")
def repurpose_cmd(source: str, topic: str = typer.Option("")):
    path = Path(source)
    if path.exists():
        staged = repurpose.stage_input(path)
        console.print(json.dumps(staged, indent=2))
        if staged["kind"] == "video":
            clips = repurpose.clip_candidates(Path(staged["staged"]))
            console.print(json.dumps(clips, indent=2))
        pack_topic = topic or path.stem
    else:
        pack_topic = topic or source
        staged = {"kind": "prompt", "staged": source}
    drafts = posts.generate_drafts(pack_topic, prof.load_profile(), use_llm=False)
    imgs = images.generate(pack_topic, provider="template", size="feed",
                           profile=prof.load_profile())
    pack = approval.create_pack(pack_topic, drafts, media={"images": imgs, "input": staged})
    console.print(f"[green]✓[/green] Pack {pack['id']} created with {len(pack['drafts'])} drafts.")


@app.command("queue")
def queue_cmd():
    packs = approval.list_packs()
    if not packs:
        console.print("No packs yet.")
        return
    for p in packs:
        statuses = {}
        for d in p["drafts"]:
            statuses[d["status"]] = statuses.get(d["status"], 0) + 1
        console.print(f"{p['id']}  {p['topic'][:50]}  {statuses}")


@app.command("approve")
def approve_cmd(pack_id: str, platform: Optional[str] = typer.Option(None)):
    approval.approve_pack(pack_id, platform)
    console.print(f"[green]✓[/green] Approved pack {pack_id}" + (f" for {platform}" if platform else " (all platforms)"))


@app.command("reject")
def reject_cmd(pack_id: str, platform: Optional[str] = typer.Option(None)):
    approval.reject_pack(pack_id, platform)
    console.print(f"Rejected pack {pack_id}" + (f" for {platform}" if platform else ""))


@app.command("publish")
def publish_cmd(pack_id: str, platform: str = typer.Option("x"),
                dry_run: bool = typer.Option(True, help="Dry-run only; real publishing is not wired up")):
    if not dry_run:
        console.print("[red]Real live publishing is NOT wired up in this local MVP. Use --dry-run.[/red]")
        raise typer.Exit(1)
    entry = approval.publish_dry_run(pack_id, platform)
    console.print(json.dumps(entry, indent=2))


@app.command("demo")
def demo_cmd(topic: str = typer.Option("morning fishing catch and cook")):
    """End-to-end demo, offline and dry-run only:
    profile -> post drafts -> image candidates -> pack -> approve -> dry-run publish."""
    console.print("Step 1: profile (demo, employer_opt_in=False)")
    prof.set_profile(name="Demo Creator", does="makes short cooking videos",
                     niche="outdoors cooking", tone="plain and friendly",
                     style="warm light, simple, no clutter",
                     do_not_say="Acme Corp", employer_opt_in=False)
    console.print("Step 2: post drafts (template, labelled as template)")
    drafts = posts.generate_drafts(topic, prof.load_profile(), use_llm=False)
    for plat, items in drafts["platforms"].items():
        console.print(f"  {plat}: {items[0]['text'][:100]}... (source={items[0]['source']})")
    console.print("Step 3: image candidates (Pillow template, not AI)")
    imgs = images.generate(topic, provider="template", size="feed",
                           profile=prof.load_profile())
    for c in imgs["candidates"]:
        console.print(f"  candidate {c['candidate']}: {c['path']}")
    console.print("Step 4: pack")
    pack = approval.create_pack(topic, drafts, media={"images": imgs})
    console.print(f"  pack {pack['id']} with {len(pack['drafts'])} drafts")
    console.print("Step 5: approve (all platforms)")
    approval.approve_pack(pack["id"])
    console.print("Step 6: dry-run publish (x only, idempotent)")
    entry = approval.publish_dry_run(pack["id"], "x")
    console.print(json.dumps(entry, indent=2))
    console.print(f"[green]✓ Demo complete. Pack {pack['id']} - no real post was made.[/green]")
