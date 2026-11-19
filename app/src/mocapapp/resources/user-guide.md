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

**Project files.** Everything is saved automatically in the session folder. To keep a session as a file you can find and open again, use **File > Save project** (Ctrl+S) or **Save project as ...**. This writes a small `.mocapproj` file that points to the session folder and remembers the step you were on; closing the app updates that step. **File > Open project ...** (Ctrl+O) reopens it at that step; on Windows you can also double-click the project file. Keep the project file next to the session folder, or move both together: the project file does not contain the videos or results, only where they are. **File > Open session folder ...** still opens a session folder directly.

### Step 1 – Session

1. Choose an **output folder** whose path contains only plain letters (no æ/ø/å or other accents); the 3D software cannot save elsewhere. You can change it later: **Change output folder ...** in this step (or **File > Move session to another output folder ...**) moves the session folder, with everything in it, into another folder. On the same drive this takes a moment; to another drive the files are copied, the copy is checked, and only then is the old folder removed. If the old folder cannot be removed (a file in it is open), the app says so: delete it yourself, as it still holds participant data. An open project file is updated to the new place.
2. Optionally enter a **study code** (up to 8 letters or digits, e.g. `PA26`). It starts every pseudonym.
3. Add one **folder per camera**. Each folder holds the camera's `intrinsic` and `extrinsic` videos and one video per trial; before the recordings, the folders may still be empty. If the folders are named after the cameras (`cam01`, `cam02`, ...) and sit side by side, choosing one with **Browse ...** fills in the others.
4. Set **Trials per participant** (usually 1). A session holds at most 10 trials in total.
5. Add a row per participant with **Add participant** and enter each **height**, in metres or centimetres. Without it, the height is estimated, which is less accurate.
6. Press **Create session**. Every participant gets a **pseudonym**, such as `PA26-K7M2QX4`. You can still add a participant later, or remove one who has no videos yet (select the row, then **Remove participant**). The pseudonym is random; you can type another one (letters, digits, `-` or `_`, never a real name) or click the arrow next to it for a new random one, until you tick **Signed consent received** (`mocapapp session pseudonym --participant N --set P01` or `--random`).
7. Press **Generate ...** in a participant's row for their **participation consent form**. The first time, a window asks for the study details: study name, purpose, researchers, data handling, risks and compensation. They are printed on the form as normal text; a field left blank gets a line to write on. The form is meant to be printed; it has no fields to fill in on screen. The details are kept for the next forms of the session; only the pseudonym changes. **Generate all consent forms ...** makes one PDF with every participant's form, ready to print.
8. When you hold a signed form, tick **Signed consent received** for that participant.
9. Tick **Consented to storing raw footage** only if the participant agreed to it. **Delete raw footage** then follows automatically; you can change it.
10. Under **Participant details asked per trial**, tick what you want to record for every trial: height (ticked by default), weight, handedness, age, sex, or your own details with **Add detail ...** (a number with a unit, a choice such as *left / right*, or a short text). Never ask for names or other details that identify the person. The ticked details become columns of the trial table in step 2. A custom detail can be removed again; built-in ones are unticked instead (values already entered are kept).

A typical day: create the session and print the forms before the recordings, have the participants sign them, record, then continue with step 2.

Keep the signed forms separate from the data: the pseudonym is the only link between a person and their data.

### Step 2 – Videos

Press **Check videos**. The app reads every file and:

- finds the calibration videos;
- matches the trial videos across cameras;
- checks frame rate, resolution and length.

Problems are listed with what to do. In particular:

- If a calibration video is missing or named differently, use **Choose intrinsic/extrinsic ...**; the file may be in any folder.
- If the app matched trial videos by recording time instead of by name, check the **mapping table** and correct it if needed.
- With more than one trial per participant, the mapping table also has a **Participant** column: choose whose trial each row is. The app fills it in recording order (the first participant's trials first).
- The table has a column for every **participant detail** ticked in step 1. Fill them in per trial; changes are saved straight away. When a participant has several trials, the values entered for one are copied into their other trials that are still empty, so normally you enter them once and change only what differs (for example the weight on a later day). An empty height uses the participant's height from step 1.
- **Add trial ...** adds a trial for a participant, for example one recorded on a later day: choose its video for every camera, **Save this mapping**, check and copy again. Trials already processed keep their numbers and results. **Remove trial ...** removes the selected trial (click its row number first). A trial that already has copied videos or results is deleted with them, after the list of files is shown and you type the pseudonym; the participant's other trials stay.

The page then shows how many videos will be copied and how much disk space this needs. Press **Copy videos into the session**: the app copies the videos, converting them if needed; your original files are never changed.

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

**A camera was moved between trials** (an exception: avoid it if you can). If a camera was moved or knocked between trials, record a new `extrinsic` video with it in its new place, showing the reference points. Then use the small **A camera was moved between trials ...** button under the table: choose the camera, the new video and the trials recorded from the new place. The camera gets an extra row; click the points on the new video as usual. Trials recorded from the new place use that position; the others keep the first one. **Position ...** in that row changes its trials or removes it. Trials whose camera position changes after they were processed have their 3D results recomputed at the next run.

### Step 6 – Run

The summary shows every trial, camera and setting. Tick **I recorded a sync cue at the start of every trial**, then press **Run**.

- **Time:** the estimate is shown before you start. On a laptop without a graphics card, expect several minutes per camera and minute of video.
- **Stop:** you can stop at any time and press **Run** again later to resume; finished stages are kept.
- **One trial at a time:** **Run this trial** in a trial's row runs only that trial. You can save the project, close the app, record the next participant another day, open the project again, add the trial (step 2), and run just that one; finished trials are not touched.
- **Failures:** if one trial fails, the others continue. The reason is shown in plain words, with the full log under `trials/trial_<label>/mocap_logs/`.
- **Synchronisation:** if the app is unsure that the cameras are lined up for a trial, that trial stops. Choose **Check sync ...** to inspect it in a player, or **Accept as is**.

**Advanced** settings are folded away. The defaults suit most studies.

- **Pose detection:** lightweight is fastest, performance most accurate.
- **Keypoints:** *Body with feet* (26 points) suits most studies. *Whole body* adds hands and face (133 points); it is slower and needs a further download of about 210 MB the first time. *Whole body with wrists* uses the same detection but keeps only two points per hand.
- **OpenSim body model:** *Full* has muscles and joint constraints. *Simple* computes joint angles over 10 times faster, but without muscles and with a stiffer spine and shoulders.
- A **BVH animation** is always made, next to the OpenSim results.
- **Save overlay videos:** a copy of each video with the detected skeleton drawn on it, to check the detection by eye. Off by default: they are about 1.5 times the size of the videos.
- **Keep the 2D keypoint files of finished trials:** off by default. Finding keypoints writes one file per video frame, twice; with videos of several minutes that is several GB per trial. Once a trial has finished, the app removes them; its 3D results and joint angles stay. Later changes to smoothing or the body model still re-run without them; a change of keypoints or pose detection starts again from the videos anyway. Trials that stop early (for example to check the sync) keep theirs until they finish.

**Disk space.** Before a run, the app estimates the space the working files need. If the drive may fill up, it warns you: free some space, run a few trials at a time, or move the session to a larger drive (step 1, **Change output folder ...**). Short recordings of only the movement you study save the most space and time.

- **Smoothing cut-off:** 6 Hz suits walking. Faster movements need more, but the cut-off must stay below half the video frame rate: below 15 Hz for 30 fps video, below 30 Hz for 60 fps. The app checks this before it runs.
- **Changing settings after a run:** allowed. **Run** then repeats only the stages the change affects, for example smoothing and everything after it. A change of pose detection repeats everything.

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

Under **Export**, tick what you need and press **Export**. Files go to the session's `export/` folder, named `<pseudonym>_trial<label>_<content>`, together with:

- `README.txt`, which describes every file;
- `manifest.json`, which records versions, settings and file checksums.

Press **Next** for the **Viewer** (no export needed).

### Step 8 – Viewer

Choose a **trial** at the top. The **3D** tab plays the movement as a skeleton: **Play**, drag the slider to go to a moment, drag in the picture to rotate, use the mouse wheel to zoom, double-click to reset. Blue is left, red is right, grey dots are the LSTM markers; the grid is the floor in 0.5 m squares. **3D markers** chooses the marker file (smoothed + LSTM, smoothed, raw).

The **Data** tab shows the numbers:

- **Joint angles (.mot)**: one column per joint angle in degrees (for the arm: `arm_flex`, `arm_add`, `arm_rot`, `elbow_flex`, `pro_sup`, `wrist_flex`, `wrist_dev`; `_r` right, `_l` left).
- **Marker positions (.trc)**: X, Y, Z of every marker in metres (Y is up).

Tick columns to plot them; type in the filter box to find them (e.g. `elbow`). The table below the plot lists the values frame by frame, and the line under the plot gives each column's minimum, maximum, range and **peak speed** (deg/s or m/s). **Compare all trials** draws the same columns of every trial on top of each other; **Plot speed** draws the rate of change instead of the value. The toolbar above the plot zooms, pans and saves the plot as an image. **Save as CSV ...** writes the ticked columns (or all) for Excel, R, Python or MATLAB.

With the default keypoints there are no points on the hand, so wrist and forearm rotation angles are not reliable; shoulder and elbow are.

When you are done, press **Finished!**. The app saves the session, reminds you of raw footage that is still stored, and returns to step 1 for the next session. You can reopen the session at any time from **File > Recent sessions**, or from its project file.

## 4. Participant data and deletion

**File > Participant data and deletion** is available at any time, as are the buttons on the Session and Results steps. For each participant you can:

- **Delete raw footage**: removes the copies of their videos, the overlay videos and the 2D keypoint files. The 3D results stay. Optionally, it also deletes the original recordings in the camera folders; you must type the pseudonym to confirm.
- **Delete all data of this participant** (for example, when they withdraw consent): removes their footage, results and exported files. You must type the pseudonym to confirm.

A single trial (for example a failed take) is deleted with **Remove trial ...** in step 2: its copies, results and exported files go, and it leaves the session. The participant's other trials stay.

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
| "The smoothing cut-off is too high for this video's frame rate" | Lower **Advanced > Smoothing cut-off** below half the frame rate (for 30 fps video: 14 Hz or less), then press **Run**. Only smoothing and later stages are repeated. |
| Head below the feet | Z points down in your reference points: Z must point up. |
| "Raw footage was deleted in this session" | After a deletion, the session's videos can no longer be changed. Start a new session for new recordings. |
| "No space left on device" in a log | The drive filled up during the run. Free some space (or move the session to a larger drive) and press **Run** again: finished stages are kept. |
| "No video chosen for trial N" | A trial was added: choose its video for every camera in the mapping table (step 2) and press **Save this mapping**. |
| "extrinsics missing" for a camera position | A camera was marked as moved: click the reference points on its new extrinsic video (step 5), or remove that position. |
| Anything else | **Help > Copy diagnostics** copies version and setup information (without your name or any participant data); paste it into your bug report. |

## 6. Command line (optional)

Every step also works without the window, for scripting and lab servers. Run `mocapapp --help`, or see the project's `app/README.md`. For example, `mocapapp data --session <folder> --trial 1 --columns elbow_flex_r` prints the minimum, maximum and peak speed of a joint angle, and `--csv out.csv` saves the values.

## 7. Citing

If you publish results, cite Pose2Sim; **Help > About** shows the reference. mocapapp is for research use only.
