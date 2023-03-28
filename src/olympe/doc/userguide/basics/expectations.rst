Changing a drone setting - Understand the "expectation" mechanism
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will change the "maximum tilt" drone setting. This setting indirectly controls
the maximum drone acceleration and speed. The more the drone can tilt, the more the drone gain
speed.

The maximum tilt setting itself must be within a minimum and maximum range. A drone with a max
tilt value of 0° is not particularly useful while a maximum tilt over 180° might only be useful for
a racer drone. For ANAFI the maximum tilt setting must be within 1° and 40°.

You might be wondering:

    - What is happening when you send an invalid setting value (ex: 180°)?
    - How does the drone respond to that?
    - How do we catch this kind of error with Olympe?

Let's see how this is done in the following example. Some important explanations will follow.

First, reset the simulation (``sphinx-cli action -m world fwman world_reset_all`` in a terminal).

Create the following Python ``maxtilt.py`` script somewhere in your home directory:

.. literalinclude:: ../../examples/maxtilt.py
    :language: python
    :linenos:

This time, the script starts by importing the
:py:func:`~olympe.messages.ardrone3.PilotingSettings.MaxTilt` command from the ``ardrone3`` feature.
Then, it connects to the drone and sends two MaxTilt commands. The first one with a 10° tilt value,
the second with a 0° tilt value.


Note that this time, we are assigning into the ``maxTiltAction`` variable the object returned by the
``.wait()`` method. For now, all you have to know is that you can call ``.success() -> bool`` on an
"action" object (the more general term is "expectation" object) if you want to know if your command
succeeded or not. The ``success()`` function just returns ``True`` in case of success and ``False``
otherwise.

You can also call ``.timedout() -> bool`` on an "action" object to know if your command message
timed out.  This ``.timedout()`` method is not particularly useful in this example because we always
call ``.wait()`` on the action object, so the action is either successful or has timed out.

To execute this script, from the same shell/terminal you have source'd the ``shell`` script in:

.. code-block:: console

    $ python ./maxtilt.py

If all goes well, you should see the following output in your terminal:

.. code-block:: console

    MaxTilt(10) success
    MaxTilt(0) timedout

Obviously, the 10° maximum tilt value is correct, so the first command succeeded while the second
command failed to set an incorrect 0° maximum tilt value.

It is important to understand how Olympe knows if a particular command succeeded or not. When
Olympe sends a **command message**, it usually implicitly expects an **event message** in return.

Up until now, we have only explicitly used **command messages**. Command messages and event messages
are somewhat similar. They are both associated with an internal unique ID and eventually with some
arguments (ex: the maximum tilt value) and they both travel from one source to a destination.

A **command message** travel from the controller (Olympe) to the drone while an **event message**
travel the other way around.

Here, when Olympe sends the ``MaxTilt(10)`` **command message** it implicitly expects a
``MaxTiltChanged(10)`` **event message** in return. If the event is received in time, everything is
fine: ``maxTiltAction.success() is True and maxTiltAction.timedout() is False``. Otherwise, the
``maxTiltAction`` times out (``maxTiltAction.success() is False and maxTiltAction.timedout() is
True``).

The :ref:`following sequence diagram<max-tilt-diag>` illustrates what is happening here.
For the second maximum tilt command, when Olympe sends the ``MaxTilt(0)`` **command message** it
receives a ``MaxTiltChanged(1)`` **event message** because 0° is an invalid setting value, so the
drone just informs the controller that it has set the minimum setting value instead (1°). Olympe
**does not assume** that this response means "No, I won't do what you are asking". Instead, it still
waits for a ``MaxTiltChanged(0)`` event that will never come and the command message times out:
(``maxTiltAction.success() is False and maxTiltAction.timedout() is True``). This behavior is
identical for every command message: **when Olympe sends a command message to a drone, it either
result in a success or a timeout**.

.. _max-tilt-diag:

.. seqdiag::
   :caption: Setting the drone MaxTilt
   :align: center

   seqdiag {
      activation = none;
      edge_length = 400;
      default_fontsize = 14;

      Olympe => Drone [label = "connect", return = "connected"]
      Olympe ->> Drone  [label = "MaxTilt(10)", leftnote="maxTiltAction pending"]
      Olympe <<-- Drone [label = "MaxTiltChanged(current=10., min=1., max=40.)", leftnote="maxTiltAction successful"];
      Olympe ->> Drone  [label = "MaxTilt(0)", leftnote="maxTiltAction pending"]
      Olympe <<-- Drone [label = "MaxTiltChanged(current=1., min=1., max=40.)", leftnote="maxTiltAction still pending"];
      Olympe -> Olympe [leftnote = "maxTiltAction timedout"]
      Olympe => Drone [label = "disconnect", return = "disconnected"]
   }


The arsdk protocol defined in `arsdk-xml` does not provide a way to report errors uniformly. This is
why Olympe cannot detect errors like this one and just time out instead. Olympe associates to each
command a default timeout that can be overridden with the `_timeout` message parameter. For example:

.. code-block:: python

    maxTiltAction = drone(MaxTilt(10, _timeout=1)).wait()

