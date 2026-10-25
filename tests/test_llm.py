from shortsmash import llm as llm_mod
from shortsmash.config import Config


def test_provider_prefers_nvidia(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    assert llm_mod.provider(Config({"llm_provider": "auto"})) == "nvidia"


def _no_connector(monkeypatch):
    # Neutralize the vault paths so tests exercise the env-only routing.
    import pathlib
    monkeypatch.setattr(llm_mod, "_NVIDIA_HELPER",
                        pathlib.Path("/nonexistent/nvidia.py"))
    monkeypatch.setattr(llm_mod, "_GEMINI_HELPER",
                        pathlib.Path("/nonexistent/gemini.py"))


def test_provider_falls_back_to_gemini(monkeypatch):
    _no_connector(monkeypatch)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    assert llm_mod.provider(Config({"llm_provider": "auto"})) == "gemini"


def test_provider_template_when_no_keys(monkeypatch):
    _no_connector(monkeypatch)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert llm_mod.provider(Config({"llm_provider": "auto"})) == "template"


def test_provider_pinned():
    assert llm_mod.provider(Config({"llm_provider": "gemini"})) == "gemini"


def test_extract_json_tolerant():
    raw = '```json\n{"a": 1}\n```'
    assert llm_mod._extract_json(raw) == {"a": 1}


def test_pick_model_prefers_known_good():
    available = {"openai/gpt-oss-120b",
                 "nvidia/nemotron-3.5-lightning-30b-a3b",
                 "some/unknown-model"}
    assert llm_mod._pick_model(available, llm_mod._NVIDIA_PREFERENCE) == \
        "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_pick_model_falls_back_to_any_instruct():
    available = {"acme/new-instruct-model", "acme/embed-model"}
    assert llm_mod._pick_model(available, llm_mod._NVIDIA_PREFERENCE) == \
        "acme/new-instruct-model"


def test_pick_model_skips_dead():
    llm_mod._dead_models.add("nvidia/nemotron-3.5-lightning-30b-a3b")
    try:
        available = {"nvidia/nemotron-3.5-lightning-30b-a3b",
                     "mistralai/mistral-7b-instruct-v0.3"}
        assert llm_mod._pick_model(available, llm_mod._NVIDIA_PREFERENCE) == \
            "mistralai/mistral-7b-instruct-v0.3"
    finally:
        llm_mod._dead_models.discard("nvidia/nemotron-3.5-lightning-30b-a3b")


class _StubCfg:
    def get(self, key, default=None):
        return {"gemini_model": "gemini-3.8-flash"}.get(key, default)

    def gemini_key(self):
        return "dummy-key"


def test_gemini_uses_supported_sdk_mapping(monkeypatch):
    """google-genai mapping: model, contents, system instruction, mime type,
    temperature, and key must reach the client exactly (2026-10-25 migration
    off end-of-life google-generativeai). No live call: Client is faked."""
    from google import genai

    calls = []

    class FakeResp:
        text = "hello"

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls.append({"model": model, "contents": contents, "config": config})
            return FakeResp()

    class FakeClient:
        def __init__(self, api_key=None):
            calls.append({"api_key": api_key})
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    out = llm_mod._gemini("sys", "user hi", _StubCfg(), 0.5, True)
    assert out == "hello"
    assert calls[0] == {"api_key": "dummy-key"}
    assert calls[1]["model"] == "gemini-3.8-flash"
    assert calls[1]["contents"] == "user hi"
    assert calls[1]["config"].system_instruction == "sys"
    assert calls[1]["config"].response_mime_type == "application/json"
    assert calls[1]["config"].temperature == 0.5
    llm_mod._gemini("sys", "user hi", _StubCfg(), 0.5, False)
    assert calls[-1]["config"].response_mime_type == "text/plain"
