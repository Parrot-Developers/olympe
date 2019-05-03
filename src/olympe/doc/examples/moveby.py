# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing

drone = olympe.Drone("10.202.0.1")
drone.connection()
drone(TakeOff()).wait()
drone(moveBy(10, 0, 0, 0)).wait()
drone(Landing()).wait()
drone.disconnection()
