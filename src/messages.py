"""i18n messages: all user-facing text, loaded from resources/lang/<lang>.json.

Usage:
    from .messages import msg
    msg("restart_done", name="Converter")  # → "[TC] Converter: restart complete, ..."

To add a language, drop a new <lang>.json next to en.json with the same keys.
Missing keys fall back to English, then to the key itself.
"""

import json
import os
import sys

from .config import LANG

LANG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lang")
FALLBACK_LANG = "en"

_tables: dict[str, dict[str, str]] = {}


def available_langs() -> list[str]:
    """Language codes that have a locale file."""
    return sorted(f[:-5] for f in os.listdir(LANG_DIR) if f.endswith(".json"))


def _table(lang: str) -> dict[str, str]:
    """Load (and cache) the strings for a language. Unknown or broken files yield {}."""
    if lang not in _tables:
        try:
            with open(os.path.join(LANG_DIR, f"{lang}.json"), encoding="utf-8") as f:
                _tables[lang] = json.load(f)
        except FileNotFoundError:
            print(f"[messages] no locale for lang '{lang}', using '{FALLBACK_LANG}' "
                  f"(available: {', '.join(available_langs())})", file=sys.stderr)
            _tables[lang] = {}
        except (OSError, ValueError) as e:
            print(f"[messages] failed to load locale '{lang}': {e}", file=sys.stderr)
            _tables[lang] = {}
    return _tables[lang]


def msg_for(lang: str, key: str, **kwargs) -> str:
    """Return the text for a key in the given language, formatted with kwargs."""
    text = _table(lang).get(key)
    if text is None:
        text = _table(FALLBACK_LANG).get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def msg(key: str, **kwargs) -> str:
    """Return the text for a message key in the configured language, formatted with kwargs."""
    return msg_for(LANG, key, **kwargs)
