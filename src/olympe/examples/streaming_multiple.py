import os
import time

from pathlib import Path

import olympe

from olympe.utils.enum import get_enum_value_as_int
from olympe.video.pdraw import (
    Callbacks,
    StreamInfo,
    OutfilesPath,
    video_frame_type,
    h264_coded_data_format,
)

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = os.environ.get("DRONE_IP", "192.168.43.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT")


class MultiStream_WithRenderer:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

    def start(self):
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        # Set the streams list to play
        down_stereo_left_camera_stream = StreamInfo(
            name="Down stereo left camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.bytestream),
                    ),
                ),
            ],
            has_renderer=True,
        )
        front_camera_stream = StreamInfo(
            name="Front camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.bytestream),
                    ),
                ),
            ],
            has_renderer=True,
        )
        horizontal_stereo_left_camera_stream = StreamInfo(
            name="Horizontal stereo left camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.bytestream),
                    ),
                ),
            ],
            has_renderer=True,
        )
        # Request to play multiple streams
        self.drone.streaming.play_multiple_stream(
            streams_list=[
                down_stereo_left_camera_stream,
                front_camera_stream,
                horizontal_stereo_left_camera_stream,
            ]
        )

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


def test_streaming_multiple_with_renderer():
    # Instantiate the video multistream
    multi_stream = MultiStream_WithRenderer()
    # Start the video multistream
    multi_stream.start()
    # Wait
    time.sleep(10)
    # Stop the video multistream
    multi_stream.stop()


class MultiStream_WithRenderer_AndOutputFiles(MultiStream_WithRenderer):
    def __init__(self):
        super().__init__()
        # Record the video stream from the drone for further post processing
        path = Path("/tmp/outfiles/")
        path.mkdir(parents=True, exist_ok=True)

    def start(self):
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        down_stereo_left_camera_stream = StreamInfo(
            name="Down stereo left camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.avcc),
                    ),
                ),
            ],
            has_renderer=True,
            outfiles_path=OutfilesPath(
                info="/tmp/outfiles/dst_camera_info.json",
                video="/tmp/outfiles/dst_camera_video.mp4",
                meta="/tmp/outfiles/dst_camera_meta.json",
            ),
        )
        front_camera_stream = StreamInfo(
            name="Front camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.avcc),
                    ),
                ),
            ],
            has_renderer=True,
            outfiles_path=OutfilesPath(
                info="/tmp/outfiles/front_camera_info.json",
                video="/tmp/outfiles/front_camera_video.mp4",
                meta="/tmp/outfiles/front_camera_meta.json",
            ),
        )
        horizontal_stereo_left_camera_stream = StreamInfo(
            name="Horizontal stereo left camera",
            callbacks=[
                Callbacks(
                    media_type=(get_enum_value_as_int(video_frame_type.raw), None),
                ),
                Callbacks(
                    media_type=(
                        get_enum_value_as_int(video_frame_type.coded),
                        get_enum_value_as_int(h264_coded_data_format.avcc),
                    ),
                ),
            ],
            has_renderer=True,
            outfiles_path=OutfilesPath(
                info="/tmp/outfiles/hst_camera_info.json",
                video="/tmp/outfiles/hst_camera_video.mp4",
                meta="/tmp/outfiles/hst_camera_meta.json",
            ),
        )
        # Request to play multiple streams
        self.drone.streaming.play_multiple_stream(
            streams_list=[
                down_stereo_left_camera_stream,
                front_camera_stream,
                horizontal_stereo_left_camera_stream,
            ]
        )


def test_streaming_multiple_with_renderer_and_outputfiles():
    # Instantiate the video multistream
    multi_stream = MultiStream_WithRenderer_AndOutputFiles()
    # Start the video multistream
    multi_stream.start()
    # Wait
    time.sleep(10)
    # Stop the video multistream
    multi_stream.stop()


if __name__ == "__main__":
    test_streaming_multiple_with_renderer()
    time.sleep(10)
    test_streaming_multiple_with_renderer_and_outputfiles()
