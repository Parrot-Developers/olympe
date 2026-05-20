import requests

import olympe
import olympe.log

from logging import getLogger

logger = getLogger(__name__)
olympe.log.update_config(
    {"loggers": {__name__: {"handlers": ["console"], "level": "INFO"}}}
)

DRONE_IP = "192.168.43.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()

    # Get the device current version
    version = None
    try:
        version = drone.update.get_current_version()
    except ValueError:
        logger.error("Device current version cannot be retrieved. End of test.")
        drone.disconnect()
        raise SystemExit
    logger.info(f"Device current version: {version}")

    # Download the update file
    url = "https://firmware.parrot.com/Versions/anafi2/classic/%23latest/images/anafi2-classic-update-dev-osonly.tar.gz"
    local_filename = url.split("/")[-1]
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        # Write the update file to disk
        with open(local_filename, "wb") as fd:
            for chunk in response.iter_content(chunk_size=4096):
                fd.write(chunk)
    # Upload the update file
    with open(local_filename, "rb") as fd:
        assert drone.update.upload(fd)

    drone.disconnect()
