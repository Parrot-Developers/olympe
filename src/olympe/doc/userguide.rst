.. _user-guide:

User guide
==========

This guide will walk you through Olympe API using a series of examples that increasingly demonstrate
more advanced usage.

Before continuing you should first read the Olympe :ref:`installation procedure<installation>`.

For your own safety and the safety of others, the following examples will use a simulated ANAFI
drone but remember that Olympe is also capable of communicating with a physical drone. As far as
Olympe is concerned, the main difference between a physical and a simulated drone is the drone IP
address (``192.168.42.1`` for a physical drone and ``10.202.0.1`` for a simulated one).

At the end of each example, if you are using a simulated drone, remember to reset the simulation
before getting into the next example. Each example assumes that the drone is landed with a fully
charged battery. Just enter ``sphinx-cli action -m world fwman world_reset_all`` in a terminal to
reset the current simulation.

The full code of each example can be found in the
`src/olympe/doc/examples/ <https://github.com/Parrot-Developers/olympe/tree/master/src/olympe/doc/examples>`_
folder.

Create a simulated drone
------------------------

First things first, you need a drone to connect to. For this example we will use Sphinx_ to create
a simulated drone and then connect to it using Olympe before sending our first commands.

If you haven't installed Sphinx_ yet, now is a good time to install it.

.. _sphinx: {{ sphinx_doc_url }}

Then in a shell enter the following commands:

.. code-block:: console

    $ sudo systemctl start firmwared.service
    $ sphinx "/opt/parrot-sphinx/usr/share/sphinx/drones/anafi_ai.drone"::firmware="ftp://<login>:<pass>@ftp2.parrot.biz/versions/anafi2/pc/%23latest/images/anafi2-pc.ext2.zip"

Where ``login`` is the one from your Parrot partner FTP account and ``pass`` is the associated
password.

The core application is now waiting for an UE4 application to connect… In a second shell, do:

.. code-block:: console

   $ parrot-ue4-empty

The above commands start a simulation of an ANAFI Ai drone in an empty world. In the following
examples, we will be using the virtual ethernet interface of the simulated drone, and reach it at
``10.202.0.1``.

For more information on Sphinx, please consult its comprehensive `user documentation <{{sphinx_doc_url}}>`_.

Set up your shell environment
-----------------------------

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


Olympe's basics
---------------

Taking off - "Hello world" example
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The first thing you might want to do with Olympe is making your drone to take off. In this example
we'll write a simple Python script that will connect to the simulated drone we've just created
and then send it a :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff` command.

Create the following Python ``takeoff.py`` script somewhere in your home directory:

.. literalinclude:: examples/takeoff.py
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

    ({{ python_prompt }}) $ python ./takeoff.py


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

.. literalinclude:: examples/maxtiltget.py
    :language: python
    :linenos:

To execute this script and see your drone taking off, from the same shell/terminal you've just
source'd the ``shell`` script:

.. code-block:: console

    ({{ python_prompt }}) $ python ./maxtiltget.py

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

.. literalinclude:: examples/maxtilt.py
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

    ({{ python_prompt }}) $ python ./maxtilt.py

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

.. _moving-around-example:

Moving around - Waiting for a 'hovering' flying state
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In this example, we will move our drone around using the
:py:func:`~olympe.messages.ardrone3.Piloting.moveBy` command message.

First, reset the simulation (``sphinx-cli action -m world fwman world_reset_all`` in a terminal).

Create the following Python ``moveby.py`` script somewhere in your home directory:

.. literalinclude:: examples/moveby.py
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

    ({{ python_prompt }}) $ python ./moveby.py

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

.. literalinclude:: examples/moveby2.py
    :language: python
    :linenos:

This new script will wait for the hovering state after each command sent to the drone.
To do that, it imports the :py:func:`~olympe.messages.ardrone3.PilotingState.FlyingStateChanged`
**event message** from the same ``ardrone3.PilotingState`` module feature.


Note: The expectations for each command message are defined in the ``arsdk-xml`` source repo along
with the command and event messages themselves.

.. literalinclude:: examples/moveby2.py
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

    ({{ python_prompt }}) $ python ./moveby.py

And it should work now! The drone should take off, perform a forward move by 10 meters and then
land.


Explore available ARSDK commands
--------------------------------

If you followed this guide so far, you might want to explore the
:ref:`messages-reference-documentation`.
If you are looking for a specific message or feature, you can also use the search box within the
sidebar on the left.

Alternatively, you can also use Olympe in an interactive Python console with ``ipython`` and
leverage the autocompletion and the ``help`` function to browse the available ARSDK messages.

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


Using Parrot AirSDK missions with Olympe
----------------------------------------

Olympe integrates with the `Parrot AirSDK <https://developer.parrot.com/docs/airsdk/general/overview.html>`_
and enables you to install AirSDK "missions" (i.e. Parrot and Parrot partners applications) onto
a remote drone connected to Olympe.

Once installed onto the done, Olympe is able to exchange mission specific messages with the drone.

The example below illustrate this installation process and some basic interaction with the `Air SDK
"Hello, Drone!" <https://dpc-dev.parrot.com/docs/airsdk/general/sample_hello.html#sample-hello>`_
mission.

.. literalinclude:: examples/mission.py
    :language: python
    :linenos:

.. _Olympe eDSL:

Olympe Expectation objects and the Olympe eDSL
----------------------------------------------

Before introducing more advanced feature, it seems important to take a moment to have a better
understanding of the Olympe specific usage of Python operators to compose "Expectation" objects
inside the ``drone()`` functor.

Olympe Expectation objects
^^^^^^^^^^^^^^^^^^^^^^^^^^

First, let's explain what is an "Expectation" object.  "Expectation" objects are a special kind of
"`Future <https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future>`_"-like
objects from the Python stdlib. People more familiar with Javascript might want to compare
"Expectation" classes with the "`Promise <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise>`_"
class from the standard.

Olympe creates "Expectation" objects whenever a message object is "called". For example,
``takeoff_expectation = TakeOff()`` creates a ``takeoff_expectation`` object from the
``ardrone3.Piloting.TakeOff`` command message we've been using in the previous example.

Simply creating an expectation object has no side effect, you must pass the expectation object to a
``drone`` object to "schedule" it. Continuing with our previous example
``drone(takeoff_expectation)`` will "schedule" the ``takeoff_expectation``. Here "scheduling" this
expectation actually means sending the ``TakeOff`` message to the drone and wait for the ``TakeOff``
message default expectations (``FlyingStateChanged(state="takingoff")``). Let us pause on that. This
means that an expectation objects:
- has a potential side effect when it is "scheduled" by a ``drone`` object (here we send the
``TakeOff`` message)
- may have "sub-expectation(s)" (here the ``FlyingStateChanged(state="takingoff")`` event message)

For convenience, the ``drone()`` functor returns the expectation object it has received in
parameter. This enables the possibility to create and schedule an expectation object in one
expression, for example: ``takeoff_expectation = drone(TakeOff())``.

Olympe Expectation eDSL
^^^^^^^^^^^^^^^^^^^^^^^

Now that we know that one "Expectation" object can be comprised of other expectation objects, like
this is the case of the ``takeoff_expectation`` in the previous paragraph, we might want to compose
expectation objects ourselves.

Olympe supports the composition of expectation objects with 3 Python binary operators:
``|`` ("OR"), ``&`` ("AND"), and ``>>`` ("AND THEN"). This feature has been briefly introduced in
the ``Moving around - Waiting for a 'hovering' flying state`` previous example where the ``>>``
"and then" operator is used to wait for the "hovering" flying state after a ``moveBy`` command.

.. code-block:: python

    expectation_object = drone(
        moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
    )

This specific syntax that makes use of the Python operator overloading feature is what is called
an "`embedded Domain Specific Language
<https://en.wikipedia.org/wiki/Domain-specific_language#External_and_Embedded_Domain_Specific_Languages>`_"
and we might refer to it as the Olympe "Expectation eDSL".

Here, the ``drone()`` functor accepts more than just one command message expectation. The
``drone()`` functor takes an expression that may be a combination of command and event messages to
process. This expression actually results in the creation of a compound expectation object.
The ">>" operator is used to combine two expressions with an "and then" semantic. This example
could be read as "Take off and then wait a maximum of 5 seconds for the 'hovering' flying state").

You can choose to schedule an Olympe eDSL expression with or without waiting for the end of its
execution, just call ``.wait()`` on the expectation object to block the current thread until the
expectation object is done (i.e. successful or timedout).

When a compound expectation fails (or times out) you might want to understand what went wrong. To
that end, you can use the ``.explain()`` that returns a string representation of the compound
expectation. The ``.explain()`` method highlights in green the part of the expectation that was
successful and in red the part of the compound expectation that has failed.

Programmatic eDSL construct
^^^^^^^^^^^^^^^^^^^^^^^^^^^

You also have the ability to construct a compound expectation object programmatically before
scheduling it, for example:

.. code-block:: python

    expectation_object = (
        moveBy(10, 0, 0, 0)
        >> FlyingStateChanged(state="hovering", _timeout=5)
    )
    for i in range(3):
        expectation_object = expectation_object
            >> moveBy(10, 0, 0, 0)
            >> FlyingStateChanged(state="hovering", _timeout=5)
        )
    drone(expectation_object).wait(60)
    assert expectation_object.success()


Each expectation part of a compound expectation may be given a specific `_timeout` value in seconds
that is independent of the global compound expectation timeout value that may be specified later to
the ``.wait()`` method. When ``.wait()`` is called without a timeout value, this method blocks the
current thread indefinitely until the expectation succeeds or until a blocking sub-expectation has
timedout. In the example above, if any of the ``FlyingStateChanged`` expectations times out after 5
seconds, the call to ``drone(expectation_object).wait(60)`` returns and
``expectation_object.success()`` would return ``False``. Likewise, if the drone takes more than 60
seconds to complete this ``moveBy``, the ``expectation_object`` compound expectation times out and
``expectation_object.success()`` returns ``False`` even if no individual ``FlyingStateChanged``
expectation has timedout.

Olympe eDSL operators semantic
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To conclude this quick tour of the expectation eDSL, let's focus on the specific semantic of each
of the supported operator.

The ``>>`` "and then" operator is probably the most useful of them. When an "and then" compound
expectation is scheduled, the left hand side expectation is scheduled and awaited. When the left
hand side expectation is satisfied the right-hand side expectation is scheduled and awaited. If the
left-hand side expectation times out, the left-hand side is never scheduled nor awaited and the
compound expectation times out. The compound "and then" expectation is successful when the
right-hand side is successful.

The ``&`` "and" operator schedules and awaits both the left-hand side and right-hand side
expectations objects simultaneously. The compound "and" expectation is successful when both the
left-hand side and the right hand side expectation are successful without any specific order
requirement. The compound expectation times out if the left-hand side or the right-hand side of the
expectation times out.

The ``|`` "or" operator schedules and awaits both the left-hand side and right-hand side
expectations objects simultaneously. The compound "or" expectation is successful if one of the
left-hand side and the right hand side expectation is successful (or both). The
compound expectation times out if both the left-hand side and the right-hand side of the expectation
times out.

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

Reset the simulation (``sphinx-cli action -m world fwman world_reset_all`` in a terminal) and
execute this script, from the same shell/terminal you have source'd the ``shell`` script:

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
types are implicitly constructed from a string when you create a message object, so the following
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

Using Olympe expectation eDSL
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Before continuing with this Olympe example, you might want to read the :ref:`Olympe eDSL <Olympe eDSL>` section.

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
:code:`TakeOff` command. :code:`TakeOff(_no_expect=True)` sends the takeoff command and does not
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
:py:func:`olympe.Drone.streaming.start` function and the drone will start sending its
video stream to Olympe. Call :py:func:`olympe.Drone.streaming.stop` the video streaming.

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
    :lineno-start: 30
    :lines: 30-44

Our objective is to start the video stream, fly the drone around, perform some
live video processing, stop the video stream and finally perform some video
postprocessing.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 200
    :lines: 200-209

Before we start the video streaming, we must connect to the drone and optionally
register our callback functions and output files for the recorded video stream.

.. literalinclude:: examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 46
    :lines: 46-72

The :py:func:`StreamingExample.yuv_frame_cb` and
:py:func:`StreamingExample.h264_frame_cb` receives an
:py:func:`olympe.VideoFrame` object in parameter that you can use to access a
video frame data (see: :py:func:`olympe.VideoFrame.as_ndarray`,
:py:func:`olympe.VideoFrame.as_ctypes_pointer`) and its metadata
(see: :py:func:`olympe.VideoFrame.info` and :py:func:`olympe.VideoFrame.vmeta`).

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
box to a drone Wi-Fi  access point. Once you are connected to your drone over Wi-Fi,
you just need to specify the drone ip address on its Wi-Fi interface ("192.168.42.1").

.. literalinclude:: examples/physical_drone.py
    :language: python
    :linenos:


Connect to a SkyController
^^^^^^^^^^^^^^^^^^^^^^^^^^

To connect Olympe to a physical SkyController, you first need to connect to your Linux
node to the SkyController 3 USB-C port. Then you should be able to connect to your SkyController
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
