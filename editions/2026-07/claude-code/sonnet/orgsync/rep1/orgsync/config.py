"""Loading of `config.json`, which names the two services orgsync talks to.

The file is provisioned by the environment orgsync runs in; orgsync only ever
reads it. Keeping the parse in one place means every command fails the same
way — with a clear message naming the file — when it is missing or incomplete.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_CONFIG_PATH = "config.json"

#: Keys `config.json` must carry. Extra keys are ignored, so the environment
#: can add its own without breaking the tool.
REQUIRED_KEYS = ("directory_url", "ledger_url")


class ConfigError(Exception):
    """Raised when `config.json` is missing, unreadable, or incomplete."""


@dataclass(frozen=True)
class Config:
    """Base URLs of the two services, without a trailing slash."""

    directory_url: str
    ledger_url: str


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    """Read and validate `config.json`.

    URLs come back stripped of whitespace and of any trailing slash, so a
    caller can concatenate a path beginning with "/" without doubling it.
    Raises `ConfigError` if the file is absent, is not a JSON object, is not
    valid JSON, or a required key is missing or not a non-empty string.
    """
    if not os.path.exists(path):
        raise ConfigError("config file not found: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read %s: %s" % (path, exc)) from exc

    if not isinstance(raw, dict):
        raise ConfigError("%s must contain a JSON object" % path)

    values = {}
    for key in REQUIRED_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError("%s: missing or empty %r" % (path, key))
        values[key] = value.strip().rstrip("/")
    return Config(**values)
