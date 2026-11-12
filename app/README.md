# mocapapp

Desktop app around Pose2Sim: multi-camera videos of one person → validated 3D pose (`.trc`/`.c3d`) and OpenSim kinematics (`.osim`/`.mot`/`.bvh`). **Research use only.** Spec: [`../PROJECT.md`](../PROJECT.md).

Status: Milestone 3 (session setup, validation and trial mapping, video normalisation, lens and camera-position calibration, per-trial project assembly, the Pose2Sim runner with resume/cancel and sync checks, QA report with 3D preview, export with manifest). The GUI wizard (M4) is next. Upstream findings: [`docs/upstream-notes.md`](docs/upstream-notes.md).

## Command line (so far)

```
mocapapp doctor
mocapapp session new --study PA26 --trials 2 --heights 178,1.65 --cam cam01=<folder> --cam cam02=<folder> --out <root>
mocapapp validate --session <root>/<session>          # checks inputs, prints the trial mapping table
mocapapp pick --session <dir> --camera cam02 --role extrinsic --file <any video>
mocapapp map --session <dir> --set cam01:1=<file> --set cam02:1=<file> ...
mocapapp prepare --session <dir> [--dry-run]           # convert/copy videos into the session
mocapapp calibrate intrinsics --session <dir> [--camera cam01]
mocapapp scene set --session <dir> --csv points.csv --origin "tape cross by the door" --x-axis "the window"
mocapapp scene check|show --session <dir>
mocapapp calibrate frame --session <dir> --camera cam01          # PNG to read pixel positions from
mocapapp calibrate extrinsics --session <dir> --camera cam01 --points clicks.csv   # label,u,v or hidden
mocapapp calibrate status --session <dir>
mocapapp assemble --session <dir>                       # build trials/trial_<label>/ with Config.toml + calibration
mocapapp confirm --session <dir> --sync-cue                # a sync cue was recorded in every trial
mocapapp consent --session <dir> --trial 1 --attest [--raw-storage yes|no]
mocapapp run --session <dir> [--trial 1] [--restart] [--accept-sync] [--verbose]   # resumes by default; Ctrl+C stops
mocapapp run --session <dir> --trial 1 --fix-sync          # open upstream's sync player, then continue
mocapapp qa --session <dir>                                # qa/qa_report.html, qa/qa.json, qa/trial_<label>_preview.html
mocapapp export --session <dir> [--select config,trc.filt_lstm,c3d.filt_lstm,osim,mot,bvh]
```

Stage output is logged to `trials/trial_<label>/mocap_logs/<stage>.log`. The slow end-to-end test on upstream's demo: `uv run pytest -m slow`.

Each camera folder holds `intrinsic.<ext>`, `extrinsic.<ext>` and one video per trial. The output root must be an ASCII-only path.

## Setup (developers)

Needs [uv](https://docs.astral.sh/uv/). From this folder:

```
uv sync
uv run mocapapp doctor
uv run pytest
```

Pose2Sim is installed editable from the fork root (`..`), so upstream fixes merged into the fork are picked up without reinstalling.

Run the upstream demo headless in the per-trial layout (takes ~7 min on CPU; the output path must be ASCII-only):

```
uv run python scripts/m0_spike.py <empty output folder>
```

## Layout

```
src/mocapapp/core/   headless logic (no GUI imports)
src/mocapapp/cli/    `mocapapp ...` commands
src/mocapapp/gui/    PySide6 wizard (Milestone 4)
docs/                decisions.md, upstream-notes.md, upstream-patches.md
scripts/             one-off and spike scripts
```

Never commit participant data (videos, consent records, session folders, outputs). The root `.gitignore` blocks the usual file types; check `git status` before every commit anyway.

## Citation

Pose2Sim is BSD-3-Clause. Cite Pagnon et al., JOSS 2022 (doi:10.21105/joss.04362); Sensors 2022 (doi:10.3390/s22072712); Sensors 2021 (doi:10.3390/s21196530).
