from os import environ
from time import sleep

import olympe

from olympe.messages import logsync
from olympe.messages.battery import reliability
from olympe.messages.common.CommonState import BatteryStateChanged

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


# Create custom listener dedicated to a specific message or category of messages
# Inherit from olympe.EventListener when creating your custom Listener class
class LogsyncListener(olympe.EventListener):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.listened: bool = False

    # Expose one method that matches every event message of that type and prints the
    # event and its args.
    # Use the `olympe.listen_event` decorator to inform the type(s) of events associated
    # with the method in question
    @olympe.listen_event(logsync.Event.Identifier(_policy="wait"))
    def onLogsyncIdentifier(self, event, scheduler):
        boot_id = event.args["boot_id"]
        model = event.args["model"]
        role = event.args["role"]
        print(
            " >>> Received a logsync message from a Node, whose charateristics are "
            f"boot_id:{boot_id}, model:{model}, role:{role}"
        )
        self.listened = True


# You can also handle multiple message types with the same method
class StatesListener(olympe.EventListener):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

    # OR
    @olympe.listen_event(
        BatteryStateChanged(_policy="wait")
        | reliability(is_charge_level_reliable=1, _policy="wait")
    )
    def onBatteryStateChanged(self, event, scheduler):
        # This method is called once for each received event BatteryStateChanged or reliability
        print(
            f" >>> Event received (BatteryStateChanged or reliability): {event.message.name} - {event.args}"
        )

    # Sequence of event AND THEN
    # If ever you use an expectation as a group of command messages to listen to, it
    # won't send any message
    # For example, "FlyingStateChanged(state="landed") >> TakeOff()" won't make the drone
    # take off filled in a listener
    @olympe.listen_event(
        BatteryStateChanged(_policy="wait")
        # Messages don't take implicit `None` parameters
        # Thus every argument should be provided
        >> reliability(is_charge_level_reliable=1, _policy="wait")
    )
    def onBatteryStateChangedSequence(self, event, scheduler):
        # This method is called once for each completed sequence of event
        print(
            f" >>> Events sequence received (BatteryStateChanged >> reliability): {event.message.name} - {event.args}"
        )

    # The `default` listener method is only called when no other method
    # matched the event message The `olympe.listen_event` decorator usage
    # is optional for the default method, but you can use it to further
    # restrict the event messages handled by this method or to limit the
    # maximum size of it associated event queue (remember that the oldest
    # events are dropped silently when the event queue is full).
    @olympe.listen_event(queue_size=100)
    def default(self, event, scheduler):
        # Generates too many log, so we inhibit this logging in this example
        # The print is here to show that this default event is still working, without
        # shadowing the previous logs that interest us the most
        print(" >>> ")


def listener():
    drone = olympe.Drone(DRONE_IP)

    # Explicit subscription
    logsync_event_listener = LogsyncListener()
    assert logsync_event_listener is not None
    logsync_event_listener.subscribe()

    assert drone.connect()

    sleep(30)
    assert logsync_event_listener.listened
    logsync_event_listener.unsubscribe()

    # You can also subscribe/unsubscribe automatically using a with statement
    with StatesListener(drone) as states_listener:
        sleep(10)

    assert drone.disconnect()


if __name__ == "__main__":
    listener()
