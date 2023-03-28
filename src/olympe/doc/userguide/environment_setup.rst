Environment setup
------------------------

Create a simulated drone
^^^^^^^^^^^^^^^^^^^^^^^^

First things first, you need a drone to connect to. For this example we will use Sphinx_ to create
a simulated drone and then connect to it using Olympe before sending our first commands.

If you haven't installed Sphinx_ yet, now is a good time to install it.

.. _sphinx: {{ sphinx_doc_url }}

Then in a shell enter the following commands:

.. code-block:: console

    $ sudo systemctl start firmwared.service
    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ai.drone"::firmware="https://firmware.parrot.com/Versions/anafi2/pc/%23latest/images/anafi2-pc.ext2.zip"

The core application is now waiting for an UE4 application to connect… In a second shell, do:

.. code-block:: console

   $ parrot-ue4-empty

The above commands start a simulation of an ANAFI Ai drone in an empty world. In the following
examples, we will be using the virtual ethernet interface of the simulated drone, and reach it at
``10.202.0.1``.

At the end of each example, remember to reset the simulation before getting into the next example.
Each example assumes that the drone is landed with a fully charged battery. Just enter
``sphinx-cli action -m world fwman world_reset_all`` in a terminal to reset the current simulation.

For more information on Sphinx, please consult its comprehensive `user documentation <{{sphinx_doc_url}}>`_.

Set up your shell environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you've built Olympe from source, you should activate your Python environment 
before continuing this user guide.

.. code-block:: console

    $ source ~/code/{{ workspace }}/shell
    (olympe-python3) $

