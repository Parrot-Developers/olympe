# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState

drone = olympe.Drone("10.202.0.1", loglevel=2)
drone.connection()

drone(FlyingStateChanged(state="hovering")).wait(5)
# is equivalent to
drone(FlyingStateChanged(state=FlyingState.hovering)).wait(5)
