from os import environ

import olympe

from olympe.enums import gimbal as gimbal_enums
from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing
from olympe.messages.ardrone3.PilotingEvent import moveByEnd
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.camera2 import Command


DRONE_IP = environ.get("DRONE_IP", "10.202.0.1")


def asyncaction():
    with olympe.Drone(DRONE_IP) as drone:
        assert drone.connect()

        # Start a flying action asynchronously. The commands are sent to the drone but
        # are not requested to be executed right away
        flying_expectation = drone(
            TakeOff()
            >> FlyingStateChanged(state="hovering", _timeout=20)
            >> moveBy(2, 0, 0, 0, _no_expect=True)
            >> moveByEnd(error="ok")
            >> FlyingStateChanged(state="hovering", _timeout=20)
            >> Landing()
        )

        # Because recording mode is the default mode, there is no need to send a
        # configuration as long as the mode requested doesn't switch to photo

        # Start the video recording while the drone is flying
        if not drone(Command.StartRecording(camera_id=1)).wait(_timeout=5).success():
            assert False, "Cannot start video recording"

        # Send a gimbal pitch velocity target while the drone is flying
        cameraAction = drone(
            gimbal.set_target(
                gimbal_id=0,
                control_mode=gimbal_enums.control_mode.velocity,
                yaw_frame_of_reference="none",
                yaw=0.0,
                pitch_frame_of_reference="none",
                pitch=0.1,
                roll_frame_of_reference="none",
                roll=0.0,
            )
        ).wait(_timeout=5)

        if not cameraAction.success():
            assert False, "Cannot set gimbal velocity target"

        # Wait for the end of the flying action
        if not flying_expectation.wait().success():
            assert False, "Cannot complete the flying action"

        # Stop the video recording while the drone is flying
        if not drone(Command.StopRecording(camera_id=1)).wait(_timeout=5).success():
            assert False, "Cannot stop video recording"

        # When leaving the with statement scope, there is an implicit drone.disconnect()
        # but that is still a good practice to perform the drone disconnection explicitly
        assert drone.disconnect()


if __name__ == "__main__":
    asyncaction()
