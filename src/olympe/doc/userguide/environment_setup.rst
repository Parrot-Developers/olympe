Environment setup
-----------------

Create a simulated drone
^^^^^^^^^^^^^^^^^^^^^^^^

First things first, you need a drone to connect to. For this example we will use Sphinx
to create a simulated drone.

If you haven't installed `Sphinx
<https://developer.parrot.com/docs/sphinx/index.html>`_ yet, now is a good time to install it.


Once installed, enter the following commands in a shell:

.. code-block:: console

    $ sudo systemctl start firmwared.service
    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ai.drone"
    or
    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ukr.drone"

This will load the latest version of the Anafi software.
If you want to select a particular software, use the ``::firmware`` option:

.. code-block:: console

    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ukr.drone"::firmware="https://firmware.parrot.com/Versions/anafi3/pc/%23latest/images/anafi3-pc.ext2.zip"

If you want to deactivate WiFi usage, use the ``::wifi_iface`` option with no argument:

.. code-block:: console

    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ukr.drone"::"wifi_iface="

This will free your computer WiFi slot, so that you will still be able to use your WiFi for
another application.

The core application is now waiting for an UE4 application to connect. Run in a second
shell:

.. code-block:: console

   $ parrot-ue4-empty

.. note::

    There are plenty of other worlds available. Check Sphinx documentation to explore them!

If you do have latency loading this world, use the low quality option:

.. code-block:: console

   $ parrot-ue4-empty -quality=low

.. note::

    This will downgrade the simulated drone performance.

The above commands start a simulation of an Anafi Ai or UKR drone in an empty world. In
the following examples, we will be using the virtual ethernet interface of the simulated
drone, and reach it at ``10.202.0.1``.

At the end of each example, remember to reset the simulation before getting into the next
example. Each example assumes that the drone is landed with a fully charged battery (and
that does not discharge).
To reset the current simulation:

.. code-block:: console

   $ sphinx-cli action -m world fwman world_reset_all

For more information on Sphinx, please consult its user documentation, as mentioned above.

Set up your shell environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you've built Olympe from source, you should activate your Python environment before
continuing:

.. code-block:: console

    $ source ~/code/{{ workspace }}/shell
    (olympe-python3) $

