import olympe
import os
import subprocess
import tempfile

import olympe.features.media as media_api

from olympe.messages.camera2 import Command, Config, Event
from olympe.messages.mediastore import counters

from logging import getLogger

olympe.log.update_config(
    {
        "loggers": {
            "olympe": {"level": "INFO", "handlers": ["console"]},
            "urllib3": {"level": "DEBUG", "handlers": ["console"]},
        }
    }
)
logger = getLogger("olympe.example.media_ukr")

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")
DRONE_HTTP_PORT = int(os.environ.get("DRONE_HTTP_PORT", "80"))


class MediaEventListener(olympe.EventListener):
    def __init__(self, media):
        super().__init__(media, timeout=60)
        self._media = media
        self._media_id = []
        self._downloaded_resources = []
        self.remote_resource_count = 0
        self.local_resource_count = 0

    @olympe.listen_event(media_api.media_created())
    def onMediaCreated(self, event, scheduler):
        # `media_created` event is sent by the drone when a photo has been taken and its
        # resource is available for download.
        self._media_id.append(event.media_id)
        logger.info(f"LISTENER: media_created '{event.media_id}'")
        self._media(
            media_api.DownloadThumbnailExpectation(event.media_id)
            & media_api.DownloadMediaExpectation(event.media_id, integrity_check=True)
        )

    @olympe.listen_event(media_api.media_removed())
    def onMediaRemoved(self, event, scheduler):
        logger.info(f"LISTENER: media_removed - id '{event.media_id}'")

    @olympe.listen_event(media_api.resource_downloaded())
    def onResourceDownloaded(self, event, scheduler):
        # Thumbnail resources are ignored. Resources are only downloaded if they're
        # original content
        if event.is_thumbnail:
            return
        logger.info(
            f"LISTENER: resource_downloaded - id '{event.resource_id}', data '{event.data['download_path']}'"
        )
        self._downloaded_resources.append(
            self._media.resource_info(resource_id=event.resource_id)
        )

    @olympe.listen_event()
    def default(self, event, scheduler):
        if isinstance(event, media_api.MediaEvent):
            logger.info(f"LISTENER: other MediaEvent received: '{event}'")

    def _md5_checksum(self):
        # Check that each downloaded media is not corrupted and its integrity is guaranteed
        for resource in self._downloaded_resources:
            # Run the md5sum command on each resource path
            check = subprocess.run(
                # Call "md5summ *.md5"
                ["md5sum", resource.download_md5_path],
                # Redirect stdout to make it readable from Python
                stdout=subprocess.PIPE,
                # Detail which folder to run the command into
                # If the command is run from another folder, md5sum may not find the file
                # to check, because it expects file.md5 to be in the same folder than file
                cwd=os.path.dirname(resource.download_path),
            )
            # Get and print result
            stdout = check.stdout.decode().strip()
            logger.info(f"SANITY CHECK 1/2: Integrity md5 checksum check: {stdout}")
            # Return succes or error code
            return check.returncode == 0

    def _quantitative_consistency_check(self):
        # Check that as many resources have been localy downloaded than present remotely
        # on the drone
        # Sum the number of resources of the remote media
        self.remote_resource_count = sum(
            map(
                lambda id_: len(self._media.resource_info(media_id=id_)), self._media_id
            )
        )
        # Count the numer of resources of local media
        self.local_resource_count = len(self._downloaded_resources)
        # Return the comparison between expected remote media and downloaded local media
        return self.local_resource_count == self.remote_resource_count

    def _cancel_media_on_drone(self):
        # Enables to free storage memory on the drone once the sanity checks (md5
        # checksum and quantitative coherency) are done
        for media_id in self._media_id:
            delete = media_api.delete_media(media_id, _timeout=10)
            if not self._media(delete).wait().success():
                logger.error(f"Failed to delete media '{media_id}': {delete.explain()}")

    def unsubscribe(self):
        # Wait for all local downloads to end
        self._media.wait_for_pending_downloads()
        # Sanity check 1/2: md5 checksum
        # The integrity check has already been performed internally by Olympe
        # But because here is an example, it is demonstrated in md5_checksum how to
        # perform the media integrity check using the "md5summ --check *.md5" command
        if not self._md5_checksum():
            logger.error("Sanity check 1/2: md5 checksum failed")
            # Unsubscription must be done whatsoever
            super().unsubscribe()
            return
        # It would be unecessary to delete media if their integrity are not guaranteed
        # Thus the second sanity check is only done if the first one succeeded
        # Sanity check 2/2: local downloaded resources equal the number of remote resources
        if not self._quantitative_consistency_check():
            logger.error(
                "Sanity check 2/2: quantitative consistency failed"
                f"\n{self.local_resource_count} resources have been downloaded instead of {self.remote_resource_count} remote"
            )
            # Unsubscription must be done whatsoever
            super().unsubscribe()
            return
        # It would be unecessary to delete media if the download is not complete
        # Thus the media deletion is only done if the second sanity check succeeded
        # The remote media can now be safely deleted
        self._cancel_media_on_drone()
        # Listeners cancellation and deletion, unsubscription of the media monitoring
        # and final cleanup
        super().unsubscribe()


def configure_photo_mode(drone):
    drone(
        Command.Configure(
            camera_id=1,
            config=Config(
                camera_mode="photo",
                photo_mode="single",
                photo_format="rectilinear",
                photo_file_format="jpeg",
                photo_dynamic_range="standard",
                exposure_mode="automatic",
                white_balance_mode="automatic",
                ev_compensation="0_00",
            ),
            _timeout=3.0,
        )
    ).wait()


def take_photo(drone, media=None):
    # Process case 2 test_media_ukr_internal_media
    if media is None:
        assert not drone.is_media_custom
        media = drone.media
    # Store downloaded photo in a temporary folder
    media.download_dir = tempfile.mkdtemp(prefix="olympe_example_media_ukr_")
    # Activate automatic folder integrity check (md5sum) after download
    # It is what is detailed as an example in _md5_checksum, already done internally by default
    media.integrity_check = True
    # Configure drone photo mode
    configure_photo_mode(drone)
    # Media indexing
    logger.info("Waiting for media resources to be indexing...")
    # Drone media must scan his media database (SD card) before any download
    assert isinstance(media, media_api.Media)
    if not media(media_api.indexing_state(state="indexed")).wait(_timeout=60).success():
        logger.error("Media resources indexing timed out")
        return
    logger.info("Media resources successfully indexed")
    with MediaEventListener(media) as media_listener:
        # Subscription the events listened to above (media_created, resource_created, ...)
        # Wait for a new media to be created on the drone storage
        # No photo has been taken yet, but the listener will monitor the media_created
        # Event from now. If this line is removed, the photo would be shot in perfect
        # unawareness, and the event media_created would be missed, so it won't be sure
        # the media is indeed created
        media_state = drone(media_api.media_created(_timeout=3.0))
        initial_counters = drone.get_state(counters)
        # Take a photo and verify the capture is done. This command will generate the
        # media_created Event
        photo_capture = drone(
            counters(
                video_media_count=initial_counters["video_media_count"],
                photo_media_count=initial_counters["photo_media_count"],
                video_resource_count=initial_counters["video_resource_count"],
                photo_resource_count=initial_counters["photo_resource_count"],
                _policy="check",
            )
            >> Command.StartPhoto(camera_id=1)
            & Event.Photo(
                camera_id=1,
                type="stop",
                stop_reason="capture_done",
                _timeout=3.0,
                _policy="wait",
            )
            >> counters(
                video_media_count=initial_counters["video_media_count"],
                photo_media_count=initial_counters["photo_media_count"] + 1,
                video_resource_count=initial_counters["video_resource_count"],
                photo_resource_count=initial_counters["photo_resource_count"] + 1,
                _timeout=5,
            )
        ).wait(_timeout=30)
        assert photo_capture.success(), photo_capture.explain()
        # Because the media creation is being monitored, it makes sense to wait for it
        # within 3 seconds delay for the drone to store the new media and signals it
        media_state.wait(_timeout=3.0)
        # Check the success of the media creation
        # assert media_state.success(), media_state.explain()
    # unsubscribe() is called when exiting the with block
    # Check that the drone had at least 1 media to download
    assert media_listener.remote_resource_count > 0, "remote_resource_count == 0"
    # Check that all the remote media have been downloaded
    assert (
        media_listener.remote_resource_count == media_listener.local_resource_count
    ), f"remote_resource_count (={media_listener.remote_resource_count}) != local_resource_count (={media_listener.local_resource_count})"


def test_media_ukr_custom_media():
    # Case 1: olympe.Media instantiated class usage as a standalone API
    # Media autoconnection is disabled (drone.media is not used)
    # In this case the media API is manually instantiated and connected to
    # Media instantiation
    media_instance = media_api.Media(
        name="example_custom_media",
        hostname=f"{DRONE_IP}:{DRONE_HTTP_PORT}",
    )
    with olympe.Drone(
        DRONE_IP,
        http_port=DRONE_HTTP_PORT,
        name="example_custom_media",
        media=media_instance,
    ) as drone:
        # Drone connection
        assert drone.connect()
        # Media connection
        assert media_instance.connect()
        # Photo script
        take_photo(drone, media_instance)
        # Media disconnection
        assert media_instance.disconnect()
        # Drone disconnection
        assert drone.disconnect()


def test_media_ukr_internal_media():
    # Case 2 : olympe.Media instantiated class internally by Olympe
    # Media autoconnection enabled (by default) (drone.media property)
    # In this case the connection to the remote media API endpoint is automatically
    # handled by the Olympe controller class.
    with olympe.Drone(
        DRONE_IP,
        http_port=DRONE_HTTP_PORT,
        name="example_internal_media",
    ) as drone:
        # Drone connection
        assert drone.connect()
        # Photo script
        take_photo(drone)
        # Drone disconnection
        assert drone.disconnect()


if __name__ == "__main__":
    test_media_ukr_custom_media()
    test_media_ukr_internal_media()
