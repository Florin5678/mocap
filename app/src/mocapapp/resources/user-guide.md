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

Your project folders are **never** removed by uninstalling.

## 2. First start

- **Greeting window.** The app opens with two buttons. Choose **How it works** for the tutorial (recommended the first time), or **Start**.
- **Environment check.** If something is wrong with the installation, the app tells you what to do before anything else. You can repeat the check with **Help > Environment check**.
- **Pose models.** If the models were not downloaded during installation, the app offers to download them once (about 145 MB). You can also do this later with **Help > Download pose models**.
- **Settings.** **Help > Settings ...** holds the options of the app as a whole, on this computer: the **output folder** for new projects (*Folders*), and whether the screen stays on while the app is working (*Running*; on by default, which also keeps the computer from going to sleep and pausing a run). The settings of a project, such as pose detection or smoothing, are in its **Session settings**.
- **Sample data.** **File > Try with sample data** creates a ready-made project from a demo recording (calibration included), so you can try a run and *Results* before you film anything. A run on a laptop takes about 10 minutes.

## 3. A project, section by section

A **project** is one study, recorded over one day or several. It holds its **cameras** (each with its lens calibration), its **layouts** (where the cameras stood: their positions, from an extrinsic video and the reference points), its **participants** and their **trials**. A trial is one recording of one participant, made with one layout. Calibration is done once and reused: a camera keeps its lens calibration for every layout, and a layout is used by every trial recorded while the cameras stood in that place.

The window has five **sections**, listed on the left: **Session**, **Participants**, **Cameras**, **Layouts** and **Results**. Open them in any order. Next to each name the list shows **✓** when nothing is left to do there, or **N to do**. Everything is saved straight away. Close the app at any time: **File > Recent projects** reopens a project where you left off.

While the app works (a run, a calibration, copying videos), you can open **Session** and **Results** (to see trials as they finish), and the section where the work started; the other sections wait until it is done.

**Project files.** Everything is saved in the project folder. To keep a project as a file you can find and open again, use **File > Save project** (Ctrl+S) or **Save project as ...**. This writes a small `.mocapproj` file that points to the project folder and remembers the section you were in; closing the app updates it. **File > Open project ...** (Ctrl+O) reopens it there; on Windows you can also double-click the project file. Keep the project file next to the project folder, or move both together: it does not contain videos or results, only where they are.

**A typical study.** Day 1: create the project, add the cameras and calibrate their lenses, enter the reference points, make a layout and click the points in each camera's extrinsic video, add the participants and print their consent forms, record, add the trials, run. Day 2: if the cameras stayed where they were, add the new participants and trials and run them. If the cameras were set up again, record new extrinsic videos and make a new layout first (**Duplicate** the old one: same cameras and points, new videos).

### Session

With no project open, Session starts one:

1. Choose an **output folder** whose path contains only plain letters (no æ/ø/å or other accents); the 3D software cannot save elsewhere. It starts as the folder chosen in **Help > Settings**.
2. Optionally enter a **study code** (up to 8 letters or digits, e.g. `PA26`). It starts every pseudonym.
3. Set **Trials per participant** (usually 1). Each participant can have more or fewer.
4. Press **Create project**. The project starts empty. **Open a project ...** and **Try with sample data ...** are next to it.

With a project open, Session is the **hub**: the trials, their state, and the run. While the project is new, a **First steps** list links to what is still to do (cameras, lenses, reference points, layout, participants, first trial).

**Session settings ...** holds what applies to the whole project: the project folder (**Change output folder ...** moves the project, with everything in it, to another folder; to another drive the files are copied, checked, and only then removed), the study code (for new pseudonyms), trials per participant, notes, the processing settings (below), and **Delete raw footage for all participants** (section 4).

**The trials table.** One row per trial: its **number** (fixed: folders and results are named after it), its **name / note** (free text, such as *walking*), **participant**, **layout**, **date** (the day its videos were recorded) and **Status**:

- **not ready** – with what is still missing (consent, height, a video, a calibration, copying);
- **ready**, **running** (with the current step), **needs sync fix**, **failed**, **done**;
- **out of date** – a setting changed after it ran; running it again repeats only the stages the change affects.

Select a row to work on that trial; select several with **Ctrl**-click or **Shift**-click. The buttons above the table act on the selected trials:

- **Run** runs the selected trials. With none selected it runs every trial that is ready (or failed, or out of date). Trials that are not ready are left out and named first. **Start over (ignore finished stages)** next to it computes every stage again; it unticks itself after the run.
- **Duplicate** adds a trial like each selected one: same participant, layout and name, its own videos.
- **Change layout ...** sets the layout the trials were recorded with.
- **Check and copy videos** checks the videos (frame rate, size against the camera's lens calibration, length) and copies them into the project, converting them when needed; your original files are never changed. Each trial is checked on its own: a problem in one never holds the others back. With none selected, it checks every trial whose videos are chosen but not copied yet.
- **Delete raw footage ...** and **Remove trial ...** (section 4). A trial without copied videos or results is removed after one question; its participant stays.
- **Check sync ...** and **Accept sync as is** for the selected trials that stopped with *needs sync fix* (none selected: all of them), also while other trials run (below).
- **Consent forms ...** makes one PDF with the forms of the selected trials' participants.

Each row also has **Run this trial**, and, when a trial stopped for its synchronisation, **Check sync ...** and **Accept as is** (below).

**Adding a trial.** The row under the table is filled in from the previous trial; change what differs and press **Add**. The **participant** (highlighted) is the previous trial's participant while they have fewer trials than *trials per participant*, otherwise a participant without trials yet, otherwise a **new participant** with a random pseudonym. The layout and name come from the previous trial (the layout: a newer one, when one was made on a later day); the date is left empty and becomes the day the videos were recorded.

**The trial panel** opens under the table when one trial is selected:

- its participant, layout, date and name, which you can change (participant and layout only until the trial has run);
- a **video for every camera of its layout**: **Choose ...** picks one, starting in the camera's folder. A video named with the trial's number (trial 1: `1`, `01`, `trial01`, `walk1`, ...) is suggested and highlighted: **Use the suggested videos** takes them;
- **Check and copy videos** for this trial, with the problems found, the space used and the space left;
- its status, what it still needs, messages from the run, and the participant's height, consent and what happens to the raw footage;
- a warning when the trial was recorded on another day than its layout was made: were the cameras set up again? Then make a new layout. If they did not move, **Use this layout anyway** (recorded with the trial).

**The run.** Press **Run** (or **Run this trial**). The app first shows a rough time estimate and the space the working files need, and any warning. The bar at the bottom of Session shows the trial and step being worked on, the overall progress and the estimate; **Show Pose2Sim's own output** shows the log.

- **Time:** on a laptop without a graphics card, expect several minutes per camera and minute of video.
- **Screen:** while a run is going, the screen stays on and the computer does not go to sleep, unless this is turned off in **Help > Settings**.
- **Stop:** stops after the current step; run the trials again later to continue. Finished stages are kept.
- **A trial recorded later** (another participant, another day): add it, choose its videos, check and copy, and run just that one. Finished trials are not touched.
- **Failures:** if one trial fails, the others continue. The reason is shown in its Status, with the full log under `trials/trial_<number>/mocap_logs/`.
- **Synchronisation:** if the app is unsure that the cameras are lined up for a trial (a low correlation), that trial stops with **needs sync fix**. **Check sync ...** opens a player in a separate window; close it when the offsets look right and the trial continues by itself in the background (it first copies the trial's keypoint files, which can take several minutes). For several trials, select them and use **Check sync (n) ...** above the table: their players open one after another, and each trial starts running as soon as its player is closed, so you can check the next one meanwhile. This also works while other trials run: the checked trials join the run. **Accept as is** (or **Accept sync as is** for the selected trials) continues with the automatic result. **Stop** drops the players not opened yet; a trial whose sync you checked but that did not run keeps the checked sync: press **Accept as is** later to continue with it. An offset longer than 5 s does not stop the trial: it is listed in the quality report, and is fine if the cameras were started that far apart.

**Processing settings** (Session settings). The defaults suit most studies.

- **Pose detection:** lightweight is fastest, performance most accurate.
- **Keypoints:** *Body with feet* (26 points) suits most studies. *Whole body* adds hands and face (133 points); it is slower and needs a further download of about 210 MB the first time. *Whole body with wrists* keeps only two points per hand.
- **Smoothing cut-off:** 6 Hz suits walking. Faster movements need more, but the cut-off must stay below half the video frame rate: below 15 Hz for 30 fps video. The app checks this before it runs.
- **OpenSim body model:** *Full* has muscles and joint constraints. *Simple* computes joint angles over 10 times faster, without muscles and with a stiffer spine and shoulders. A **BVH animation** is always made.
- **Save overlay videos:** a copy of each video with the detected skeleton drawn on it. Off by default: about 1.5 times the size of the videos.
- **Keep the 2D keypoint files of finished trials:** off by default. They take several GB per trial with long videos; once a trial has finished the app removes them, and its 3D results stay.
- **Changing a setting after a run** is allowed: the trials it affects become **out of date**, and running them again repeats only the stages concerned (a change of pose detection repeats everything).

**Disk space.** Before a run the app estimates the space the working files need and warns you if the drive may fill up: free some space, run a few trials at a time, or move the project to a larger drive (**Session settings > Change output folder ...**). Short recordings of only the movement you study save the most space and time.

### Participants

One row per participant; changes are saved straight away.

- **Pseudonym**, such as `PA26-K7M2QX4`. Type another one to rename (letters, digits, `-` or `_`, never a real name), or select rows and press **New pseudonym** for random ones. Once consent is given or results exist, the app first says what a rename affects (write the new pseudonym on the signed form; the report and exported files are renamed) and asks you to confirm.
- **Height**, in metres or centimetres. An empty height is estimated, which is less accurate.
- The columns chosen with **Columns ...**: weight, handedness, age, gender, or your own (a number with a unit, a choice, or a short text). Never ask for names or details that identify the person. Values are kept with each of the participant's trials.
- **Consent**: *Not signed yet*, *Signed, delete footage* or *Signed, keep footage* (when the participant agreed to storing the raw footage). A trial runs only once consent is signed. **Raw footage** shows what will happen to the footage.
- **Trials**: the participant's trials. **Consent form**: **Generate ...** makes the participant's form.

**Add participant** adds one with a new pseudonym and the usual number of trials. **Remove** removes the selected participants and their trials (only without videos or results, for example someone who did not come). **Consent forms ...** makes one PDF with the selected participants' forms (none selected: everyone's), ready to print. The first time, a window asks for the study details (study name, purpose, researchers, data handling, risks, compensation); they are printed on every form, and a field left blank gets a line to write on.

Keep the signed forms separate from the data: the pseudonym is the only link between a person and their data.

### Cameras

One row per camera: its name, **intrinsic video** (the board video for its lens), the lens result, error, frames used, coverage, tilt spread, the layouts using it, and advice. A camera keeps its lens calibration wherever it stands.

- **Add camera ...**: a name, the camera's **folder** (where its videos are; a video named `intrinsic` there is found by itself), the intrinsic video, and optional notes (resolution, zoom, focus, stabilisation, frame rate).
- **Edit ...**, **Duplicate**, **Delete** (refused while a layout uses the camera). Select several rows to duplicate or delete several.
- **Calibrate all cameras** computes every lens not calibrated yet; **Calibrate selected again** redoes the selected ones. **Pass** is fine; **Check** works but could be better (read the advice); **Fail** needs a new intrinsic video in which the board covers the whole picture, including the corners.
- Once a trial using the camera has run, its lens calibration is locked: for new settings (another zoom), **Duplicate** the camera and calibrate the copy.
- The **board** settings and **Board PDF ...** (print it before the recordings).
- **Import from another project ...** copies cameras with their lens calibration (also from the File menu).

### Layouts

A layout is where the cameras stood: which cameras, in which order, the reference points, and each camera's position. The list on the left shows the layouts; select one to see it.

- **New layout ...**: a name, a date (the day of its extrinsic videos), and its cameras in order. **Edit ...**, **Duplicate** (same cameras and points, new videos: for cameras that were set up again) and **Delete**; select several to duplicate or delete several.
- **Reference points.** **Edit reference points ...**: first choose the **unit of the numbers** (metres, centimetres, inches, feet), then enter the measured points (at least 10, Z up), import them from a CSV file, or paste a Config.toml snippet. Describe the **origin** and the **+X direction** in words. The app checks for duplicates, points on a line, values far from the origin (centimetres with the unit set to metres) and too few points above the floor. **Save as preset ...** keeps the points for other layouts; **Use a preset ...** copies them in. Changing the points later does not mean clicking again: every camera position is recomputed from the clicks you made.
- **Camera rows.** For each camera: **Choose video ...** (its extrinsic video, recorded in this place), then **Click points ...**: click every reference point you can see in the order shown, and skip hidden ones. The app computes the camera's position and its error: **below 1 cm** is good; **above 2.5 cm**, look for a wrongly clicked point (the app shows how far each point is off) or a wrong measurement. If points are *behind the camera*, an axis is probably flipped; the app suggests which. **Review image** shows the points as the camera sees them.
- **Import from another project ...** copies layouts and presets.

Cameras must not move while a layout is used. If one is moved, record a new extrinsic video and make a new layout.

### Results

The trials are listed on the left with a **traffic light**: **Good**, **Check** or **Problem** (grey: not run yet). Results can be opened during a run: trials appear as they finish. Choose a trial; the tabs show it.

**Quality**: the metrics behind the light, warnings, and a link to the **3D preview** (watch for limbs that jump or swap sides). **Open the quality report** shows every trial on one page.

| Metric | Means | Good |
|---|---|---|
| Sync confidence | how well the cameras were lined up | ≥ 0.6 |
| 3D reprojection error | how well the cameras agree on each point | ≤ 20 mm |
| Frames without 2+ cameras | how often a point had to be filled in | ≤ 5 % |
| Model fit (IK RMS) | how well the body model follows the points | ≤ 30 mm |
| Feet height above floor | should be near 0 if the floor is Z = 0 | ≤ 5 cm |

**3D** plays the movement as a skeleton: **Play**, drag the slider to go to a moment, drag in the picture to rotate, use the mouse wheel to zoom, double-click to reset. Blue is left, red is right, grey dots are the LSTM markers; the grid is the floor in 0.5 m squares. **3D markers** chooses the marker file (smoothed + LSTM, smoothed, raw).

**Data** shows the numbers: **joint angles (.mot)** in degrees (for the arm: `arm_flex`, `arm_add`, `arm_rot`, `elbow_flex`, `pro_sup`, `wrist_flex`, `wrist_dev`; `_r` right, `_l` left) or **marker positions (.trc)** in metres (Y is up). Tick columns to plot them; type in the filter box to find them (e.g. `elbow`). The table lists the values frame by frame; the line under the plot gives each column's minimum, maximum, range and **peak speed**. **Compare all trials** draws the same columns of every trial together; **Plot speed** draws the rate of change. **Save as CSV ...** writes the ticked columns (or all) for Excel, R, Python or MATLAB. With the default keypoints there are no points on the hand, so wrist and forearm rotation angles are not reliable; shoulder and elbow are.

**Export**: tick what you need and press **Export** (once the run has finished). Files go to the project's `export/` folder, named `<pseudonym>_trial<number>_<content>`, with `README.txt` (what every file is) and `manifest.json` (versions, settings and checksums). The tab also shows what happens to each trial's raw footage, with **Delete raw footage now ...** and **Participant data and deletion ...** (section 4).

## 4. Participant data and deletion

**File > Participant data and deletion** is available at any time, as are the buttons in Session (for the selected trials), Participants and Results. For each participant you can:

- **Delete raw footage**: removes the copies of their videos, the overlay videos and the 2D keypoint files. The 3D results stay. Optionally, it also deletes the original recordings in the camera folders; you must type the pseudonym to confirm.
- **Delete all data of this participant** (for example, when they withdraw consent): removes their footage, results and exported files. You must type the pseudonym to confirm.

A single trial (for example a failed take) is deleted with **Remove trial ...** in Session: its copies, results and exported files go, and it leaves the project. The participant's other trials stay.

You can also delete the **whole project**; you must type the project folder name to confirm.

Every deletion first shows the exact list of files and sizes. Files are deleted permanently, not moved to the Recycle Bin. Afterwards, the app reminds you of copies it cannot reach:

- SD cards and phones;
- cloud backups;
- external drives.

Raw footage is to be deleted when the participant signed without consenting to storing it (*Signed, delete footage*), or for everyone when **Delete raw footage for all participants** is ticked in Session settings (after the trial is processed, or after the results are exported). Results (Export tab) then offers **Delete raw footage now ...** once the results are complete (and exported, when that is the timing). Footage kept with consent is never deleted automatically. The recent-projects list marks projects whose footage is still waiting to be deleted.

Details: the project's `app/docs/privacy.md`.

## 5. Troubleshooting

| Message or symptom | What to do |
|---|---|
| "Session path contains non-ASCII letters" | Move the project (Session settings > Change output folder) to a folder whose path has only plain letters and digits. |
| "No body keypoints were found in at least one camera" | Check that camera's video: is the person fully visible, and is the light sufficient? |
| A trial stops with "needs sync fix" | Use **Check sync ...**. Next time, give a clear clap or jump at the start, visible to every camera. |
| Large reprojection error | A camera may have moved after its `extrinsic` video, or a reference point was clicked or measured wrongly. Click the points again for that camera in its layout (Layouts), or make a new layout. |
| Feet far above or below the floor | The reference points' heights or the floor (Z = 0) are off. Check the measurements. |
| "The smoothing cut-off is too high for this video's frame rate" | Lower **Smoothing cut-off** in Session settings below half the frame rate (for 30 fps video: 14 Hz or less), then run the trial again. Only smoothing and later stages are repeated. |
| Head below the feet | Z points down in your reference points: Z must point up. |
| "its raw footage was deleted" | After a deletion, that trial's videos and layout can no longer change; its results stay. Add a new trial for new recordings. |
| "No space left on device" in a log | The drive filled up during the run. Free some space (or move the project to a larger drive) and run the trials again: finished stages are kept. |
| "No video chosen for cam01" in a trial's Status | Select the trial in Session and choose its videos in the trial panel (or **Use the suggested videos**), then **Check and copy videos**. |
| "Were the cameras set up again?" | The trial was recorded on another day than its layout. If the cameras moved, make a new layout (Layouts > Duplicate, new extrinsic videos) and change the trial's layout; if not, **Use this layout anyway** in the trial panel. |
| Anything else | **Help > Copy diagnostics** copies version and setup information (without your name or any participant data); paste it into your bug report. |

## 6. Command line (optional)

Everything also works without the window, for scripting and lab servers. Run `mocapapp --help`, or see the project's `app/README.md`. For example, `mocapapp data --session <folder> --trial 1 --columns elbow_flex_r` prints the minimum, maximum and peak speed of a joint angle, and `--csv out.csv` saves the values.

## 7. Citing

If you publish results, cite Pose2Sim; **Help > About** shows the reference. mocapapp is for research use only.
