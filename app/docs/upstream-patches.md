# Changes to upstream files

Every change outside `app/` is marked `MOCAP-PATCH` in the file and listed here, so upstream merges stay easy.

| File | Change | Why |
|---|---|---|
| `.gitignore` | Appended block ignoring participant data (videos, session/consent/manifest JSON, `inputs/`, `calibration_work/`, `export/`, `qa/`), re-including `Pose2Sim/Demo_*/**/*.mp4`. | Public fork; rule 7. |

No changes to `Pose2Sim/` so far.
