# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    drone(TakeOff())

    assert drone(FlyingStateChanged(state="hovering")).wait(5).success()
    # is equivalent to
    assert drone(FlyingStateChanged(state=FlyingState.hovering)).wait(5).success()
    drone.disconnect()
