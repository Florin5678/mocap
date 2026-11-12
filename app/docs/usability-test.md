# Usability test protocol (M6 exit criterion)

PROJECT.md §11 requires that **three people** who have never used OpenSim or Pose2Sim complete a session from raw videos, using only the tutorial and the user guide. The test records time-to-result and every point of confusion, and the problems found are fixed before release. This file is the protocol and the record sheet. **The test itself has to be run by the owner with real participants**: it cannot be automated.

## Participants

- 3 adults without experience of OpenSim, Pose2Sim or motion capture software, for example students or lab staff from another field.
- At least one Windows user and one macOS user, if both platforms are to be released.
- The test participants are users of the app, not mocap subjects. Their screen recording (if any) is personal data: get their consent and delete the recording after analysis.

## Material prepared by the facilitator

- A clean computer (or a fresh user account) with internet access, and the release-candidate installer.
- One recorded session: 3 cameras, both calibration videos per camera, 2 trials, and a sheet with the measured reference points (≥ 10, in metres, with the origin and +X described). Record it once and reuse it for all participants. The person filmed must have consented to the footage being used for testing.
- The printed calibration board is not needed: the videos already exist.
- A stopwatch and this record sheet.

## Tasks (read aloud, one at a time; give no other help)

| # | Task | Done when |
|---|---|---|
| T1 | "Install the app and open it." | The greeting window is visible. |
| T2 | "Find out how the app works, then start a new session with these camera folders. The participants are 1.78 m and 1.65 m tall; both signed consent; neither agreed to storing raw footage." | The session exists; heights are entered; consent is ticked; *Delete raw footage* is ticked. |
| T3 | "Get the videos ready." | The Videos step is complete. |
| T4 | "Calibrate the cameras using this measurement sheet." | Lens calibration, reference points and camera positions are complete; every camera is below 2.5 cm. |
| T5 | "Process both trials." | Both trials are done. If a sync check appears, it was handled. |
| T6 | "Tell me whether the results are good, and export the joint angles and markers." | The participant names the traffic lights correctly; the export exists. |
| T7 | "Participant 2 withdraws consent. Remove their data." | Trial 2 is withdrawn, and the participant mentions the checklist (SD cards, cloud). |
| T8 | "Delete the raw footage you are no longer allowed to keep." | Trial 1's raw footage is deleted. |

Think-aloud: ask the participant to say what they are looking for and what they expect to happen. Answer questions only with "What would you try?". If a participant is stuck for **5 minutes**, give the smallest hint that unblocks them and record it as a **critical incident**.

## Record sheet (one per participant)

| Task | Start | End | Completed (yes / with hint / no) | Confusions, errors, quotes | Severity (1 cosmetic – 4 blocker) |
|---|---|---|---|---|---|
| T1 | | | | | |
| T2 | | | | | |
| T3 | | | | | |
| T4 | | | | | |
| T5 | | | | | |
| T6 | | | | | |
| T7 | | | | | |
| T8 | | | | | |

After the session, ask:

1. What was the hardest part?
2. Was anything surprising or worrying, for example about deleting?
3. Would you trust the results? Why or why not?

## Pass criteria

- Every participant completes T1–T8, and **no task needs more than one hint**.
- **No severity-4 issue** remains open.
- Time-to-result is recorded. As an orientation, T2–T4 should take under 60 minutes for a first-time user; computation time in T5 is not counted.
- Every issue of severity 3 or 4 is fixed, or recorded with a reason in `decisions.md`. Re-test changed screens with one new participant.

## Results

| Date | Participant (P1–P3), OS | Time T2–T4 | Hints | Severity 3–4 issues | Fixed in |
|---|---|---|---|---|---|
| | | | | | |
