import os

import olympe
from olympe.messages.drone_manager import connection_state
from olympe.messages import network
from olympe.enums.network import LinkType, LinkStatus


olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")


def test_cellular():
    """
    This script allows to pair in cellular a SkyController and a Drone previously paired together in wifi.
    """

    print("Test of cellular pairing")

    # Create a skycontroller
    skyctrl = olympe.SkyController(SKYCTRL_IP)
    # Connect to skycontroller
    assert skyctrl.connect()
    print("- SkyController connected")

    # Wait for the skycontroller and the drone to be connected
    skyctrl(connection_state(state="connected")).wait()
    print("- Drone connected")

    # Get the cellular link status before pairing
    assert skyctrl(network.Command.GetState() >> network.Event.State()).wait().success()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    print(f"    cellular link status: {cellular_link['status']}")
    # Should be different from LinkStatus.running

    # Pair the SkyController and the Drone in cellular
    print("- Cellular pairing of the SkyController and the Drone")
    assert skyctrl.cellular.pair()

    print("- Waiting for cellular link to be running")

    # Wait for cellular link status pass to Link Status.running
    while cellular_link["status"] != LinkStatus.running:
        skyctrl(network.Event.State(_policy="wait"))
        links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
        cellular_link = next(
            filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links),
            None,
        )

    # Log cellular link status
    print(f"    cellular link status: {cellular_link['status']}")

    # Disconnect the skycontroller
    skyctrl.disconnect()
    print("- SkyController disconnected")

if __name__ == "__main__":
    test_cellular()
