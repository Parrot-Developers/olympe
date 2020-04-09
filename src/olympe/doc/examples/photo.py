from olympe.messages.camera import (
    set_camera_mode,
    set_photo_mode,
    take_photo,
    photo_progress,
)
from olympe.media import download_media, indexing_state
from logging import getLogger
import olympe
import re
import tempfile
import xml.etree.ElementTree as ET


olympe.log.update_config({
    "loggers": {
        "olympe": {"level": "INFO"},
        "photo_example": {
            "level": "INFO",
            "handlers": ["console"],
        },
    }
})

logger = getLogger("photo_example")

# Drone IP
DRONE_IP = "192.168.42.1"

XMP_TAGS_OF_INTEREST = (
    "CameraRollDegree",
    "CameraPitchDegree",
    "CameraYawDegree",
    "CaptureTsUs",
    # NOTE: GPS metadata is only present if the drone has a GPS fix
    # (i.e. they won't be present indoor)
    "GPSLatitude",
    "GPSLongitude",
    "GPSAltitude",
)


def take_photo_burst(drone):
    # take a photo burst and get the associated media_id
    photo_saved = drone(photo_progress(result="photo_saved", _policy="wait"))
    drone(take_photo(cam_id=0)).wait()
    if not photo_saved.wait(_timeout=30).success():
        assert False, "take_photo timedout"
    media_id = photo_saved.received_events().last().args["media_id"]
    # download the photos associated with this media id
    drone.media.download_dir = tempfile.mkdtemp(prefix="olympe_photo_example")
    logger.info(
        "Download photo burst resources for media_id: {} in {}".format(
            media_id,
            drone.media.download_dir,
        )
    )
    media_download = drone(download_media(media_id, integrity_check=True))
    # Iterate over the downloaded media on the fly
    resources = media_download.as_completed(timeout=60)
    resource_count = 0
    for resource in resources:
        logger.warning("Resource: {}".format(resource.resource_id))
        if not resource.success():
            logger.info("Failed to download {}".format(resource.resource_id))
            continue
        # parse the xmp metadata
        with open(resource.download_path, "rb") as image_file:
            image_data = image_file.read()
            image_xmp_start = image_data.find(b"<x:xmpmeta")
            image_xmp_end = image_data.find(b"</x:xmpmeta")
            image_xmp = ET.fromstring(image_data[image_xmp_start: image_xmp_end + 12])
            for image_meta in image_xmp[0][0]:
                xmp_tag = re.sub(r"{[^}]*}", "", image_meta.tag)
                xmp_value = image_meta.text
                # only print the XMP tags we are interested in
                if xmp_tag in XMP_TAGS_OF_INTEREST:
                    logger.info("{} {} {}".format(resource.resource_id, xmp_tag, xmp_value))
        resource_count += 1
    logger.info("{} media resource downloaded".format(resource_count))
    assert resource_count == 14, "resource count == {} != 14".format(resource_count)
    assert media_download.success(), "Photo burst media download"


def setup_photo_burst_mode(drone):
    drone(set_camera_mode(cam_id=0, value="photo")).wait()
    # For the file_format: jpeg is the only available option
    # dng is not supported in burst mode
    assert drone(
        set_photo_mode(
            cam_id=0,
            mode="burst",
            format="rectilinear",
            file_format="jpeg",
            burst="burst_14_over_1s",
            bracketing="preset_1ev",
            capture_interval=0.0,
        )
    ).wait().success()


def main(drone):
    drone.connect()
    assert drone.media(
        indexing_state(state="indexed")
    ).wait(_timeout=60).success()
    setup_photo_burst_mode(drone)
    take_photo_burst(drone)
    drone.disconnect()


if __name__ == "__main__":
    with olympe.Drone(DRONE_IP) as drone:
        main(drone)
