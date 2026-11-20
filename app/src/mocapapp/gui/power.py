"""Keep the screen on (and the computer awake) while the app is working. Off again as soon as it is idle."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

# Windows SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """start() / stop() may be called any number of times; only the first start and the matching stop act."""

    def __init__(self) -> None:
        self.active = False
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.active:
            return
        self.active = True
        try:
            if sys.platform == "win32":
                # called from the GUI thread, which lives as long as the app: the request holds until stop()
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            elif sys.platform == "darwin":
                # -d display, -i system; -w ends it with the app should the app crash
                self._proc = subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(os.getpid())],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except (OSError, AttributeError):  # not available here: the run itself is not affected
            pass

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        try:
            if sys.platform == "win32":
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            elif self._proc is not None:
                self._proc.terminate()
                self._proc = None
        except (OSError, AttributeError):
            pass
