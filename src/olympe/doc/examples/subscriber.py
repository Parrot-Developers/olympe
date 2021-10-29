import olympe
import os
from olympe.messages.ardrone3.Piloting import TakeOff, Landing, moveBy
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged

olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def test_subscriber():
    drone = olympe.Drone(DRONE_IP, media_autoconnect=False)
    # subscribe to all events during the drone connection
    with drone.subscribe(
        lambda event, controller: print(f"{event}")
    ):
        drone.connect()

    # Subscribe to FlyingStateChanged
    # If you call `drone.subscribe` without using the `with` statement,
    # you'll have to call `drone.unsubscribe()` later.
    flying_sub = drone.subscribe(
        lambda event, controller: print("Flyingstate =", event.args["state"]),
        FlyingStateChanged(_policy="check"),
    )
    assert (
        drone(
            FlyingStateChanged(state="hovering")
            | (TakeOff() & FlyingStateChanged(state="hovering"))
        )
        .wait(5)
        .success()
    )
    assert drone(moveBy(10, 0, 0, 0)).wait().success()
    drone(Landing()).wait()
    assert drone(FlyingStateChanged(state="landed")).wait().success()
    # unsubscribe from FlyingStateChanged
    drone.unsubscribe(flying_sub)
    drone.disconnect()


if __name__ == "__main__":
    test_subscriber()
