from os import environ
from time import sleep

import olympe

from olympe.messages.battery import reliability

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def subscriber():
    drone = olympe.Drone(DRONE_IP)

    ### 'WITH' BLOCK SUBSCRIPTION

    # Subscribe to 'reliability' event before the drone connection. Will unsubscribe
    # outside the scope 'with' automatically
    with drone.subscribe(
        lambda event, controller: print(
            f" >>> Event received (reliability): {event.message.name} - {event.args}"
        ),
        reliability(is_charge_level_reliable=1, _policy="wait"),
    ):
        assert drone.connect()
        sleep(10)
        assert drone.disconnect()

    ### END 'WITH' BLOCK SUBSCRIPTION

    assert drone.connect()

    ### DIRECT SUBSCRIPTION

    # Subscribe to 'reliability' event after the drone connection
    # If you call `drone.subscribe` without using the `with` statement,
    # you'll have to call `drone.unsubscribe()` later.
    battery_reliability_subscription = drone.subscribe(
        lambda event, controller: print(
            f" >>> Event received (reliability): {event.message.name} - {event.args}"
        ),
        reliability(is_charge_level_reliable=1, _policy="wait"),
    )

    sleep(10)

    # unsubscribe from BatteryStateChanged
    drone.unsubscribe(battery_reliability_subscription)

    ### END DIRECT SUBSCRIPTION

    assert drone.disconnect()


if __name__ == "__main__":
    subscriber()
