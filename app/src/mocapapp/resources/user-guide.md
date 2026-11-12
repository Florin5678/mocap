# mocapapp user guide

mocapapp turns videos of one person, filmed with two or more cameras at the same time, into 3D movement data and joint angles. It runs Pose2Sim and OpenSim for you. **Research use only.**

This guide covers installing, using and troubleshooting the app. For how to film, see **Help > How it works**: it explains cameras, calibration videos, reference points and the sync cue, step by step.

---

## 1. Install

You need:

- Windows 10/11, or macOS 15 (Sequoia) or newer;
- about **10 GB** free disk space for the app, plus space for your videos;
- an internet connection during installation only. Afterwards, the app works offline.

**Windows:** run `mocapapp-setup.exe` and follow the steps. If Windows warns that it protected your PC, choose **More info > Run anyway** (the installer is not code-signed). The setup window shows progress while it downloads Python, the app and the pose models; this takes 10–30 minutes. When it finishes, start **mocapapp** from the Start menu or the desktop shortcut.

**macOS:** unzip `mocapapp-macos.zip`, then double-click `Install mocapapp.command`. If macOS says the file is from an unidentified developer, right-click it, choose **Open**, then **Open** again. A window shows progress. When it finishes, **mocapapp** is in your *Applications* folder (your own, inside your home folder).

To uninstall:

- **Windows:** *Settings > Apps > mocapapp > Uninstall*.
- **macOS:** run `Uninstall mocapapp.command` from the same zip.

Your session folders are **never** removed by uninstalling.

## 2. First start

- **Greeting window.** The app opens with two buttons. Choose **How it works** for the tutorial (recommended the first time), or **Start session**.
- **Environment check.** If something is wrong with the installation, the app tells you what to do before anything else. You can repeat the check with **Help > Environment check**.
- **Pose models.** If the models were not downloaded during installation, the app offers to download them once (about 145 MB). You can also do this later with **Help > Download pose models**.
- **Sample data.** **File > Try with sample data** creates a ready-made session from a demo recording, so you can try *Run* and *Results* before you film anything. A run on a laptop takes about 10 minutes.

## 3. A session, step by step

A **session** is one recording day with fixed cameras. Each **trial** is a different participant. Use up to 10 trials per session. If a camera is moved, start a new session.

The steps are listed on the left. A step becomes available when the previous ones are done; **Back** never loses work. Close the app at any time: **File > Recent sessions** reopens a session where you left off.

### Step 1 – Session

1. Choose an **output folder** whose path contains only plain letters (no æ/ø/å or other accents); the 3D software cannot save elsewhere.
2. Optionally enter a **study code** (up to 8 letters or digits, e.g. `PA26`). It starts every trial code.
3. Enter the number of **trials** and add one **folder per camera**. Each folder holds the camera's `intrinsic` and `extrinsic` videos and one video per trial.
4. Enter each participant's **height**, in metres or centimetres. Without it, the height is estimated, which is less accurate.
5. Press **Create session**. Every trial gets a **trial code**, such as `PA26-K7M2QX4`.
6. Use **Generate ...** to print a consent form with that code filled in.
7. When you hold a signed form, tick **Signed consent received** for that trial.
8. Tick **Consented to storing raw footage** only if the participant agreed to it. **Delete raw footage** then follows automatically; you can change it.

Keep the signed forms separate from the data: the code is the only link between a person and their data.

### Step 2 – Videos

Press **Check videos**. The app reads every file and:

- finds the calibration videos;
- matches the trial videos across cameras;
- checks frame rate, resolution and length.

Problems are listed with what to do. In particular:

- If a calibration video is missing or named differently, use **Choose intrinsic/extrinsic ...**; the file may be in any folder.
- If the app matched trial videos by recording time instead of by name, check the **mapping table** and correct it if needed.

Then press **Copy videos into the session**. The app copies the videos, converting them if needed; your original files are never changed. The app shows how much space this needs before it starts.

### Step 3 – Lens calibration

Press **Calibrate all cameras**. Each camera gets a result:

- **Pass:** fine.
- **Check:** works, but could be better. Read the advice shown.
- **Fail:** must be redone, usually with a new `intrinsic` video in which the board covers the whole picture, including the corners.

### Step 4 – Reference points

Enter the measured points (at least 10, in metres, Z up), import them from a CSV file, or paste a Config.toml snippet. Then describe the **origin** and the **+X direction** in words. The app checks the points:

- duplicates;
- points that all lie on a line;
- values that look like centimetres typed as metres;
- too few points above the floor.

### Step 5 – Camera positions

For each camera, open the picture from its `extrinsic` video and **click every reference point you can see**, in the order shown. Skip points that are hidden. The app then computes the camera's position and shows the error in centimetres:

- **below 1 cm:** good;
- **above 2.5 cm:** look for a wrongly clicked point (the app shows how many pixels each point is off) or a wrong measurement.

If the app says points are *behind the camera*, an axis is probably flipped. It suggests which one.

### Step 6 – Run

The summary shows every trial, camera and setting. Tick **I recorded a sync cue at the start of every trial**, then press **Run**.

- **Time:** the estimate is shown before you start. On a laptop without a graphics card, expect several minutes per camera and minute of video.
- **Stop:** you can stop at any time and press **Run** again later to resume; finished stages are kept.
- **Failures:** if one trial fails, the others continue. The reason is shown in plain words, with the full log under `trials/trial_<label>/mocap_logs/`.
- **Synchronisation:** if the app is unsure that the cameras are lined up for a trial, that trial stops. Choose **Check sync ...** to inspect it in a player, or **Accept as is**.

**Advanced** settings (pose detection speed and accuracy, smoothing, BVH) are folded away. The defaults suit most studies.

### Step 7 – Results

Each trial gets a **traffic light**: **Good**, **Check** or **Problem**. The metrics behind it:

| Metric | Means | Good |
|---|---|---|
| Sync confidence | how well the cameras were lined up | ≥ 0.6 |
| 3D reprojection error | how well the cameras agree on each point | ≤ 20 mm |
| Frames without 2+ cameras | how often a point had to be filled in | ≤ 5 % |
| Model fit (IK RMS) | how well the body model follows the points | ≤ 30 mm |
| Feet height above floor | should be near 0 if the floor is Z = 0 | ≤ 5 cm |

Open the **3D preview** and watch for limbs that jump or swap sides.

Under **Export**, tick what you need and press **Export**. Files go to the session's `export/` folder, named `<trial code>_trial<label>_<content>`, together with:

- `README.txt`, which describes every file;
- `manifest.json`, which records versions, settings and file checksums.

## 4. Participant data and deletion

**File > Participant data and deletion** is available at any time, as are the buttons on the Session and Results steps. For each participant you can:

- **Delete raw footage**: removes the copies of their videos, the overlay videos and the 2D keypoint files. The 3D results stay. Optionally, it also deletes the original recordings in the camera folders; you must type the trial code to confirm.
- **Delete all data of this participant** (for example, when they withdraw consent): removes their footage, results and exported files. You must type the trial code to confirm.

You can also delete the **whole session**; you must type the session folder name to confirm.

Every deletion first shows the exact list of files and sizes. Files are deleted permanently, not moved to the Recycle Bin. Afterwards, the app reminds you of copies it cannot reach:

- SD cards and phones;
- cloud backups;
- external drives.

When *Delete raw footage* was ticked in step 1, the Results step offers **Delete raw footage now** once the results are complete and exported. Footage kept with consent is never deleted automatically. The recent-sessions list marks sessions whose footage is still waiting to be deleted.

Details: the project's `app/docs/privacy.md`.

## 5. Troubleshooting

| Message or symptom | What to do |
|---|---|
| "Session path contains non-ASCII letters" | Move the session to a folder whose path has only plain letters and digits. |
| "No body keypoints were found in at least one camera" | Check that camera's video: is the person fully visible, and is the light sufficient? |
| A trial stops with "needs sync fix" | Use **Check sync ...**. Next time, give a clear clap or jump at the start, visible to every camera. |
| Large reprojection error | A camera may have moved after its `extrinsic` video, or a reference point was clicked or measured wrongly. Redo step 5 for that camera. |
| Feet far above or below the floor | The reference points' heights or the floor (Z = 0) are off. Check the measurements. |
| Head below the feet | Z points down in your reference points: Z must point up. |
| "Raw footage was deleted in this session" | After a deletion, the session's videos can no longer be changed. Start a new session for new recordings. |
| Anything else | **Help > Copy diagnostics** copies version and setup information (without your name or any participant data); paste it into your bug report. |

## 6. Command line (optional)

Every step also works without the window, for scripting and lab servers. Run `mocapapp --help`, or see the project's `app/README.md`.

## 7. Citing

If you publish results, cite Pose2Sim; **Help > About** shows the reference. mocapapp is for research use only.
