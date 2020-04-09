# -*- coding: UTF-8 -*-

from __future__ import print_function  # python2/3 compatibility for the print function
import olympe
from olympe.messages.ardrone3.PilotingSettings import MaxTilt

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    maxTiltAction = drone(MaxTilt(10)).wait()
    if maxTiltAction.success():
        print("MaxTilt(10) success")
    elif maxTiltAction.timedout():
        print("MaxTilt(10) timedout")
    else:
        # If ".wait()" is called on the ``maxTiltAction`` this shouldn't happen
        print("MaxTilt(10) is still in progress")
    maxTiltAction = drone(MaxTilt(0)).wait()
    if maxTiltAction.success():
        print("MaxTilt(0) success")
    elif maxTiltAction.timedout():
        print("MaxTilt(0) timedout")
    else:
        # If ".wait()" is called on the ``maxTiltAction`` this shouldn't happen
        print("MaxTilt(0) is still in progress")
    drone.disconnect()
