"""ShortSmash CLI — prompt in, viral short out."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, agent, engage, pipeline
from creator_publish import schedule as sched_mod
from .config import CONFIG_FILE, DEFAULTS, Config, ensure_home
from creator_publish.publish import ADAPTERS

app = typer.Typer(help="ShortSmash — prompt in, viral short out.",
                  no_args_is_help=True)
console = Console()

# ShortSmash Social 0.4 (local MVP): profile/post/image/approval, dry-run only.
from . import social_cli  # noqa: E402

app.add_typer(social_cli.app, name="social")


def _cfg(**overrides) -> Config:
    return Config({k: v for k, v in overrides.items() if v is not None})


@app.command()
def init():
    """Create ~/.shortsmash/config.yaml and show what's needed."""
    ensure_home()
    if not CONFIG_FILE.exists():
        import yaml
        CONFIG_FILE.write_text(yaml.safe_dump(DEFAULTS, sort_keys=False))
    (Path.home() / ".shortsmash" / "music").mkdir(exist_ok=True)
    console.print(f"[green]✓[/green] Config at {CONFIG_FILE}")
    console.print("Next: copy .env.example → .env and add keys (Gemini, Pexels…). "
                  "Only Pexels/Edge-TTS/Gemini-free are needed for the basics — "
                  "Edge-TTS needs no key at all.")


@app.command()
def doctor():
    """Check every dependency and credential. Fails fast with fixes."""
    from . import edit
    ok = True

    def check(name: str, fn):
        nonlocal ok
        try:
            fn()
            console.print(f"[green]✓[/green] {name}")
        except Exception as e:  # noqa: BLE001
            ok = False
            console.print(f"[red]✗[/red] {name}: {e}")

    check("ffmpeg + ffprobe + libass", edit.render_quiet_check)
    check("Edge-TTS (voice)", lambda: __import__("edge_tts"))
    check("faster-whisper (captions)", lambda: __import__("faster_whisper"))
    check("google-generativeai (script/drafts)", lambda: __import__("google.generativeai"))
    cfg = Config()
    console.print(f"[{'green' if cfg.gemini_key() else 'yellow'}]"
                  f"{'✓' if cfg.gemini_key() else '○'}[/] GEMINI_API_KEY "
                  f"{'(set)' if cfg.gemini_key() else '(missing — offline template will be used)'}")
    console.print(f"[{'green' if cfg.pexels_key() else 'yellow'}]"
                  f"{'✓' if cfg.pexels_key() else '○'}[/] PEXELS_API_KEY "
                  f"{'(set)' if cfg.pexels_key() else '(missing — generated placeholders will be used)'}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def new(topic: str, duration: int = typer.Option(30, help="Target seconds (5-180)"),
        voice: Optional[str] = typer.Option(None, help="Edge-TTS voice id"),
        style: Optional[str] = typer.Option(None, help="karaoke|minimal|bold"),
        ratio: Optional[str] = typer.Option(None, help="9:16|16:9|1:1|4:5|21:9")):
    """Create a video from a prompt: script → voice → footage → captions → render."""
    from .edit import ASPECT_RATIOS
    overrides: dict = {"voice": voice, "caption_style": style}
    if ratio and ratio in ASPECT_RATIOS:
        overrides["width"] = ASPECT_RATIOS[ratio]["w"]
        overrides["height"] = ASPECT_RATIOS[ratio]["h"]
    elif ratio:
        console.print(f"[yellow]Unknown ratio {ratio!r}; using 9:16.[/yellow]")
    cfg = _cfg(**{k: v for k, v in overrides.items() if v is not None})
    workdir = pipeline.create_project(topic, duration, cfg)
    console.print(f"Project: [bold]{workdir.name}[/bold]  •  "
                  f"Publish with: shortsmash publish {workdir.name} --platforms youtube,tiktok")


@app.command()
def resume(slug: str):
    """Resume a project that failed partway — only missing stages re-run."""
    cfg = _cfg()
    pipeline.resume_project(slug, cfg)


@app.command()
def publish(slug: str,
            platforms: str = typer.Option("youtube,tiktok,instagram,x",
                                          help="Comma-separated"),
            dry_run: bool = typer.Option(False, help="Don't actually upload"),
            video_url: Optional[str] = typer.Option(None, help="Public MP4 URL (Instagram)")):
    """Upload a finished project via official platform APIs."""
    plats = [p.strip() for p in platforms.split(",") if p.strip()]
    bad = [p for p in plats if p not in ADAPTERS]
    if bad:
        raise typer.BadParameter(f"Unknown platforms: {bad}. Choose from {ADAPTERS}.")
    kw = {"video_url": video_url} if video_url else {}
    pipeline.publish_project(slug, plats, dry_run=dry_run, **kw)


@app.command()
def post(video: str,
         title: Optional[str] = typer.Option(None, help="Video title"),
         desc: Optional[str] = typer.Option(None, help="Description"),
         tags: str = typer.Option("", help="Comma-separated hashtags"),
         platforms: str = typer.Option("youtube,tiktok,instagram,x",
                                        help="Comma-separated"),
         at: Optional[str] = typer.Option(None,
                                           help='Schedule for later, e.g. "Friday 6pm"'),
         dry_run: bool = typer.Option(False, help="Don't actually upload"),
         caption: bool = typer.Option(False,
                                       help="AI-generate title/desc/hashtags from the video")):
    """Post YOUR OWN video file — now, or scheduled. Not just AI-made ones."""
    from pathlib import Path as _Path

    cfg = _cfg()
    src = _Path(video)
    plats = [p.strip() for p in platforms.split(",") if p.strip()]
    bad = [p for p in plats if p not in ADAPTERS]
    if bad:
        raise typer.BadParameter(f"Unknown platforms: {bad}. Choose from {ADAPTERS}.")

    # Pre-flight: probe + warn before anything else.
    try:
        info = pipeline.validate_video(src, plats)
    except (FileNotFoundError, ValueError) as e:
        raise typer.BadParameter(str(e))
    console.print(f"[dim]📹 {info['width']}x{info['height']} {info['fps']}fps, "
                  f"{info['duration_str']}, {info['size_mb']}MB[/dim]")
    for w in info["warnings"]:
        console.print(f"[yellow]⚠ {w}[/yellow]")

    hashtags = [t.strip() for t in tags.split(",") if t.strip()]
    hashtags = [t if t.startswith("#") else f"#{t}" for t in hashtags]
    if caption or not title:
        console.print("[dim]✨ Generating caption with AI…[/dim]")
        try:
            sug = pipeline.suggest_caption(src, cfg)
            title = title or sug["title"]
            desc = desc if desc is not None else sug["description"]
            hashtags = hashtags or sug["hashtags"]
            console.print(f"[green]✓[/green] Title: [bold]{title}[/bold]")
        except RuntimeError as e:
            console.print(f"[yellow]⚠ {e} — continuing without AI caption.[/yellow]")
    title = title or src.stem.replace("-", " ").replace("_", " ").title()
    desc = desc or ""

    if at:
        run_at = sched_mod.parse_when(at, cfg.get("timezone"))
        staged = pipeline.stage_upload(src, cfg)
        job_id = sched_mod.enqueue(run_at, "publish_video", {
            "video": str(staged), "title": title, "description": desc,
            "hashtags": hashtags, "platforms": plats, "dry_run": dry_run})
        console.print(f"[green]✓[/green] Queued [bold]{title}[/bold] → "
                      f"{', '.join(plats)} at {run_at:%A %Y-%m-%d %H:%M %Z} "
                      f"(job #{job_id}). Run [bold]shortsmash worker[/bold] "
                      f"to process the queue.")
    else:
        pipeline.publish_video(src, title, desc, hashtags, plats, cfg,
                               dry_run=dry_run)


@app.command()
def schedule(topic: str,
             at: str = typer.Option(..., help='When, e.g. "Friday 6pm"'),
             duration: int = typer.Option(30),
             platforms: str = typer.Option("youtube,tiktok,instagram,x")):
    """Render now, publish later. The video is built immediately; the upload
    is queued for the time you name (timezone-aware)."""
    cfg = Config()
    workdir = pipeline.create_project(topic, duration, cfg)
    run_at = sched_mod.parse_when(at, cfg.get("timezone"))
    plats = [p.strip() for p in platforms.split(",")]
    job_id = sched_mod.enqueue(run_at, "publish",
                              {"slug": workdir.name, "platforms": plats})
    console.print(f"[green]✓[/green] Queued publish of [bold]{workdir.name}[/bold] "
                  f"→ {', '.join(plats)} at {run_at:%A %Y-%m-%d %H:%M %Z} "
                  f"(job #{job_id}). Run [bold]shortsmash worker[/bold] to process the queue.")


@app.command()
def queue(state: Optional[str] = typer.Option(None, help="queued|done|failed")):
    """Show the publish queue."""
    jobs = sched_mod.list_jobs(state)
    table = Table("id", "run at", "kind", "state", "tries", "detail")
    for j in jobs:
        table.add_row(str(j["id"]), j["run_at"][:16], j["kind"], j["state"],
                      str(j["attempts"]), (j["last_error"] or j["payload"])[:60])
    console.print(table)


@app.command()
def cancel(job_id: int):
    """Cancel a queued job."""
    if sched_mod.cancel(job_id):
        console.print(f"[green]✓[/green] Job {job_id} cancelled.")
    else:
        console.print(f"[yellow]![/yellow] Job {job_id} not found or already ran.")


@app.command()
def worker(once: bool = typer.Option(False, help="Process due jobs and exit")):
    """Run the publish queue (polls every 30s)."""
    console.print("Worker running — Ctrl+C to stop." if not once else "Processing due jobs…")
    sched_mod.run_worker(once=once)


@app.command(name="engage")
def engage_cmd(platform: str = typer.Option("youtube",
                                             help="youtube|tiktok|instagram|x")):
    """Pull recent comments, draft replies, approve each with one tap."""
    cfg = Config()
    if platform not in engage.FETCHERS:
        raise typer.BadParameter(f"Choose from {list(engage.FETCHERS)}.")
    comments = engage.FETCHERS[platform](cfg)
    if not comments:
        console.print("No recent comments found.")
        return
    posted = 0
    for c in comments[:20]:
        console.print(f"\n[bold]@{c.author}[/bold] on [dim]{c.video_title[:60]}[/dim]")
        console.print(f"  {c.text[:200]}")
        draft = engage.draft_reply(c, cfg)
        if draft.strip() == "SKIP":
            console.print("  [dim]skipped (spam/hate)[/dim]")
            continue
        console.print(f"  [cyan]Draft:[/cyan] {draft}")
        choice = typer.prompt("  [a]pprove / [e]dit / [s]kip", default="s")
        if choice == "a":
            engage.post_reply(c, draft, cfg)
            posted += 1
        elif choice == "e":
            edited = typer.prompt("  Your reply", default=draft)
            engage.post_reply(c, edited, cfg)
            posted += 1
    console.print(f"\n[green]✓[/green] Posted {posted} replies. Nothing else went out.")


@app.command(name="agent")
def agent_cmd(request: str = typer.Argument(..., help='e.g. "make a 30s reel about X, post Friday 6pm"')):
    """Natural-language control: describe what you want, confirm the plan."""
    cfg = Config()
    job = agent.parse_request(request, cfg)
    console.print(f"\n[bold]Plan:[/bold] {agent.describe(job)}")
    if not typer.confirm("Run it?", default=True):
        console.print("Cancelled — nothing was created.")
        return
    if job.action == "status":
        queue()
        for p in pipeline.list_projects()[:10]:
            console.print(f"  {p['slug']}  video={'✓' if p['has_video'] else '—'}")
    elif job.action == "engage":
        engage_cmd(job.platforms[0] if job.platforms else "youtube")
    elif job.action in ("create", "schedule", "publish"):
        if job.action == "publish" and not job.project_slug and not job.topic:
            console.print("[red]Which project?[/red] Name it: --slug or describe the topic.")
            return
        if job.action == "publish":
            slug = job.project_slug or _pick_project(job.topic)
            pipeline.publish_project(slug, job.platforms)
        else:
            workdir = pipeline.create_project(job.topic or "untitled", job.duration, cfg)
            if job.schedule_at:
                from datetime import datetime
                run_at = datetime.fromisoformat(job.schedule_at)
                jid = sched_mod.enqueue(run_at, "publish",
                                       {"slug": workdir.name, "platforms": job.platforms})
                console.print(f"[green]✓[/green] Publish queued (job #{jid}) for {run_at}.")
            else:
                pipeline.publish_project(workdir.name, job.platforms)
    console.print("[green]✓ Done.[/green]")


def _pick_project(topic: str) -> str:
    projects = pipeline.list_projects()
    if not projects:
        raise typer.BadParameter("No projects yet — create one first.")
    if topic:
        hits = [p for p in projects if topic.lower() in p["slug"].lower()]
        if hits:
            return hits[0]["slug"]
    return projects[0]["slug"]


@app.command(name="list")
def list_projects():
    """Show projects and their render state."""
    for p in pipeline.list_projects():
        icon = "🎬" if p["has_video"] else "📝"
        size = f"  {p['size'] // 1024}KB" if p["has_video"] else ""
        console.print(f"{icon} {p['slug']}{size}")


@app.command()
def trends(niche: str = typer.Argument("general", help="Topic niche"),
           limit: int = typer.Option(10, help="How many trends to show")):
    """Show what's trending right now in a niche (free, no API keys)."""
    from . import trends as tmod
    console.print(f"[bold]Trending in {niche}:[/bold]")
    results = tmod.harvest(niche, limit)
    if not results:
        console.print("[yellow]No trends found — check your connection.[/yellow]")
        return
    for i, t in enumerate(results, 1):
        console.print(f"  {i}. [cyan]{t.title}[/cyan]")
        console.print(f"     [dim]{t.source} · score {t.score}[/dim]")


@app.command()
def batch(niche: str = typer.Argument(..., help="Niche to harvest trends from"),
          count: int = typer.Option(3, help="Videos to produce"),
          duration: int = typer.Option(30, help="Seconds per video"),
          topics: str = typer.Option("", help="Comma-separated topics (skips trend harvest)")):
    """Produce a batch of videos: harvest trends, then render one video each."""
    from . import trends as tmod
    cfg = _cfg()
    if topics:
        prompts = [p.strip() for p in topics.split(",") if p.strip()]
    else:
        console.print(f"[bold]Harvesting trends in {niche}…[/bold]")
        harvested = tmod.harvest(niche, count)
        if not harvested:
            console.print("[red]No trends found — pass --topics instead.[/red]")
            raise typer.Exit(1)
        prompts = [tmod.to_video_prompt(t, niche) for t in harvested]
        for p in prompts:
            console.print(f"  • {p}")
    done, failed = [], []
    for i, prompt in enumerate(prompts, 1):
        console.print(f"\n[bold][{i}/{len(prompts)}] {prompt}[/bold]")
        try:
            workdir = pipeline.create_project(prompt, duration, cfg)
            done.append(workdir.name)
            console.print(f"[green]✓[/green] {workdir.name}")
        except Exception as e:
            failed.append((prompt, str(e)))
            console.print(f"[red]✗[/red] {prompt}: {e}")
    console.print(f"\n[bold]Batch done:[/bold] {len(done)} ok, {len(failed)} failed")
    for slug in done:
        console.print(f"  🎬 {slug}")


@app.command()
def serve(host: str = typer.Option("127.0.0.1", help="Bind address"),
          port: int = typer.Option(8000, help="Port")):
    """Launch ShortSmash Studio — the web UI."""
    from .web import main as serve_main
    console.print(f"[bold]ShortSmash Studio:[/bold] http://{host}:{port}")
    serve_main(host, port)


@app.command()
def mcp():
    """Run the ShortSmash MCP server over stdio (for Claude Code, Cursor, …)."""
    from .mcp_server import main as mcp_main
    mcp_main()


@app.command()
def analytics():
    """Show real view/like/comment stats for published videos."""
    from . import analytics as amod
    rows = amod.collect()
    if not rows:
        console.print("[yellow]No published videos found.[/yellow]")
        return
    total_views = sum(r.views for r in rows)
    console.print(f"[bold]{len(rows)} published videos · "
                  f"{total_views:,} total views[/bold]\n")
    for r in rows:
        console.print(f"  [cyan]{r.slug[:40]}[/cyan] [{r.platform}]")
        if r.views or r.likes or r.comments:
            console.print(f"    👁 {r.views:,}  ♥ {r.likes:,}  💬 {r.comments:,}")
        console.print(f"    [dim]{r.url}[/dim]")


@app.command()
def dub(slug: str, lang: str = typer.Option(..., help="ur|ur-roman|hi|hi-roman|ar|es|fr|de|pt")):
    """Re-voice a project in another language (same clips, new narration)."""
    from . import dub as dub_mod
    from .voice import DUB_LANGUAGES
    if lang not in DUB_LANGUAGES:
        console.print(f"[red]Choose from: {', '.join(DUB_LANGUAGES)}[/red]")
        raise typer.Exit(1)
    final = dub_mod.dub_project(slug, lang, _cfg())
    console.print(f"[green]✓ Dubbed ({DUB_LANGUAGES[lang]}):[/green] {final}")


@app.command()
def version():
    console.print(f"ShortSmash {__version__}")


@app.command()
def lyrics(lyrics_file: str = typer.Argument(..., help="Text file with lyrics (one line per screen)"),
           title: str = typer.Option("", help="Video title (defaults to first line)"),
           duration: int = typer.Option(30, help="Target seconds (5-180)")):
    """Render a karaoke-style lyrics video from a text file."""
    from pathlib import Path as _P
    cfg = _cfg()
    text = _P(lyrics_file).read_text(encoding="utf-8")
    workdir = pipeline.create_lyrics_project(text, title, duration, cfg)
    console.print(f"[bold green]🎵 Lyrics video ready:[/bold green] {workdir}")


@app.command()
def update():
    """Pull the latest ShortSmash from GitHub and reinstall. Keeps your config."""
    import subprocess
    repo = Path(__file__).resolve().parent.parent
    console.print("[bold]Updating ShortSmash…[/bold]")
    try:
        subprocess.run(["git", "pull", "--ff-only"], cwd=repo, check=True,
                       capture_output=True, text=True)
        console.print("[green]✓[/green] Latest code pulled")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip().split("\n")[-1]
        console.print(f"[red]✗[/red] git pull failed: {err}")
        console.print("  Are you inside the cloned repo?")
        raise typer.Exit(1)
    vpy = repo / (".venv/Scripts/python.exe" if os.name == "nt"
                  else ".venv/bin/python")
    if vpy.exists():
        console.print("Reinstalling into your venv…")
        subprocess.run([str(vpy), "-m", "pip", "-q", "install", "-e", "."],
                       cwd=repo, check=True)
        console.print("[green]✓[/green] Reinstalled")
    else:
        console.print("[yellow]![/yellow] No .venv found — run ./install.sh (or install.bat) once.")
    console.print(f"[bold green]ShortSmash is up to date.[/bold green]")


if __name__ == "__main__":
    app()
