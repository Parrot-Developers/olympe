import os
import signal
import threading

import olympe

from olympe.controllers.mixins.cellular import CellularAutoconfigureFailure
from olympe.enums.network import LinkType, LinkStatus
from olympe.messages import network
from olympe.messages.drone_manager import connection_state


olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")

APC_TOKEN = "DUMMY"


stop_event = threading.Event()


def handle_sigint(signum, frame):
    print("\nCtrl+C called")
    stop_event.set()


def test_cellular_explicite():
    """
    This script allows to pair and configue explicitly the cellular of the SkyController
    to a Drone, as long as they're previously connected together in wifi.
    """

    global APC_TOKEN

    print(" *** Test of explicite cellular pairing")

    # Create a SkyController instance
    skyctrl = olympe.SkyController(SKYCTRL_IP)
    # Connect to the SkyController
    assert skyctrl.connect()
    print("SkyController connected")

    # Wait for the SkyController and the drone to be connected together
    assert skyctrl(connection_state(state="connected")).wait(_timeout=15).success()
    print("Drone connected")

    # Wait for the SkyController to receive a status for a cellular-type link
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status before pairing
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    # Pair the SkyController and the Drone in cellular with a custom anonymous user APC
    # token
    print("Cellular pairing of the SkyController and the Drone")
    token = skyctrl.cellular.pair()
    assert token is not None

    print("Connect cellular using the new user APC token")
    skyctrl.cellular.configure(token)

    print("Waiting for cellular link to be running")

    # Wait for cellular link status to update to Link Status.running
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[
                    network.LinksStatus.LinkInfo(
                        type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running
                    )
                ]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    print(f"    Cellular user APC token: {skyctrl.cellular.user_apc_token}")

    signal.signal(signal.SIGINT, handle_sigint)
    print("Waiting for ctrl+c to disconnect")
    stop_event.wait()

    # Remember APC token for `test_cellular_giving_token`
    APC_TOKEN = skyctrl.cellular.user_apc_token

    # Disconnect the SkyController
    assert skyctrl.disconnect()
    skyctrl.destroy()


def test_cellular_giving_token():
    """
    This script allows to configure automatically the SkyController cellular with a user
    APC token already paired the drone.
    """

    global APC_TOKEN

    print(" *** Test of cellular connection with a known token")

    # Create a SkyController instance
    skyctrl = olympe.SkyController(SKYCTRL_IP, user_apc_token=APC_TOKEN)
    # Subscribe to the cellular autoconfigure failure event
    skyctrl.subscribe(_on_autoconfigure_failure, CellularAutoconfigureFailure())

    # Connect to the SkyController
    assert skyctrl.connect()
    print("SkyController connected")

    # Wait for the SkyController and the drone to be connected together
    assert skyctrl(connection_state(state="connected")).wait(_timeout=15).success()
    print("Drone connected")

    # Wait for the SkyController to receive a status for a cellular-type link
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status before pairing
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    # Wait for cellular link status pass to Link Status.running
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[
                    network.LinksStatus.LinkInfo(
                        type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running
                    )
                ]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )

    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    signal.signal(signal.SIGINT, handle_sigint)
    print("Waiting for ctrl+c to disconnect")
    stop_event.wait()

    # Disconnect the SkyController
    assert skyctrl.disconnect()
    skyctrl.destroy()


def test_cellular_auto():
    """
    This script allows to pair automatically the SkyController cellular and the Drone,
    connected in wifi, with a new user APC token generated, after the wifi connection
    and to configue the cellular to used this new user APC token generated.
    """

    print(" *** Test of automatic cellular pairing and configuration")

    # Create a SkyController instance
    skyctrl = olympe.SkyController(SKYCTRL_IP, cellular_autoconfigure=True)
    # Subscribe to the cellular autoconfigure failure event
    skyctrl.subscribe(_on_autoconfigure_failure, CellularAutoconfigureFailure())

    # Connect to SkyController
    assert skyctrl.connect()
    print("SkyController connected")

    # Wait for the SkyController and the drone to be connected together
    skyctrl(connection_state(state="connected")).wait(_timeout=15).success()
    print("Drone connected")

    # Wait for the SkyController to receive a status for a cellular-type link
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[network.LinksStatus.LinkInfo(type=LinkType.LINK_TYPE_CELLULAR)]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status before pairing
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    print("Waiting for cellular link to be running")

    # Wait for cellular link status pass to Link Status.running
    skyctrl(
        network.Event.State(
            links_status=network.LinksStatus(
                links=[
                    network.LinksStatus.LinkInfo(
                        type=LinkType.LINK_TYPE_CELLULAR, status=LinkStatus.running
                    )
                ]
            )
        )
    ).wait(_timeout=5)
    # Get the cellular link status
    links = skyctrl.get_state(network.Event.State)["links_status"]["links"]
    cellular_link = next(
        filter(lambda link: link["type"] == LinkType.LINK_TYPE_CELLULAR, links), None
    )
    # Because we've waited for the cellular link, it is certain that there is a link state
    # of type LinkType.LINK_TYPE_CELLULAR
    assert cellular_link is not None
    print(f"    Cellular link status: {cellular_link['status']}")

    signal.signal(signal.SIGINT, handle_sigint)
    print("Waiting for ctrl+c to disconnect")
    stop_event.wait()

    # Disconnect the SkyController
    assert skyctrl.disconnect()
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
