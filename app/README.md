# mocapapp

Desktop app around Pose2Sim: multi-camera videos of one person → validated 3D pose (`.trc`/`.c3d`) and OpenSim kinematics (`.osim`/`.mot`/`.bvh`). **Research use only.** Spec: [`../PROJECT.md`](../PROJECT.md).

Status: Milestone 1 (session setup, validation, trial mapping, video normalisation, per-trial project assembly). Calibration (M2) and the runner (M3) are next. Upstream findings: [`docs/upstream-notes.md`](docs/upstream-notes.md).

## Command line (so far)

```
mocapapp doctor
mocapapp session new --study PA26 --trials 2 --heights 178,1.65 --cam cam01=<folder> --cam cam02=<folder> --out <root>
mocapapp validate --session <root>/<session>          # checks inputs, prints the trial mapping table
mocapapp pick --session <dir> --camera cam02 --role extrinsic --file <any video>
mocapapp map --session <dir> --set cam01:1=<file> --set cam02:1=<file> ...
mocapapp assemble --session <dir> [--dry-run]          # convert videos, build trials/trial_<label>/
```

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
