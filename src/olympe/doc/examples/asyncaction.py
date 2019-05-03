# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, moveBy, Landing
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.camera import start_recording, stop_recording
from olympe.messages import gimbal

with olympe.Drone("10.202.0.1") as drone:
    drone.connection()

    # Start a flying action asynchronously
    flyingAction = drone(
        TakeOff()
        >> FlyingStateChanged(state="hovering", _timeout=5)
        >> moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
        >> Landing()
    )

    # Start video recording while the drone is flying
    if not drone(start_recording(cam_id=0)).wait().success():
        raise RuntimeError("Cannot start video recording")

    # Send a gimbal pitch velocity target while the drone is flying
    cameraAction = drone(gimbal.set_target(
        gimbal_id=0,
        control_mode="velocity",
        yaw_frame_of_reference="none",
        yaw=0.0,
        pitch_frame_of_reference="none",
        pitch=0.1,
        roll_frame_of_reference="none",
        roll=0.0,
    )).wait()

    if not cameraAction.success():
        raise RuntimeError("Cannot set gimbal velocity target")

    # Wait for the end of the flying action
    if not flyingAction.wait().success():
        raise RuntimeError("Cannot complete the flying action")

    # Stop video recording while the drone is flying
    if not drone(stop_recording(cam_id=0)).wait().success():
        raise RuntimeError("Cannot stop video recording")

    # Leaving the with statement scope: implicit drone.disconnection()
