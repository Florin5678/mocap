# Markerless Mocap Desktop App — Project Spec (v0.3, 2026-11-12)

Working title: `mocap-pipeline-app` (rename freely).
Target repo: https://github.com/Florin5678/mocap — a **public fork** of `perfanalytics/pose2sim` (default branch `main`; upstream HEAD at time of writing: `d391b7b`, 2026-11-10).
Audience: Claude Code (builder) and the project owner (Florin).
Changes from v0.1: owner decisions applied (§0.1); facts re-verified against the **fork's source code**, which is newer than the PyPI README used for v0.1 (§3).

---

## 0. How to use this file (instructions to Claude Code)

1. Read the whole file before writing code. Work **milestone by milestone** (§12); do not start one until the previous one's exit criteria pass.
2. Tags:
   - **[VERIFIED]** = confirmed in this repo's source/config (file given) or in official docs.
   - **[VERIFY]** = plausible but unconfirmed. Confirm in Milestone 0 against the code in this repo and record the answer in `app/docs/upstream-notes.md`. Never guess upstream behaviour; if it can't be confirmed, design around it.
   - **[DECIDE]** = open design choice. Pick the recommended default, put it behind a setting if cheap, record it in `app/docs/decisions.md`.
3. Keep the **core headless**. Every capability must work from a CLI before the GUI exists; the GUI is a thin wrapper.
4. Every user-facing error says **what is wrong, where, and what to do next**, in plain language. The users are experimenters, not engineers.
5. Never delete user data without the safeguards in §8.4.
6. Commit small, with tests; pin dependency versions; write `app/docs/` as you go.
7. **Never commit participant data** (videos, consent records, session folders, outputs). Add them to `.gitignore` in the first commit.

### 0.1 Owner decisions (fixed — do not re-litigate)

| # | Decision |
|---|---|
| D1 | Use the repo `https://github.com/Florin5678/mocap`. It is a public fork and **stays public** (option A, §2). |
| D2 | The **session-setup dialog** must show a camera-synchronisation reminder, in addition to the tutorial page. |
| D3 | Each camera directory must contain **two different calibration videos: `intrinsic` and `extrinsic`**. If either is missing, the user can **manually pick the file from any other directory**, per camera and per role. |
| D4 | A **separate dialog for extrinsic reference points**: set X, Y, Z for ≥ 10 points (as in the settings file) and show what each axis means relative to the origin. |
| D5 | **One extrinsic calibration per camera per session.** No per-trial groups; if cameras are repositioned, run a new session. (Still one Pose2Sim project per trial, §5.2.) |
| D6 | `.mot` is derived from LSTM-augmented markers by default; label this clearly. *(BVH: v0.1 said "drop"; **revised** — this fork supports BVH natively, so it stays, unticked by default. §6.7. **Revised again 2026-11-14 (rc7): always made, no checkbox.**)* |
| D7 | Trial/file mapping: implement the best solution — tiered resolver, §6.2. |
| D8 | Raw-footage deletion is **optional**, but must exist (also after the fact, in case a participant requires it). |
| D9 | Replace the 4-digit participant ID with a **trial-specific identifier** (§8.1). **Each trial is a different participant**, so the identifier, height, consent and delete option are all **per trial**. Ask **only for height**; no mass. **English only.** The owner reviews the consent form for compliance afterwards. |
| D14 | If the delete option is selected, delete **both the raw footage and the overlay videos** (and the 2D keypoint JSONs). |
| D15 | The app opens with a **greeting dialog** with exactly two buttons: "How it works" (recommended for first use) and "Start session" (§6.0). |
| D16 | Target OS: Windows 10/11 + macOS. BVH export available, unticked by default *(revised 2026-11-14: always made)*. |
| D10 | QA reports + 3D preview. |
| D11 | Progress/resume; bootstrap installer; CPU-first. |
| D12 | **Research use only** — no commercial use. |
| D13 | Check overlap with any upstream GUI in M0. |

---

## 1. Goal, users, scope

**Goal.** One desktop application that takes multi-camera videos of a single person and produces validated 3D pose data and OpenSim kinematics — with calibration, consent/GDPR handling and export included — so an experimenter who has never heard of Pose2Sim or OpenSim goes from "I filmed my participant" to `.trc` / `.c3d` / `.mot` / `.osim` (/ `.bvh`) files in one place.

**Users.** Students and researchers (cognitive science, sport science, rehab) running small experiments with 2+ cameras (phones, action cams, webcams). Not programmers. Probably Windows/macOS laptops, mostly without a GPU.

**In scope (v1).** Single person; ≥ 2 cameras; 1–10 trials per session (**each trial a different participant**); tutorial + sync reminder + downloadable PDFs; input validation and trial mapping; intrinsic calibration from the `intrinsic` video; reference-points dialog + extrinsic calibration from the `extrinsic` video; full Pose2Sim run (pose → sync → association → triangulation → filtering → LSTM augmentation → OpenSim scaling + IK); export dialog; optional/after-the-fact raw-footage deletion; QA report.

**Out of scope (v1).** Live streaming / phone-as-camera; cloud or Google Drive input; multi-person; automatic trial cutting; animal models; kinetics; pose models other than `Body_with_feet` (HALPE_26); static-board and keypoint extrinsic methods; Danish/other UI languages; accounts and telemetry; commercial distribution.

---

## 2. Repository strategy

**Fact [VERIFIED]:** `Florin5678/mocap` is a **fork of `perfanalytics/pose2sim`** and is **public**. GitHub: "Public repository forks are public … You cannot change the visibility of a fork by itself"; a branch belongs to one repository, so a "private branch" does not exist here. Anything pushed to this repo is public. ([GitHub Docs: About forks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/about-forks))

**Decided (D1): keep the public fork, option A. Option B is kept for reference only:**
- **A. Keep the public fork.** Fine for BSD-3 research tooling with no personal data in it (see rule 7 in §0). Zero setup.
- **B. Private copy (recommended if the code or consent text should stay private).** Create a *new private, non-fork* repo (e.g. `mocap-private`), `git clone --bare` the fork and `git push --mirror` into it, keep `upstream` = `https://github.com/perfanalytics/pose2sim` for updates. The public fork can then be deleted or left as the upstream-contribution vehicle.
Claude Code: work as in A; commit nothing that must stay private.

**Layout inside the repo (keeps upstream merges clean):**
```
mocap/                      # fork root — upstream files, patch sparingly
  Pose2Sim/                 # upstream package (any change marked `# MOCAP-PATCH`, listed in app/docs/upstream-patches.md)
  pyproject.toml            # upstream's — untouched
  app/                      # NEW: our application
    pyproject.toml          # depends on the local fork (pip install -e ..) + app-only deps
    src/mocapapp/  tests/  docs/  samples/
  PROJECT.md                # this file
```
Upstream already depends on `opensim`, `rtmlib`, `c3d`, `bvhsdk`, `PySide6-essentials`, `caliscope`, `rtoml`, `av`, `openvino`, and requires Python ≥ 3.11 [VERIFIED, `pyproject.toml`]; OpenSim's PyPI package works for OpenSim ≥ 4.6 on Python 3.11–3.13 [VERIFIED, OpenSim docs].

**Licences.** Pose2Sim is BSD-3-Clause: keep `LICENSE` and notices. Use is **research only** (D12), so no commercial-licence review is needed now; if the app is ever distributed outside the research group or commercialised, re-check model weights/datasets, the LSTM model and the ffmpeg build. Cite (README request): Pagnon et al., JOSS 2022 (doi 10.21105/joss.04362), Sensors 2022 (10.3390/s22072712), Sensors 2021 (10.3390/s21196530) — show in About and write into every export manifest.

---

## 3. Verified upstream facts (design constraints)

Sources: the fork's code at `d391b7b` (paths relative to `Pose2Sim/`) and the PyPI README.

**Pipeline API** [`Pose2Sim.py`]: class `Pose2SimPipeline(config)` with methods `calibration, poseEstimation, synchronization, personAssociation, triangulation, filtering, markerAugmentation, kinematics`, and `runAll(do_calibration, do_poseEstimation, do_synchronization, do_personAssociation, do_triangulation, do_filtering, do_markerAugmentation, do_kinematics)` (all default True). `config` may be a dict, a `.toml` path or a directory. There is **no cancel/progress hook** in the pipeline API → run in a subprocess and parse `logs.txt` (§4).

**Project levels/layout** [`Pose2Sim.py: read_config_files`, `calibration.py:1722`, `personAssociation.py:602-613`, `kinematics.py:611`]: a trial folder merges its `Config.toml` over the parent's. The `calibration*` folder is searched in `session_dir` = the **parent** folder *if the parent contains a `Config.toml`*, otherwise the trial folder itself. ⇒ A trial folder with its own `Config.toml` and `calibration/`, under a parent **without** a `Config.toml`, is a self-contained project with its own calibration. **[VERIFY in M0]** with two such trials under one parent.

**Calibration** [`calibration.py`, `Demo_SinglePerson/Config.toml`]
- Intrinsics: `calibration/intrinsics/<cam>.<video>` (cam name = file stem) or `intrinsics/<cam>/` with images/video; frames extracted every `extract_every_N_sec` (default 1 s); `board_type` `'chess'|'charuco'`; `corners_nb` = **internal** corners `[rows-1, cols-1]`; `square_size` in **mm**; comment: ≥ 20 corners/markers, board may be as small as A4; ChArUco "expect 0.5 px error instead of 0.2" (chess). `show_detection_intrinsics` opens an interactive window.
- Extrinsics methods: `'scene'`, `'chess_static'`, `'charuco_static'` (single image); `'charuco'`, `'keypoints'` are *coming soon*. Input: `calibration/extrinsics/<cam>.<image|video>` (or in a `<cam>/` subfolder); **only the first frame of a video is used**. Result math: `cv2.solvePnP(objp*1000, imgp, K, D)` then `t/1000`.
- `scene`: `object_coords_3d` is a list of `[X,Y,Z]` in **metres** (unlike intrinsics in mm). Upstream requires **≥ 6 clicked points per camera** ("No points clicked (or fewer than 6)"), warns below 10; reprojection includes hidden points. Clicking uses an interactive matplotlib window; **previously saved `Image_points.json` is reloaded but then a Qt message box asks "Are you satisfied…?"** (modal) → upstream calibration cannot run unattended.
- **Axes [VERIFIED, `common.py: zup2yup`, `calibration.py: trc_write`]: the scene frame is Z-UP.** Scene `[X,Y,Z]` → TRC/OpenSim `[Y,Z,X]`, i.e. OpenSim X (forward) = scene Y, OpenSim Y (up) = scene Z, OpenSim Z (right) = scene X. (The comment in `Config.toml` says "becomes [Z,X,Y]" — it contradicts the code; trust the code and add a test, M0.)
- Output: `Calib_<method>.toml` (e.g. `Calib_scene.toml`) in the calibration folder; written by `calibration.toml_write(...)`. Camera `name` keys are used downstream (`triangulation.py:267`). Calibration quality targets (README): intrinsics < 0.5 px, extrinsics < 1 cm (≤ 2.5 cm possibly acceptable).

**Synchronisation** [`synchronization.py`, Config]: vertical keypoint speed correlation; `synchronization_gui = true` (default) opens a player; `display_sync_plots`, `save_sync_plots`, `keypoints_to_consider`, `approx_time_maxspeed`, `time_range_around_maxspeed`, `filter_cutoff = 6`. Needs ≥ 5–10 s of clear vertical movement or a clear vertical event (clap/flash cue).

**Pose estimation** [Config]: RTMPose via RTMLib; `pose_model='Body_with_feet'` (HALPE_26), `mode='balanced'` (also `lightweight|performance`), `device='auto'`, `backend='auto'`, `det_frequency=4`, `parallel_workers_pose='auto'`, `display_detection=true` (**must be false** headless/parallel), `save_video='to_video'`, **`blur_faces=true`** (faces blurred in *saved* overlay videos/images only — not in the raw footage), `average_likelihood_threshold=0.5`, `multi_person=false` (keeps lowest-reprojection-error person). Defaults `frame_rate='auto'`, `frame_range='auto'` (trims to low-reprojection-error frames; if cameras unsynchronised, uses the shortest camera's range).

**Triangulation** [Config]: `make_c3d = true` (`.c3d` next to `.trc`), `interp_if_gap_smaller_than=20`, `min_cameras_for_triangulation=2`.

**Filtering** [Config; `filtering.py:902`]: Hampel `reject_outliers=true`, then `type='butterworth'`, **6 Hz, order 4** (comment: 3–6 Hz walking/slow, 6–15 Hz running/fast, 15+ Hz impulsive); `display_figures=true` (**set false**); `make_c3d=true`. Output name: `<stem>_filt_<type>.trc`.

**Marker augmentation (LSTM)** [`markerAugmentation.py:182,207`; Config]: input = `.trc` files in `pose-3d/`; output `<stem>_LSTM.trc` (also `.c3d` if `make_c3d`). Requires hips, knees, ankles, heels, small/big toes; correct `participant_height`; no NaNs [README].

**Kinematics** [`kinematics.py:618,766-773`; Config]: `use_augmentation=true`, `use_simple_model=false`, `filter_ik=false`, **`make_bvh=true` — BVH is native**: written next to each `.mot` via `Utilities/osim_to_bvh.export_to_bvh`. `right_left_symmetry=true`, `default_height=1.7`, `large_hip_knee_angles=90`, `trimmed_extrema_percent=50`. `participant_height = 'auto'` or float (m) — used for augmentation; `participant_mass = 70.0` default — "only used for marker augmentation and scaling, **no impact on results unless you need to further compute forces**" → we don't collect mass (D9). Scaling uses the slowest frames of each trial, so no separate scaling trial is needed (changelog).

**Utilities available** [`Utilities/`]: `trc_to_c3d`, `c3d_to_trc`, `osim_to_bvh`, `trc_Zup_to_Yup`, `trc_plot`, `face_blurring`, `reproj_from_trc_calib`, `calib_from_checkerboard`, others.

**Stated limitations (surface them in the tutorial):** poor on upside-down acrobatics; stiff-spine/ball-joint-shoulder simple model; scaling less reliable for crouching/sitting; no top-down views; < 60 fps degrades fast movements; tripods may be detected as people; augmentation fails with NaNs.

**Interactive/blocking upstream behaviours the app must neutralise [VERIFIED]:** `show_detection_intrinsics`, `show_reprojection_error`, matplotlib `plt.show(block=True)` and Qt message boxes in `calibration.py`, `display_detection`, `synchronization_gui`, `display_sync_plots`, `display_figures`. Policy: the app **does its own calibration UI** (§6.3–6.5) and writes `Calib_scene.toml` using upstream's `toml_write` and the same `solvePnP` math; then calls the pipeline with `do_calibration=False` and every `display*`/`*_gui` flag false (sync GUI only in the explicit "Fix sync" fallback). Prefer calling upstream functions programmatically (e.g. `calibrate_intrinsics`) only if M0 shows they run without prompts with the display flags off.

---

## 4. Architecture (under `app/src/mocapapp/`)

```
core/                        # headless, no GUI imports
  session.py                 # Session model (pydantic), session.json load/save
  validate.py                # input validator + trial mapping (§6.2)
  media.py                   # ffprobe/ffmpeg: probe, CFR transcode, rotate, frame extract
  calibration/
    intrinsics.py            # frame extraction, board detection, calibrate, QA
    scene_points.py          # reference points, axis definition, validation (§6.4)
    extrinsics.py            # solvePnP from clicked points, error, mirror/handedness hints
    calibfile.py             # write Calib_scene.toml (via Pose2Sim.calibration.toml_write); round-trip tests
  assemble.py                # build one Pose2Sim project per trial (§5.2)
  config_gen.py              # write Config.toml (tomlkit/rtoml), enforce non-interactive overrides (§3)
  runner.py                  # run stages in a subprocess, checkpoints, progress, cancel, resume
  qa.py                      # QA metrics + HTML/JSON report
  export.py                  # export selection -> export/ + manifest.json
  privacy.py                 # trial codes, consent records, deletion (§8)
  pdfs.py                    # intrinsic-board PDF, consent form PDF (fillable)
  env.py                     # `doctor`
cli/                         # `mocapapp ...` (§10)
gui/                         # PySide6 wizard; calls core only
resources/                   # tutorial text/images, consent template (English), strings
```
Stack suggestion (replaceable; justify in `decisions.md`): Python 3.12, `uv`, PySide6, pydantic v2, tomlkit/rtoml, OpenCV, ffmpeg (static binary or `imageio-ffmpeg`), reportlab + pypdf (fillable PDFs), pytest(+pytest-qt), ruff.

**Process model.** Pose2Sim stages run in a **subprocess** (isolates crashes/GPU memory, enables cancel). Progress = stage markers + parsed `logs.txt`; if brittle, wrap each stage call in our own runner script that emits JSON progress events.

---

## 5. Data model and on-disk layout

### 5.1 Session folder
```
<output_root>/<STUDY>_<YYYYMMDD-HHMM>/       # one session; each trial = a different participant
  session.json   scene.json                  # scene.json = reference points + axis text (§6.4)
  inputs/cam01/ intrinsic.<ext>  extrinsic.<ext>  trial_<label>.<ext>   # working copies only
  calibration_work/                          # chosen extrinsic frames (PNG), clicked points, debug images, QA plots
  trials/                                    # MUST NOT contain a Config.toml (see 5.2)
    trial_<label>/                           # one Pose2Sim project per trial
      Config.toml
      calibration/Calib_scene.toml           # session's intrinsics + extrinsics (same in every trial)
      consent.json                           # this trial's participant (§8.3)
      videos/cam01.mp4 ...                   # normalised copies, camera names cam01.. [VERIFY mapping rule]
      pose/ pose-sync/ pose-associated/ pose-3d/ kinematics/ logs.txt   # [VERIFY folder names]
  qa/   export/   manifest.json
```
### 5.2 Per-trial projects (firm)
Per-trial calibration via the batch layout is not documented, but §3 shows the calibration folder is resolved per project when the parent has no `Config.toml`. So **assemble one self-contained project per trial** with its own `calibration/Calib_scene.toml`, holding the session's intrinsics + extrinsics (identical in every trial). The per-trial layout is kept because each trial is a different participant with its own height, consent and outputs. There is **one extrinsic calibration per session** (D5): cameras must not be moved during a session; to reposition, start a new session.

### 5.3 `session.json` (sketch)
```json
{
  "schema": 1, "app_version": "…", "pose2sim_commit": "…", "opensim_version": "…",
  "study_code": "PA26",
  "trials": [{"label": "0", "mapping_method": "digit", "code": "PA26-K7M2QX4", "height_m": 1.78,
     "consent": {"attested": true, "attested_at": "…", "raw_footage_storage": false, "template_version": "1"},
     "delete_raw": true}],
  "cameras": [{"name": "cam01", "source_dir": "…",
     "intrinsic": {"file": "…", "origin": "found|manual", "rms_px": 0.52, "status": "ok"},
     "extrinsic": {"file": "…", "origin": "found|manual"},
     "trial_files": {"0": "…"}}],
  "extrinsics": [{"camera": "cam01", "frame_index": 0, "n_points": 11, "error_cm": 0.7}],
  "settings": {"pose_mode": "balanced", "device": "auto", "filter": {"type": "butterworth", "cutoff_hz": 6, "order": 4},
               "use_augmentation": true},
  "run_state": {"stage_done": {}, "last_error": null}
}
```
All stages must be **idempotent and resumable** from `run_state`.

---

## 6. Functional requirements (mapped to the owner's pipeline)

### 6.0 Greeting dialog (D15)
The app opens with a small dialog showing **only two buttons**:
1. **How it works** (labelled "recommended for first-time use") → opens the step-by-step **tutorial** (content in §6.2). It ends with two buttons: **Back** (to the greeting dialog) and **Start session**.
2. **Start session** → goes straight to session setup (§6.2).
No other controls on this dialog (recent sessions, settings and Help live elsewhere, after a session starts).

### 6.1 Install & open (step 1)
- FR-1.1 First run does an environment check (`doctor`): Python/OpenSim/Pose2Sim imports, ffmpeg, GPU/backends, free disk. Failures show a fix-it message, never a stack trace.
- FR-1.2 First run offers to download model weights (progress, resume) so later runs work offline; state what/where.
- FR-1.3 CPU-only works out of the box; GPU used when available (`device='auto'`).
- AC: on clean Windows 11 and macOS (Apple Silicon), a non-technical user reaches the main window without a terminal (M6).

### 6.2 Session setup (step 2)

**Tutorial** (opened from the greeting dialog's "How it works", §6.0; step by step; ends with **Back** and **Start session**; also re-openable from Help): (1) pipeline overview; (2) camera/fps/quality — **60 fps, 1080p**, same fps on all cameras, fixed zoom/focus, stabilisation and lens-switching off, tripods; (3) limitations (**one person only**, no second person or tripod-as-person in frame, no top-down views, caveats for seated/crouching/upside-down movement; ≥ 3 cameras recommended, 2 minimum); (4) intrinsic vs extrinsic calibration in plain words, and **what the two videos must contain** (below); (5) **how to place and measure reference points** (floor tape crosses, laser/tape measure, ±5 mm, spread through the capture volume, some at different heights, Z is up); (6) synchronisation cue; (7) downloadable intrinsic-board PDF; (8) downloadable consent form.

**Required video content (shown in tutorial and next to the folder pickers):**
- `intrinsic` — the printed board moved slowly through the frame, close/far, tilted, covering the whole image; recorded at the **same resolution/zoom/focus/stabilisation** as the trials.
- `extrinsic` — recorded with the camera **in its final trial position**, showing the reference points (the physical markers measured in §6.4). Touching or bumping the camera afterwards invalidates it.

**Camera synchronisation reminder (D2) — in the setup dialog itself,** next to the camera directory list, always visible (not only in the tutorial): *"Before each trial: start all cameras, then give a clear sync cue visible/audible to all (clap with raised hands, or a flash), and include ≥ 5 s of clear up-and-down movement. Cameras that were not started with a cue cannot be synchronised reliably."* The pre-Run summary screen contains a **required tick-box**: "I recorded a sync cue at the start of every trial." (Failing sync is handled in §6.6, FR-4.4.)

**Intrinsic-board PDF (`pdfs.py`).** Generated from parameters (board type, squares, dictionary, square size) so it always matches `corners_nb`/`square_size`. Includes "print at 100 % / actual size", a **5 cm scale bar** to check with a ruler, and a flat-mounting tip. Upstream's own board PDFs (`Content/intrinsic_charuco_5_8_25.pdf` etc.) are reference only. Default board = ChArUco (owner's pipeline); setting to switch to chess (slightly more accurate per upstream, less occlusion-tolerant).

**Consent form.** Content in §8.2. The tutorial offers the generic form (session-code field blank, fillable); setup offers "Generate form" per trial (that trial's code pre-filled).

**Fields.** Study code (optional); number of trials (1–10); number of cameras (≥ 2, soft cap 16); per camera: a directory picker; and a **participant table with one row per trial** (each trial is a different participant): generated **trial code** (§8.1), **height** (metres; also accept cm and convert, show the converted value; range 1.00–2.30; blank falls back to `'auto'` with a "not recommended" warning), "signed consent received" attestation (§8.3), optional raw-footage-storage consent, and the **delete-raw-footage** option (below). No mass field.

**Directory rules** (all unit-tested):
1. **Calibration videos (D3).** In each camera directory look for exactly one video whose stem is `intrinsic` and exactly one whose stem is `extrinsic` (case-insensitive, any supported extension). If one is **missing or ambiguous** (e.g. two `intrinsic.*`), it is **not a hard error**: show a **"Choose file…"** control for that camera and role; the file may come from **any directory**; it goes through the same checks (decodable, probe) and is recorded as `origin: manual`. Both roles must be resolved for every camera before Run. The two roles must be **different files** (error if same path or same hash); the same file may not serve two cameras.
2. Stems `intrinsic`/`extrinsic` never count as trials. A name like `intrinsic2.mp4` is *not* an intrinsic video (it ends in a digit) → treated as a trial file candidate with an explanatory warning.
3. The remaining videos in the directory must number exactly `n_trials`. Ignore hidden/non-video files (info message). Containers: mp4, mov, avi, mkv (decode-tested with ffprobe); reject zero-byte/corrupt files.
4. **Trial mapping (D7) — tiered resolver.**
   - **Tier 1 – trailing digit:** the final character of the file stem is the trial digit. Accepted only if it yields exactly one file per digit **per camera** and the **same digit set across all cameras**.
   - **Tier 2 – chronological order** (when Tier 1 is ambiguous/fails for any camera, e.g. `GX010012` vs `GX010022`): sort that camera's trial videos by container `creation_time` → file mtime → natural filename order; trial *i* = *i*-th file. **Cross-check** against the other cameras: the *i*-th videos' durations must agree within a tolerance (default ±3 s or ±10 %); flag mismatches (likely a missing/extra file).
   - **Tier 3 – manual table:** a cameras × trials grid with dropdowns, always **shown for confirmation whenever Tier ≠ 1 or any cross-check warning exists**, with low-confidence cells highlighted. The mapping and method are stored in `session.json` and shown on the pre-Run summary.
   - Trial labels: the digit for Tier 1, otherwise `1…N`. Never silently guess.
5. Directories must be distinct and not nested; warn if inside a cloud-synced folder (OneDrive/Dropbox/Google Drive/iCloud) — relevant to deletion (§8.4).
6. Probe every file (resolution, fps, **variable frame rate** via `r_frame_rate` vs `avg_frame_rate`, duration, rotation metadata, codec). Warnings (overridable): fps < 60, resolution < 1080p, fps differs between cameras or trials, duration differs across cameras by > 2 s, fewer than 3 cameras. Errors: unreadable files; fps mismatch that cannot be reconciled.
7. **Normalise** into the working folders: copy (or hard-link if already constant-frame-rate, correctly oriented H.264), otherwise transcode to constant frame rate with rotation applied. Never modify originals. Show a disk-space estimate first.

**Delete raw footage — optional (D8).** A checkbox per trial/participant in the participant table; when selected, **both the raw footage and the overlay videos are deleted** (D14, §8.4). Its **default** follows the consent attestation: participant consented to raw-footage storage → unticked; did not consent → ticked. The experimenter can change it either way. If consent for storage was *not* given and the box is unticked, show a non-blocking warning ("the form promises deletion"). Independently, deletion is **always available later** (session page, CLI) — see §8.4.

**Run** is enabled only when validation passes and the pre-Run summary is confirmed (trial codes, cameras ↔ folders, trial mapping table, warnings acknowledged, sync tick-box, consent attestation).

### 6.3 Intrinsic calibration (step 3.1) — per camera
- FR-3.1 Uses the camera's `intrinsic` video. Extract frames (default every 1 s, configurable), keep frames where the board is detected with enough corners; show **coverage** (image-area heat-map, tilt/scale diversity) and **RMS reprojection error**.
- FR-3.2 Thresholds [DECIDE with real data in M2]: pass ≤ 0.6 px for ChArUco (upstream expects ≈ 0.5) and ≤ 0.5 px for chess; warn up to 1.0 px (continue allowed); fail above, or fewer than 20 usable frames. On failure give concrete advice (closer/farther, tilt, cover whole frame, avoid blur) and allow picking another video.
- FR-3.3 Intrinsics are per camera and survive repositioning; warn that changing zoom, resolution, focus mode, stabilisation or lens between calibration and trials invalidates them.
- FR-3.4 Save to `calibration_work/intrinsics_<cam>.json`; reuse on resume (hash-checked).
- FR-3.5 Board params (`corners_nb` = internal corners, `square_size` mm) come from one shared setting that also drives the PDF.

### 6.4 Extrinsic reference-points dialog (D4) — once per session, before the per-camera step
A **separate dialog** that defines the physical points and the world axes. The points are shared by all cameras; there is one extrinsic calibration per session (D5).

**Table:** columns *Label · X (m) · Y (m) · Z (m)* — the same values and units as `object_coords_3d` in the settings file. Add/remove/reorder rows, **≥ 10 rows required**. Also: **paste** a `object_coords_3d = [[x,y,z],…]` snippet, **import/export CSV**, and **"Show as Config.toml"** (shown, and written into each trial's `Config.toml` under `[calibration.calculate.extrinsics.scene]` for provenance).

**World axes panel (what each axis means relative to the origin).** Pose2Sim's scene frame is **right-handed and Z-up [VERIFIED]**:
- **Origin (0,0,0)** — required text: "The origin is at ____" (e.g. "floor corner by the door"). Recommend placing the origin on the floor and giving it a point row labelled `origin`.
- **+Z = UP**, away from the floor — fixed, not editable. Floor should be Z = 0.
- **+X** — required text: "+X points from the origin toward ____ (horizontal)".
- **+Y** — **derived** (not free): shown as "Seen from above, +Y is 90° counter-clockwise from +X (right-hand rule). Standing at the origin facing +X with Z up, +Y is on your left." Optional text for the user's own description.
- Live **diagram** with origin, labelled arrows (X red, Y green, Z blue), plus a **top-down map (X–Y)** and **side view (X–Z)** of the entered points with their labels, so the user can compare the picture with the room.
- A one-line note: in the exported OpenSim/TRC frame these become X_osim = Y, Y_osim = Z (up), Z_osim = X [VERIFIED]; the subject's own heading in the room does not matter.

**Validation:** numeric, finite, metres (flag |value| > 20 m as probable cm/mm typo); unique labels; no duplicate coordinates; not collinear; warn if nearly coplanar ("add points at different heights") and if the spread is small (e.g. < 1 m in two dimensions) [DECIDE thresholds]; reminder that each camera must later see ≥ 6 (recommend ≥ 10) of them.
**Presets:** save/load named point sets ("Lab room A") in the app data folder (no personal data).
**Editing after use:** changing points after any extrinsic has been computed warns and invalidates the computed extrinsics.
**Measuring help:** collapsible guide with a worked example and the accuracy budget (points to ±5 mm; target extrinsic error < 1 cm).

### 6.5 Extrinsic calibration dialog (step 3.2) — per camera, once per session (D5)
1. **Source image.** The camera's `extrinsic` video (found or manually chosen, D3), with a **frame scrubber** (default = first frame, which is what upstream would use); the chosen frame is saved as PNG in `calibration_work/`.
2. **Click the points.** The list shows the reference points from §6.4. Select a point → click it in the image; zoom/pan, undo, "not visible" toggle. Require **≥ 6 clicked points** (upstream minimum [VERIFIED]); recommend ≥ 10 and spread over the frame. After 6 points show live reprojection of **all** points (hidden ones included) and the error.
3. **Compute** with `solvePnP(objp*1000, imgp, K, D)`, `t/1000` (as upstream). Report error in px and cm. Targets: **< 1 cm pass; 1–2.5 cm warn; > 2.5 cm blocked** unless explicitly overridden (override stored in `session.json` and the QA report).
4. **Diagnostics:** if the error is large, automatically test sign flips of each axis and, if one removes the error, show "your axes may be mirrored (left-handed): flip X/Y" rather than a generic failure; warn if the computed camera position is implausible (below the floor, Z ≤ 0).
5. **Applies to all trials of the session** — no per-trial choice. State plainly in the dialog and tutorial: cameras must not be moved during the session; if one is repositioned, start a new session.
6. **Review & confirm:** per-camera errors and thumbnails of each clicked setup.
7. Write each trial's `Calib_scene.toml` (§5.2) and a round-trip test against upstream's reader.

### 6.6 Run the rest of the pipeline (step 4)
Per trial, via the subprocess runner: pose estimation → synchronisation → person association → triangulation → filtering → marker augmentation → kinematics, calling the pipeline with `do_calibration=False`.

- FR-4.1 One-click Run. All human interaction (reference points, intrinsics review, extrinsics) is finished **before** the long compute so the user can leave the machine; the only later interaction is the "Fix sync" queue (FR-4.4), because sync needs 2D poses.
- FR-4.2 Enforced config (written by `config_gen.py`, user-visible in "Advanced"): `pose_model='Body_with_feet'` (since rc7 a choice: also `Whole_body_wrist`, `Whole_body`), `mode='balanced'`, `device='auto'`, `multi_person=false`, `display_detection=false`, `blur_faces=true`, `save_video='to_video'` (overlay videos for QA, faces blurred), `synchronization_gui=false`, `display_sync_plots=false`, `display_figures=false`, filter `butterworth` 6 Hz order 4 with a plain-language hint (6 Hz suits walking-type movement; fast movements need 6–15 Hz), `reject_outliers=true`, `use_augmentation=true`, `participant_height=<that trial's participant height>` (or `'auto'`), `participant_mass` left at upstream default, `make_c3d=true` (all stages), `make_bvh=true`.
- FR-4.3 Progress, ETA, cancel, resume per trial and stage; benchmark the first N frames for a time estimate; show "CPU run: expect ≈ X" when no GPU.
- FR-4.4 **Synchronisation:** run automatically; compute and display per-camera offsets with a confidence plot. If confidence is low or the offset implausible, queue the trial for a **"Fix sync"** step (upstream's `synchronization_gui` player, or our own) — never continue silently.
- FR-4.5 One failed trial never aborts others; list causes with fixes ("person not detected in cam02", "augmentation failed: NaNs remain").
- FR-4.6 **Kinematics source label (D6):** the `.mot/.osim/.bvh` come from LSTM-augmented markers by default (`use_augmentation=true`). Show this wherever those files are offered; Advanced switch: "compute kinematics from non-augmented markers".
- FR-4.7 Warn when the movement is seated/crouching/lying (self-declared checkbox) — scaling is less reliable (upstream); `right_left_symmetry` exposed (off for prostheses).
- FR-4.8 **QA report** per trial (`qa.py`): fraction of frames with ≥ 2 cameras per keypoint, mean reprojection error, interpolated-gap fraction, sync offsets, IK marker RMS/max, floor/axis sanity (feet near Z = 0 in scene frame / Y = 0 in output), warnings; traffic-light summary on the completion dialog; HTML + JSON. Plus a **3D skeleton preview** (animate the `.trc`) so a non-expert sees obviously wrong results before exporting.

### 6.7 Completion dialog & export (step 5)
Expand/collapse **Export**; (*) = ticked by default.

| Item | Source | Notes |
|---|---|---|
| Config.toml (*) | the effective per-trial `Config.toml` + session settings | includes defaults and versions |
| pose-3d → `.c3d`: raw / butterworth / butterworth LSTM (*) | `make_c3d` at triangulation / filtering / augmentation [VERIFIED] | pattern: raw `…trc`, `…_filt_butterworth.trc`, `…_filt_butterworth_LSTM.trc` [VERIFY exact stems] |
| pose-3d → `.trc`: raw / butterworth / butterworth LSTM (*) | `pose-3d/` | as above |
| kinematics → `.bvh` | **native** (`make_bvh`; `Utilities/osim_to_bvh`) [VERIFIED] | always made since rc7 (owner, 2026-11-14); label the source (FR-4.6); [VERIFY] with a Blender/Motionbuilder import test |
| kinematics → `.osim` (*) | scaled model per trial | |
| kinematics → `.mot` (*) | `kinematics/` | from augmented markers by default |

Also **Open containing folder** (`os.startfile` / `open` / `xdg-open`) and **Close** (disabled while export/deletion runs). Exports go to `export/` with self-explanatory names, e.g. `<CODE>_trial3_markers_butterworth-lstm.trc`, plus `README.txt` describing each file and `manifest.json` (app/Pose2Sim-commit/OpenSim versions, settings, file hashes, citation text, QA summary). The completion dialog also shows the deletion state/action (§8.4).

---

## 7. UX principles ("easy for non-experts")

- One window, a **linear wizard** with a step bar and non-destructive "back"; advanced settings folded away; sensible defaults.
- Each screen: a one-sentence purpose, a "why?" link into the tutorial, a worked example; plain words ("camera position calibration (extrinsic)").
- **English only**; strings still kept in `resources/` (not hard-coded) so a translation stays possible.
- Sessions can be closed and resumed; recent-sessions list.
- Everything the app does to files is shown before it happens (copy, transcode, delete).
- Keyboard navigation, readable fonts, never colour-only status.
- A bundled **sample dataset** (based on `Pose2Sim/Demo_SinglePerson`, if its licence permits) and a "Try with sample data" button to verify the install.

---

## 8. Privacy, consent and GDPR

> The owner will review the consent form and its compliance afterwards. Ship it labelled **TEMPLATE**, with every institution-specific field as an explicit placeholder. This is not legal advice.

### 8.1 Data inventory, minimisation, identifier
Personal data handled: **video of identifiable people**, trial code, height, derived 3D movement data (gait/body movement can be identifying), consent record. Not collected: **mass** (D9), names, emails, signatures.
- **Trial code = the "trial-specific identifier" (D9).** Generated **per trial** — each trial is a different participant — so a code is never reused across trials, sessions or studies: `<STUDY>-<6 random chars><check char>`, e.g. `PA26-K7M2QX4`. `STUDY` = optional experimenter-set label (A–Z/0–9, ≤ 8). Random part from an unambiguous alphabet (no 0/O/1/I/L), generated with `secrets`; the check character catches transcription errors; collisions are checked against existing session folders in the output root (cheap — no registry). The code is that participant's identifier and appears in that trial's file names (`<CODE>_trial<label>_…`). Within a session all codes share the study prefix.
  *Confirmed by the owner:* one code per trial/participant. A person who took part in several trials or sessions would get different codes; linking them (if ever needed) is the experimenter's job in their own key file.
- The code is a **pseudonym, not anonymisation** — the form and UI say "pseudonymised", never "anonymous".
- The signed form stays with the experimenter **separately** from the data; the code is the only link.

### 8.2 Consent form (default template, English, fillable PDF)
Sections: study title/purpose (placeholder); who is responsible, contact, DPO contact (placeholders); what is collected (video, height, derived 3D movement data); purpose/legal basis (consent); storage location, retention, who has access; pseudonymisation and the trial code; whether/when raw video is deleted; **risks** (privacy risk of video, physical effort/discomfort) and **benefits/rewards** (placeholder); voluntariness; **right to withdraw consent at any time without penalty** (state the cut-off after which pooled data can no longer be removed); rights (access, rectification, erasure, restriction, portability where applicable, complaint to the supervisory authority — in Denmark Datatilsynet); contacts. Fields: trial code (pre-filled when generated from setup), **height (m)**, checkbox "I have read and consent to …", **separate optional checkbox** "I consent to my raw video/photo footage being stored until data analysis is finished", name, date, signature. Adults only in v1 (state it).

### 8.3 What the app records
One `consent.json` **per trial** (in the trial folder): `{"code", "attested": bool, "attested_at", "raw_footage_storage": bool, "template_version"}`. The experimenter ticks "Signed consent received for code XXXX" for each trial; **Run is blocked until every trial is attested** (experimenter-attested; the app cannot verify).

### 8.4 Deleting raw footage — optional, always available (D8)
**Entry points:** the setup checkbox (§6.2), the completion dialog, the session page, `mocapapp delete-raw`, and "Delete all participant data" (withdrawal; removes **that trial's** participant data, or the whole session on request). Deletion always works **per trial/participant**. These work **at any later time**, not just during the run, in case a participant requires it.

**Safeguards (mandatory for every deletion):**
1. If triggered by a run: only after all selected stages succeeded **and** outputs exist, are non-empty and parse (`.trc`/`.mot` header check) **and** export finished.
2. Show an exact list with paths and sizes, grouped as (a) **working copies** (`inputs/`, `trials/*/videos/`), (b) **derived material that shows the person** — overlay videos/images (faces are blurred by `blur_faces`, but not reliably) and 2D keypoint JSONs — **always deleted together with (a)** when deletion is selected (D14), (c) **original source files** in the camera folders.
3. Group (c) needs a **separate confirmation: type that trial's code**. Perform a real delete, not Recycle Bin/Trash; document the limits of normal deletion (SSD/backups).
4. **Keep** (as specified by the owner): `intrinsic`/`extrinsic` videos, `.trc/.c3d/.mot/.osim/.bvh`, configs, QA. Warn if a calibration video shows a person.
5. After deleting, show a checklist the app cannot do: "Have you also removed the footage from the cameras/SD cards, phones, and any cloud backup/sync?"; warn up front when sources sit in a synced folder.
6. If storage consent **was** given and the user declines deletion: record `retention_until = "analysis finished"`, show **"Delete raw footage now"** on the session page and a reminder in the recent-sessions list. Never delete automatically.
7. Log every deletion (what, when, code — no personal data) in `manifest.json`.

---

## 9. Non-functional requirements

- **Performance:** pose estimation dominates (cameras × trials × frames at 60 fps). Provide time estimates, GPU/CPU detection, the `lightweight|balanced|performance` selector, parallel workers (`parallel_workers_pose='auto'` needs `display_detection=false`), resumability; never hold videos in memory.
- **Robustness:** every stage checkpointed; atomic writes; crash/power-loss-safe resume; originals never modified; low-disk pre-flight with margin.
- **Reproducibility:** `manifest.json` records app/Pose2Sim-commit/OpenSim/ONNX/Torch versions, effective config, input hashes.
- **Logging:** readable `logs.txt` plus a "Copy diagnostics" button that excludes personal data.
- **Security/privacy:** offline after setup (except optional model download); no telemetry; no network during processing.
- **Platforms:** Windows 10/11 (priority), macOS (Apple Silicon), Linux (CLI at least). `pathlib` throughout; test paths with spaces and non-ASCII characters (æ/ø/å) — upstream is historically fragile here [VERIFY].
- **Packaging [DECIDE after M0]:** install is large (README: up to ~14 GB with GPU extras) and includes OpenSim + ONNX/Torch; PyInstaller-style bundles tend to be fragile for this stack. Preferred: a thin installer that bootstraps a managed environment (uv or conda-constructor) on first run with progress, CPU default, optional GPU download; plus a CLI/Docker path for lab servers.

---

## 10. CLI surface (also the integration-test surface)

```
mocapapp doctor
mocapapp pdf board --out board.pdf [--type charuco|chess --cols .. --rows .. --square-mm ..]
mocapapp pdf consent --code PA26-K7M2QX4 --out consent.pdf
mocapapp session new --study PA26 --heights 1.78,1.65,... --cam cam01=<dir> [--intrinsic <file>] [--extrinsic <file>] ... --trials N --out <root>
mocapapp validate --session <dir>                    # also prints the trial mapping table
mocapapp scene set --session <dir> --csv points.csv  # reference points; `scene show|check`
mocapapp calibrate intrinsics --session <dir> [--camera cam01]
mocapapp calibrate extrinsics --session <dir> [--camera cam01]     # once per session
mocapapp run --session <dir> [--trial 3] [--resume]
mocapapp export --session <dir> --select config,trc.filt_lstm,c3d.filt_lstm,osim,mot[,bvh]
mocapapp delete-raw --session <dir> --trial 3 [--include-originals]   # raw + overlay videos + 2D JSON
mocapapp delete-session --session <dir>              # withdrawal
```

---

## 11. Testing

- **Golden test:** run the pipeline headless on the repo's `Pose2Sim/Demo_SinglePerson` data (and verify against its shipped calibration) — assert output files exist and key values (marker count, frame count, IK RMS) are within tolerance; CPU CI.
- **Unit tests:** validator rules in §6.2 using tiny ffmpeg-generated videos — including missing `intrinsic`/`extrinsic` → manual-pick flow, same file for both roles, `intrinsic2.mp4` handling; trial-mapping tiers incl. `GX010012`/`GX010022`, chronological fallback and cross-camera duration check; session-code generator (format, check char, collision); scene-point validation (≥ 10, duplicates, collinear, coplanar warning, unit-typo flag); mirrored-axes detection; `Calib_scene.toml` round-trip vs upstream's reader; config generation + non-interactive overrides (assert no GUI flag is true); deletion safeguards (dry-run and real on temp dirs); consent-attestation → default-delete binding.
- **Calibration tests:** synthetic ChArUco renders; synthetic scene with known 3D points and a known camera, error recovered within tolerance; Z-up→OpenSim axis mapping test (answers the Config-comment-vs-code discrepancy).
- **GUI smoke tests:** wizard navigation with `pytest-qt`; no logic in the GUI layer.
- **Usability test (M6):** 3 people who have never used OpenSim/Pose2Sim complete a session from raw videos using only the tutorial; record time-to-result and every confusion; fix before release.

---

## 12. Milestones and exit criteria

| # | Milestone | Exit criteria |
|---|---|---|
| **M0** | Repo layout, env, **upstream spike** | `app/` scaffold in the repo (§2); pinned env (Python 3.11–3.13); demo runs headless end-to-end; **every [VERIFY] answered** in `app/docs/upstream-notes.md` (per-trial project layout with its own calibration; camera-name↔video↔calibration mapping and ordering; `Calib_scene.toml` skip behaviour with `do_calibration=False`; Z-up→Y-up test; exact output stems incl. LSTM; progress parsing from logs; non-ASCII paths; whether an upstream GUI now exists); owner's decision on repo visibility (§2). |
| **M1** | Core session + validator + assembler | `session new`, `validate` (incl. D3 manual-pick and tiered trial mapping), per-trial project assembly + `Config.toml` generation with non-interactive overrides; CFR/rotation normalisation. |
| **M2** | Calibration | Intrinsics from video with QA; **reference-points model + validation**; extrinsics via clicked points with error/diagnostics; one extrinsic calibration per camera per session; `Calib_scene.toml` verified against upstream. |
| **M3** | Runner + QA + export | `run --resume`, progress/cancel, failure isolation, sync QA + Fix-sync, QA report, export selection + manifest, 3D preview, BVH import check. |
| **M4** | GUI wizard + greeting dialog + tutorial + PDFs | Greeting dialog (§6.0) and full flow of §6 in the GUI on sample data, incl. the sync reminder, reference-points dialog and extrinsic dialog; board PDF + consent PDF. |
| **M5** | Privacy features | Per-trial codes, per-trial consent attestation, optional/after-the-fact per-trial deletion with all safeguards, "Delete all participant data", retention reminders. |
| **M6** | Packaging + docs + usability test | Installer/bootstrapper for Windows + macOS; user guide; usability test passed; release candidate. |
| Later | Static-board extrinsics; scaling trial; many-participant batches; other languages; optional live/phone capture | — |

---

## 13. Remaining risks and open points

1. **Repo is a public fork** (accepted, D1) — never commit participant data or anything private.
2. **Upstream calibration is interactive and modal**, so the app owns calibration UI and bypasses upstream's calibration stage (§3). Risk: drift from upstream's math → mitigated by round-trip/golden tests.
3. **Axis convention:** scene frame is Z-up; the Config comment disagrees with the code — settle by test in M0; the §6.4 dialog text depends on it.
4. **Cameras must stay put for the whole session** (one extrinsic per camera, D5). A bump mid-session silently invalidates later trials; the tutorial and pre-Run summary must say so, and the QA report's reprojection error is the only safety net.
5. **Measuring reference points is the hardest part for novices** (needs a tape/laser, ±5 mm). The tutorial and dialog must carry most of the weight; usability-test it first.
6. **Sync is not automatic for unsynchronised phones** — depends on the clap/flash cue and enough vertical motion; Fix-sync fallback needed.
7. **Overlay videos** (faces blurred, body visible) are still personal data → included in deletion scope by default.
8. **Codes are per trial**, so a person appearing in several trials/sessions is not linkable by the app (§8.1).
9. **Compute time and install size** (CPU runs can take hours; up to ~14 GB with extras).
10. **Scaling** is less reliable for seated/crouching/lying trials and asymmetric bodies.

## 14. Decisions resolved (2026-11-12) — none open

1. Repo: public fork kept (D1).
2. Identifier: one per trial/participant (D9).
3. Overlay videos and 2D keypoint JSONs are deleted with the raw footage (D14).
4. Target OS: Windows 10/11 + macOS (D16).
5. BVH: available, unticked by default (D16). *(Revised 2026-11-14: always made.)*

## 15. References

- Pose2Sim fork source (this repo, commit `d391b7b`): `Pose2Sim/Pose2Sim.py`, `calibration.py`, `common.py`, `filtering.py`, `markerAugmentation.py`, `kinematics.py`, `Demo_SinglePerson/Config.toml`, `Utilities/`.
- Pose2Sim README (PyPI): https://pypi.org/project/pose2sim/
- OpenSim — Scripting in Python: https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53085346/Scripting+in+Python
- GitHub Docs — About forks: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/about-forks

---

## 16. Planned: project-based layout (v0.2, agreed 2026-11-25, reviewed 2026-11-25)

**Status:** plan only; nothing built yet. It replaces the one-way wizard (Session → Trials → Intrinsic → Extrinsic → Run → Results → Viewer) with a hub of five sections around a trials table. Once it lands it supersedes **D5** (already relaxed in 0.1.0rc17), **D9** (already relaxed: participants can have several trials), §5.1–5.3 and the step numbering of §6. Everything else in this spec stays.

**Aims.** (1) Scale: set up cameras, reference points and participants once and reuse them. (2) Speed: a new trial is one pre-filled row. (3) **Keep every current function**; only reorganise (§16.7 maps each one).

### 16.1 Owner decisions (R = revamp; fixed — do not re-litigate)

| # | Decision |
|---|---|
| R1 | The app works per **project**. A project is a study, not a single day: it can cover several days of filming and processing. Projects are saved to and opened from project files (today's `.mocapproj` + folder). |
| R2 | **Participants exist only inside a project**; their data is saved with the project. No app-wide participant list. |
| R3 | **Cameras, reference-point presets and layouts** are stored in the project. *Import from another project ...* copies any of them from another project's file. |
| R4 | Sections: **Session, Participants, Cameras, Layouts, Results.** Run is a button on Session, not a section. |
| R5 | **Cameras:** a name (default `camNN`) and optional fields: resolution, zoom, focus, stabilisation, frame rate (for organisation). Saved cameras can be **duplicated, edited and deleted**. Intrinsic calibration works as on today's Intrinsic calibration page; the result is stored with the camera and used by every trial whose layout includes it. |
| R6 | **No intrinsic recalibration of a camera that is already used** (see §16.2 for "used"). To recalibrate, duplicate the camera and calibrate the copy. |
| R7 | **Layouts:** what today's Extrinsic calibration page does (reference points, click points, review image, errors). A layout says which cameras are used and where they stood, so **trials show only the layout**; the cameras follow from it. |
| R8 | **Warn when a trial reuses a layout** recorded on another day (including one imported from another project): "Were the cameras set up again? Record new extrinsic videos." The researcher can **override** it (*Use this layout anyway*); the override is recorded with the trial. |
| R9 | **Trials table** (Session): one row per trial; **trial numbers are fixed** (folders and results are named after them), with a free-text **name/note**; a **Status** column: *not ready (what is missing), ready, running, needs sync fix, done, out of date*. *Run this trial*, *Fix sync ...* and *Accept as is* stay in the row. Rows are **multi-selectable** so one action applies to several trials. A **Duplicate** action. Clicking a row opens a **trial panel** with its videos, checks and state. |
| R10 | **Add-trial row** under the table, pre-filled from the previous trial and editable before **Add**. Participant default: the previous trial's participant while they have fewer trials than *trials per participant*, otherwise a new participant (random pseudonym). The participant field is **highlighted**. |
| R11 | **Participants table:** random pseudonyms (editable, as now), mandatory **Height**, optional columns via *Edit columns ...* (weight, handedness, age, gender, custom), consent form buttons. Consent in **one drop-down column**: *Not signed yet / Signed, delete footage / Signed, keep footage*, same rules as today's three columns. |
| R12 | **Details stay stored per trial** (as now). Differences between days are recorded with custom columns. **No per-trial override UI.** |
| R13 | Raw-footage deletion at **trial level and project level**, working as it does now (§8.4). Session settings gain **Delete raw footage for all participants** (tick) with a drop-down for **when** (e.g. after the results are exported). |
| R14 | **App settings** (Help > Settings, this computer) and **Session settings** (this project) stay separate. |
| R15 | **Run progress per trial** in the Status column, plus a **bar at the bottom of the Session page** showing what the run is working on now. |
| R16 | **Results** combines today's Results and Viewer: trials on the left; tabs **Quality, 3D, Data, Export**. Finished trials appear while others still run. **No Finished!/Close button.** |
| R17 | **Keep the tutorial** (greeting "How it works"), rewritten for the new layout: calibration, the ChArUco board download, sync cue, reference points, etc. |
| R18 | **No sync-cue tick.** Only a reminder to record a sync cue (such as a clap) at the start of every video, shown in the tutorial and on the Session page next to the trials table (keeps D2). It never blocks a run. |
| R19 | **Start over (ignore finished stages)** stays a separate checkbox next to Run, as today, so it is hard to use by mistake. |
| R20 | **Freezing after deletion is per trial** (§16.2): a trial whose raw footage was deleted keeps its results but cannot change videos or re-run the stages that need them; other trials and new trials are unaffected. |

### 16.2 Data model

- **Project** = today's session folder + `session.json` (schema 2) + optional `.mocapproj` pointer. Holds settings, cameras, point presets, layouts, participants, trials, run state. The project folder stays self-contained (movable, reproducible); imports **copy** into it.
- **Camera** `{id, name, resolution?, zoom?, focus?, stabilisation?, fps?, intrinsic_video, intrinsics (result + QA), notes}`. **Used** = referenced by a layout of a trial that has started running (any finished stage). Used cameras: name and optional fields editable; intrinsic video and result locked (R6). Owner confirmed. Before use, recalibrating recomputes dependent layouts from their saved clicks (as today).
- **Reference-point preset** `{id, name, unit, points, origin text, +X text}`: today's `scene.json`, named. Importable (R3).
- **Layout** `{id, name, date, points (copied from a preset when created, editable), cameras: [{camera_id, extrinsic_video, clicks, frame, extrinsics + QA}]}`. Importable with its cameras (R3). **Date** = recording time of its extrinsic videos when readable, else the day it was made; editable.
- **Trial** `{label (fixed), name/note, date, participant, layout_id, videos per camera of the layout, details (per trial, R12), status, run state, raw_present, delete flag}`. A trial uses every camera of its layout. **Date** = recording time of its videos when readable, else the day added. R8 compares trial date with layout date; `layout_override` records an accepted warning.
- **Participant** as now (pseudonym, former pseudonyms, consent, delete choice, withdrawn). The consent drop-down maps onto today's fields: *Not signed yet* = not attested; *Signed, delete footage* = attested, no storage consent; *Signed, keep footage* = attested + storage consent.
- **Freezing after deletion becomes per trial.** Today, once raw footage of **any** trial is deleted the whole session is frozen: no re-checking, re-mapping, re-copying or adding trials ("start a new session"). In a project this would block all later work, so: a trial whose raw footage was deleted keeps its results but its videos can no longer be changed and stages that need the videos cannot re-run; every other trial, and new trials, are unaffected.
- **Calibration per trial:** each trial gets its own `Calib_scene.toml` from its layout (and the layout's cameras' intrinsics); 0.1.0rc17 already writes per-trial files.
- **Migration** of 0.1.x sessions on open: cameras → camera entries (each extra intrinsic video → a duplicated camera); `scene.json` → preset "Points 1"; for each distinct combination of extrinsic video (and intrinsic video) per camera used by the trials → one layout; trials point to their layout. Tested on fixtures of rc13–rc17 sessions.

### 16.3 Sections

**Session** (home)
- Trials table (R9) with columns: Trial, Name/note, Participant, Layout, Status, row buttons (Run this trial, Fix sync ..., Accept as is). Header fixed, scrolls.
- Add-trial row (R10) + **Add**. Videos for each layout camera are suggested by number (today's name rule) and confirmed in the trial panel.
- Actions on selected trials: Run, Duplicate, Change layout, Check and copy videos, Delete raw footage ..., Remove trial ..., Generate consent forms.
- Buttons: **Session settings ...**, **Run** (runs the selected trials; with none selected, every ready trial), and the **Start over (ignore finished stages)** checkbox next to it (R19).
- Sync reminder next to the trials table: record a sync cue such as a clap at the start of every video (R18; no tick).
- Bottom bar (R15): current stage and trial, overall progress, **Stop**, *Show Pose2Sim's own output*, time and disk estimate.
- Empty project: a short checklist (add cameras → calibrate → reference points → layout → participants → first trial) linking to each section; sidebar badges (✓ or "N to do").

**Trial panel** (opens on row click): participant, layout, date, name/note; video per camera (choose, suggested ones highlighted); *Check videos* results (fps, resolution vs the camera's calibration, length, sync); copy status and disk space; per-trial summary (today's *Before you run*); state and messages; layout-from-another-day warning with *Use this layout anyway* (R8).

**Participants**: R11 table, one row per participant (pseudonym, height, chosen columns, consent drop-down, consent form *Generate ...*); *Generate all consent forms ...*; *Edit columns ...*; issues (consent missing). A value entered here is written to all of that participant's trials (R12: stored per trial, as today's copy rule; no separate per-trial editor). Owner confirmed.

**Cameras**: table of saved cameras (name, optional fields, intrinsic video, result PASS/CHECK/FAIL, error, used by N trials); Add, Duplicate, Edit, Delete (refused while used), *Choose intrinsic video ...*, *Calibrate* / *Calibrate all*, board settings, advice; *Import from another project ...*.

**Layouts**: list of layouts (name, date, cameras, state); per layout: reference points (from a preset; *Edit reference points ...*, issues, *Save as preset ...*), camera rows (camera, extrinsic video, Click points ..., Review image, result, points, error, position, notes); New layout (pick cameras, preset), Duplicate layout (same cameras, new videos). Presets: list. Layouts and presets: *Import from another project ...*.

**Results**: trials on the left (traffic light); tabs **Quality** (metrics, QA report, 3D preview link), **3D** (viewer), **Data** (joint angles / markers, plot, compare trials, speed, CSV), **Export** (choices, Export, *Delete raw footage now*, deletion notices). Opens after Run; shows trials as they finish.

**Session settings**: output folder (*Change output folder ...*), study code, trials per participant, notes, pose detection, keypoints, OpenSim model, overlays, keep 2D files, smoothing, **Delete raw footage for all participants** + when (R13). **App settings** unchanged (R14).

### 16.4 Run readiness (per trial; owner confirmed)
A trial is **ready** when: its participant has signed consent and a height (optional columns never block); its layout exists and every layout camera has an intrinsic result and extrinsics that do not block; a video is chosen, checked and copied for every layout camera; a layout warning (R8) is resolved or overridden. *Not ready* lists what is missing. Run never waits for unrelated trials.

### 16.5 CLI (core and CLI first, §0.3)
`project new|open|info|import --from <project file> [--cameras] [--points] [--layouts]`; `camera add|list|edit|duplicate|delete|calibrate`; `points preset save|list|show|check`; `layout new|list|edit|duplicate|click|status`; `participant add|list|set|consent|form`; `trial add [--like N]|list|edit|duplicate|remove|videos|check|status`; `run [--trial ..]`, `export`, `delete-raw`, `delete-session` as now. 0.1.x commands keep working where they still make sense and otherwise say which command replaces them.

### 16.6 Phases (branch `app/projects`; 0.1.x stays the release line for fixes)

| Phase | Content | Exit criteria |
|---|---|---|
| P1 | Core model (§16.2): schema 2, cameras, presets, layouts, trials→layout, per-trial freeze, readiness (§16.4), per-trial calibration files, migration of 0.1.x | Unit tests for model, readiness, R6 lock, R8 warning, migration of rc13–rc17 fixtures; golden pipeline test passes from a migrated session |
| P2 | CLI (§16.5), import from another project | Full session from raw videos by CLI only; import copies cameras, presets and layouts (with their videos) and no participant data |
| P3 | GUI shell: five sections, sidebar badges, Session settings, Cameras and Layouts sections | Calibrate cameras and a layout in the GUI on sample data |
| P4 | Participants section (consent drop-down) | Consent/deletion rules tests as today (§8) |
| P5 | Session hub: table, add row + pre-fill, multi-select actions, duplicate, trial panel, status, run per trial, bottom bar | Full wizard test rewritten on the hub; no loss vs §16.7 |
| P6 | Results (+ viewer), tutorial, user guide, privacy notes, decisions, screenshots | Usability check of a new project with 2 days of trials; release **0.2.0rc1** |

### 16.7 Where every current function goes

| Today | New home |
|---|---|
| Session step: output folder, move, study code, trials per participant, notes | Session settings |
| Session step: camera folders | Cameras (folder per camera for suggestions) and trial panel |
| Trials: participant ID, height, columns, consent ticks, consent forms, issues | Participants |
| Trials: add/remove trial | Session table (add row, actions) |
| Trials: Check videos, mapping, name suggestions, copy + disk space | Trial panel; multi-select *Check and copy videos* |
| Trials: calibration videos and per-trial assignment dialog | Cameras (intrinsic) and Layouts (extrinsic); trial → layout |
| Intrinsic calibration page | Cameras |
| Extrinsic calibration page (reference points + camera positions) | Layouts (+ presets) |
| Run: summary, sync reminder (tick removed, R18), advanced settings, Run all/selected/this trial, Stop, Start over checkbox, Fix sync, Accept as is, progress, Pose2Sim output, disk check | Session (table, actions, bottom bar), trial panel, Session settings |
| Results: traffic lights, metrics, 3D preview, export, Delete raw footage now | Results: Quality, Export |
| Viewer: 3D, data, compare, speed, CSV | Results: 3D, Data |
| Finished! (save, reminders, back to start) | Dropped (R16): saving is automatic; reminders shown on Session and in recent projects |
| File menu (participant data and deletion, recent, projects, sample data, move), Help (tutorial, board PDF, environment check, models, settings) | Unchanged; "session" wording becomes "project" |
