import os

import olympe
from olympe.messages.drone_manager import connection_state
from olympe.messages import network
from olympe.enums.network import LinkType, LinkStatus
from olympe.mixins.cellular import CellularAutoconfigureFailure


olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")

APC_TOKEN = "DUMMY"

def test_cellular_explicite():
    """
    This script allows to pair and to configue explicitly the cellular of the SkyController and a Drone
    previously connected together in wifi.
    """

    global APC_TOKEN

    print("Test of cellular explicite pairing")

    # Create a skycontroller
    skyctrl = olympe.SkyController(SKYCTRL_IP)
    # Connect to skycontroller
    assert skyctrl.connect()
    print("- SkyController connected")

    # Wait for the skycontroller and the drone to be connected
    skyctrl(connection_state(state="connected")).wait()
    print("- Drone connected")

    # Get the cellular link status before pairing
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    print(f"    cellular link status: {cellular_link['status']}")

    # Pair the SkyController and the Drone in cellular with a new anonymous user APC token
    print("- Cellular pairing of the SkyController and the Drone")
    token = skyctrl.cellular.pair()
    assert token is not None

    print("- Connect cellular using the new user APC token")
    skyctrl.cellular.configure(token)

    print("- Waiting for cellular link to be running")

    # Wait for cellular link status pass to Link Status.running
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )

    # Log cellular link status
    print(f"    cellular link status: {cellular_link['status']}")

    print(f"    cellular user_apc_token: {skyctrl.cellular.user_apc_token}")

    # Remember APC token for `test_cellular_giving_token`
    APC_TOKEN = skyctrl.cellular.user_apc_token

    # Disconnect the skycontroller
    skyctrl.disconnect()
    skyctrl.destroy()


def test_cellular_giving_token():
    """
    This script allows to configure automatically the SkyController cellular with a user APC token already paired the the drone.
    """

    global APC_TOKEN

    print("Test of cellular connection by giving an token")

    # Create a skycontroller
    skyctrl = olympe.SkyController(SKYCTRL_IP, user_apc_token=APC_TOKEN)
    # Subscribe to the cellular autoconfigure failure event
    skyctrl.subscribe(
        _on_autoconfigure_failure, CellularAutoconfigureFailure()
    )

    # Connect to skycontroller
    assert skyctrl.connect()
    print("- SkyController connected")

    # If the user_apc_token is valid, the drone can be connected directly in cellular
    print("- Waiting for drone connection")

    # Wait for the skycontroller and the drone to be connected
    skyctrl(connection_state(state="connected")).wait()
    print("- Drone connected")

    # Get the cellular link status before pairing
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    print(f"    cellular link status: {cellular_link['status']}")

    print("- Waiting for cellular link to be running")

    # Wait for cellular link status pass to Link Status.running
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )

    # Log cellular link status
    print(f"    cellular link status: {cellular_link['status']}")

    # Disconnect the skycontroller
    skyctrl.disconnect()
    skyctrl.destroy()


def test_cellular_auto():
    """
    This script allows to pair automatically the SkyController cellular and the Drone, connected in wifi,
    with a new user APC token generated, after the wifi connection and to configue the cellular to used this new user APC token generated.
    """

    print("Test of automatic cellular pairing and configuration")

    # Create a skycontroller
    skyctrl = olympe.SkyController(SKYCTRL_IP, cellular_autoconfigure=True)
    # Subscribe to the cellular autoconfigure failure event
    skyctrl.subscribe(
        _on_autoconfigure_failure, CellularAutoconfigureFailure()
    )

    # Connect to skycontroller
    assert skyctrl.connect()
    print("- SkyController connected")

    # Wait for the skycontroller and the drone to be connected
    skyctrl(connection_state(state="connected")).wait()
    print("- Drone connected")

    # Get the cellular link status
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    print(f"    cellular link status: {cellular_link['status']}")

    print("- Waiting for cellular link to be running")

    # Wait for cellular link status pass to Link Status.running
    skyctrl(network.Event.State(
        links_status=network.LinksStatus(links=[
            network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running)]
        )
    )).wait()
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )

    # Log cellular link status
    print(f"    cellular link status: {cellular_link['status']}")

    # Disconnect the skycontroller
    skyctrl.disconnect()
    skyctrl.destroy()

def _on_autoconfigure_failure(event, *_):
    """
    Called at auto configure failure.
    """

    print(f"    autoconfigure failure exception: {event.exception}")
    raise AssertionError

if __name__ == "__main__":
    test_cellular_explicite()
    test_cellular_giving_token()
    test_cellular_auto()
