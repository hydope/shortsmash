# Dev.to article draft — ShortSmash

**Title:** I built a prompt-to-published short video pipeline — here's how the footage verification works

**Tags:** opensource, python, ai, video

---

Every AI shorts tool has the same dirty secret: they assemble clips blindly. The stock API returns *something* for "espresso," and that something goes into your video. I've seen burgers in cooking videos. Robins in cooking tutorials.

So when I built ShortSmash (https://github.com/hydope/shortsmash), I made footage verification the core of the pipeline, not an afterthought.

## The two-stage vision gate

Every candidate clip passes two independent judges before it ships:

1. **Local CLIP (ViT-B/32)** — runs fully offline. The clip's frames are scored against the scene description *and* against 15 negative labels ("cartoon," "watermark," "unrelated object"…). If a negative label wins, the clip dies here.
2. **Gemini vision** — the surviving frames go to Gemini with the scene description. Both judges must agree.

Either one can veto. Gemini errors fail *open* (a down API shouldn't block your render), but CLIP never sleeps — it's local.

## The rest of the pipeline

- **Script**: LLM writes the narration + a pydantic-validated shot-by-shot edit plan (cinematographer-style shot lists, not "clip 1, clip 2")
- **Voice**: 50 languages, native neural voices. Urdu captions render in Nastaliq calligraphy with RTL-correct karaoke highlighting — the hardest caption problem I've ever solved, because libass applies karaoke in visual order
- **Visuals**: Pixabay stock (free), Agnes AI (free tier), Veo 3.1 / Kling (paid, BYO key), or any generator via a `visual_command` shell template
- **Publish**: one click to YouTube, TikTok, Instagram, X, Threads, Bluesky, LinkedIn, Pinterest, Facebook — official APIs only
- **Auto mode**: trend → video → scheduled publish, with per-API quota budgeting so you never burn a free tier by accident

## Try it

```bash
curl -fsSL https://raw.githubusercontent.com/hydope/shortsmash/master/install.sh | bash
shortsmash serve
```

MIT licensed. The README demo (including the Mandarin version) was made end-to-end by the tool itself. If you build something with it, I'd love to see it.
