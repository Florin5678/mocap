"""Trial codes (pseudonyms, one per trial/participant). Consent records and deletion arrive in Milestone 5."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable

# No 0/O, 1/I/L: codes are copied by hand onto paper consent forms.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
RANDOM_LEN = 6
STUDY_RE = re.compile(r"^[A-Z0-9]{1,8}$")
CODE_RE = re.compile(rf"^(?:([A-Z0-9]{{1,8}})-)?([{ALPHABET}]{{{RANDOM_LEN + 1}}})$")


def check_char(body: str) -> str:
    """Weighted check character mod 31 (prime): catches every single typo and every swap of neighbours.

    Chosen so that sum((i + 1) * value_i) over body + check is 0 mod 31.
    """
    n = len(ALPHABET)  # 31
    total = sum((i + 1) * ALPHABET.index(ch) for i, ch in enumerate(body))
    weight = len(body) + 1
    return ALPHABET[(-total * pow(weight, -1, n)) % n]


def normalise_study(study: str | None) -> str:
    study = (study or "").strip().upper()
    if study and not STUDY_RE.match(study):
        raise ValueError(f"Study code '{study}' must be 1-8 letters or digits (A-Z, 0-9), no spaces.")
    return study


def make_code(study: str | None = None, existing: Iterable[str] = ()) -> str:
    """New trial code such as 'PA26-K7M2QX4' (or 'K7M2QX4' without a study label), unique against `existing`."""
    study = normalise_study(study)
    taken = {c.upper() for c in existing}
    while True:
        body = "".join(secrets.choice(ALPHABET) for _ in range(RANDOM_LEN))
        code = body + check_char(body)
        code = f"{study}-{code}" if study else code
        if code not in taken:
            return code


def is_valid_code(code: str) -> bool:
    m = CODE_RE.match(code.strip().upper())
    if not m:
        return False
    rand = m.group(2)
    return check_char(rand[:-1]) == rand[-1]
