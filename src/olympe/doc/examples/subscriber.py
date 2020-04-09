# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, Landing, moveBy
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from pprint import pformat

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP, media_autoconnect=False)
    # subscribe to all events during the drone connection
    with drone.subscribe(
        lambda event, controller:
            print("{}({})".format(event.message.fullName, pformat(event.args)))
    ):
        drone.connect()

    # Subscribe to FlyingStateChanged
    # If you call `drone.subscribe` without using the `with` statement,
    # you'll have to call `drone.unsubscribe()` later.
    flying_sub = drone.subscribe(
        lambda event, controller: print("Flyingstate =", event.args["state"]), FlyingStateChanged()
    )
    assert drone(
        FlyingStateChanged(state="hovering")
        | (TakeOff() & FlyingStateChanged(state="hovering"))
    ).wait().success()
    assert drone(moveBy(10, 0, 0, 0)).wait().success()
    drone(Landing()).wait()
    assert drone(FlyingStateChanged(state="landed")).wait().success()
    # unsubscribe from FlyingStateChanged
    drone.unsubscribe(flying_sub)
    drone.disconnect()
