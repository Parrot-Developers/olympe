from os import environ
from pathlib import Path
from time import sleep

import olympe

from olympe.features.video.pdraw import (
    OutfilesPath,
    h264_coded_data_format,
)

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = environ.get("DRONE_IP", "192.168.43.1")
DRONE_RTSP_PORT = environ.get("DRONE_RTSP_PORT")


class SingleStream_WithRenderer:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

    def start(self):
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        # Set the stream config to play
        self.drone.streaming.play(
            media_name="Front camera",
            data_formats=[h264_coded_data_format.bytestream],
            has_renderer=True,
        )

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


def test_streaming_single_with_renderer():
    # Instantiate the video single stream
    single_stream = SingleStream_WithRenderer()
    # Start the video stream
    single_stream.start()
    # Wait
    sleep(10)
    # Stop the video single stream
    single_stream.stop()


class SingleStream_WithRenderer_AndOutputFiles(SingleStream_WithRenderer):
    def __init__(self):
        super().__init__()
        # Record the video stream from the drone for further post processing
        path = Path("/tmp/outfiles/")
        path.mkdir(parents=True, exist_ok=True)

    def start(self):
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        self.drone.streaming.play(
            media_name="Front camera",
            data_formats=[h264_coded_data_format.avcc],
            has_renderer=True,
            outfiles_path=OutfilesPath(
                info="/tmp/outfiles/front_camera_info.json",
                video="/tmp/outfiles/front_camera_video.mp4",
                meta="/tmp/outfiles/front_camera_meta.json",
            ),
        )


def test_streaming_single_with_renderer_and_outputfiles():
    # Instantiate the video single stream
    single_stream = SingleStream_WithRenderer_AndOutputFiles()
    # Start the video stream
    single_stream.start()
    # Wait
    sleep(10)
    # Stop the video single stream
    single_stream.stop()


if __name__ == "__main__":
    test_streaming_single_with_renderer()
    sleep(10)
    test_streaming_single_with_renderer_and_outputfiles()
