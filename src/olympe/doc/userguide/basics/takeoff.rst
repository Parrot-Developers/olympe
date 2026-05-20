Take off
^^^^^^^^

**Objective:**

Write a simple Python script that connects to the simulated drone and sends a
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command.

Copy paste the following Python ``takeoff.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/takeoff.py
    :language: python
    :linenos:

.. important::

    When connecting or disconnecting to a drone or a SkyController, do not forget to
    ``assert``. That lets time to do the whole process calmy to the device, without any
    interferences that you may introduce if you forget them.
    Thus, remember, ``assert device.(dis)connect()`` are the first basic commands to fill
    your script with.


*Imports*
~~~~~~~~~
Among other things, this script imports the ``olympe`` Python package and the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message from the `Piloting`
feature module (one of the :ref:`ardrone3-features` module).

.. note::

    A "feature" is just a collection of related command and event messages that the drone
    exchanges with its controller (`FreeFlight`, `SkyController`, `Olympe`, ...).

.. note::

    Keep in mind that there are two different kinds of ARSDK messages : Commands and Events.
    Commands are messages sent from the controller (`Olympe`) to the drone. Events are
    those sent from the drone to the controller.

*Body*
~~~~~~
Next, this script creates the ``drone`` instance from the :py:class:`olympe.Drone` class.
This class constructor requires only one argument: the IP address of the drone. For a
simulated drone, we can use ``10.202.0.1`` which is the default drone IP address over
the virtual Ethernet interface.

:py:meth:`olympe.Drone.connect` actually performs the connection to the drone. This would
fail and return `False` if the drone is unreachable (or non-existent) for some reason.

Then, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message to the drone and
then waits for the drone to acknowledge the command. When the ``wait()`` function returns,
the simulated drone should takeoff.

.. note::

    For now, we will always use the ``drone(....).wait()`` construct to send command message
    and will explain later what the ``wait()`` function does and what we could do differently
    with or without it.

Finally, :py:meth:`olympe.Drone.disconnect` disconnects Olympe from the drone properly.
And the ``assert`` guarantees the disconnection has succeeded.

*Execution*
~~~~~~~~~~~
To execute this script and see your drone take off, run the following command from the
shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./takeoff.py

