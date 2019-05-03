# -*- coding: UTF-8 -*-

from __future__ import print_function  # python2/3 compatibility for the print function
import olympe
from olympe.messages.ardrone3.PilotingSettingsState import MaxTiltChanged

drone = olympe.Drone("10.202.0.1")
drone.connection()
print("Drone MaxTilt = ", drone.get_state(MaxTiltChanged)["current"])
drone.disconnection()
