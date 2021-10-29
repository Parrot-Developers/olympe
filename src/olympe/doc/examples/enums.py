import olympe
import os
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def test_enums():
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    assert drone(FlyingStateChanged(state="landed")).wait(5).success()
    # is equivalent to
    assert drone(FlyingStateChanged(state=FlyingState.landed)).wait(5).success()
    drone.disconnect()


if __name__ == "__main__":
    test_enums()
