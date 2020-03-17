import olympe
import os
import subprocess
import tempfile
from olympe.media import (
    media_created,
    resource_created,
    media_removed,
    resource_removed,
    resource_downloaded,
    indexing_state,
    delete_media,
    download_media,
    download_resource,
    download_media_thumbnail,
    download_resource_thumbnail,
    MediaEvent,
)
from olympe.messages.camera import (
    set_camera_mode,
    set_photo_mode,
    take_photo,
    photo_progress,
)
from logging import getLogger

olympe.log.update_config(
    {
        "loggers": {
            "olympe": {"level": "INFO", "handlers": ["console"]},
            "urllib3": {"level": "DEBUG", "handlers": ["console"]},
            __name__: {"level": "DEBUG", "handlers": ["console"]},
        }
    }
)

logger = getLogger(__name__)

# Drone IP
ANAFI_IP = "192.168.42.1"


class MediaEventListener(olympe.EventListener):
    def __init__(self, media):
        super().__init__(media, timeout=60)
        self._media = media
        self._media_id = []
        self._downloaded_resources = []
        self._downloading = set()

    @olympe.listen_event(media_created())
    def onMediaCreated(self, event, scheduler):
        self._media_id.append(event.media_id)
        logger.info("media_created {}".format(event.media_id))
        # When using the photo burst mode, the `media_created` event is sent by the
        # drone when the first photo resource is available for download.  The
        # `media_created` event does not include a full listing of all the future
        # resources of this media. The `resource_created` event will be sent
        # by the drone for the remaining resources.
        # For this reason, we have to download the resources included in this
        # `media_created` event (here we use `download_media` but we could have used
        # `download_resource` for every resources included in this event) and we'll
        # also have to download the resources included in future `resource_created`
        # events associated with this media.
        self._media(
            download_media_thumbnail(event.media_id) &
            download_media(event.media_id)
        )
        for resource_id in event.media.resources:
            self._downloading.add(resource_id)

    @olympe.listen_event(resource_created())
    def onResourceCreated(self, event, scheduler):
        logger.info("resource_created {}".format(event.resource_id))
        self._media(
            download_resource_thumbnail(event.resource_id) &
            download_resource(event.resource_id)
        )
        self._downloading.add(event.resource_id)

    @olympe.listen_event(media_removed())
    def onMediaRemoved(self, event, scheduler):
        logger.info("media_removed {}".format(event.media_id))

    @olympe.listen_event(resource_removed())
    def onResourceRemoved(self, event, scheduler):
        logger.info("resource_removed {}".format(event.resource_id))

    @olympe.listen_event(resource_downloaded())
    def onResourceDownloaded(self, event, scheduler):
        if not event.is_thumbnail:
            logger.info("resource_downloaded {} {}".format(event.resource_id, event.is_thumbnail))
            self._downloading.remove(event.resource_id)
            self._downloaded_resources.append(
                self._media.resource_info(resource_id=event.resource_id))

    @olympe.listen_event()
    def default(self, event, scheduler):
        if isinstance(event, MediaEvent):
            logger.info(event)

    def unsubscribe(self):
        self._media.wait_for_pending_downloads()
        # Sanity check 1/3: no pending downloads.
        # If any integrity check has failed we will stop right here
        if self._downloading:
            logger.error("Downloading resources {} is still in progress".format(
                ",".join(sorted(self._downloading))))
            super().unsubscribe()
            return

        # Sanity check 2/3: md5 checksum
        # The integrity check has already been performed by Olympe
        # For this example the following step demonstrate how to perform the media
        # integrity check afterward using the "md5summ --check *.md5" command.
        for resource in self._downloaded_resources:
            check = subprocess.run(
                ["md5sum", "--check", "{}.md5".format(resource.download_path)],
                stdout=subprocess.PIPE,
                cwd=os.path.dirname(resource.download_path),
            )
            stdout = check.stdout.decode().strip()
            if check.returncode == 0:
                logger.info("Integrity check: " + stdout)
            else:
                logger.error("Integrity check: " + stdout)
                super().unsubscribe()
                return

        # Sanity check 3/3: local downloaded resources equals the number of remote resources
        remote_resource_count = sum(map(
            lambda id_: len(self._media.resource_info(media_id=id_)), self._media_id))
        local_resource_count = len(self._downloaded_resources)
        if local_resource_count != remote_resource_count:
            logger.error(
                "Downloaded {} resources instead of {}".format(
                    local_resource_count,
                    remote_resource_count,
                )
            )
            super().unsubscribe()
            return

        # OK then, we can now safely delete the remote media
        for media_id in self._media_id:
            delete = delete_media(media_id, _timeout=10)
            if not self._media(delete).wait().success():
                logger.error("Failed to delete media {} {}".format(
                    media_id, delete.explain()))
        super().unsubscribe()


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
            # the following parameters are ignored in photo burst mode but we
            # must provide valid values for them anyway
            bracketing="preset_1ev",
            capture_interval=5.0,
        )
    ).wait()


def main(drone, media=None):
    drone.connect()
    setup_photo_burst_mode(drone)
    if media is None:
        assert drone.media_autoconnect
        media = drone.media
    media.download_dir = tempfile.mkdtemp(prefix="olympe_media_example_")
    media.integrity_check = True
    logger.info("waiting for media resources indexing...")
    if not media(indexing_state(state="indexed")).wait(_timeout=60).success():
        logger.error("Media indexing timed out")
        return
    logger.info("media resources indexed")
    with MediaEventListener(media):
        photo_saved = drone(photo_progress(result="photo_saved", _policy="wait"))
        drone(take_photo(cam_id=0)).wait()
        if not photo_saved.wait(_timeout=30).success():
            logger.error("Photo not saved: {}".format(photo_saved.explain()))


if __name__ == "__main__":
    # Here we want to demonstrate olympe.Media class usage as a standalone API
    # so we disable the drone object media autoconnection (we won't use
    # drone.media) and choose to instantiate the olympe.Media class ourselves
    with olympe.Drone(ANAFI_IP, media_autoconnect=False, name="example_media_standalone") as drone:
        media = olympe.Media(ANAFI_IP, name="example_media_standalone")
        media.connect()
        main(drone, media)
        media.shutdown()

    # By default, the drone instantiate an internal olympe.Media object (media_autoconnect=True
    # by default). This olympe.Media object is exposed through the Drone.media property. In this
    # case the connection to the remote media API endpoint is automatically handled by the olympe
    # drone controller class.
    with olympe.Drone(ANAFI_IP, name="example_media_autoconnect") as drone:
        main(drone)
