import olympe
import olympe.log
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
    download_media_thumbnail,
    MediaEvent,
)
from olympe.messages.camera2 import Command, Config, Event
from logging import getLogger

olympe.log.update_config(
    {
        "loggers": {
            "olympe": {"level": "INFO", "handlers": ["console"]},
            "ulog": {"level": "INFO", "handlers": ["console"]},
            __name__: {"level": "DEBUG", "handlers": ["console"]},
        }
    }
)

logger = getLogger(__name__)

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")


class MediaEventListener(olympe.EventListener):
    def __init__(self, media):
        super().__init__(media, timeout=60)
        self._media = media
        self._media_id = []
        self._downloaded_resources = []
        self.remote_resource_count = 0
        self.local_resource_count = 0

    @olympe.listen_event(media_created())
    def onMediaCreated(self, event, scheduler):
        self._media_id.append(event.media_id)
        logger.info(f"media_created {event.media_id}")
        # When using the photo burst mode, the `media_created` event is sent by the
        # drone when the first photo resource is available for download.  The
        # `media_created` event does not include a full listing of all the future
        # resources of this media. The `resource_created` event will be sent
        # by the drone for the remaining resources.
        # However, the "download_media" and "download_media_thumbnail" will do
        # the right thing and download for you any subsequent resources associated
        # to this media id automatically.
        self._media(
            download_media_thumbnail(event.media_id) & download_media(event.media_id)
        )

    @olympe.listen_event(resource_created())
    def onResourceCreated(self, event, scheduler):
        logger.info(f"resource_created {event.resource_id}")

    @olympe.listen_event(media_removed())
    def onMediaRemoved(self, event, scheduler):
        logger.info(f"media_removed {event.media_id}")

    @olympe.listen_event(resource_removed())
    def onResourceRemoved(self, event, scheduler):
        logger.info(f"resource_removed {event.resource_id}")

    @olympe.listen_event(resource_downloaded())
    def onResourceDownloaded(self, event, scheduler):
        if event.is_thumbnail:
            return
        logger.info(
            "resource_downloaded {} {}".format(
                event.resource_id,
                event.data["download_path"],
            )
        )
        self._downloaded_resources.append(
            self._media.resource_info(resource_id=event.resource_id)
        )

    @olympe.listen_event()
    def default(self, event, scheduler):
        if isinstance(event, MediaEvent):
            logger.info(event)

    def unsubscribe(self):
        self._media.wait_for_pending_downloads()
        # Sanity check 1/2: md5 checksum
        # The integrity check has already been performed by Olympe
        # For this example the following step demonstrate how to perform the media
        # integrity check afterward using the "md5summ --check *.md5" command.
        for resource in self._downloaded_resources:
            check = subprocess.run(
                ["md5sum", "--check", resource.download_md5_path],
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

        # Sanity check 2/2: local downloaded resources equals the number of remote
        # resources
        self.remote_resource_count = sum(
            map(
                lambda id_: len(self._media.resource_info(media_id=id_)), self._media_id
            )
        )
        self.local_resource_count = len(self._downloaded_resources)
        if self.local_resource_count != self.remote_resource_count:
            logger.error(
                "Downloaded {} resources instead of {}".format(
                    self.local_resource_count,
                    self.remote_resource_count,
                )
            )
            super().unsubscribe()
            return

        # OK then, we can now safely delete the remote media
        for media_id in self._media_id:
            delete = delete_media(media_id, _timeout=10)
            if not self._media(delete).wait().success():
                logger.error(f"Failed to delete media {media_id} {delete.explain()}")
        super().unsubscribe()


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
                photo_burst_value="14_over_1s",
                photo_dynamic_range="standard",
                photo_resolution="12_mega_pixels",
            ),
            _timeout=3.0,
        )
    ).wait()


def test_media():
    # By default, the SkyController class instantiate an internal olympe.Media object
    # (media_autoconnect=True by default). This olympe.Media object is exposed throught
    # the SkyController.media property. In this case the connection to the remote media
    # API endpoint is automatically handled by the olympe.SkyController controller
    # class.
    with olympe.SkyController4(SKYCTRL_IP) as skyctrl:
        assert skyctrl.connect(retry=5, timeout=60)
        setup_photo_burst_mode(skyctrl)
        skyctrl.media.download_dir = tempfile.mkdtemp(
            prefix="olympe_skyctrl_media_example_"
        )
        skyctrl.media.integrity_check = True
        logger.info("waiting for media resources indexing...")
        if (
            not skyctrl.media(indexing_state(state="indexed"))
            .wait(_timeout=60)
            .success()
        ):
            logger.error("Media indexing timed out")
            return
        logger.info("media resources indexed")
        with MediaEventListener(skyctrl.media) as media_listener:
            media_state = skyctrl(media_created(_timeout=3.0))
            photo_capture = skyctrl(
                Event.Photo(
                    type="stop",
                    stop_reason="capture_done",
                    _timeout=3.0,
                    _policy="wait",
                )
                & Command.StartPhoto(camera_id=0)
            ).wait()
            assert photo_capture, photo_capture.explain()
            media_state.wait()
            assert media_state, media_state.explain()
        assert media_listener.remote_resource_count > 0, "remote resource count == 0"
        assert (
            media_listener.remote_resource_count == media_listener.local_resource_count
        ), "remote resource count = {} != {}".format(
            media_listener.remote_resource_count, media_listener.local_resource_count
        )
        assert skyctrl.disconnect()


if __name__ == "__main__":
    test_media()
