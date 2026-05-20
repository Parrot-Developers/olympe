Change a drone setting - Understand the "expectation" mechanism
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Objective:**

Change the current value of the "maximum tilt" drone's setting.

*A little explanation first*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
This setting indirectly controls the maximum drone acceleration and speed. The more the
drone can tilt, the more speed the drone gains.

The maximum tilt value must be within a minimum and maximum range. A drone with
a max tilt value of 0° is not particularly useful while a maximum tilt over 180° might
only be useful for a racer.

.. note::

    For Anafi the maximum tilt setting must be within 1° and 40°.

You might be wondering:

    - What is happening when you send an invalid setting value (ex: 180°)?
    - How does the drone respond to that?
    - How do we catch this kind of error with Olympe?

Let's see how this is done in the following example. First, reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal.


Copy paste the following Python ``maxtilt_set.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/maxtilt_set.py
    :language: python
    :linenos:

*Body*
~~~~~~
This time, the script connects to the drone and sends two MaxTilt commands. The first
one with a 10° tilt value, the second with a 0° tilt value.

Note that this time, we are assigning the object returned by the :py:meth:`olympe.Expectation.wait`
method
into the ``maxTilt_expectation`` variable. For now, all you have to know is that you can call
``.success() -> bool`` on an "expectation" object to know if your command
has succeeded or not. The :py:meth:`olympe.Expectation.success` function returns ``True``
in case of success and ``False`` otherwise.

You can also call ``.timedout() -> bool`` on an "action" object to know if your command
message timed out.  This :py:meth:`olympe.Expectation.timedout` method is not particularly
useful in this example because we always call :py:meth:`olympe.Expectation.wait` on the
action object, so the action is either successful or has timed out already.

*Execution*
~~~~~~~~~~~
To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./maxtilt_set.py

If all goes well, you should see the following output in your terminal:

.. code-block:: console

    MaxTilt(10) set succeeded
    MaxTilt(0) set timedout

Obviously, the 10° maximum tilt value is correct, so the first command succeeded while
the second command failed to set an incorrect 0° maximum tilt value.

How does Olympe deal with expectation timeout and drone refusal when setting wrong
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
It is important to understand how Olympe knows if a particular command succeeded or not.
When Olympe sends a **command message**, it usually implicitly expects an **event message**
in return.

.. note::

    Command messages and event messages are somewhat similar. They are both associated
    with an internal unique ID and eventually with some arguments (ex: the maximum tilt
    value) and they both travel from one source to a destination.

When Olympe sends the ``MaxTilt(10)`` command message, it implicitly expects a
``MaxTiltChanged(10)`` event message in return. If the event is received in time,
everything is fine: ``maxTilt_expectation.success()`` is True and ``maxTilt_expectation.timedout()``
is False. Otherwise, the ``maxTilt_expectation`` times out (``maxTilt_expectation.success()`` is
False and ``maxTilt_expectation.timedout()`` is True).

When Olympe sends the ``MaxTilt(0)`` command message, it receives a ``MaxTiltChanged(1)``
event message because 0° is an invalid setting value. The drone informs the controller
that it has set the minimum setting value instead (1°). Olympe **does not assume** that
this response means "No, I won't do what you are asking". Instead, it still waits for a
``MaxTiltChanged(0)`` event that will never come and the command message times out:
(``maxTilt_expectation.success()`` is False and ``maxTilt_expectation.timedout()`` is True).

This behavior is identical for every command message: **when Olympe sends a command
message to a drone, it either results in a success or a timeout**.

.. important::

    The ARSDK protocol defined in `arsdk-xml` does not provide a way to report errors
    uniformly. This is why Olympe cannot detect errors like this one and just time out
    instead. Olympe associates to each command a default timeout that can be overridden with
    the `_timeout` message parameter. For example:

    .. code-block:: python

        maxTilt_expectation = drone(MaxTilt(10, _timeout=1)).wait()

