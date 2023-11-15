from olympe.messages.camera2 import Command, Config, Event
from olympe.media import download_media, indexing_state, media_created
from logging import getLogger
import olympe
import os
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
DRONE_IP = os.environ.get("DRONE_IP", "192.168.42.1")
DRONE_MEDIA_PORT = os.environ.get("DRONE_MEDIA_PORT", "80")

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
    media_state = drone(media_created(_timeout=3.0))
    photo_capture = drone(
        Event.Photo(
            type="stop",
            stop_reason="capture_done",
            _timeout=3.0,
            _policy="wait",
        )
        & Command.StartPhoto(camera_id=0)
    )
    if not photo_capture.wait(_timeout=30).success():
        assert False, "take_photo timedout"
    media_state.wait()
    assert media_state, media_state.explain()
    media_info = media_state.received_events().last()
    media_id = media_info.media_id
    photo_count = len(drone.media.resource_info(media_id=media_id))
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
    resources = media_download.as_completed(expected_count=photo_count, timeout=60)
    resource_count = 0
    for resource in resources:
        logger.info(f"Resource: {resource.resource_id}")
        if not resource.success():
            logger.error(f"Failed to download {resource.resource_id}")
            continue
        resource_count += 1
        # parse the xmp metadata
        with open(resource.download_path, "rb") as image_file:
            image_data = image_file.read()
            image_xmp_start = image_data.find(b"<x:xmpmeta")
            image_xmp_end = image_data.find(b"</x:xmpmeta")
            if image_xmp_start < 0 or image_xmp_end < 0:
                logger.error(
                    f"Failed to find XMP photo metadata {resource.resource_id}"
                )
                continue
            image_xmp = ET.fromstring(image_data[image_xmp_start: image_xmp_end + 12])
            for image_meta in image_xmp[0][0]:
                xmp_tag = re.sub(r"{[^}]*}", "", image_meta.tag)
                xmp_value = image_meta.text
                # only print the XMP tags we are interested in
                if xmp_tag in XMP_TAGS_OF_INTEREST:
                    logger.info(f"{resource.resource_id} {xmp_tag} {xmp_value}")
    logger.info(f"{resource_count} media resource downloaded")
    assert resource_count == 10, f"resource count == {resource_count} != 10"
    assert media_download.wait(1.).success(), "Photo burst media download"


def setup_photo_burst_mode(drone):
    # For the file_format: jpeg is the only available option
    # dng is not supported in burst mode
    drone(
        Command.Configure(
            camera_id=0,
            config=Config(
                camera_mode="photo",
                photo_mode="burst",
                photo_file_format="jpeg",
                photo_burst_value="10_over_1s",
                photo_dynamic_range="standard",
                photo_resolution="12_mega_pixels",
                photo_format=("rectilinear"),
                photo_signature="none"
            ),
            _timeout=3.0,
        )
    ).wait()


def main(drone):
    drone.connect()
    assert drone.media(
        indexing_state(state="indexed")
    ).wait(_timeout=60).success()
    setup_photo_burst_mode(drone)
    take_photo_burst(drone)
    drone.disconnect()


def test_photo():
    with olympe.Drone(DRONE_IP, media_port=DRONE_MEDIA_PORT) as drone:
        main(drone)


if __name__ == "__main__":
    test_photo()
