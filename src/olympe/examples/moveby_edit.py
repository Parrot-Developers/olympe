import os

import olympe

from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing
from olympe.messages.ardrone3.PilotingEvent import moveByEnd
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


def moveby_edit():
    drone = olympe.Drone(DRONE_IP)
    assert drone.connect()

    # Ask for take off and make sure the drone is in 'hovering' state, i.e. his take
    # off has been completed and it now waits in the air
    assert (
        drone(TakeOff() >> FlyingStateChanged(state="hovering"))
        .wait(_timeout=20)
        .success()
    )

    expectation_moveby = drone(
        # _no_expect argument enables to not wait for the automatic and already-registered
        # response 'moveByEnd' sent after firing a moveBy command. This information is
        # retrievable in the ARSDK xml and protobuf messages documentation. Receiving a
        # 'moveByEnd' response when expecting on a 'moveBy' command, would result in a
        # fail of the 'moveBy' expectation, because we expect a 'moveBy' response, not a
        # 'moveByEnd', when firing a 'moveBy' command. Nonetheless, once the moveByEnd
        # motion has completed, a 'moveByEnd' event is sent from the drone to the controller,
        # with the real displacement executed, and the error state, which we expect it to
        # be "ok". This 'moveByEnd' is legitimately waited for
        moveBy(2, 0, 0, 0, _no_expect=True)
        >> FlyingStateChanged(state="flying")
        >> moveByEnd(error="ok")
        >> FlyingStateChanged(state="hovering")
    ).wait(_timeout=20)
    assert expectation_moveby.success(), expectation_moveby.explain()

    # Ask for a landing and make sure the drone is indeed landed eventually
    assert drone(Landing() >> FlyingStateChanged(state="landed")).wait().success()

    assert drone.disconnect()


if __name__ == "__main__":
    moveby_edit()
