# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()

    flying_states = FlyingState._bitfield_type_("takingoff|hovering|flying")

    if drone.get_state(FlyingStateChanged)["state"] in flying_states:
        assert False, "The drone should not be flying"
    else:
        print("The drone is not in flight")
    drone.disconnect()
