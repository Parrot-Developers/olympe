from logging import getLogger
from os import environ
from pprint import pformat

import olympe
import olympe.log

from olympe.controller import Disconnected
from olympe.messages import mission
from olympe.messages.common.Common import Reboot


DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")
HELLO_MISSION_URL = environ.get(
    "HELLO_MISSION_URL", "com.parrot.missions.samples.hello.tar.gz"
)

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})
logger = getLogger("olympe")


def test_hello_mission():
    drone = olympe.Drone(DRONE_IP)
    mission_controller = drone.mission
    with mission_controller.from_path(HELLO_MISSION_URL) as hello_mission:
        # Mission messages modules are now available from the Python path
        from olympe.airsdk.messages.parrot.missions.samples.hello.Command import (
            Say,
            Hold,
        )
        from olympe.airsdk.messages.parrot.missions.samples.hello.Event import count

        # Mission messages are also available under the Mission.messages dictionary
        assert (
            hello_mission.messages["parrot.missions.samples.hello"].Command.Say is Say
        )
        assert (
            hello_mission.messages["parrot.missions.samples.hello"].Event.count is count
        )

        # Connect to the drone, install the 'hello' mission and reboot the drone
        assert drone.connect()
        assert hello_mission.install(allow_overwrite=True, is_default=True)
        assert drone(Reboot() >> Disconnected()).wait(_timeout=10)

        # Reconnect to the drone after reboot, and wait for the mission to be ready to
        # communicate with the drone
        # Keep a high timeout so that Olympe reconnection benefits from DiscoveryNet (HTTP)
        # and do not switch to DiscoveryNetRaw (UDP). In case of DiscoveryNetRaw, arsdk
        # C-code can't parse handshake's JSON (arsdkctrl_net issue with drone's firmware).
        # Thus, that may be interesting to give some time up to DiscoveryNet, not to fall
        # back to DiscoveryNetRaw system, that doesn't work, due to Arsdk parsing unability.
        # After a mission installation and reboot, HTTP server takes time to boot.
        # In controller.py, the timeout dedicated to DiscoveryNet is half the global timeout
        # filled in here : timeout = (deadline - time.time()) / 2
        assert drone.connect(timeout=300)
        assert hello_mission.wait_ready(timeout=10)

        # Wait for the current mission to be activated
        mission_activated_expectation = drone(mission.state(state="active")).wait(
            _timeout=10
        )
        assert mission_activated_expectation, mission_activated_expectation.explain()

        logger.info(" ----- Mission list: " + pformat(drone.mission.list_remote()))

        # Make the drone say 'Hello' (nod its gimbal)
        assert drone(Say()).wait(_timeout=10)

        # If the drone has already nodded, that count is taken on the fly for the next nod
        count_expectation = drone(count(_policy="wait")).wait(_timeout=10)
        assert count_expectation, count_expectation.explain()
        # Catch that value
        counter = count_expectation.received_events().last().args["value"]
        logger.info(f" ----- The drone has already performed {counter} nods")

        # Wait for 3 nods of the drone's gimbal
        count_expectation = drone(count(value=counter + 3)).wait(_timeout=30)
        assert count_expectation, count_expectation.explain()
        # Check the drone has performed the correct number of nods
        nods_total = count_expectation.received_events().last().args["value"]
        counter += 3
        assert counter == nods_total
        logger.info(f" ----- The drone has performed {counter} nods so far")

        # Hold the nodding
        assert drone(Hold()).wait(_timeout=10)
        # Check that the drone doesn't nod anymore
        expectation_no_nod_left = drone(count(_policy="wait")).wait(_timeout=10)
        assert not expectation_no_nod_left, expectation_no_nod_left.explain()
        # Check counter remains unchanged
        assert drone(count(value=counter, _policy="check"))
        logger.info(f" ----- The drone has performed {counter} nods on the whole")
        # Disconnect
        assert drone.disconnect()


if __name__ == "__main__":
    test_hello_mission()
