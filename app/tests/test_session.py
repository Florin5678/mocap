import pytest

from mocapapp.core.session import Participant, Session, Trial, load_session, parse_height, save_session


@pytest.mark.parametrize("raw,expected", [("1.78", 1.78), ("178", 1.78), ("178 cm", 1.78), ("1,65", 1.65),
                                          ("", None), ("auto", None), (None, None)])
def test_parse_height(raw, expected):
    assert parse_height(raw) == expected


@pytest.mark.parametrize("raw", ["0.5", "250", "abc"])
def test_parse_height_rejects(raw):
    with pytest.raises(ValueError):
        parse_height(raw)


def test_round_trip(tmp_path):
    s = Session(n_trials=1, participants=[Participant(label="1", code="ABCDEFG", height_m="180")],
                trials=[Trial(label="1", participant="1")], cameras=[])
    save_session(s, tmp_path)
    loaded = load_session(tmp_path)
    assert loaded.trials[0].height_m == 1.8 and loaded.schema_ == 1
    assert '"schema": 1' in (tmp_path / "session.json").read_text()


def test_existing_codes_scans_sessions(tmp_path):
    from mocapapp.core.session import existing_codes

    s = Session.model_validate({"n_trials": 1, "trials": [{"label": "1", "code": "PA26-ABCDEFG"}], "cameras": []})
    save_session(s, tmp_path / "PA26_20261112-1000")
    assert existing_codes(tmp_path) == {"PA26-ABCDEFG"}
