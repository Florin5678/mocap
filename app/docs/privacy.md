# Participant data, consent and deletion

How the app handles personal data (PROJECT.md §8). This is not legal advice. The participation consent form has blanks for the study's details; the owner fills them in and has the form reviewed before use.

## What is stored, and where

| Data | Where | Personal? |
|---|---|---|
| Original recordings | the camera folders you chose (the app never changes them) | yes: video of an identifiable person |
| Working copies | `inputs/camNN/trial_<label>.mp4`, `trials/trial_<label>/videos/` | yes |
| Overlay videos/images, 2D keypoints | `trials/trial_<label>/pose/`, `pose-sync/camNN_json/`, `pose-associated/camNN_json/` | yes (faces are blurred, but not reliably) |
| Height, pseudonym, consent record | `session.json`, `trials/trial_<label>/consent.json` | pseudonymised |
| 3D markers, joint angles, scaled model | `pose-3d/`, `kinematics/`, `export/` | pseudonymised (movement can still identify a person) |

Each participant's **pseudonym** (for example `PA26-K7M2QX4`) is pseudonymisation, not anonymisation. The signed consent form, which links the pseudonym to a name, stays with the experimenter, stored separately from the data. The app never stores names, e-mail addresses or body mass. The researcher can replace the random pseudonym with one of their own (letters, digits, `-`, `_`; it must not be the person's name) until signed consent is ticked or the participant has results; after that it is fixed, because the signed form and the exported file names carry it.

## Consent

Tick **Signed consent received** for each participant once you have the signed form for that pseudonym (`mocapapp consent --attest`). The app cannot verify the form. You cannot run a participant's trials until their consent is ticked.

The **Delete raw footage** option follows the participant's answer about storing raw footage:

- no storage consent: the option is ticked;
- storage consented: the option is unticked, and the footage is kept until the study is finished.

You can change the option either way. If storage was not consented and the option is unticked, the app shows a warning, because the consent form promises deletion.

## Deleting

Deleting is always possible, at any time: raw footage one trial at a time, and all data one participant (with all their trials) at a time:

- **in the app:** *File > Participant data and deletion*, or the buttons on the Session and Results steps;
- **from the command line:** the `delete-raw` and `delete-session` commands.

Every deletion first lists the exact files and their sizes, in these groups:

- **(a) Working copies** of the trial videos.
- **(b) Derived material that shows or traces the person:** overlay videos/images and 2D keypoint files. These are always deleted together with (a).
- **(c) The original recordings** in the camera folders. Only deleted if you choose to, and you must type the pseudonym to confirm.

These are kept:

- the intrinsic and extrinsic calibration videos;
- the 3D results (`.trc`/`.c3d`/`.mot`/`.osim`/`.bvh`);
- configs and quality reports.

If a person is visible in a calibration video, delete that video by hand.

There are three kinds of deletion:

| Action | What goes | Confirmation |
|---|---|---|
| **Delete raw footage** (`delete-raw`) | (a) + (b), optionally (c) | (c) needs the pseudonym typed |
| **Delete all data of this participant**, for withdrawal (`delete-session --trial`) | (a) + (b) + every result of every trial of that participant: the trial folders, their QA files and their exports. Optionally (c). | always type the pseudonym |
| **Delete the whole session** (`delete-session`) | the session folder, optionally every trial's originals | type the session folder name |

On the command line, nothing is deleted without `--yes`; without it, the command only lists what would go.

**Deletion chosen at setup.** When *Delete raw footage* was ticked at setup, the app deletes only after:

- every processing step has finished;
- the results can be read;
- the results have been exported.

You start this deletion on the Results step with **Delete raw footage now**; the command-line equivalent is `delete-raw --after-run`. A deletion on request (for example, a participant asks) works at any time. If the results are incomplete, the app warns you first: once the footage is deleted, that trial can never be processed again.

**After a deletion:**

- The session's videos are fixed. You cannot check, remap or prepare them again; start a new session for new recordings.
- Every deletion is logged in `manifest.json` in the session folder, and also in `export/manifest.json` when it exists. The log records when, which trial and pseudonym, how many items and bytes, and session-relative paths. It records no names, and no paths of the original recordings, because those can contain names.
- Withdrawing a participant also removes their height from `session.json` and their files from the export manifest.

## Limits of deletion: check these yourself

The app deletes files directly; they do not go to the Recycle Bin or Trash. Normal deletion still does not overwrite the data on disk: SSDs, backups and file-recovery tools can keep traces. After each deletion, the app shows this checklist:

- Delete the recordings from the cameras' SD cards and from any phones used for filming.
- Delete copies in cloud backups or sync folders (OneDrive, Dropbox, Google Drive, iCloud), including their online recycle bins.
- Delete copies on external drives, USB sticks and other computers.

The app warns you in advance when the session or the camera folders are inside a cloud-synced folder.

## Reminders

Footage that is still stored is never deleted automatically. The recent-sessions menu marks a session as *raw footage to delete* when either of these applies:

- footage is kept with consent;
- footage was marked for deletion but has not been deleted yet.

`mocapapp reminders --root <output folder>` lists the same sessions.

## Moving a session

Moving a session to another output folder (**Change output folder ...**, `mocapapp session move`) renames the folder on the same drive. To another drive it copies the folder, checks that every file arrived with its size, and only then removes the old folder; if that removal fails, the app says the old folder still holds participant data and must be deleted by hand. Pseudonyms must stay unique within an output folder, so a move into a folder whose sessions already use one of them is refused.
