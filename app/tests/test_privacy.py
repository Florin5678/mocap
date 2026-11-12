import pytest

from mocapapp.core.privacy import ALPHABET, check_char, is_valid_code, make_code, normalise_study


def test_code_format_and_check_char():
    code = make_code("pa26")
    study, body = code.split("-")
    assert study == "PA26" and len(body) == 7
    assert set(body) <= set(ALPHABET)
    assert is_valid_code(code)


def test_single_typo_is_caught():
    body = make_code()
    for i in range(len(body)):
        for ch in ALPHABET:
            if ch != body[i]:
                assert not is_valid_code(body[:i] + ch + body[i + 1:])


def test_no_ambiguous_characters():
    assert not set("01OIL") & set(ALPHABET)


def test_collision_avoided(monkeypatch):
    import mocapapp.core.privacy as privacy

    seq = iter("222222" + "333333")  # first draw repeats the existing code
    monkeypatch.setattr(privacy.secrets, "choice", lambda _: next(seq))
    first = "222222" + check_char("222222")
    assert make_code(existing=[first]) == "333333" + check_char("333333")


def test_bad_study_label():
    with pytest.raises(ValueError):
        normalise_study("too long label")


def test_neighbour_swap_is_caught():
    for _ in range(50):
        code = make_code()
        for i in range(len(code) - 1):
            if code[i] != code[i + 1]:
                swapped = code[:i] + code[i + 1] + code[i] + code[i + 2:]
                assert not is_valid_code(swapped)
