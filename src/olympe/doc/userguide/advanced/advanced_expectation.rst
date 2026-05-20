Exploit advanced expectations
-----------------------------

Before continuing, you might want to read the :ref:`Olympe eDSL <Olympe eDSL>` section
if you haven't read it already.

**Objective:**

Send a command to the drone only if it is in a specific state.

For example, if the drone is already 'hovering' when you start an Olympe script, you might
want to skip the usual takeoff command. This can be useful if a previous execution of
your script left your drone in a 'hovering' state.

Copy paste the following Python ``takeoff_if_necessary_1.py`` script somewhere in your
directory:

.. literalinclude:: ../../examples/takeoff_if_necessary_1.py
    :language: python
    :linenos:


*A little explanation*
~~~~~~~~~~~~~~~~~~~~~~
Here, :py:meth:`olympe.Drone.get_state` is used to check the current flying state of the
drone. If the drone is not in a 'hovering' state, we wait for a GPS fix and request a
take off.

.. _expect_policy:

.. note::

  Note that "check_wait" is the default value for the `_policy` parameter. The possible
  values for the `_policy` parameter are:

      - "check": check the current state of the drone (i.e. match the last event
        message of this kind received from the drone).
      - "wait": wait for a new event message from the drone (even if the last event
        message of this kind that has been received would have matched).
      - "check_wait" (the default): check the current state of the drone and if necessary
        "wait" for a matching event message.

*Body*
~~~~~~
In the above example we are using a compound expectation expression to send a take off
command after the drone has had a GPS fix, and we wait for the 'takingoff' state.

The default expectations for the :code:`TakeOff` command are:
:code:`FlyingStateChanged(state='motor_ramping', _policy='wait')` &
:code:`FlyingStateChanged(state='takingoff', _policy='wait')`.

.. note::

  If you need more info on that, see the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`
  command documentation.

When the controller receives the 'takingoff' flying state a few milliseconds after the
:code:`TakeOff` command has been sent, the drone has just climbed a few centimeters.

.. important::

  We don't really care about this 'takingoff' flying state and this is why we are disabling
  the default expectations of the :code:`TakeOff` command. :code:`TakeOff(_no_expect=True)`
  sends the :code:`TakeOff` command and does not wait for the default expectations of
  this command.

Instead of the default expectation, we directly expect the 'hovering' flying state. We
are using the '&' ("AND") operator instead of '>>' ("AND THEN") to wait for this event while
Olympe sends the :code:`TakeOff` command *concurrently*.

If the ">>" ("AND THEN") operator was to be used instead, we might (theoretically) miss the
*FlyingStateChanged* drone's event response while Olympe sends the 'TakeOff' message.

As demonstrated below, this problem can also be solved without using any control flow
statements:

.. literalinclude:: ../../examples/takeoff_if_necessary_2.py
    :language: python
    :linenos:

Here, the '|' ("OR") operator is used to "check" if the current flying state is 'hovering'.
If not, we wait for a GPS fix if necessary with the implicit "check_wait" policy. Then
(">>") we send the takeoff command and override the default expectations to wait for
the 'hovering' flying state as before.

