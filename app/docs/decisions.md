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
| 2026-11-12 | Video probing | PyAV (already a Pose2Sim dependency) instead of ffprobe: `average_rate` for fps, first decoded frame's `rotation` for the display matrix, packet-timestamp spread (>2 % of intervals off the median) for variable frame rate. | The bundled `imageio-ffmpeg` has no ffprobe; PyAV reads the same metadata without another binary. |
| 2026-11-12 | Normalisation | Hard-link when the source is already constant-rate, upright H.264 in .mp4 (copy if on another drive); otherwise transcode with libx264 CRF 17 `-preset veryfast`, constant frame rate, rotation applied, audio dropped. If trial fps differ by > 0.5, all videos are converted to the most common rate. | Originals are never modified; quality loss of CRF 17 is negligible for 2D pose; audio is not needed. |
| 2026-11-12 | Trial-code check character | Weighted sum mod 31 (alphabet size, prime), weights 1..7. | Catches every single-character typo and every swap of neighbours; Luhn mod N only guarantees that for even N. |
| 2026-11-12 | Trial labels and heights | Labels come from the mapping (the digit for tier 1, else 1..N). Heights and codes are attached to trials in label order; `session new --heights` takes them in that order. | Keeps the participant table and the file mapping in one order the experimenter can check. |
| 2026-11-12 | Extra CLI commands | `mocapapp pick` (choose a calibration video from any folder, D3), `mocapapp map` (manual trial mapping, tier 3), `mocapapp assemble [--dry-run]` (normalise + build trial projects). | Every GUI action needs a CLI equivalent (PROJECT.md §0 rule 3). |
| 2026-11-12 | Thresholds | fps warning below 59 (accepts 59.94); resolution warning when the short side < 1080; cross-camera duration warning > 2 s; tier-2 duration cross-check max(3 s, 10 %). | As specified; 59 so NTSC-style 59.94 fps is not flagged. |
