"""Pin the upstream Pose2Sim behaviour the app relies on (see docs/upstream-notes.md)."""

import numpy as np
import pandas as pd
from Pose2Sim.common import sort_stringlist_by_last_number, zup2yup


def test_scene_z_up_maps_to_opensim_y_up():
    # Scene point X=1, Y=2, Z=3 (Z up). Code: X_osim = Y, Y_osim = Z, Z_osim = X.
    # The Config.toml comment ("becomes [Z,X,Y]") is wrong; this test is the reference.
    q = pd.DataFrame([[1.0, 2.0, 3.0]], columns=["X", "Y", "Z"])
    out = zup2yup(q).to_numpy()[0]
    np.testing.assert_array_equal(out, [2.0, 3.0, 1.0])


def test_cameras_are_matched_by_last_number_not_by_name():
    # Pose folders are ordered by their last number; calibration cameras by file order.
    # The app must therefore write Calib_scene.toml in cam01, cam02, ... order.
    names = ["cam10_json", "cam02_json", "cam01_json"]
    assert sort_stringlist_by_last_number(names) == ["cam01_json", "cam02_json", "cam10_json"]
