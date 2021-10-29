import olympe
import os
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def test_takeoff_if_necessary_1():
    with olympe.Drone(DRONE_IP) as drone:
        drone.connect()
        print("Takeoff if necessary...")
        if (drone.get_state(FlyingStateChanged)["state"] is not
                FlyingStateChanged_State.hovering):
            drone(GPSFixStateChanged(fixed=1, _timeout=10, _policy="check_wait")).wait()
            drone(
                TakeOff(_no_expect=True)
                & FlyingStateChanged(state="takingoff", _policy="wait", _timeout=5)
            ).wait()
        drone.disconnect()


if __name__ == "__main__":
    test_takeoff_if_necessary_1()
