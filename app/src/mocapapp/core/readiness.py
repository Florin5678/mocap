"""Run readiness per trial (PROJECT.md §16.4): what is still missing before a trial can run.

Run never waits for unrelated trials: each trial is judged on its own participant, layout and videos."""

from __future__ import annotations

from pathlib import Path

from mocapapp.core.session import Session


def trial_missing(session: Session, root: Path, label: str) -> list[str]:
    """Plain-language list of what trial `label` still needs; empty = ready to run."""
    from mocapapp.core.calibration import service
    from mocapapp.core.workflow import trial_inputs_ready

    trial = session.trial(label)
    if trial.withdrawn_at:
        return ["The participant withdrew."]
    out: list[str] = []
    if not trial.consent.attested:
        out.append(f"Signed consent for {trial.code} is not confirmed.")
    if trial.height_m is None:
        out.append(f"No height for {trial.code}.")
    lay = session.trial_layout(label)
    if not trial.layout:
        out.append("No layout chosen.")
    elif lay is None:
        out.append(f"Layout {trial.layout} does not exist.")
    else:
        for what, state in service.trial_calibration_state(session, root, label).items():
            if state != "ready":
                out.append(f"{what}: {state}.")
        if not trial.raw_present:
            return out  # deleted footage: its results stay; nothing more to choose or copy
        absent = [session.camera(c).name for c in lay.camera_ids() if not trial.videos.get(c)]
        if absent:
            out.append(f"No video chosen for {', '.join(absent)}.")
        elif not trial_inputs_ready(session, root, label):
            out.append("Videos are not checked and copied yet.")
        warning = session.layout_warning(label)
        if warning:
            out.append(warning)
    return out


def trial_ready(session: Session, root: Path, label: str) -> bool:
    return not trial_missing(session, root, label)


def ready_trials(session: Session, root: Path) -> list[str]:
    return [t.label for t in session.trials if trial_ready(session, root, t.label)]


def trial_status(session: Session, root: Path, label: str) -> str:
    """R9 Status column: not ready, ready, running, needs sync fix, failed, done, out of date (or withdrawn)."""
    from mocapapp.core.runner import stale_stages

    trial = session.trial(label)
    if trial.withdrawn_at:
        return "withdrawn"
    state = session.run_state.trial_status.get(label)
    if state == "running":
        return "running"
    if state == "needs_sync_fix":
        return "needs sync fix"
    if state in ("done", "partial"):
        return "out of date" if stale_stages(session, label) or state == "partial" else "done"
    if not trial.raw_present:  # footage deleted before it finished: nothing more can run
        return "footage deleted"
    if trial_missing(session, root, label):
        return "not ready"
    return "failed" if state == "failed" else "ready"


# ---------- the window's sidebar and the empty-project checklist (§16.3) ----------

def _layout_ready(session: Session, root: Path, layout: str) -> bool:
    from mocapapp.core.calibration import service

    return all(v == "ready" for v in service.layout_state(session, root, layout).values())


def section_todo(session: Session, root: Path) -> dict[str, int | None]:
    """Per section: how many things still need doing (0 = all done, None = nothing there yet)."""
    from mocapapp.core.calibration import service

    cams = [c for c in session.cameras]
    people = session.active_participants
    trials = session.active_trials
    return {
        "session": sum(1 for t in trials if trial_status(session, root, t.label) == "not ready") if trials else None,
        "participants": sum(1 for p in people if not p.consent.attested or p.height_m is None) if people else None,
        "cameras": sum(1 for c in cams if service.lens_result(root, c.id) is None) if cams else None,
        "layouts": sum(1 for x in session.layouts if not _layout_ready(session, root, x.id))
        if session.layouts else None,
        "results": None,
    }


def badge(todo: int | None) -> str:
    """Sidebar badge: a tick, 'N to do', or nothing."""
    if todo is None:
        return ""
    return "\u2713" if todo == 0 else f"{todo} to do"


def checklist(session: Session, root: Path) -> list[tuple[str, str, bool]]:
    """An empty project's first steps: (section, what to do, done)."""
    from mocapapp.core.calibration import service

    has_points = bool(session.presets) or any(x.points for x in session.layouts)
    return [
        ("cameras", "Add the cameras", bool(session.cameras)),
        ("cameras", "Calibrate their lenses (intrinsic videos of the board)",
         bool(session.cameras) and all(service.lens_result(root, c.id) for c in session.cameras)),
        ("layouts", "Enter the measured reference points", has_points),
        ("layouts", "Make a layout and click the points in each camera's extrinsic video",
         any(_layout_ready(session, root, x.id) for x in session.layouts)),
        ("participants", "Add participants: pseudonym, height, consent",
         any(p.consent.attested and p.height_m for p in session.active_participants)),
        ("session", "Add the first trial and choose its videos", bool(session.trials)),
    ]
