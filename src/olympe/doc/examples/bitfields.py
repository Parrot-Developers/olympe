# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})
drone = olympe.Drone("10.202.0.1")
drone.connect()

flying_states = FlyingState._bitfield_type_("takingoff|hovering|flying")

if drone.get_state(FlyingStateChanged)["state"] in flying_states:
    print("The drone is in flight")
else:
    print("The drone is not in flight")
