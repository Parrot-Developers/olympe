from os import environ

import olympe

from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.enums.ardrone3.PilotingState import FlyingStateChanged_State as FlyingState


DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def bitfields():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    flying_states = FlyingState._bitfield_type_("takingoff|hovering|flying")

    if drone.get_state(FlyingStateChanged)["state"] in flying_states:
        assert False, "The drone should not be flying"
    else:
        print("The drone is not in flight")

    assert drone.disconnect()


if __name__ == "__main__":
    bitfields()
