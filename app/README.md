# mocapapp

Desktop app around Pose2Sim: multi-camera videos of one person → validated 3D pose (`.trc`/`.c3d`) and OpenSim kinematics (`.osim`/`.mot`/`.bvh`). **Research use only.** Spec: [`../PROJECT.md`](../PROJECT.md).

Status: **0.1.0rc9, Milestone 6 (release candidate)**: installers for Windows and macOS, pose-model pre-download, first-start checks, user guide (Help > User guide, [`src/mocapapp/resources/user-guide.md`](src/mocapapp/resources/user-guide.md)) and the usability-test protocol ([`docs/usability-test.md`](docs/usability-test.md)) on top of M0-M5: the headless core (session setup, validation, calibration, the Pose2Sim runner, QA, export), the app window, and the privacy features ([`docs/privacy.md`](docs/privacy.md)). Still open for the release: the usability test with three new users. Upstream findings: [`docs/upstream-notes.md`](docs/upstream-notes.md).

## Install (users)

Windows: run `mocapapp-setup.exe`; macOS (15 or newer): unzip `mocapapp-macos.zip` and double-click `Install mocapapp.command`. Both download Python, the locked dependencies and the pose models (about 10-30 minutes, ~10 GB), then work offline. How the installers work and how to make a release: [`installer/README.md`](installer/README.md).

## The app window

```
uv run mocapapp gui                       # greeting dialog -> tutorial or a new session
uv run mocapapp gui --session <dir>       # reopen a session
```

Steps: Session -> Videos -> Lens calibration -> Reference points -> Camera positions -> Run -> Results. **File > Try with sample data** builds a ready-to-run session from upstream's demo (its calibration is imported, so the calibration steps are skipped). **File > Participant data and deletion** (also on the Session and Results steps) deletes raw footage or all data of a participant at any time; every deletion lists the exact files first.

## Command line

```
mocapapp doctor
mocapapp pdf board --out board.pdf [--type charuco|chess --cols 9 --rows 6 --square-mm 25]
mocapapp pdf consent --out consent.pdf [--code PA26-K7M2QX4]
mocapapp sample --out <root>                           # ready-to-run session from the demo videos
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
mocapapp delete-raw --session <dir> --trial 1 [--include-originals] [--after-run]     # lists only; add --yes [--confirm CODE]
mocapapp delete-session --session <dir> [--trial 1] [--include-originals]           # withdrawal / whole session; --yes --confirm
mocapapp reminders --root <output root>                    # sessions whose raw footage is still stored
mocapapp models [--download] [--mode balanced|all]       # pose models, needed once; then offline
mocapapp diagnostics                                       # versions and checks for bug reports (no personal data)
mocapapp --version
```

Stage output is logged to `trials/trial_<label>/mocap_logs/<stage>.log`. The slow end-to-end tests on upstream's demo (CLI and GUI, ~4 min each): `uv run pytest -m slow`. GUI tests run offscreen; `MOCAP_SCREENSHOTS=<folder>` saves a PNG of every wizard step.

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
src/mocapapp/gui/    PySide6 wizard (calls core only)
src/mocapapp/resources/  tutorial.md, strings.toml, consent_template.md (English texts, editable)
docs/                decisions.md, upstream-notes.md, upstream-patches.md
scripts/             one-off and spike scripts
```

Never commit participant data (videos, consent records, session folders, outputs). The root `.gitignore` blocks the usual file types; check `git status` before every commit anyway.

## Citation

Pose2Sim is BSD-3-Clause. Cite Pagnon et al., JOSS 2022 (doi:10.21105/joss.04362); Sensors 2022 (doi:10.3390/s22072712); Sensors 2021 (doi:10.3390/s21196530).
