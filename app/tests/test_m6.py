"""M6: model pre-download, diagnostics without personal data, version, installer scripts."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from mocapapp import __version__
from mocapapp.cli.main import main
from mocapapp.core import diagnostics, models

INSTALLER = Path(__file__).parents[1] / "installer"


@pytest.fixture
def fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "cache_dir", lambda: tmp_path / "checkpoints")
    (tmp_path / "checkpoints").mkdir()
    return tmp_path / "checkpoints"


def test_models_missing_and_download(fake_cache, monkeypatch, capsys):
    need = models.models(("balanced",))
    assert [m.role for m in need] == ["person detector", "pose"]
    assert all("halpe26" in m.url or "yolox" in m.url for m in need)
    assert len(models.missing()) == 2
    assert main(["models"]) == 1 and "--download" in capsys.readouterr().out

    calls = []

    def fake_download(url, dst, progress=False):  # what rtmlib leaves behind: the .onnx (+ zip, tmp)
        calls.append(url)
        stem = Path(url).name.split(".")[0]
        (Path(dst) / f"{stem}.onnx").write_bytes(b"onnx")
        (Path(dst) / f"{stem}.zip").write_bytes(b"zip")
        (Path(dst) / "tmp").mkdir(exist_ok=True)
        return str(Path(dst) / f"{stem}.onnx")

    import rtmlib.tools.file

    monkeypatch.setattr(rtmlib.tools.file, "download_checkpoint", fake_download)
    assert main(["models", "--download"]) == 0
    assert len(calls) == 2 and not models.missing()
    assert not list(fake_cache.glob("*.zip")) and not (fake_cache / "tmp").exists()
    assert main(["models", "--download"]) == 0 and len(calls) == 2  # nothing downloaded twice

    def offline(url, dst, progress=False):
        raise ConnectionError("no network")

    monkeypatch.setattr(rtmlib.tools.file, "download_checkpoint", offline)
    with pytest.raises(OSError, match="internet connection"):
        models.download(("lightweight",), progress=lambda m: None)
    with pytest.raises(ValueError):
        models.models(("fast",))


def test_doctor_reports_missing_models(fake_cache):
    from mocapapp.core.env import _models_check

    check = _models_check()
    assert not check.ok and "mocapapp models --download" in check.fix


def test_diagnostics_hide_home_and_user(monkeypatch):
    home = str(Path.home())
    monkeypatch.setattr(diagnostics.getpass, "getuser", lambda: "jdoe42")
    text = diagnostics._scrub(f"{home}\\x and {Path.home().as_posix()}/y by jdoe42")
    assert home not in text and "jdoe42" not in text and text.startswith("~")
    full = diagnostics.diagnostics_text()
    assert f"mocapapp {__version__}" in full and str(Path.home()) not in full


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"mocapapp {__version__}"
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", __version__)


def test_installer_scripts_are_consistent():
    ps1 = (INSTALLER / "windows" / "install.ps1").read_text(encoding="ascii")  # PowerShell 5.1 needs ASCII
    sh = (INSTALLER / "macos" / "Install mocapapp.command").read_text(encoding="utf-8")
    iss = (INSTALLER / "windows" / "mocapapp.iss").read_text(encoding="utf-8")
    uv_ps = re.search(r'\$UvVersion = "([\d.]+)"', ps1).group(1)
    uv_sh = re.search(r'UV_VERSION="([\d.]+)"', sh).group(1)
    assert uv_ps == uv_sh
    # a source zip has no .git, so Pose2Sim's setuptools-scm version must be given, the same on both
    p2s_ps = re.search(r'\$Pose2SimVersion = "([^"]+)"', ps1).group(1)
    p2s_sh = re.search(r'POSE2SIM_VERSION="([^"]+)"', sh).group(1)
    assert p2s_ps == p2s_sh and "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_POSE2SIM" in ps1 + sh
    for text in (ps1, sh, iss):
        assert f"mocapapp-v{__version__}" in text  # installers fetch the tag of this version
    assert re.search(r'#define AppVersion "' + re.escape(__version__) + '"', iss)
    assert "RedirectionGuard=no" in iss  # else uv's Python junction fails under setup.exe (os error 448)
    assert "--frozen" in ps1 and "--frozen" in sh  # always the locked versions
    assert len(re.findall(r"\b[0-9a-f]{64}\b", sh)) == 2 and len(re.findall(r"\b[0-9a-f]{64}\b", ps1)) == 1


@pytest.mark.skipif(sys.platform == "win32" and not Path("C:/Program Files/Git/bin/bash.exe").exists(),
                    reason="needs bash")
def test_macos_installer_syntax():
    bash = "C:/Program Files/Git/bin/bash.exe" if sys.platform == "win32" else "bash"
    for name in ("Install mocapapp.command", "Uninstall mocapapp.command"):
        assert subprocess.run([bash, "-n", str(INSTALLER / "macos" / name)], check=False).returncode == 0


def test_cli_video_steps_explain_imported_sessions(tmp_path, capsys):
    """The sample session keeps all cameras in one folder; 'validate' must not report that as an error."""
    from mocapapp.cli.main import main
    from mocapapp.core.session import save_session
    from tests.m3_fakes import make_session

    root = tmp_path / "S"
    session = make_session(root)
    session.calibration_source = "imported"
    save_session(session, root)
    for cmd in ("validate", "assemble"):
        assert main([cmd, "--session", str(root)]) == 0
        assert "Checking or copying videos is not needed" in capsys.readouterr().out
