Taking off - "Hello world" example
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The first thing you might want to do with Olympe is making your drone to take off. In this example
we'll write a simple Python script that will connect to the simulated drone we've just created
and then send it a :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command.

Create the following Python ``takeoff.py`` script somewhere in your home directory:

.. literalinclude:: ../../examples/takeoff.py
    :language: python
    :linenos:

First, this script imports the ``olympe`` Python package and then the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message from the `Piloting`
feature module (one of the :ref:`ardrone3-features` module). A "feature" is just a collection
of related command and event messages that the drone exchanges its controller (`FreeFlight`,
`SkyController`, `Olympe`, ...).

Next, this script creates the ``drone`` object of the :py:class:`olympe.Drone` class.
This class constructor requires only one argument: the IP address of the drone. For a simulated
drone, we can use ``10.202.0.1`` which is the default drone IP address over the virtual Ethernet
interface. For a physical drone, it would be ``192.168.42.1`` which is the de default drone IP
address over Wi-Fi. Finally, when connected to a SkyController over USB, the SkyController is
reachable at ``192.168.43.1``.

:py:meth:`olympe.Drone.connect` actually performs the connection to the drone. This would fail and
return `False` if the drone is unreachable (or non-existent) for some reason.

Then, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message to the drone and then waits
for the drone to acknowledge the command. When the ``wait()`` function returns, our simulated drone
should be taking off. For now, we will always use the ``drone(....).wait()`` construct to send
command message and will explain later what the ``wait()`` function does and what we could do
differently with or without it.

Finally, :py:meth:`olympe.Drone.disconnect` disconnect Olympe from the drone properly.

To execute this script and see your drone taking off, from the same shell/terminal you've just
source'd the ``shell`` script:

.. code-block:: console

    $ python ./takeoff.py

