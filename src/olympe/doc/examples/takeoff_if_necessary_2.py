# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged

with olympe.Drone("10.202.0.1") as drone:
    drone.connection()
    print("Takeoff if necessary...")
    drone(
        FlyingStateChanged(state="hovering", _policy="check")
        | (
            GPSFixStateChanged(fixed=1, _timeout=10)
            >> (
                TakeOff(_no_expect=True)
                & FlyingStateChanged(state="hovering", _policy="wait", _timeout=5)
            )
        )
    ).wait()
