import olympe
import os
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def test_takeoff_if_necessary_2():
    with olympe.Drone(DRONE_IP) as drone:
        drone.connect()
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
        drone.disconnect()


if __name__ == "__main__":
    test_takeoff_if_necessary_2()
