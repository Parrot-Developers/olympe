.. _move_around:

Move around - Wait for a 'hovering' state
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Objective:**

Move the drone around with the :py:func:`~olympe.messages.ardrone3.Piloting.moveBy`
command message.

First, reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal.


Copy paste the following Python ``moveby.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/moveby.py
    :language: python
    :linenos:

*Body*
~~~~~~

Once connected to the drone, this script sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` and
:py:func:`~olympe.messages.ardrone3.Piloting.Landing` commands.

This script should work as-is, right? Let's see.

*Execution*
~~~~~~~~~~~
To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./moveby.py

Wait! The drone takes off and then eventually lands without performing the moveBy?! What happened?

How does Olympe deal with sequential movement
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When Olympe sends a command message to the drone, it expects an acknowledgment event
message from the drone in return. In this script, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command to the drone and expects a
``FlyingStateChanged(state="takingoff")`` event message. It automatically waits for that
event for you.

The problem with the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command is that it is rejected by
the drone as long as the drone is not in the 'hovering' state. In this example,
it is rejected because the drone is in 'takingoff' state after the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command. So, to fix this script, we
will have to wait for the ``hovering`` state before sending the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command.

*Body edit*
~~~~~~~~~~~
Edit ``moveby.py`` with the following corrected script:

.. literalinclude:: ../../examples/moveby_edit.py
    :language: python
    :linenos:

This new script will wait for the 'hovering' state after each command sent to the drone.
To do that, it imports the :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
event message from the same ``ardrone3.PilotingState`` module feature.


.. note::

    The expectation of every command message is defined in the ``arsdk-xml`` source repo,
    along with the command and event messages themselves.

:py:class:`olympe.Drone` class accepts more than just a command message. It takes an
expectation that may be a combination of command and event messages to process.
The ``>>`` operator is used to combine two expressions with an "AND THEN" semantic. This
example could be read as "*Take off and then wait a maximum of 20 seconds to reach the
'hovering' state*".

The rest of the example should be easy to understand now. After the drone has taken off,
this script waits for the drone's 'hovering' state and then sends the :py:func:`~olympe.messages.ardrone3.Piloting.moveBy`
command.
It waits for the 'flying' state, the acknowledgment of the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy`, :py:func:`~olympe.messages.ardrone3.PilotingEvent.moveByEnd`
with the status error ``"ok"``, the 'hovering' state again and then sends the landing
command.


*Execution*
~~~~~~~~~~~
Let's check everything works! Reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal

To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./moveby_edit.py

And it should work now! The drone should take off, perform a forward move by 2 meters
and eventually land.
