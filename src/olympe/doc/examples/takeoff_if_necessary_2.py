# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    with olympe.Drone(DRONE_IP) as drone:
        drone.connect()
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
        drone.disconnect()
