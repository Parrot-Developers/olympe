from os import environ

import olympe

from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def moveby():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    assert drone(TakeOff()).wait().success()
    drone(moveBy(10, 0, 0, 0)).wait()
    assert drone(Landing()).wait().success()

    assert drone.disconnect()


if __name__ == "__main__":
    moveby()
