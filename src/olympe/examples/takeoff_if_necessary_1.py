from os import environ

import olympe

from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def takeoff_if_necessary_1():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    print("Takeoff if necessary...")
    if (
        drone.get_state(FlyingStateChanged)["state"]
        is not FlyingStateChanged_State.hovering
    ):
        drone(GPSFixStateChanged(fixed=1, _timeout=10, _policy="check_wait")).wait()
        drone(
            TakeOff(_no_expect=True)
            & FlyingStateChanged(state="takingoff", _policy="wait", _timeout=5)
        ).wait()

    assert drone.disconnect()


if __name__ == "__main__":
    takeoff_if_necessary_1()
