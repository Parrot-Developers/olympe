Overview
========

Olympe provides a Python controller programming interface for Parrot Drone. This means you can use
Olympe to connect and control a drone from a remote Python script executed from your computer.
Olympe is primarily intended to be used with a simulated drone (using Sphinx, the Parrot drone
simulator) but may also be used to connect to physical drones. Like GroundSDK-iOS and
GroundSDK-Android, Olympe is based on arsdk-ng/arsdk-xml.

Olympe Features:

    - Connect to simulated or physical drones
    - Send command messages to the drone (piloting, camera orientation, RTH, FlightPlan, ...)
    - Check the current state of the drone and wait for event messages (flying state, ...)
    - Get the current state of the drone (settings, feature availability status, ...)
    - Start and stop the drone video streaming
    - Record the video stream from the drone and the associated metadata
