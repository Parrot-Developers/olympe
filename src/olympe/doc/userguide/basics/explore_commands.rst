Explore available ARSDK commands
--------------------------------

If you followed this guide so far, you might want to explore the
:ref:`messages-reference-documentation`.
If you are looking for a specific message or feature, you can also use the search box within the
sidebar on the left.

Alternatively, you can also use Olympe in an interactive Python console with `IPython
<https://ipython.readthedocs.io/en/stable/index.html>`_ and
leverage the autocompletion and the ``help`` function to browse the available ARSDK messages.

.. code-block:: console

    $ ipython
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

