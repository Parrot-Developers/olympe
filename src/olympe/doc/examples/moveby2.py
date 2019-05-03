# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged

drone = olympe.Drone("10.202.0.1")
drone.connection()
drone(
    TakeOff()
    >> FlyingStateChanged(state="hovering", _timeout=5)
).wait()
drone(
    moveBy(10, 0, 0, 0)
    >> FlyingStateChanged(state="hovering", _timeout=5)
).wait()
drone(Landing()).wait()
drone.disconnection()
