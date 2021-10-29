Overview
========

Olympe is an SDK that provides a Python controller programming interface for Parrot ANAFI drones.
It can be used to connect to and control a drone from a remote Python script running on a Linux
based computer. Olympe is compatible with the following drones from Parrot: ANAFI, ANAFI Thermal,
ANAFI USA, and ANAFI AI.

To help you develop your application based on Olympe, you can use Sphinx (the Parrot drones
simulation software) to create virtual drones and control them with your Olympe-based application.

Like Parrot GroundSDK for iOS and Android, Olympe for Linux is powered by C libraries (libarsdk,
libpdraw, libpomp, ...) that are also developed and maintained by Parrot.

Olympe Features:

    - Connect to, communicate and control an ANAFI drone (manual piloting, camera orientation, ...)
    - Drone state monitoring
    - Install and communicate with onboard AirSDK missions
    - Access to the photos and videos stored on the drone
    - Video rendering of the stream casted from the drone
    - Real-time programmatic access to the streamed video frames and metadata
    - Record the video stream from the drone and the associated metadata
