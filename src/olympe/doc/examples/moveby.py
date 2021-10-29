import olympe
import os
from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def test_moveby():
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    assert drone(TakeOff()).wait().success()
    drone(moveBy(10, 0, 0, 0)).wait()
    assert drone(Landing()).wait().success()
    drone.disconnect()


if __name__ == "__main__":
    test_moveby()
