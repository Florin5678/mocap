from mocapapp.cli.main import main


def test_doctor_runs_and_reports(capsys):
    main(["doctor"])
    out = capsys.readouterr().out
    for label in ("Python", "Pose2Sim", "OpenSim", "OpenCV", "Free disk"):
        assert label in out
