from os import environ
from time import sleep

import olympe

from olympe.messages.ardrone3.Piloting import TakeOff, Landing

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def takeoff():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    assert drone(TakeOff()).wait().success()
    sleep(10)
    assert drone(Landing()).wait().success()

    assert drone.disconnect()


if __name__ == "__main__":
    takeoff()
