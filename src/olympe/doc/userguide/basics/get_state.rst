Getting the current value of a drone state or setting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will be using the :py:meth:`~olympe.Drone.get_state` method to query the current
value of the "maximum tilt" drone's setting.

When the maximum tilt drone setting is changed by the controller (Olympe) with the
:py:func:`~olympe.messages.ardrone3.PilotingSettings.MaxTilt` command message the drone sends the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event message in response.
Changing a drone setting will be demonstrated in the following example. Here, we're just interested
in getting the current drone setting.

When Olympe connects to a drone it also asks the drone to send back all its states and settings
event messages. Olympe can later provide you with this information through the
:py:meth:`olympe.Drone.get_state` method. So **if Olympe is connected to a drone**,
:py:meth:`olympe.Drone.get_state` always returns the current drone state associated to an
**event message**.

In this case, we will be passing the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` message to the
:py:meth:`olympe.Drone.get_state` method. This will return a dictionary of the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event message which
provides the following parameters:

    :MaxTiltChanged Parameters:
        - current (float) – Current max tilt
        - min (float) – Range min of tilt
        - max (float) – Range max of tilt

Note: Don't be confused here, the "min" and "max" parameters are actually the minimum and the
maximum values for the "maximum tilt" setting. Here, we are only interested in the "current"
value of this setting.

Let's get down to some practice! First, reset the simulation
(``sphinx-cli action -m world fwman world_reset_all`` in a terminal).

Create the following Python ``maxtiltget.py`` script somewhere in your home directory:

.. literalinclude:: ../../examples/maxtiltget.py
    :language: python
    :linenos:

To execute this script and see your drone taking off, from the same shell/terminal you've just
source'd the ``shell`` script:

.. code-block:: console

    $ python ./maxtiltget.py

This should print the current maximum tilt value in your terminal. The following sequence diagram
illustrate what is happening in this simple example.

.. _max-tilt-get-diag:

.. seqdiag::
   :caption: Getting the drone MaxTilt
   :align: center

   seqdiag {
      activation = none;
      edge_length = 280;
      default_fontsize = 11;

      Olympe ->> Drone [label = "connect"]
      Olympe <<-- Drone [label = "connected", rightnote="also send all event messages"]
      Olympe -> Olympe  [leftnote = "get_state(MaxTiltChanged)"]
      Olympe => Drone [label = "disconnect", return = "disconnected"]
    }

