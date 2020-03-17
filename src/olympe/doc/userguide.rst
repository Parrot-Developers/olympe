.. _user-guide:

User guide
==========

This guide will walk you through Olympe API using a series of examples that increasingly demonstrate
more advanced usage.

If you haven't followed Olympe :ref:`installation procedure<installation>`, you should do it now.

For your own safety and the safety of others, the following examples will use a simuated ANAFI drone
but remember that you can also connect to a physical drone.

At the end of each example, remember to reset the simulation before getting into the next example
because each example assume that the drone is landed with a fully charged battery. Just hit
Ctrl+R inside the Sphinx GUI to reset the simulation.

The full code of each example can be found in the
`src/olympe/doc/examples/ <https://github.com/Parrot-Developers/olympe/tree/master/src/olympe/doc/examples>`_
folder.

Create a simulated drone
------------------------

First things first, you need a drone to connect to. For this example we will use (sphinx_) to create
a simulated drone and then connect to it using Olympe before sending our first commands.

If you haven't installed (sphinx_) yet, now is a good time to install it.

.. _sphinx: {{ sphinx_doc_url }}

.. code-block:: console

    $ sudo systemctl start firmwared
    $ sphinx /opt/parrot-sphinx/usr/share/sphinx/drones/anafi4k.drone::stolen_interface=::simple_front_cam=true

The above commands start a simulation of an ANAFI drone with a simplified front camera and without
a wifi interface. In the following examples, we will be using the virtual ethernet interface, and
reach for the simulated drone at "10.202.0.1".

Setup your shell environment
----------------------------

Don't forget to :ref:`set up your Python environment<environment-setup>` using the
``shell``.

.. code-block:: console

    $ source ~/code/{{ workspace }}/{{ olympe_scripts_path }}/shell
    ({{ python_prompt }}) $

.. _arsdk-messages-intro:

ARSDK messages explained
------------------------

At its core, Olympe basically just send and receive
:ref:`ARSDK messages<messages-reference-documentation>` to control a drone.
For example, the following sequence diagram shows what is happening when an Olympe scripts sends a
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
above, it then waits for a response from the drone, the
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


Olympe basics
-------------

Taking off - "Hello world" example
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The first thing you might want to do with Olympe is making your drone to take off. In this example
we'll write a simple python script that will connect to the simulated drone we've just created
and then send it a :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command.

Create the following python ``takeoff.py`` script somewhere in your home directory:

.. literalinclude:: examples/takeoff.py
    :language: python
    :linenos:

First, this script imports the ``olympe`` module and then the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command message from the arsdk
:ref:`ardrone3-features`. A "feature" is just a collection of related command and event messages
that the drone exchanges with the controller (`FreeFlight`, `Skycontroller`, `Olympe`, ...).

Next, this script creates the ``drone`` interface object with the :py:class:`olympe.Drone` class.
For ``anafi`` this class constructor requires only one argument: the drone IP address. For a
simulated drone, we can use "10.202.0.1" which is the default drone IP address over the virtual
Ethernet interface.

:py:meth:`olympe.Drone.connect` actually performs the connection to the drone. This would fail if
the drone is unreachable (or non-existent) for some reason.

Then, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command to the drone and then waits for
the drone to acknowledge the command. When the ``wait()`` function returns, our simulated drone
should be taking off. For now, we will always use the ``drone(....).wait()`` construct to send
command message and will explain later what the ``wait()`` function does and what we could do
differently with or without it.

Finally, :py:meth:`olympe.Drone.disconnect` disconnect Olympe from the drone properly.

To execute this script and see your drone taking off, from the same shell/terminal you've just
source'd the ``shell`` script:

.. code-block:: console

    ({{ python_prompt }}) $ python ./takeoff.py


Getting the current value of a drone state or setting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will be using the :py:meth:`~olympe.Drone.get_state` method to query the current
"maximum tilt" setting value.

When the maximum tilt drone setting is changed by the controller (Olympe) with the
:py:func:`~olympe.messages.ardrone3.PilotingSettings.MaxTilt` command message the drone sends the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event message in response.
Changing a drone setting will be demonstrated in the following example. Here, we're just interested
in getting the current drone setting.

When Olympe connects to a drone it also asks the drone to send back all its event messages in order
to initialize Olympe drone state information as returned by the :py:meth:`olympe.Drone.get_state`
method. So **if Olympe is connected to a drone** :py:meth:`olympe.Drone.get_state` always returns
the current drone state associated to an **event message**.

In this case, we will be passing the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` message to the
:py:meth:`olympe.Drone.get_state` method. This will return a dictionary of the
:py:func:`~olympe.messages.ardrone3.PilotingSettingsState.MaxTiltChanged` event message which
provide the following parameters:

    :MaxTiltChanged Parameters:
        - current (float) – Current max tilt
        - min (float) – Range min of tilt
        - max (float) – Range max of tilt

Note: Don't be confused here, the "min" and "max" parameters are actually the minimum and the
maximum values for the "maximum tilt" setting. Here, we are only interested in the "current"
value of this setting.

Let's practice! First, reset the simulation (Ctrl+R inside the Sphinx GUI).

Create the following python ``maxtiltget.py`` script somewhere in your home directory:

.. literalinclude:: examples/maxtiltget.py
    :language: python
    :linenos:

To execute this script and see your drone taking off, from the same shell/terminal you've just
source'd the ``shell`` script:

.. code-block:: console

    ({{ python_prompt }}) $ python ./maxtiltget.py

This should print the current maximum tilt value in your console. The following sequence diagram
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

Changing a drone setting - Understand the "expectation" mechanism
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will change the "maximum tilt" drone setting. This setting indirectly controls
the maximum drone acceleration and speed. The more the drone can tilt, the more the drone gain
speed.

The maximum tilt setting itself must be within a minimum and a maximum value. A drone with a max
tilt value of 0° is not particularly useful while a maximum tilt of 180° might only be useful for
a racer drone. For ANAFI the maximum tilt setting must be within 1° and 40°.

You might be wondering:

    - What is happening when you send an invalid setting value (ex: 180°)?
    - How does the drone respond to that?
    - How do we catch this kind of error with Olympe?

Let's see how it's done in the following example. Some important explanations will follow.

First, reset the simulation (Ctrl+R inside the Sphinx GUI).

Create the following python ``maxtilt.py`` script somewhere in your home directory:

.. literalinclude:: examples/maxtilt.py
    :language: python
    :linenos:

This time, the script starts by importing the
:py:func:`~olympe.messages.ardrone3.PilotingSettings.MaxTilt` command from the ``ardrone3`` feature.
Then, it connects to the drone and sends two MaxTilt commands. The first one with a 10° tilt value,
the second with a 0° tilt value.


Note that this time, we are assigning into the ``maxTiltAction`` variable the object returned by the
``.wait()`` method. For now, all you have to know is that you can call ``.success()`` on an action
object if you want to know if your command succeeded or not. The ``success()`` function just returns
``True`` in case of success and ``False`` otherwise. You can also call ``.timedout()`` on an action
to know if the your command message timed out. This ``.timedout()`` method is not particularly
useful in this example because we always call ``.wait()`` on the action object so the action is
either successful or has timed out.

To execute this script, from the same shell/terminal you have source'd the ``shell`` script in:

.. code-block:: console

    ({{ python_prompt }}) $ python ./maxtilt.py

If all goes well, you should see the following output in your console:

.. code-block:: console

    MaxTilt(10) success
    MaxTilt(0) timedout

Obviously, the 10° maximum tilt value is correct so the first command succeeded while the second
command failed to set an incorrect 0° maximum tilt value.

It is important to understand how Olympe knows if a particular command succeeded or not. When
olympe sends a **command message**, it usually implicitly expects an **event message** in return.

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
receives a ``MaxTiltChanged(1)`` **event message** because 0° is an invalid setting value so the
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

.. _moving-around-example:

Moving around - Waiting for a 'hovering' flying state
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will move our drone around using the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command.

First, reset the simulation (Ctrl+R inside the Sphinx GUI).

Create the following python ``moveby.py`` script somewhere in your home directory:

.. literalinclude:: examples/moveby.py
    :language: python
    :linenos:

First, this script imports the ``olympe`` module and then the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` and
:py:func:`~olympe.messages.ardrone3.Piloting.Landing`
**command messages** from the ``ardrone3`` feature. It then connects to the drone and send the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` and
:py:func:`~olympe.messages.ardrone3.Piloting.Landing` commands.

This script should work as-is, right? Let's see.

To execute this script, from the same shell/terminal you've source'd the ``shell`` script:

.. code-block:: console

    ({{ python_prompt }}) $ python ./moveby.py

The output of this script should be:

.. code-block:: console

    moveBy timedout

Wait! The drone takes off and then eventually lands without performing the moveBy?! What happened?

When olympe sends a command message to the drone it expects an acknowledgement event message from
the drone in return. In this script, ``drone(TakeOff()).wait()`` sends the
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command to the drone and then **waits for the
drone taking off event message** as an acknowledgment from the drone. Olympe knows that after a
:py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command it should expect a
``FlyingStateChanged(state='takingoff')`` and automatically waits for that event for you.


The problem with the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command is that it is rejected by the drone as
long as the drone is not in the "hovering" flying state. In this case it is rejected because after
the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command the drone is in ``takingoff``
flying state.  So, to correct this script we will have to wait for the ``hovering`` state before
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

.. literalinclude:: examples/moveby2.py
    :language: python
    :linenos:

This new script will wait for the hovering state after each command sent to the drone.
To do that, it imports the :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
**event message** from the same ``ardrone3`` feature.


Note: The expectations for each command messages are defined in ``arsdk-xml`` along with the command
and event messages themselves.

.. literalinclude:: examples/moveby2.py
    :language: python
    :linenos:
    :lineno-start: 9
    :lines: 9-12

In this new example after the drone connection, the above code tells Olympe to:

    1. Send the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command
    2. Then, implicitly wait for the expectations of the
       :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command:
       ``FlyingStateChanged(state='takingoff')``
    3. Then, explicitly wait for the drone ``hovering`` flying state event:
       ``FlyingStateChanged(state='hovering')``


Here, the ``drone()`` functor accepts more than just a command message. The ``drone()`` takes an
expression that may be a combination of command and event messages to process.
The ">>" operator is used to combine two expressions with an "and then" semantic. This example
could be read as "Take off and then wait a maximum of 5 seconds for the "hovering" flying state").

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

Let's check everything works! Reset the simulation (Ctrl+R inside the Sphinx GUI) and execute this
script, from the same shell/terminal you have source'd the ``shell`` script:

.. code-block:: console

    ({{ python_prompt }}) $ python ./moveby.py

And it should work now! The drone should take off, perform a forward move by 10 meters and then
land.


Explore available commands
--------------------------

If you followed this guide so far, you might want to explore the
:ref:`messages-reference-documentation`.
If you are looking for a specific message or feature, you can also use the search box within the
sidebar on the left.

Alternatively, you can also use Olympe in an interactive Python console with ``ipython`` and
leverage the autocompletion and the help functions to browse the ARSDK messages.

.. code-block:: console

    ({{ python_prompt }}) $ ipython
    In [1]: import olympe
    In [2]: from olympe.messages.<TAB>
    olympe.messages.animation       olympe.messages.camera          olympe.messages.debug           olympe.messages.generic         olympe.messages.mapper          olympe.messages.precise_home    olympe.messages.thermal
    olympe.messages.ardrone3        olympe.messages.common          olympe.messages.drone_manager   olympe.messages.gimbal          olympe.messages.mediastore      olympe.messages.rth             olympe.messages.user_storage
    olympe.messages.battery         olympe.messages.controller_info olympe.messages.follow_me       olympe.messages.leds            olympe.messages.powerup         olympe.messages.skyctrl         olympe.messages.wifi

    In [3]: from olympe.messages.ardrone3.Piloting import <TAB>
    AutoTakeOffMode  CancelMoveTo     Emergency        Landing          PCMD             StopPilotedPOI   UserTakeOff      moveTo
    CancelMoveBy     Circle           FlatTrim         NavigateHome     StartPilotedPOI  TakeOff          moveBy

    In [4]: from olympe.messages.ardrone3.Piloting import TakeOff
    In [5]: help(TakeOff)
    Help on ardrone3.Piloting.TakeOff object:

    class ardrone3.Piloting.TakeOff(ArsdkMessage)
     |  Ardrone3.Piloting.TakeOff
     |
     |
     |  Ask the drone to take off.
     |
     |  Result: On the quadcopters: the drone takes off if its :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
     |  was landed. On the fixed wings, the landing process is aborted if the
     |  :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged` was landing. Then, event :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
     |  is triggered.


You should now understand the basics of Olympe and should be able to write your own scripts.
The rest of this guide will walk you through the most advanced (nevertheless important) features of
Olympe.

Advanced usage examples
-----------------------

Using Olympe asynchronously
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In the basic examples above we've always performed actions `synchronously` because we were always
immediately waiting for an action to complete with the ``.wait()`` method.

In this example, we will send some flying commands to the drone asynchronously. While the drone
executes those commands, we will start a video recording and change the gimbal velocity target along
the pitch axis. After sending those camera-related commands, we will call the ``.wait()`` method
on the "flying action" and then stop the recording.

Create the following python ``asyncaction.py`` script somewhere in your home directory:

.. literalinclude:: examples/asyncaction.py
    :language: python
    :linenos:

Reset the simulation (Ctrl+R inside the Sphinx GUI) and execute this
script, from the same shell/terminal you have source'd the ``shell`` script:

In this example, the :py:meth:`olympe.Drone.__call__` functor process commands and events
asynchronously so that multiple commands can be sent to the drone and processed concurrently.
The events associated to asynchronous actions are interleaved in an undefined order.
The following sequence diagram illustrates a **possible sequence of event** for this script.

.. _asyncaction-diag:

.. seqdiag::
   :caption: Asynchronous command examples
   :align: center

   seqdiag {
      activation = none;
      edge_length = 260;
      default_fontsize = 10;

      Olympe => Drone [label = "connect", return = "connected"]
      Olympe ->> Drone  [label = "TakeOff()", leftnote="flyingAction pending"]
      Olympe ->> Drone  [label = "VideoV2(record='start')", leftnote="start record pending"]
      Olympe <<-- Drone [label = "VideoStateChangedV2(state='started')", leftnote="start record successful"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='motor_ramping')", leftnote="flyingAction pending"];
      Olympe ->> Drone  [label = "set_target(control_mode='velocity', ...)", leftnote="cameraAction successful"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='takingoff')", leftnote="flyingAction pending"];
      Olympe --> Olympe [leftnote = "waiting for FlyingStateChanged(state='hovering')"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='hovering')", leftnote = "flyingAction pending"];
      Olympe ->> Drone  [label = "moveBy()", leftnote="flyingAction pending"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='flying')", leftnote = "flyingAction pending"];
      Olympe <<-- Drone [label = "moveByEnd()", leftnote = "flyingAction pending"]
      Olympe --> Olympe [leftnote = "waiting for FlyingStateChanged(state='hovering')"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='hovering')", leftnote = "flyingAction pending"];
      Olympe ->> Drone  [label = "Landing()", leftnote="flyingAction pending"]
      Olympe <<-- Drone [label = "FlyingStateChanged(state='landing')", leftnote = "flyingAction pending"];
      Olympe <<-- Drone [label = "FlyingStateChanged(state='landed')", leftnote = "flyingAction successful "];
      Olympe ->> Drone  [label = "VideoV2(record='stop')", leftnote="stop record pending"]
      Olympe <<-- Drone [label = "VideoStateChangedV2(state='stopped')", leftnote="stop record successful"]
      Olympe => Drone [label = "disconnect", return = "disconnected"]
   }

Using Olympe enums types
^^^^^^^^^^^^^^^^^^^^^^^^

In addition to the :ref:`ARSDK messages<arsdk-messages-intro>`, Olympe also provides
Python Enum and Bitfield types in the `olympe.enums.<feature_name>[.<class_name>]` modules.

Most of the time, you shouldn't really need to import an enum type into your script because enum
types are implicitly constructed from a string when you create a message object so the following
examples are roughly equivalent:

.. literalinclude:: examples/enums.py
    :language: python
    :linenos:

Olympe enum types should behave very much like Python 3 `enum.Enum`.

Bitfields types are associated to each ARSDK enums types and are occasionally used by ARSDK
messages. A Bitfield type can hold any combination of its associated Enum type values.

Bitfield example:

.. literalinclude:: examples/bitfields.py
    :language: python
    :linenos:

Additional usage examples are available in the unit tests of `olympe.arsdkng.enums`.

Using Olympe exptectation expressions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sometimes it can be useful to send a command to the drone only if it is in a specific state.
For example, if the drone is already hovering when you start an Olympe script, you might want
to skip the usual taking off command. This can be useful if a previous execution of your script left
your drone in a hovering state.

.. literalinclude:: examples/takeoff_if_necessary_1.py
    :language: python
    :linenos:

Here :py:meth:`olympe.Drone.get_state` is used to check the current flying state of the drone. If
the drone is not in hovering, we check and eventually wait for a GPS fix. Note that "check_wait" is
the default value for the `_policy` parameter. The possible values for the `_policy` parameter are:

    - "check", to check the current state of the drone (i.e. match the last event message of this
      kind received from the drone).
    - "wait", to wait for a new event message from the drone (even if the last event message of this
      kind that has been received would have matched).
    - "check_wait" (the default), to "check" the current state of the drone and if necessary "wait"
      for a matching event message.

In the above example we are using a compound expectation expression to send a taking off command
when the drone has a GPS fix and when it is not already in the hovering state.

The default expectations for the :code:`TakeOff` command are:
:code:`FlyingStateChanged(state='motor_ramping', _policy='wait')` & :code:`FlyingStateChanged(state='takingoff', _policy='wait')`
(see the :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command documentation).  When the
controller receives the "takingoff" flying state a few milliseconds after the :code:`TakeOff`
command has been sent, the drone has just climbed a few centimeters. Here, we don't really care for
this "takingoff" flying state and this is why we are disabling the default expectations of the
:code:`TakeOff` command. :code:`TakeOff(_no_expect=True)` sends the take off command and does not
wait for the default expectations for this command. Instead of the default expectations, we are
directly expecting the "hovering" flying state. We are using the '&' ("AND") operator instead of
'>>' ("THEN") to wait for this event while Olympe sends the :code:`TakeOff` command *concurrently*.
If the ">>" ("THEN") operator were to be used instead, we might (theoretically) miss the
*FlyingStateChanged* event drone response while Olympe sends the 'TakeOff' message.

As demonstrated below, this problem can also be solved without using any control flow statements:

.. literalinclude:: examples/takeoff_if_necessary_2.py
    :language: python
    :linenos:

Here, the '|' ("OR") operator is used to "check" if the current flying state is "hovering".  If not,
we wait for a GPS fix if necessary with the implicit "check_wait" policy. Then (">>") we send the
taking off command and override the default expectations to wait for the "hovering" flying state as
before.

Capture the video streaming and its metadata
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Once you are connected to a drone with Olympe, to start the video streaming just call the
:py:func:`olympe.Drone.start_video_streaming` function and the drone will start sending its
video stream to Olympe. Call :py:func:`olympe.Drone.stop_video_streaming` the video streaming.

Realtime capture
""""""""""""""""

Before you start the video streaming, you can register some callback functions that will be called
whenever Olympe receive/decode a new video frame. See
:py:func:`~olympe.Drone.set_streaming_callbacks`.

Record the live/replayed video stream for a post-processing
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

Before you start the video streaming, you can specify some output files that will be used by Olympe
to record the video stream and its metadata.
:py:func:`~olympe.Drone.set_streaming_output_files`.

Video streaming example
"""""""""""""""""""""""

The following example shows how to get the video stream from the drone using
Olympe. Internally, Olympe leverages Parrot libpdraw to:

    - initialize the video streaming from the drone
    - decode the H.264 video stream
    - register user provided callback functions that are called for
      each (encoded or decoded) frame with its associated metadata
    - record the live video stream from the drone to the disk

When using Olympe to access the video stream you can't use the
`PDrAW <https://developer.parrot.com/docs/pdraw/overview.html>`_ standalone
executable to view the video stream (the drone only supports one video client
at a time).

For this example, we first create a fixture class that will hold our
olympe.Drone object and some H.264 statistics.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 28
    :lines: 28-44

Our objective is to start the video stream, fly the drone around, perform some
live video processing, stop the video stream and finally perform some video
postprocessing.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 222
    :lines: 222-231

Before we start the video streaming, we must connect to the drone and optionally
register our callback functions and output files for the recorded video stream.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 46
    :lines: 46-69

The :py:func:`StreamingExample.yuv_frame_cb` and
:py:func:`StreamingExample.h264_frame_cb` receives an
:py:func:`olympe.VideoFrame` object in parameter that you can use to access a
video frame data (see: :py:func:`olympe.VideoFrame.as_ndarray`,
:py:func:`olympe.VideoFrame.as_ctypes_pointer`) and its metadata
(see: :py:func:`olympe.VideoFrame.info` and :py:func:`olympe.VideoFrame.vmeta`).

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 130
    :lines: 130-152

The `.264` file recorded by Olympe contains raw H.264 frames. In order to
view this file with your favorite media player, you might need to convert it
into an `.mp4` file. Here as our postprocessing step, we are merely copying the
H.264 video frames into an MP4 container.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 205
    :lines: 205-219

The full code of this example can be found in
`src/olympe/doc/examples/streaming.py <https://github.com/Parrot-Developers/olympe/blob/master/src/olympe/doc/examples/streaming.py>`_.


Post-processing a recorded video
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can also use Olympe to perform some post-processing on an .MP4 file downloaded from the drone.

.. literalinclude:: examples/pdraw.py

See the :py:class:`~olympe.Pdraw` documentation for more information.

Connect to a physical drone or to a SkyController
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. warning ::

    **DISCLAIMER**
    You should really carefully validate your code before trying to control a physical drone through
    Olympe. Use at your own risk.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
    FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
    INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
    BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
    OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
    AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
    OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
    OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
    SUCH DAMAGE.


Connect to a physical drone
^^^^^^^^^^^^^^^^^^^^^^^^^^^

To connect olympe to a physical drone, you first need to connect to your linux
box to a drone wifi access point. once you are connected to your drone over wifi,
you just need to specify the drone ip address on its WiFi interface ("192.168.42.1").

.. literalinclude:: examples/physical_drone.py
    :language: python
    :linenos:


Connect to a SkyController
^^^^^^^^^^^^^^^^^^^^^^^^^^

To connect Olympe to a physical SkyController, you first need to connect to your Linux
box to the SkyController 3 USB-C port. Then you should be able to connect to your SkyController
with its RNDIS IP address ("192.168.53.1").

.. literalinclude:: examples/physical_skyctrl.py
    :language: python
    :linenos:


Pair a SkyController with a drone
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If your SkyController is not already connected to a drone, you may have to pair it first.

.. literalinclude:: examples/skyctrl_drone_pairing.py
    :language: python
    :linenos:


TODO
^^^^

.. todo::
    Document the expectation "explain" method. Maybe insert it into a "How to debug" section.
