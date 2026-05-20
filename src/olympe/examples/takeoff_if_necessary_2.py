from os import environ

import olympe

from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def takeoff_if_necessary_2():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    print("Takeoff if necessary...")
    drone(
        FlyingStateChanged(state="hovering", _policy="check")
        | (
            GPSFixStateChanged(fixed=1, _timeout=10)
            >> (
                TakeOff(_no_expect=True)
                & FlyingStateChanged(state="takingoff", _policy="wait", _timeout=5)
            )
        )
    ).wait()

    assert drone.disconnect()


if __name__ == "__main__":
    takeoff_if_necessary_2()
