from os import environ

import olympe

from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.camera2 import Config
from olympe.messages.camera2.Command import (
    Configure,
    StartRecording,
    StopRecording,
)
from olympe.messages.camera2.Event import Recording
from olympe.messages import gimbal

DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def asyncaction():
    with olympe.Drone(DRONE_IP) as drone:
        assert drone.connect()

        # Start a flying action asynchronously
        flying_expectation = drone(
            TakeOff()
            >> FlyingStateChanged(state="hovering", _timeout=10)
            >> moveBy(10, 0, 0, 0)
            >> FlyingStateChanged(state="hovering", _timeout=150)
            >> Landing()
        )

        ## While the drone is flying ##

        # Configure camera mode to 'Recording' and adjust parameters to the wanted
        # recording configuration
        recording_configure_expectation = drone(
            Configure(
                camera_id=1,
                config=Config(
                    camera_mode="recording",
                    video_recording_mode="standard",
                    video_recording_dynamic_range="standard",
                    video_recording_resolution="720p",
                    video_recording_framerate="30",
                ),
                _timeout=3.0,
            )
        ).wait()
        if not recording_configure_expectation.success():
            assert False, "Cannot configure camera to 'recording' mode"

        # Request recording start and wait for the drone to acknowledge the recording
        # has correctly started
        recording_expectation = drone(
            StartRecording(camera_id=1)
            & Recording(
                type="start",
                _timeout=3.0,
                _policy="wait",
            )
        ).wait()
        assert recording_expectation, recording_expectation.explain()

        # Send a gimbal pitch velocity target while the drone is flying
        camera_expectation = drone(
            gimbal.set_target(
                gimbal_id=0,
                control_mode="velocity",
                yaw_frame_of_reference="none",
                yaw=0.0,
                pitch_frame_of_reference="none",
                pitch=0.1,
                roll_frame_of_reference="none",
                roll=0.0,
            )
        ).wait()
        if not camera_expectation.success():
            assert False, "Cannot set gimbal velocity target"

        # Wait for the end of the flying action
        if not flying_expectation.wait().success():
            assert False, "Cannot complete the flying action"

        # Stop video recording while the drone is flying
        recording_expectation = drone(
            StopRecording(camera_id=1)
            & Recording(
                type="stop",
                stop_reason="user_request",
                _timeout=3.0,
                _policy="wait",
            )
        ).wait()
        assert recording_expectation, recording_expectation.explain()

        # To leave the *with* statement scope means implicit drone.disconnect() but
        # that is still a good idea to perform the drone disconnection explicitly
        assert drone.disconnect()


if __name__ == "__main__":
    asyncaction()
