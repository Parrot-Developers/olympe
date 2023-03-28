.. _arsdk-messages-intro:

Introduction
------------

At its core, Olympe basically just send and receive
:ref:`ARSDK messages<messages-reference-documentation>` to control a drone.
The following sequence diagram shows what is happening when an Olympe scripts sends a
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command to a drone.

.. _take-off-diag:

.. seqdiag::
   :caption: Take off command sequence
   :align: center

   seqdiag {
      activation = none;
      edge_length = 400;
      default_fontsize = 14;
      Olympe ->> Drone  [label = "TakeOff()"] {
          Olympe <<-- Drone [label = "FlyingStateChanged(state='motor_ramping')"];
          Olympe <<-- Drone [label = "FlyingStateChanged(state='takingoff')"]
      }
   }

When Olympe sends a command message like the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`
message above, it then waits for a response from the drone, the
:py:func:`FlyingStateChanged(state="takingoff")<olympe.messages.ardrone3.PilotingState.FlyingStateChanged>`
event message in this case.

Sometimes, the drone can also spontaneously notify its controller (Olympe) of a particular event.
Olympe provides a way to monitor such events (or a combination of such events).
The following sequence diagram illustrates this scenario with a
:py:func:`GPSFixStateChanged(0)<olympe.messages.ardrone3.GPSSettingsState.GPSFixStateChanged>` that
informs Olympe that the GPS fix has been lost.

.. _gps-fix-lost-diag:

.. seqdiag::
   :caption: Losing the GPS fix
   :align: center

   seqdiag {
      activation = none;
      edge_length = 400;
      default_fontsize = 14;
      Olympe <<-- Drone [label = "GPSFixStateChanged(0)"];
   }

As a user of Olympe, you might also be punctually interested in the current state of the drone without
monitoring every received message from the drone. To do this, Olympe just remembers the last received
event that provides this state information and expose this information through the
:py:meth:`olympe.Drone.get_state` method.

.. _gps-fix-get_state-diag:

.. seqdiag::
   :caption: Getting current GPS fix status
   :align: center

   seqdiag {
      activation = none;
      edge_length = 400;
      default_fontsize = 14;
      Olympe -> Olympe [label = "get_state", leftnote = "GPSFixStateChanged(0)"];
      Drone
   }

As demonstrated in the following usage examples, Olympe provides a relatively simple API to perform
the above actions (and much more) using the following olympe.Drone class methods:

    - :py:meth:`olympe.Drone.__call__`: send command messages, monitor events and check the current
      drone state
    - :py:meth:`olympe.Drone.get_state`: get the current drone state

ARSDK message Python types are available in the `olympe.messages.<feature_name>[.<class_name>]`
modules. Likewise, ARSDK enum Python types are available in the
`olympe.enums.<feature_name>[.<class_name>]` modules.

See the :ref:`messages-reference-documentation` for more information.

