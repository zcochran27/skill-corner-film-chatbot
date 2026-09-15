#!/usr/bin/env python
"""
Load local secrets from the gitignored .env at the repo root.

Kept separate so every script that needs credentials resolves them the same way, and so the
placeholder check lives in one place rather than being re-implemented per caller.

Resolution order matches the SDKs' own: an already-exported environment variable wins over
.env, so `ANTHROPIC_API_KEY=... python script.py` overrides the file without editing it.

Two providers can run the query-parsing chain. LLM_PROVIDER picks one (default: anthropic);
every credential function takes an optional `provider` and otherwise uses that setting.

Usage:
    from env import load_env, require_api_key
    load_env()
    key = require_api_key()      # exits with a useful message if absent or unreplaced
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"

#: Values that mean "the user copied the template but has not filled it in". Treated as
#: absent, because a confusing 401 from the API is a worse message than saying so directly.
PLACEHOLDERS = {"", "REPLACE_ME", "sk-ant-...", "AIza...", "your-api-key", "changeme"}

PROVIDERS = {
    "anthropic": dict(
        name="Anthropic",
        key_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        example="sk-ant-...",
        keys_url="https://console.anthropic.com/settings/keys",
    ),
    "gemini": dict(
        name="Google Gemini",
        # GEMINI_API_KEY is what AI Studio tells you to set; GOOGLE_API_KEY is the SDK's
        # other accepted name.
        key_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        example="AIza...",
        keys_url="https://aistudio.google.com/apikey",
    ),
}
DEFAULT_PROVIDER = "anthropic"

_loaded = False


def load_env(override: bool = False) -> bool:
    """Load .env into os.environ. Returns True if the file existed.

    override=False by default so a real exported variable beats the file.
    """
    global _loaded
    if _loaded and not override:
        return ENV_FILE.exists()
    _loaded = True
    if not ENV_FILE.exists():
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=override)
    except ImportError:
        # Tiny fallback so a missing python-dotenv is not a hard blocker.
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if override or key not in os.environ:
                os.environ[key] = value
    return True


def provider(explicit: str | None = None) -> str:
    """The LLM provider: an explicit choice (a --provider flag), else LLM_PROVIDER."""
    load_env()
    value = (explicit or os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if value not in PROVIDERS:
        print(f"Unknown LLM provider {value!r}; expected one of {sorted(PROVIDERS)}.",
              file=sys.stderr)
        sys.exit(2)
    return value


def api_key(provider_name: str | None = None) -> str | None:
    """The provider's credential, or None if absent or still a placeholder."""
    spec = PROVIDERS[provider(provider_name)]
    for name in spec["key_vars"]:
        value = (os.environ.get(name) or "").strip()
        if value and value not in PLACEHOLDERS:
            return value
    return None


def require_api_key(what: str = "this", provider_name: str | None = None) -> str:
    """The credential, or exit with a message that says exactly what to do."""
    prov = provider(provider_name)
    key = api_key(prov)
    if key:
        return key
    spec = PROVIDERS[prov]
    var = spec["key_vars"][0]
    raw = (os.environ.get(var) or "").strip()
    if raw in PLACEHOLDERS and raw:
        problem = f"{ENV_FILE.name} still has the placeholder value {raw!r}."
    elif ENV_FILE.exists():
        problem = f"{ENV_FILE.name} exists but sets no {var}."
    else:
        problem = f"No {ENV_FILE.name} found at the repo root."
    print(
        f"No {spec['name']} credential available, so {what} cannot run.\n"
        f"  {problem}\n\n"
        f"  Fix it with:\n"
        f"      cp {EXAMPLE_FILE.name} {ENV_FILE.name}   # if .env does not exist yet\n"
        f"      # then edit {ENV_FILE.name} and set {var}={spec['example']}\n\n"
        f"  .env is gitignored. Keys are at {spec['keys_url']}\n"
        f"  To exercise the chain without a key, pass --offline (keyword stub; it proves\n"
        f"  the plumbing works, not that extraction is any good).",
        file=sys.stderr)
    sys.exit(2)


def effort() -> str | None:
    load_env()
    value = (os.environ.get("ANTHROPIC_EFFORT") or "").strip().lower()
    return value if value in {"low", "medium", "high", "xhigh", "max"} else None


def gemini_model(default: str) -> str:
    load_env()
    return (os.environ.get("GEMINI_MODEL") or "").strip() or default


def gemini_thinking() -> str | None:
    load_env()
    value = (os.environ.get("GEMINI_THINKING") or "").strip().lower()
    return value if value in {"minimal", "low", "medium", "high"} else None


def status(provider_name: str | None = None) -> str:
    prov = provider(provider_name)
    key = api_key(prov)
    if not key:
        var = PROVIDERS[prov]["key_vars"][0]
        return (f"provider={prov}: no credential - run with --offline, or set {var} in "
                f"{ENV_FILE.name}")
    if prov == "gemini":
        return (f"provider=gemini: credential found (…{key[-4:]}), "
                f"thinking={gemini_thinking() or 'default'}")
    return f"provider=anthropic: credential found (…{key[-4:]}), effort={effort() or 'default'}"


if __name__ == "__main__":
    print(f".env present : {ENV_FILE.exists()}")
    for p in PROVIDERS:
        print(f"{p:12s} : {status(p)}")
    print(f"active       : {provider()}")
