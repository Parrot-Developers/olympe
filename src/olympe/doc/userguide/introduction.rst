Introduction
------------

At its core, Olympe basically just sends and receives
:ref:`ARSDK messages<messages-reference-documentation>` to control a drone.

Some examples are mentioned below.

When Olympe sends a command message like :py:func:`~olympe.messages.ardrone3.Piloting.TakeOff`,
it then waits for a response from the drone:
:py:func:`FlyingStateChanged("takingoff")<olympe.messages.ardrone3.PilotingState.FlyingStateChanged>`
(or :py:func:`FlyingStateChanged(state="takingoff")<olympe.messages.ardrone3.PilotingState.FlyingStateChanged>`)
event message in this case.

Sometimes, the drone can spontaneously notify its controller (Olympe) of a particular event.
Olympe provides a way to monitor such events (or a combination of such events).
The message
:py:func:`GPSFixStateChanged(0)<olympe.messages.ardrone3.GPSSettingsState.GPSFixStateChanged>`
(or :py:func:`GPSFixStateChanged(fixed=0)<olympe.messages.ardrone3.GPSSettingsState.GPSFixStateChanged>`)
informs Olympe that the GPS fix has been lost, and this message can be expected (See
:ref:`Olympe eDSL` section).

As a user of Olympe, you might be occasionally interested in the current state of the
drone, without monitoring every received message. Olympe remembers the last
received event of every aspect that provides this state information and exposes this
information through the :py:meth:`olympe.Drone.get_state` method.

As demonstrated in the following usage examples, Olympe provides a relatively simple API
to perform the above actions (and much more) using the following :py:class:`olympe.Drone<olympe.Drone>`
class and its methods.

ARSDK message Python types are available in the `olympe.messages.<feature_name>[.<class_name>]`
modules. Likewise, ARSDK enum Python types are available in the `olympe.enums.<feature_name>[.<class_name>]`
modules.

See the :ref:`messages-reference-documentation` for more information.

