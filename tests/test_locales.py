"""Locale files (src/resources/lang/*.json): every language must match en.json."""

import json
import os
import re
import string

import pytest

from src import messages
from src.messages import LANG_DIR, available_langs, msg_for

_FIELDS = string.Formatter()


def _load(lang):
    with open(os.path.join(LANG_DIR, f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


def _placeholders(text):
    return sorted(field for _, field, _, _ in _FIELDS.parse(text) if field is not None)


EN = _load("en")
OTHER_LANGS = [l for l in available_langs() if l != "en"]


def test_expected_languages_present():
    assert {"en", "ko", "az", "ru", "tr"} <= set(available_langs())


@pytest.mark.parametrize("lang", OTHER_LANGS)
def test_same_keys_as_english(lang):
    table = _load(lang)
    assert set(table) == set(EN), f"missing: {set(EN) - set(table)}, extra: {set(table) - set(EN)}"


@pytest.mark.parametrize("lang", OTHER_LANGS)
def test_placeholders_match_english(lang):
    table = _load(lang)
    bad = {k: (_placeholders(EN[k]), _placeholders(v)) for k, v in table.items()
           if k in EN and _placeholders(v) != _placeholders(EN[k])}
    assert not bad


@pytest.mark.parametrize("lang", available_langs())
def test_values_non_empty_strings(lang):
    assert all(isinstance(v, str) and v.strip() for v in _load(lang).values())


@pytest.mark.parametrize("lang", available_langs())
def test_stuck_reason_keeps_marker(lang):
    # tg_claw._restart_session checks `"STUCK" in reason`
    assert "STUCK" in _load(lang)["reason_stuck"]


@pytest.mark.parametrize("lang", available_langs())
def test_command_descriptions_fit_telegram(lang):
    from src.telegram_api import BOT_COMMANDS
    for c in BOT_COMMANDS:
        desc = msg_for(lang, f"cmd_{c}")
        assert desc != f"cmd_{c}" and 1 <= len(desc) <= 256


def test_unknown_lang_falls_back_to_english():
    assert msg_for("xx", "ask_empty") == EN["ask_empty"]


def test_unknown_key_returns_key():
    assert msg_for("en", "no_such_key") == "no_such_key"


def test_formatting_and_bad_kwargs():
    assert msg_for("en", "reason_consecutive_errors", count=3) == "3 consecutive errors"
    # wrong kwargs must not raise
    assert msg_for("en", "reason_consecutive_errors", wrong=1) == EN["reason_consecutive_errors"]


def test_msg_uses_configured_lang():
    assert messages.msg("ask_empty") == msg_for(messages.LANG, "ask_empty")
