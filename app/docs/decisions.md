# Decisions

Design choices made while building (PROJECT.md tag [DECIDE]). Owner decisions D1–D16 live in PROJECT.md §0.1 and are not repeated here.

| Date | Topic | Decision | Why |
|---|---|---|---|
| 2026-11-12 | Python / env | Python 3.12, `uv`, app in `app/` with its own `pyproject.toml`; Pose2Sim installed editable from the fork root (`[tool.uv.sources] pose2sim = { path = "..", editable = true }`); `uv.lock` committed. | Inside OpenSim's supported range (3.11–3.13); one lock file pins everything; upstream files stay untouched. |
| 2026-11-12 | Build backend | hatchling for `mocapapp`. | Simple src-layout packaging; upstream keeps setuptools. |
| 2026-11-12 | How stages are run | One subprocess per stage, `cwd` = trial folder, `Pose2SimPipeline()` called **without** a config argument. | Upstream resolves `session_dir` from the *current directory* when given a path (see upstream-notes §1); running inside the trial folder is the only form that always picks the trial's own `calibration/`. Subprocess also gives cancel and crash isolation. |
| 2026-11-12 | Data protection in git | Root `.gitignore` gets a `MOCAP-PATCH` block ignoring videos, session/consent/manifest files and session sub-folders (demo videos re-included). | Rule 7: the fork is public. |
| 2026-11-12 | Session output path | Must be ASCII-only (spaces allowed); validator error otherwise. Camera source folders may be anything. | OpenSim cannot write `.osim` under non-ASCII paths (upstream-notes §8). |
| 2026-11-12 | Subprocess environment | Every stage runs with `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `MPLBACKEND=Agg`. | Upstream prints non-cp1252 characters (upstream-notes §9) and must never open figure windows. |
| 2026-11-12 | ffmpeg | `imageio-ffmpeg` (bundled static binary) as fallback when ffmpeg is not on PATH. | Non-experts should not have to install ffmpeg by hand. |
