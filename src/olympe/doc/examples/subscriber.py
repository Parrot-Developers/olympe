# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, Landing, moveBy
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from pprint import pformat

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})
drone = olympe.Drone("10.202.0.1")
# subscribe to all events during the drone connection
with drone.subscribe(
    lambda event, controller: print("{}({})".format(event.message.fullName, pformat(event.args)))
):
    drone.connect()

# Subscribe to FlyingStateChanged
# If you call `drone.subscribe` without using the `with` statement,
# you'll have to call `drone.unsubscribe()` later.
flying_sub = drone.subscribe(
    lambda event, controller: print("Flyingstate =", event.args["state"]), FlyingStateChanged()
)
drone(
    FlyingStateChanged(state="hovering")
    | (TakeOff() & FlyingStateChanged(state="hovering"))
).wait()
drone(moveBy(10, 0, 0, 0)).wait()
drone(Landing()).wait()
drone(FlyingStateChanged(state="landed")).wait()
# unsubscribe from FlyingStateChanged
drone.unsubscribe(flying_sub)
drone.disconnect()
