from logging import getLogger
from requests import get
from requests.exceptions import HTTPError as requests_HTTPError
from os.path import join
from os import environ
from tempfile import TemporaryDirectory

from olympe import Drone
from olympe.log import update_config

logger = getLogger("example_update")
update_config(
    {"loggers": {"example_update": {"handlers": ["console"], "level": "INFO"}}}
)

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")
FIRMWARE_URL = "replace-by-firmware-path"


def update():
    drone = Drone(DRONE_IP)
    assert drone.connect()

    # Get the device current version
    try:
        version = drone.update.get_current_version()
    except ValueError:
        logger.error("Device current version cannot be retrieved. End of test")
        assert drone.disconnect()
        return
    logger.info(f"Device current version: {version}")

    # Download the update file
    url = FIRMWARE_URL
    filename = url.split("/")[-1]
    with TemporaryDirectory() as tmp_dir:
        with get(url, stream=True) as get_response:
            try:
                get_response.raise_for_status()
            except requests_HTTPError:
                logger.error(f"Unable to get firmware from '{url}'. End of test")
                assert drone.update.destroy()
                assert drone.disconnect()
                return

            # Write the update file to disk
            filepath = join(tmp_dir, filename)
            with open(filepath, "wb") as fd:
                for chunk in get_response.iter_content(chunk_size=4096):
                    fd.write(chunk)

        # Upload the update file
        with open(filepath, "rb") as fd:
            assert drone.update.upload(fd) # add reboot=False if no reboot requested. Default to True
            logger.info("Firmware has correctly been updated. Rebooting...")

    assert drone.update.destroy()
    assert drone.disconnect()


if __name__ == "__main__":
    update()
