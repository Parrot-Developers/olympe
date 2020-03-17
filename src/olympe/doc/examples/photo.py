from olympe.messages.camera import (
    set_camera_mode,
    set_photo_mode,
    take_photo,
    photo_progress,
)
import olympe
import re
import tempfile
import xml.etree.ElementTree as ET


olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})


# Drone IP
ANAFI_IP = "192.168.42.1"

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
        return
    media_id = photo_saved.received_events().last().args["media_id"]
    print(
        "Photo burst resources: {}".format(
            ", ".join(
                r.resource_id for r in drone.media.resource_info(media_id=media_id)
            )
        )
    )

    # download the photos associated with this media id
    download_dir = tempfile.mkdtemp(prefix="olympe_photo_example")
    resources = drone.media.download_media(download_dir, media_id, integrity_check=True)
    for status, resource_path, resource in resources:
        if not status:
            print("Failed to download {}".format(resource.resource_id))
            continue
        # parse the xmp metadata
        with open(resource_path, "rb") as image_file:
            image_data = image_file.read()
            image_xmp_start = image_data.find(b"<x:xmpmeta")
            image_xmp_end = image_data.find(b"</x:xmpmeta")
            image_xmp = ET.fromstring(image_data[image_xmp_start : image_xmp_end + 12])
            for image_meta in image_xmp[0][0]:
                xmp_tag = re.sub(r"{[^}]*}", "", image_meta.tag)
                xmp_value = image_meta.text
                # only print the XMP tags we are interested in
                if xmp_tag in XMP_TAGS_OF_INTEREST:
                    print(resource.resource_id, xmp_tag, xmp_value)


def setup_photo_burst_mode(drone):
    drone(set_camera_mode(cam_id=0, value="photo")).wait()
    # For the file_format: jpeg is the only available option
    # dng is not supported in burst mode
    drone(
        set_photo_mode(
            cam_id=0,
            mode="burst",
            format="rectilinear",
            file_format="jpeg",
            burst="burst_14_over_1s",
            bracketing="preset_1ev",
            capture_interval=0.0,
        )
    ).wait()


def main(drone):
    drone.connect()
    setup_photo_burst_mode(drone)
    take_photo_burst(drone)


if __name__ == "__main__":
    with olympe.Drone(ANAFI_IP) as drone:
        main(drone)
