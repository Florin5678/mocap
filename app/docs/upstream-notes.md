# Upstream notes (Milestone 0)

Answers to every [VERIFY] in PROJECT.md, checked against the fork at `d391b7b` (installed editable, reported as `0.10.49.dev44+gd391b7b05`) and by running the demo headless with `app/scripts/m0_spike.py` on 2026-11-12. Environment: Windows 11, Python 3.12.14, OpenSim 4.6, OpenCV 5.0.0, CPU only (16 threads).

## 1. Per-trial project with its own calibration — CONFIRMED, with one condition

- Every stage resolves the calibration folder the same way (`poseEstimation.py:776`, `personAssociation.py:602`, `triangulation.py:257`, `kinematics.py:611`): `session_dir` = parent folder **if it contains a `Config.toml`**, else the trial folder. Then `calib_dir` = the **first** sub-folder whose name contains `calib`, and the calibration file = the **newest** `*.toml` in it.
- So `trials/trial_X/{Config.toml, calibration/}` under a `trials/` folder **without** `Config.toml` is self-contained. Verified: the demo ran in `…/trials/trial_1/` with `trials/` having no config.
- **Condition:** `Pose2SimPipeline.__init__` sets `session_dir` from the *current working directory* when it is given a path (`Pose2Sim.py:215-222`), not from the path itself. The runner therefore starts each stage as a subprocess with `cwd = trial folder` and calls `Pose2SimPipeline()` with no argument (what the spike does).
- Consequences for the assembler:
  - Inside a trial folder, only one folder name may contain `calib` (`calibration/`). Never put `calibration_work/` inside a trial.
  - Exactly one `.toml` in `trials/X/calibration/` (`Calib_scene.toml`), otherwise "newest file wins".

## 2. Camera name ↔ video ↔ calibration mapping — BY POSITION, not by name

- Pose folders (`pose/camNN_json`) are ordered with `sort_stringlist_by_last_number` (last number in the name); videos for pose estimation with `natural_sort_key`.
- Calibration cameras are read in **file order** (`common.retrieve_calib_params`, `triangulation.py:264-268`), the `name` key is only used for logging and excluded-camera reports.
- ⇒ The app writes `videos/cam01.mp4 … camNN.mp4` and `Calib_scene.toml` sections in the same `cam01 … camNN` order. Pinned by `tests/test_upstream_contract.py`.
- Calibration section names must not contain dots: `[cam01.mp4]` is parsed by TOML as a nested table and breaks `recap_calibrate` (`KeyError: 'matrix'`). Seen in the owner's pilot when intrinsics videos sat directly in `intrinsics/` (camera name = file name with extension). The app always writes plain `camNN`.

## 3. `do_calibration=False` / skipping calibration — CONFIRMED

Stages after calibration never recompute it; they just load the newest `.toml` in the calibration folder (above). Running the stages individually (the runner's model) is equivalent to `runAll(do_calibration=False)`.

## 4. Axes — code wins over the Config comment

`common.zup2yup`: `[X, Y, Z]_scene → [Y, Z, X]`, i.e. OpenSim X = scene Y, OpenSim Y (up) = scene Z, OpenSim Z = scene X. The `Config.toml` comment ("becomes [Z,X,Y]") is wrong. Pinned by `test_scene_z_up_maps_to_opensim_y_up`.

## 5. Exact output names (demo, trial folder `trial_1`, frames 1–97)

```
pose/camNN_json/                      2D keypoints per camera (+ camNN_pose.mp4 overlays when save_video='to_video')
pose-sync/camNN_json/ sync_cam01_vs_camNN.png
pose-associated/camNN_json/
pose-3d/trial_1_1-97.trc / .c3d                               raw triangulation
pose-3d/trial_1_1-97_filt_butterworth.trc / .c3d              filtered
pose-3d/trial_1_1-97_filt_butterworth_LSTM.trc / .c3d         filtered + LSTM augmented
pose-3d/filtering_plots_ik/
kinematics/trial_1_1-97_filt_butterworth_LSTM.osim / .mot / .bvh
kinematics/_ik_marker_errors.sto   kinematics/opensim_logs.txt   opensim.log   logs.txt
```

Stem = `<trial folder name>_<first>-<last frame>`; it is not known before triangulation, so the exporter must glob (`*_filt_butterworth_LSTM.trc`, etc.), not construct names.

## 6. Demo results and timings (CPU, `mode='balanced'`, 4 cameras × ~100 frames)

| Stage | Time | Result |
|---|---|---|
| calibration (convert Qualisys) | 36 s | — |
| poseEstimation | 71 s | ~1.9 frames/s per video with 4 videos in parallel |
| synchronization (no GUI) | 183 s | offsets 0 / −2 / −1 frames, correlation 0.86 / 0.69 / 0.66 |
| personAssociation | 12 s | |
| triangulation | 15 s | mean reprojection 10.4 px ≈ 18.9 mm |
| filtering | 23 s | |
| markerAugmentation | 10 s | |
| kinematics (scaling + IK + BVH) | ~60 s | IK marker RMS mean 22.5 mm, max 28.8 mm |

Each stage pays ~10–30 s of import/start-up in its own subprocess. Acceptable; revisit if trials are short.

## 7. Progress parsing

No callback API. Usable signals:
- `logs.txt` in the trial folder (rotating handler, appended per stage) with fixed headers: `Pose estimation took …`, `Triangulation took …`, `--> Mean reprojection error for all points on frames A to B is X px, which roughly corresponds to Y mm.`, `--> Camera cam01 and camNN: K frames offset, correlation C.`
- tqdm bars on stderr during pose estimation (`Processing camNN.mp4: 37%|…| 37/100`).
- The subprocess exit code is the reliable stage result. **But kinematics catches scaling errors per file, logs `Error during scaling …` and may still exit 0/1 inconsistently** → the runner must also check that the expected outputs exist.

Plan: runner emits its own JSON events (stage start/end, exit code, output check) and forwards the parsed lines above; tqdm percentages give pose-estimation progress.

## 8. Non-ASCII paths — BROKEN in OpenSim

- Everything up to marker augmentation works in `…\sessiøn spike\trials\trial_1`.
- **Kinematics fails**: OpenSim (SimTK `Xml::writeToFile`) cannot write `…sessiøn…\kinematics\*.osim` (`Failed to write to the Xml file … (line=1, col=1)`). The same trial copied to an ASCII path (`…\session spike\…`, spaces are fine) completes kinematics.
- Decision: the session output root must be ASCII-only (validator error with a plain explanation). Camera source folders may contain any characters, since their videos are copied into the session.

## 9. Windows console encoding

`Utilities/osim_to_bvh.py:420` prints `✓`; with the default cp1252 console this raises `UnicodeEncodeError` **after** the BVH is written, and the stage exits with code 1. The runner must set `PYTHONIOENCODING=utf-8` (and `PYTHONUTF8=1`) for every subprocess. With that set (as in the spike) the stage succeeds.

## 10. Interactive behaviour with flags off

Headless run used: `pose.display_detection=false`, `synchronization.synchronization_gui=false`, `display_sync_plots=false`, `filtering.display_figures=false`, `MPLBACKEND=Agg`. Nothing blocked. Calibration was only run in `convert` mode; `calculate` mode still has `plt.show(block=True)` (`calibration.py:1013, 1557`) and Qt message boxes, so the app keeps its own calibration UI as planned (§3 of PROJECT.md).

## 11. ChArUco and OpenCV 5

OpenCV 5.0 defaults to the new ChArUco layout. calib.io boards with an even number of rows use the legacy layout. This fork already retries detection with `setLegacyPattern(True)` (`calibration.py:1118-1124`), so the owner's `calibrate.py` shim is not needed here. The app's own intrinsics code must do the same (try both layouts) and the generated board PDF should use the layout the detector tries first.

## 12. Upstream GUI (D13)

None. `QApplication` appears only in `common.py` and plotting utilities (`trc_plot.py`, `trc_mot_filter.py`) for figure windows and message boxes. No overlap with the planned wizard.

## 13. Install size

`app/.venv` (CPU, Windows) ≈ 1.3 GB. With the owner's D: drive at ~10 GB free after install, the doctor's 10 GB free-space check will warn; real 60 fps sessions need far more.

## 14. Extrinsics: no check that points are in front of the camera (found in M2)

`calibrate_extrinsics` ('scene') accepts any `solvePnP` solution. With reference points measured in a mirrored (left-handed) frame, `solvePnP` returns a perfect fit (reprojection ≈ 0 px) with the camera turned around and the points *behind* it, because projection cannot distinguish a point from its reflection through the camera centre. Upstream would report an excellent calibration and triangulate garbage. The app rejects solutions with points at depth ≤ 0 (decisions.md).

## 15. Intrinsics on the owner's pilot videos (M2 check)

Same videos, upstream vs. app (`every 0.5 s`): cam01 0.29 px from 22/28 frames, coverage 50 % → warn; cam03 9/22 usable frames → fail; cam04 board found in 1/24 frames → error. Upstream had used 4 and 6 partial detections for cam03/cam04 and produced distortion values of -0.8 to -0.9.
