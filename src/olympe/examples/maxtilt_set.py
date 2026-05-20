from os import environ

import olympe

from olympe.messages.ardrone3.PilotingSettings import MaxTilt

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def set_maxtilt():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    maxTilt_expectation = drone(MaxTilt(10)).wait()
    # Calling MaxTilt(10) or MaxTilt(current=10) are equivalent, because "current" is
    # the only mandatory argument of MaxTilt
    if maxTilt_expectation.success():
        print("MaxTilt(10) set succeeded")
    elif maxTilt_expectation.timedout():
        print("MaxTilt(10) set timedout")
    else:
        # If ".wait()" is called on ``maxTilt_expectation`` this shouldn't happen
        print("MaxTilt(10) set request is still in progress")

    maxTilt_expectation = drone(MaxTilt(0)).wait()
    if maxTilt_expectation.success():
        print("MaxTilt(0) set succeeded")
    elif maxTilt_expectation.timedout():
        print("MaxTilt(0) set timedout")
    else:
        # If ".wait()" is called on ``maxTilt_expectation`` this shouldn't happen
        print("MaxTilt(0) set is still in progress")

    assert drone.disconnect()


if __name__ == "__main__":
    set_maxtilt()
