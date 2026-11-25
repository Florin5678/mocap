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
