Get the current value of a drone state or setting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Objective:**

Query the current value of the "maximum tilt" drone's setting with the
:py:meth:`olympe.Drone.get_state` method.

*A little explanation first*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When the maximum tilt drone setting is changed by the `Olympe` controller with the
:py:func:`~olympe.messages.ardrone3.PilotingSettings.MaxTilt` command message, the drone
sends the :py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event
message in response.

.. note::

    Changing a drone setting is demonstrated in the following example. We're just here
    interested in getting the current drone setting.

When Olympe connects to a drone, it also asks the drone to send back all its states and
settings messages. Olympe can later provide with this information through the
:py:meth:`olympe.Drone.get_state` method. Thus, with Olympe connected to the drone,
:py:meth:`olympe.Drone.get_state` always returns the current drone state associated to
the event message in question.

For this example, we use the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` message passed
to the :py:meth:`olympe.Drone.get_state` method. This will return a dictionary of the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event message which
provides the following parameters:

    :MaxTiltChanged Parameters:
        - current (float) – Current max tilt
        - min (float) – Range min of tilt
        - max (float) – Range max of tilt

.. note::

    The "min" and "max" parameters are actually the minimum and the maximum values for
    the "maximum tilt" setting. Here, we are only interested in the "current"
    value of this setting.

Let's get down to some practice! First, reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal.

Copy paste the following Python ``maxtilt_get.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/maxtilt_get.py
    :language: python
    :linenos:

*Body*
~~~~~~
Once `Olympe` is connected to the drone, the drone sends its states and settings event
messages voluntarily. The last state related to the maximum tilt is stored internally.
So requesting ``drone.get_state(MaxTiltChanged)["current"]`` will retrieve the current (i.e.
last remembered) value of maximum tilt.

Except for this example purpose, there is no need to ask for it, since it is a
setting the drone naturally sends itself at connection.

*Execution*
~~~~~~~~~~~
To execute this script and see the maximum tilt value set, run the following command from the
shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./maxtilt_get.py

This should print the current maximum tilt value in your terminal.

.. _max-tilt-get-diag:


