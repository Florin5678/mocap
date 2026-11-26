"""'Copy diagnostics' (PROJECT.md §9): versions and environment checks without personal data.

The user's home folder and account name are replaced, so the text can be pasted into an issue or e-mail.
Nothing about sessions, participants or file names is included.
"""

from __future__ import annotations

import getpass
import platform
import sys
from pathlib import Path

from mocapapp import __version__


def _scrub(text: str) -> str:
    home = str(Path.home())
    for variant in {home, home.replace("\\", "/"), Path(home).as_posix()}:
        text = text.replace(variant, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3:
        text = text.replace(user, "<user>")
    return text


def diagnostics_text(projects: Path | None = None) -> str:
    from mocapapp.core.env import run_checks
    from mocapapp.core.export import _versions

    lines = [f"mocapapp {__version__}", f"Python {platform.python_version()} ({sys.executable})",
             f"Platform {platform.platform()}", "", "Versions:"]
    lines += [f"  {k}: {v}" for k, v in _versions().items()]
    lines += ["", "Checks:"]
    for c in run_checks(projects):
        lines.append(f"  [{'OK' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    return _scrub("\n".join(lines))
