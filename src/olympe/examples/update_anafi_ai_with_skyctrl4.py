import os
import requests
import time

import olympe
import olympe.log

from olympe.messages import drone_manager

from logging import getLogger

logger = getLogger(__name__)
olympe.log.update_config(
    {"loggers": {__name__: {"handlers": ["console"], "level": "INFO"}}}
)


UPDATE_FILE_URL = "https://firmware.parrot.com/Versions/anafi2/classic/%23latest/images/anafi2-classic-update-dev-osonly.tar.gz"

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")


def upload_update_file(controller, url):
    local_filename = url.split("/")[-1]
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        # Store data from the downloaded file as bytes
        with open(local_filename, "wb") as fd:
            for chunk in response.iter_content(chunk_size=4096):
                fd.write(chunk)
    # Send those bytes to the upload webmodule
    with open(local_filename, "rb") as fd:
        assert controller.update.upload(fd)


def get_current_version(controller):
    version = None
    try:
        version = controller.update.get_current_version()
    except ValueError:
        logger.error("Device current version cannot be retrieved. End of test.")
        controller.disconnect()
        raise SystemExit
    logger.info(f"Device current version: {version}")


def main():
    # Connection to SkyController
    skyctrl = olympe.SkyController4(SKYCTRL_IP)
    skyctrl.connect()

    # Retrieve drone serial to make sure SkyController reconnects to the proper drone
    drone_serial = skyctrl.get_state(drone_manager.connection_state)["serial"]

    # Upload update file
    upload_update_file(skyctrl, UPDATE_FILE_URL)

    ## Test reconnection to the drone through SkyController is successful
    # Disconnect then reconnect to the drone through SkyController
    connection = skyctrl(
        drone_manager.connection_state(state="disconnecting")
        >> drone_manager.connection_state(state="connected", serial=drone_serial)
    ).wait(_timeout=200)
    assert connection.success()

    time.sleep(5)

    # Get the device current version
    get_current_version(skyctrl)

    # Disconnection from SkyController
    skyctrl.disconnect()


if __name__ == "__main__":
    main()
