"""Load the user-facing texts in resources/ (strings.toml, tutorial.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

import rtoml


def _read(name: str) -> str:
    return resources.files("mocapapp.resources").joinpath(name).read_text(encoding="utf-8")


@cache
def strings() -> dict:
    return rtoml.loads(_read("strings.toml"))


def s(key: str) -> str:
    """strings()['a']['b'] for key 'a.b'."""
    value = strings()
    for part in key.split("."):
        value = value[part]
    return value


@dataclass(frozen=True)
class TutorialPage:
    title: str
    markdown: str
    downloads: tuple[str, ...]  # 'board' | 'consent'


@cache
def tutorial() -> list[TutorialPage]:
    pages = []
    for block in re.split(r"^=== ", _read("tutorial.md"), flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        downloads = tuple(re.findall(r"\[\[download:(\w+)\]\]", body))
        body = re.sub(r"\[\[download:\w+\]\]\n?", "", body).strip()
        pages.append(TutorialPage(title.strip(), body, downloads))
    return pages
