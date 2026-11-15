=== 1. What this app does

You film one person with two or more cameras at the same time. The app turns those videos into 3D movement data:

- **3D marker positions** over time (`.trc` and `.c3d` files), and
- **joint angles** from a scaled OpenSim body model (`.mot` and `.osim`, optionally `.bvh` for animation software).

The steps, in order:

1. **Session setup** – one folder per camera, one row per participant, and their consent forms.
2. **Lens calibration** (intrinsic) – from a video of a printed board, once per camera.
3. **Reference points** – measured points in the room that define the floor and the directions X, Y, Z.
4. **Camera positions** (extrinsic) – you click the reference points in one picture per camera.
5. **Run** – the computer does the rest; this can take a long time on a laptop, and you can leave it running.
6. **Results** – a quality report, a 3D preview and the export.

All the manual work (steps 1–4) happens before the long computation, so you can walk away once you press Run.

=== 2. Cameras and recording settings

Good recordings matter more than anything the software can fix later.

- **60 frames per second, 1080p** (or better). Fast movements filmed at 30 fps come out blurred and jittery.
- **The same frame rate on every camera.**
- **Fixed zoom and focus.** Turn off image stabilisation and automatic lens switching (phones switch lenses silently).
- **Tripods.** Cameras must not move during the whole session – not even a bump. If a camera moves, start a new session.
- Place cameras around the person at roughly chest height, looking slightly down, each seeing the whole body in every frame. **Three or more cameras** are recommended; two is the minimum.
- Good, even light; avoid strong backlight.

=== 3. Limitations

- **One person only.** Nobody else may walk through the picture. A tripod or a coat stand can be mistaken for a person – keep them out of view.
- No views from straight above.
- Results are less reliable for seated, crouching or lying movements, and poor for upside-down movements.
- The body model is simplified (a stiff spine, ball-joint shoulders).
- Very fast movements need high frame rates.

=== 4. The two calibration videos

Every camera folder needs **two extra videos**, besides one video per trial:

**`intrinsic`** – the lens calibration. Hold the printed board (step 7 of this guide) in front of the camera and move it slowly: close and far, tilted in different directions, into every corner of the picture, for about a minute. Record it with **exactly the same settings** (resolution, zoom, focus, stabilisation) as the trials. A camera keeps its lens calibration even when it is moved.

**`extrinsic`** – the camera position. Record a few seconds with the camera **in its final position for the trials**, showing the reference points on the floor and at other heights (next page). Touching or bumping the camera afterwards makes this video useless.

Name the files `intrinsic.mp4` and `extrinsic.mp4` (any video format works). If a file is named differently or sits in another folder, you can choose it by hand during setup.

=== 5. Placing and measuring reference points

The app needs at least **10 points** in the room whose positions you measured, for example tape crosses on the floor and marks on boxes or stands at different heights.

- Choose an **origin** (0, 0, 0) on the floor, e.g. a tape cross in a corner.
- **Z points up.** The floor is Z = 0.
- Choose the **+X direction** along the floor, e.g. "toward the window".
- **+Y** then follows from the right-hand rule: standing at the origin facing +X, +Y is to your **left**.
- Measure every point in **metres** with a tape or laser measure, to within **±5 mm**.
- Spread the points through the whole space where the person moves, and put **some above the floor** (a box, a stool, a stand). Points that all lie on the floor give a less reliable result.
- Each camera must see at least 6 of the points (10 or more is better).

Example: origin at a floor cross by the door, +X toward the window. A cross 2 m toward the window is (2, 0, 0); a mark on a 0.75 m stool 1 m toward the window and 1 m to the left is (1, 1, 0.75).

=== 6. Synchronising the cameras

The cameras do not start at exactly the same moment, so the app lines them up afterwards using the movement of the person.

**Before each trial:** start all cameras, then give a clear cue visible to all of them – clap with raised hands, or jump – and include at least **5 seconds of clear up-and-down movement** (e.g. a few squats or jumps). Cameras that were not started with a cue cannot be synchronised reliably.

If the app is unsure about the synchronisation of a trial, it stops that trial and asks you to check it in a player before continuing.

=== 7. The calibration board (PDF)

Print the board at **100 % / actual size** (never "fit to page") and check the 5 cm scale bar with a ruler. Glue it flat onto something rigid, such as foam board – a bent board gives a bad lens calibration.

The board in the PDF always matches the board settings of the app.

[[download:board]]

=== 8. The participation consent form (PDF)

Each participant gets their own **pseudonym**, such as `PA26-K7M2QX4`. The data are stored under the pseudonym, and only the signed participation consent form – which you keep separately – links it to the person.

During setup, **Generate ...** makes each participant's form with their pseudonym filled in. The study details (study name, purpose, researchers, data handling, risks, compensation) are asked once and printed on every form of the session; blank ones can be filled in by hand. Have the form checked by your institution.

The app does not see the signed forms. Before running, you confirm for each participant that signed consent was received.

[[download:consent]]
