"""LLM routing: one interface, multiple free providers.

Provider pick (`llm_provider` in config, default "auto"):
  nvidia   — NVIDIA NIM at integrate.api.nvidia.com/v1 (OpenAI-compatible).
             Free tier, no credit card, ~40 RPM. Already Syed's production LLM.
  gemini   — Google AI Studio free tier.
  grok     — xAI Grok (OpenAI-compatible at api.x.ai/v1). Needs XAI_API_KEY
             from https://console.x.ai. Config: grok_model
             (default grok-4-1-fast).
  openai   — any OpenAI-compatible endpoint via openai_base_url/openai_model:
             OpenRouter, DeepSeek, Together, or local Ollama
             (http://localhost:11434/v1, free, no key).
  template — offline fallback, no LLM at all.

Used for: script/edit-plan JSON (script.py), comment drafts (engage.py),
natural-language parsing (agent.py).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import httpx

from .config import Config

NVIDIA_API = "https://integrate.api.nvidia.com/v1"
NVIDIA_CHAT = f"{NVIDIA_API}/chat/completions"
NVIDIA_MODELS = f"{NVIDIA_API}/models"

# Preferred chat models, in order. NVIDIA rotates the catalog (meta/llama IDs
# went 410 in Sept 2026), so the resolver below picks the first one that's
# actually live instead of trusting a hardcoded ID.
_NVIDIA_PREFERENCE = [
    # First entry is production-proven: ShiaRishta's blog engine runs this
    # exact model on this key daily. Verified live 2026-10-16.
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/llama-3.1-nemotron-51b-instruct",
    "mistralai/mistral-large-2-instruct",
    "mistralai/mistral-7b-instruct-v0.3",
]

_model_cache: dict[str, str] = {}
_dead_models: set[str] = set()  # IDs that 404/410'd this session — skip them

# Vault-held key path: custom.nvidia connector + helper script. The key itself
# never enters this process — the helper's request carries a surrogate that
# the runtime swaps for the real credential on the approved host.
_NVIDIA_HELPER = (Path.home() / "workspace/custom-connectors/custom.nvidia"
                  / "bin/nvidia.py")
_GEMINI_HELPER = (Path.home() / "workspace/custom-connectors/custom.gemini"
                  / "bin/gemini.py")
_connector_ok: bool | None = None
_gemini_connector_ok: bool | None = None


def _helper_call(op: str, payload: dict | None = None, timeout: int = 150,
                 helper: Path | None = None) -> dict:
    proc = subprocess.run(
        [sys.executable, str(helper or _NVIDIA_HELPER), op],
        input=json.dumps(payload or {}).encode(),
        capture_output=True, timeout=timeout)
    try:
        data = json.loads(proc.stdout.decode() or "{}")
    except Exception:
        data = {}
    if not data.get("ok"):
        raise RuntimeError(
            data.get("error") or proc.stderr.decode()[:300] or "helper failed")
    return data


def _connector_available() -> bool:
    """True when the vault holds a working custom.nvidia credential.

    Probed once per process (a cheap /v1/models call) and cached.
    """
    global _connector_ok
    if _connector_ok is None:
        try:
            _helper_call("models", timeout=30)
            _connector_ok = True
        except Exception:
            _connector_ok = False
    return _connector_ok


def _pick_model(available: set[str], preference: list[str]) -> str:
    live = available - _dead_models
    for m in preference:
        if m in live:
            return m

    def score(mid: str) -> int:
        s = 0
        for fam in ("nemotron", "llama", "mistral", "qwen", "deepseek", "kimi"):
            if fam in mid:
                s += 1
        if any(bad in mid for bad in ("vision", "embed", "translate", "code",
                                      "rerank")):
            s -= 10
        return s

    cands = sorted([m for m in live if "instruct" in m],
                   key=score, reverse=True)
    if not cands:
        raise RuntimeError("No chat model found in the NVIDIA catalog.")
    return cands[0]


def _is_gone(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (404, 410)
    return "404" in str(exc) or "410" in str(exc)


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, subprocess.TimeoutExpired) or "timed out" in str(exc)


def provider(cfg: Config | None = None) -> str:
    cfg = cfg or Config()
    pinned = cfg.get("llm_provider", "auto")
    if pinned != "auto":
        return pinned
    if cfg.secret("NVIDIA_API_KEY"):
        return "nvidia"
    if _NVIDIA_HELPER.exists() and _connector_available():
        return "nvidia"
    if cfg.gemini_key():
        return "gemini"
    if _GEMINI_HELPER.exists() and _gemini_connector_available():
        return "gemini"
    if cfg.secret("XAI_API_KEY"):
        return "grok"
    if cfg.secret("OPENAI_API_KEY") or cfg.get("openai_base_url"):
        return "openai"
    return "template"


def _grok(system: str | None, user: str, cfg: Config,
          temperature: float, json_mode: bool) -> str:
    """xAI Grok via its OpenAI-compatible endpoint."""
    key = cfg.secret("XAI_API_KEY")
    if not key:
        raise RuntimeError("llm_provider=grok needs XAI_API_KEY (console.x.ai).")
    model = cfg.get("grok_model", "grok-4-1-fast")
    body: dict = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post("https://api.x.ai/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini_connector_available() -> bool:
    """True when the vault holds a working custom.gemini credential."""
    global _gemini_connector_ok
    if _gemini_connector_ok is None:
        try:
            _helper_call("generate", {"user": "ok", "model": "gemini-3.8-flash"},
                         timeout=30, helper=_GEMINI_HELPER)
            _gemini_connector_ok = True
        except Exception:
            _gemini_connector_ok = False
    return _gemini_connector_ok


def _extract_json(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("LLM returned no text to parse.")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output.")
    return json.loads(text[start:end + 1])


def _resolve_nvidia_model(cfg: Config) -> str:
    """Pick the best live model: pinned config wins, else auto-discover."""
    pinned = cfg.get("nvidia_model", "auto")
    if pinned != "auto":
        return pinned
    if "nvidia" in _model_cache:
        return _model_cache["nvidia"]
    key = cfg.secret("NVIDIA_API_KEY")
    if key:
        r = httpx.get(NVIDIA_MODELS, headers={"Authorization": f"Bearer {key}"},
                      timeout=30)
        r.raise_for_status()
        available = {m["id"] for m in r.json().get("data", [])}
    else:
        data = _helper_call("models", timeout=30)
        available = {m["id"] for m in data["data"].get("data", [])}
    model = _pick_model(available, _NVIDIA_PREFERENCE)
    _model_cache["nvidia"] = model
    return model


def _nvidia_post(system: str | None, user: str, cfg: Config, key: str,
                 model: str, temperature: float, json_mode: bool) -> str:
    body: dict = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": 16000,  # reasoning models need headroom (see _nvidia)
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(NVIDIA_CHAT, headers={"Authorization": f"Bearer {key}"},
                   json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _nvidia(system: str | None, user: str, cfg: Config,
           temperature: float, json_mode: bool) -> str:
    key = cfg.secret("NVIDIA_API_KEY")
    last: Exception | None = None
    # Try up to 3 different models: a 404/410 marks the model dead and the
    # resolver picks the next-best live one.
    for _ in range(3):
        _model_cache.pop("nvidia", None)
        model = _resolve_nvidia_model(cfg)
        payload = {"system": system, "user": user, "model": model,
                   "temperature": temperature, "json_mode": json_mode,
                   "max_tokens": 16000}
        try:
            if key:
                return _nvidia_post(system, user, cfg, key, model, temperature,
                                    json_mode)
            # Vault-held key: the helper's request carries the surrogate; the
            # key itself never enters this process.
            #
            # Production lesson (ShiaRishta, same key/model): a 200 with empty
            # content is intermittent — one same-model retry usually clears
            # it; two in a row means the model can't do this prompt.
            data = _helper_call("chat", payload)
            content = data.get("content")
            if not content:
                data = _helper_call("chat", payload)
                content = data.get("content")
            if not content:
                # Reasoning models can return empty content when the token
                # budget is eaten by thinking — rotate to the next model.
                _dead_models.add(model)
                last = RuntimeError(f"{model} returned empty content twice")
                continue
            return content
        except Exception as exc:  # noqa: BLE001 — inspected below
            if _is_gone(exc) or _is_timeout(exc):
                # Dead/unreachable/slow model: skip it for the rest of the
                # session and try the next-best live one.
                _dead_models.add(model)
                last = exc
                continue
            raise
    raise RuntimeError(f"NVIDIA: 3 models failed in a row: {last}")


def _gemini(system: str | None, user: str, cfg: Config,
           temperature: float, json_mode: bool) -> str:
    model = cfg.get("gemini_model") or "gemini-3.8-flash"
    if cfg.gemini_key():
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("pip install 'shortsmash[ai]' first.") from exc
        # google-genai (the supported SDK; google-generativeai is end-of-life).
        client = genai.Client(api_key=cfg.gemini_key())
        resp = client.models.generate_content(
            model=model,
            contents=user,
            config=genai.types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json" if json_mode else "text/plain",
                temperature=temperature,
            ),
        )
        return resp.text
    # Vault-held key: the helper's request carries the surrogate; the key
    # itself never enters this process.
    data = _helper_call("generate", {"system": system, "user": user,
                                     "model": model, "temperature": temperature,
                                     "json_mode": json_mode},
                        helper=_GEMINI_HELPER)
    content = data.get("content")
    if not content:
        raise RuntimeError("Gemini returned empty content")
    return content


def _openai(system: str | None, user: str, cfg: Config,
            temperature: float, json_mode: bool) -> str:
    """Generic OpenAI-compatible chat endpoint.

    One provider unlocks many: set openai_base_url to use OpenRouter,
    DeepSeek, Together, or a local Ollama (http://localhost:11434/v1 —
    free, no key needed beyond the URL).
    """
    base = (cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = cfg.get("openai_model") or "gpt-4o-mini"
    key = cfg.secret("OPENAI_API_KEY") or "not-needed"  # Ollama ignores it
    body: dict = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": user}],
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(f"{base}/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call(system: str | None, user: str, cfg: Config,
         temperature: float, json_mode: bool) -> str:
    p = provider(cfg)
    if p == "nvidia":
        return _nvidia(system, user, cfg, temperature, json_mode)
    if p == "gemini":
        return _gemini(system, user, cfg, temperature, json_mode)
    if p == "grok":
        return _grok(system, user, cfg, temperature, json_mode)
    if p == "openai":
        return _openai(system, user, cfg, temperature, json_mode)
    raise RuntimeError("No LLM configured (set NVIDIA_API_KEY or GEMINI_API_KEY).")


def chat_json(system: str | None, user: str, cfg: Config | None = None,
              temperature: float = 0.9) -> dict:
    """Chat expecting a JSON object back (script plans, agent parsing)."""
    return _extract_json(_call(system, user, cfg or Config(), temperature, True))


def chat_text(system: str | None, user: str, cfg: Config | None = None,
              temperature: float = 0.8) -> str:
    """Plain-text chat (comment drafts)."""
    return _call(system, user, cfg or Config(), temperature, False).strip()
