from os import environ
from time import sleep

import olympe

from olympe.messages.camera2 import Config
from olympe.messages.camera2.Command import Configure
from olympe.messages.camera2.Event import State
from olympe.video.pdraw import (
    h264_coded_data_format,
)


olympe.log.update_config(
    {"loggers": {"olympe": {"level": "INFO"}, "ulog": {"level": "INFO"}}}
)

DRONE_IP = environ.get("DRONE_IP", "192.168.43.1")
DRONE_RTSP_PORT = environ.get("DRONE_RTSP_PORT", "554")


class ThermalStream_WithRenderer:
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

    def start_thermal(self):
        # Stream thermal video for 15 seconds
        if not self.drone(
            Configure(camera_id=1, config=Config(spectrum="thermal"))
        ).wait(_timeout=5.0):
            assert False, "Cannot configure spectrum to 'thermal' mode"
        sleep(15)

        # Assert that the correct spectrum has been set
        assert self.drone(State(camera_id=1, config=Config(spectrum="thermal")))

    def switch_to_visible(self):
        # Switch back to classic video before disconnecting
        if not self.drone(
            Configure(camera_id=1, config=Config(spectrum="visible"))
        ).wait(_timeout=5.0):
            assert False, "Cannot configure spectrum to 'visible' mode"
        sleep(15)

        # Assert that the correct spectrum has been set
        assert self.drone(State(camera_id=1, config=Config(spectrum="visible")))

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


def test_streaming_thermal_with_renderer():
    # Instantiate the video single stream
    thermal_stream = ThermalStream_WithRenderer()
    # Start the video stream
    thermal_stream.start()
    # Wait
    sleep(5)
    # Start the video thermal stream
    thermal_stream.start_thermal()
    # Switch the video to visible stream
    thermal_stream.switch_to_visible()
    # Stop the video single stream
    thermal_stream.stop()


if __name__ == "__main__":
    test_streaming_thermal_with_renderer()
