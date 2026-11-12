import pytest

from mocapapp.core.media import MediaError, needs_transcode, normalise, probe


def test_probe_plain(tmp_path, video):
    info = probe(video(tmp_path / "a.mp4", seconds=1, fps=60))
    assert info.fps == 60 and not info.vfr and info.rotation == 0
    assert abs(info.duration_s - 1) < 0.1 and not needs_transcode(info)


def test_probe_rotation_and_vfr(tmp_path, video):
    assert probe(video(tmp_path / "r.mp4", rotation=90)).rotation in (90, 270)
    assert probe(video(tmp_path / "v.mp4", seconds=2, vfr=True)).vfr


@pytest.mark.parametrize("name,content", [("empty.mp4", b""), ("junk.mp4", b"not a video" * 100)])
def test_probe_rejects_empty_and_garbage(tmp_path, name, content):
    (tmp_path / name).write_bytes(content)
    with pytest.raises(MediaError, match=name):
        probe(tmp_path / name)


def test_normalise_rotated_vfr(tmp_path, video):
    src = video(tmp_path / "src.mov", seconds=1, size="64x48", rotation=90, vfr=True)
    before = src.read_bytes()
    how = normalise(src, tmp_path / "out" / "dst.mp4", target_fps=30)
    out = probe(tmp_path / "out" / "dst.mp4")
    assert how == "transcode" and out.rotation == 0 and not out.vfr
    assert out.fps == 30 and (out.width, out.height) == (48, 64)
    assert src.read_bytes() == before  # original untouched


def test_normalise_links_clean_video(tmp_path, video):
    src = video(tmp_path / "ok.mp4")
    assert normalise(src, tmp_path / "o" / "ok.mp4") in ("hardlink", "copy")
