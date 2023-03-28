.. _moving-around-example:

Moving around - Waiting for a 'hovering' flying state
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will move our drone around using the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command message.

First, reset the simulation (``sphinx-cli action -m world fwman world_reset_all`` in a terminal).

Create the following Python ``moveby.py`` script somewhere in your home directory:

.. literalinclude:: ../../examples/moveby.py
    :language: python
    :linenos:

This script starts by importing the ``olympe`` package and then the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` and
:py:func:`~olympe.messages.ardrone3.Piloting.Landing`
**command messages** from the ``ardrone3.Piloting`` feature module. It then connects to the drone
and send the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` and
:py:func:`~olympe.messages.ardrone3.Piloting.Landing` commands.

This script should work as-is, right? Let's see.

To execute this script, from the same shell/terminal you've source'd the ``shell`` script:

.. code-block:: console

    $ python ./moveby.py

The output of this script should be:

.. code-block:: console

    moveBy timedout

Wait! The drone takes off and then eventually lands without performing the moveBy?! What happened?

When olympe sends a command message to the drone it expects an acknowledgment event message from
the drone in return. In this script, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command to the drone and then **waits for the
drone taking off event message** as an acknowledgment from the drone. Olympe knows that after a
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command it should expect a
``FlyingStateChanged(state='takingoff')`` and automatically waits for that event for you.


The problem with the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command is that it is rejected by the drone as
long as the drone is not in the "hovering" flying state. In this case it is rejected because after
the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command the drone is in ``takingoff``
flying state.  So, to fix this script we will have to wait for the ``hovering`` state before
sending the :py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command. The following sequence
diagram illustrates what is happening with this first attempt to use the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command.

.. _move-by-diag:

.. seqdiag::
   :caption: Attempt to use the moveBy command
   :align: center

   seqdiag {
      activation = none;
      edge_length = 280;
      default_fontsize = 11;

      Olympe => Drone [label = "connect", return = "connected"]
      Olympe ->> Drone  [label = "TakeOff()", leftnote="takeOffAction pending"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='motor_ramping')", leftnote="takeOffAction successful"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='takingoff')", leftnote="takeOffAction successful"];
      Olympe ->> Drone  [label = "moveBy()", leftnote="moveByAction pending", rightnote="silently rejected,
      moveBy is unavailable"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='hovering')", rightnote="moveBy available"];
      Olympe -> Olympe [leftnote = "moveByAction timedout"]
      Olympe => Drone [label = "disconnect", return = "disconnected"]
   }

Edit ``moveby.py`` with the following corrected script:

.. literalinclude:: ../../examples/moveby2.py
    :language: python
    :linenos:

This new script will wait for the hovering state after each command sent to the drone.
To do that, it imports the :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
**event message** from the same ``ardrone3.PilotingState`` module feature.


Note: The expectations for each command message are defined in the ``arsdk-xml`` source repo along
with the command and event messages themselves.

.. literalinclude:: ../../examples/moveby2.py
    :language: python
    :linenos:
    :lineno-start: 9
    :lines: 9-12

In this new example after the drone connection, the above code tells Olympe to:

    1. Send the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command
    2. Then, to implicitly wait for the expectations of the
       :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command:
       ``FlyingStateChanged(state='takingoff')``
    3. Then, to explicitly wait for the drone ``hovering`` flying state event:
       ``FlyingStateChanged(state='hovering')``


Here, the ``drone()`` functor accepts more than just a command message. The ``drone()`` takes an
expression that may be a combination of command and event messages to process.
The ">>" operator is used to combine two expressions with an "and then" semantic. This example
could be read as "Take off and then wait a maximum of 5 seconds for the 'hovering' flying state").

The rest of the example should be easy to understand now. After the drone has taken off, this script
waits for the drone "hovering" state and then sends the moveBy command, waits for the "hovering"
state again and then sends the landing command. The following sequence diagram illustrates this
second (successful) attempt to use the :py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command.

.. _move-by2-diag:

.. seqdiag::
   :caption: Using the moveBy command
   :align: center

   seqdiag {
      activation = none;
      edge_length = 260;
      default_fontsize = 10;

      Olympe => Drone [label = "connect", return = "connected"]
      Olympe ->> Drone  [label = "TakeOff()", leftnote="takeOffAction pending"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='motor_ramping')", leftnote="takeOffAction pending"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='takingoff')", leftnote="takeOffAction successful"];
      Olympe --> Olympe [leftnote = "waiting for FlyingStateChanged(state='hovering')"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='hovering')", leftnote="", rightnote="moveBy available"];
      Olympe ->> Drone  [label = "moveBy()", leftnote="moveByAction pending"]
      Olympe <<-- Drone [label = "moveByEnd()", leftnote = "moveByAction successful "]
      Olympe => Drone [label = "disconnect", return = "disconnected"]
   }

Let's check everything works! Reset the simulation
(``sphinx-cli action -m world fwman world_reset_all`` in a terminal) and execute this script, from
the same shell/terminal you have source'd the ``shell`` script:

.. code-block:: console

    $ python ./moveby.py

And it should work now! The drone should take off, perform a forward move by 10 meters and then
land.

