#!/usr/bin/env python
"""
Load local secrets from the gitignored .env at the repo root.

Kept separate so every script that needs credentials resolves them the same way, and so the
placeholder check lives in one place rather than being re-implemented per caller.

Resolution order matches the SDK's own: an already-exported environment variable wins over
.env, so `ANTHROPIC_API_KEY=... python script.py` overrides the file without editing it.

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
PLACEHOLDERS = {"", "REPLACE_ME", "sk-ant-...", "your-api-key", "changeme"}

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


def api_key() -> str | None:
    """The Anthropic credential, or None if absent or still a placeholder."""
    load_env()
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value and value not in PLACEHOLDERS:
            return value
    return None


def require_api_key(what: str = "this") -> str:
    """The credential, or exit with a message that says exactly what to do."""
    key = api_key()
    if key:
        return key
    raw = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if raw in PLACEHOLDERS and raw:
        problem = f"{ENV_FILE.name} still has the placeholder value {raw!r}."
    elif ENV_FILE.exists():
        problem = f"{ENV_FILE.name} exists but sets no ANTHROPIC_API_KEY."
    else:
        problem = f"No {ENV_FILE.name} found at the repo root."
    print(
        f"No Anthropic credential available, so {what} cannot run.\n"
        f"  {problem}\n\n"
        f"  Fix it with:\n"
        f"      cp {EXAMPLE_FILE.name} {ENV_FILE.name}   # if .env does not exist yet\n"
        f"      # then edit {ENV_FILE.name} and set ANTHROPIC_API_KEY=sk-ant-...\n\n"
        f"  .env is gitignored. Keys are at https://console.anthropic.com/settings/keys\n"
        f"  To exercise the chain without a key, pass --offline (keyword stub; it proves\n"
        f"  the plumbing works, not that extraction is any good).",
        file=sys.stderr)
    sys.exit(2)


def effort() -> str | None:
    load_env()
    value = (os.environ.get("ANTHROPIC_EFFORT") or "").strip().lower()
    return value if value in {"low", "medium", "high", "xhigh", "max"} else None


def status() -> str:
    load_env()
    key = api_key()
    if key:
        return f"credential found (…{key[-4:]}), effort={effort() or 'default'}"
    return "no credential - run with --offline, or set ANTHROPIC_API_KEY in .env"


if __name__ == "__main__":
    print(f".env present : {ENV_FILE.exists()}")
    print(f"status       : {status()}")
