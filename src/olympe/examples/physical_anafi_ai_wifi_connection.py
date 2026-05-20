from os import environ

import olympe

DRONE_IP = environ.get("DRONE_IP", "192.168.42.1")


def connect_to_physical_drone_overWiFi():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()
    assert drone.disconnect()


if __name__ == "__main__":
    connect_to_physical_drone_overWiFi()
