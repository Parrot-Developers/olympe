from os import environ
from time import sleep

import olympe

from olympe.video.pdraw import (
    h264_coded_data_format,
)

olympe.log.update_config(
    {
        "loggers": {
            "olympe": {"level": "INFO"},
            "ulog": {"level": "DEBUG", "handlers": ["console"]},
        }
    }
)

SKYCTRL_IP = environ.get("SKYCTRL_IP", "192.168.53.1")


class Stream_WithSkyCtrl:
    def __init__(self):
        self.skyctrl = olympe.SkyControllerUKR(SKYCTRL_IP)

    def start(self):
        assert self.skyctrl.connect(retry=3)

        # Set the stream config to play
        self.skyctrl.streaming.play(
            media_name="Front camera",
            data_formats=[h264_coded_data_format.bytestream],
            has_renderer=True,
        )

    def stop(self):
        assert self.skyctrl.streaming.stop()
        assert self.skyctrl.disconnect()


def test_streaming_with_skyctrl():
    # Instantiate the video stream with sky controller
    skyctrl_stream = Stream_WithSkyCtrl()
    # Start the video stream
    skyctrl_stream.start()
    # Wait
    sleep(10)
    # Stop the video stream
    skyctrl_stream.stop()


if __name__ == "__main__":
    test_streaming_with_skyctrl()
