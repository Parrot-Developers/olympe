from logging import getLogger
from sys import exit
from os import environ

import olympe

import olympe.messaging.expectations

from olympe.messages import camera2

olympe.log.update_config(
    {
        "loggers": {
            "olympe": {"level": "INFO"},
            "anafi_ukr_active_camera": {
                "level": "INFO",
                "handlers": ["console"],
            },
        }
    }
)

logger = getLogger("anafi_ukr_active_camera")


SKYCTRL_IP = environ.get("DRONE_IP", "192.168.53.1")


def active_camera_anafi_ukr():
    controller = olympe.SkyControllerUKR(SKYCTRL_IP)
    assert controller.connect()
    # camera2.Command.ListCameras is sent once the SkyController is connected. Its event response
    # camera2.Event.CameraList is listened to, and a camera2.Command.GetState is sent
    # for each camera listed.
    # To monitor that a Command.ListCameras has been well sent, we create an expectation
    # on its supposed response event, Event.CameraList
    # "check_wait" policy means that we check if such an event has been already received
    # or we wait for one. Because to that point, we can't be sure the drone is already
    # connected to the SkyController (and is probably not) nor that the command has been
    # received, it is important to choose that policy, and check on past received events
    # as well as wait for future events to be received.
    expectation_camera_list = controller(camera2.Event.CameraList(_policy="check_wait"))
    # Wait a little to make sure the expectation will meet success
    expectation_camera_list.wait(1.0)
    # To that point, ListCameras has well been sent, and CameraList has well been received
    if not expectation_camera_list.success():
        logger.error(f"timeout on expectation {expectation_camera_list}")
        exit(1)

    # Get the last event matching the expectation. Which means it's gonna be an Event.CameraList,
    # which has an argument "cameras", that is a map whose keys are the drone's camera ids
    camera_ids = expectation_camera_list.received_events().last().args["cameras"].keys()
    # Initialize a new expectation to monitor the responses Event.State to the GetState
    # commands
    expectation_camera_states = olympe.messaging.expectations.SuccessExpectation()
    for camera_id in camera_ids:
        # Check that a Command.GetState is sent to each camera, by monitoring each response
        # Event.State
        expectation_camera_states = expectation_camera_states & camera2.Event.State(
            camera_id=camera_id
        )
    # Wait a little to make sure the expectation will meet success
    controller(expectation_camera_states).wait(15.0)
    # To that point, all Command.GetState have been well sent and Event.State have been
    # well received
    if not expectation_camera_states.success():
        logger.error("timeout when expecting camera states")
        exit(1)

    # Retrieve the current active camera
    for camera in controller.get_state(camera2.Event.State).values():
        if camera["active"]:
            logger.info(f"Current active camera is {camera['camera_id']}")

    controller.disconnect()


if __name__ == "__main__":
    active_camera_anafi_ukr()
