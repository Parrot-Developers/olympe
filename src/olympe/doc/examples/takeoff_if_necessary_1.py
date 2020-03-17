# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged

with olympe.Drone("10.202.0.1") as drone:
    drone.connect()
    print("Takeoff if necessary...")
    if (drone.get_state(FlyingStateChanged)["state"] is not
            FlyingStateChanged_State.hovering):
        drone(GPSFixStateChanged(fixed=1, _timeout=10, _policy="check_wait")).wait()
        drone(
            TakeOff(_no_expect=True)
            & FlyingStateChanged(state="hovering", _policy="wait", _timeout=5)
        ).wait()
