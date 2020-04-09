# -*- coding: UTF-8 -*-

from __future__ import print_function  # python2/3 compatibility for the print function
import olympe
from olympe.messages.ardrone3.PilotingSettingsState import MaxTiltChanged

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    print("Drone MaxTilt = ", drone.get_state(MaxTiltChanged)["current"])
    drone.disconnect()
