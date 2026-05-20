from os import environ

import olympe

from olympe.messages.ardrone3.PilotingSettingsState import MaxTiltChanged

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def get_maxtilt():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    print(f"Drone MaxTilt = {drone.get_state(MaxTiltChanged)['current']}")

    assert drone.disconnect()


if __name__ == "__main__":
    get_maxtilt()
