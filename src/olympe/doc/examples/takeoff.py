# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, Landing

drone = olympe.Drone("10.202.0.1")
drone.connect()
drone(TakeOff()).wait()
drone(Landing()).wait()
drone.disconnect()
