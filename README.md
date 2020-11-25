# Olympe - Python controller library for Parrot Drones

Olympe provides a Python controller programming interface for Parrot Drone. You can use Olympe to
connect and control a drone from a remote Python script executed from your computer.
Olympe is primarily intended to be used with a simulated drone (using Sphinx, the Parrot drone
simulator) but may also be used to connect to physical drones. Like GroundSDK-iOS and
GroundSDK-Android, Olympe is based on arsdk-ng/arsdk-xml.

Olympe is part of the [Parrot Ground SDK](https://developer.parrot.com/) which allows any developer
to create its own mobile or desktop application for ANAFI and ANAFI Thermal drones.


## [Olympe Documentation](https://developer.parrot.com/docs/olympe/)

* **[Olympe - Installation](https://developer.parrot.com/docs/olympe/installation.html)**
* **[Olympe - User guide](https://developer.parrot.com/docs/olympe/userguide.html)**
* **[Olympe - API Reference](https://developer.parrot.com/docs/olympe/olympeapi.html)**
* **[Olympe - SDK Messages Reference](https://developer.parrot.com/docs/olympe/arsdkng.html)**

## [Sphinx Documentation](https://developer.parrot.com/docs/sphinx/)

* **[Sphinx - System requirements](https://developer.parrot.com/docs/sphinx/system-requirements.html)**
* **[Sphinx - Installation](https://developer.parrot.com/docs/sphinx/installation.html)**
* **[Sphinx - Quick Start guide](https://developer.parrot.com/docs/sphinx/firststep.html)**


## [Parrot developers forums](https://forum.developer.parrot.com/categories)

* **Olympe:** https://forum.developer.parrot.com/c/anafi/olympe
* **Sphinx:** https://forum.developer.parrot.com/c/sphinx
* **Parrot Anafi:** https://forum.developer.parrot.com/c/anafi/

## License

BSD-3-Clause license

## Supported platform

* Linux Desktop PC (Ubuntu, Debian)

## Docker and Pycharm IDE notes

* You can use the Dockerfile to build a container that Pycharm can load using the Pycharm docker plugin. Build the container with **docker build -t olympe .** then in Pycharm set the Project Interpreter to create a new docker (Remote Python) Interpreter. Choose the olympe:latest docker image. 

* To make your Python programs work with "import olympe", add this code in the very first part of the file:

import sys;

__olympedirs = '/home/olympe/code/parrot-groundsdk/packages/olympe/src:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib/python:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/lib/python/site-packages:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/local/lib/python:/home/olympe/code/parrot-groundsdk/out/olympe-linux/final/usr/local/lib/python/site-packages:/home/olympe/code/parrot-groundsdk/out/olympe-linux/staging-host/usr/lib/arsdkgen';

sys.path.extend(__olympedirs.split(':'))

* To make the Python interactive command window work, you need to add the same code to Settings - Build - Console - Python Console - Starting script.

* Warning about the Dockerfile - I prefer to be non-root as olympe, but the Pycharm docker plugin seems to require root so the last line is USER root so files created outside the container will be owned by root. Also, it overwrites PYTHONPATH, which is why the __olympedirs command above is necessary

