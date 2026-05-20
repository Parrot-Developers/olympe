from os import environ

import olympe

from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged


DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def enums():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    assert drone(FlyingStateChanged(state="landed")).wait(5).success()
    # is equivalent to
    assert drone(FlyingStateChanged(state=FlyingState.landed)).wait(5).success()

    assert drone.disconnect()


if __name__ == "__main__":
    enums()
