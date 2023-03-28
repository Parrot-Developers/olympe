Using Olympe asynchronously
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In the basic examples above we've always performed actions `synchronously` because we were always
immediately waiting for an action to complete with the ``.wait()`` method.

In this example, we will send some flying commands to the drone asynchronously. While the drone
executes those commands, we will start a video recording and change the gimbal velocity target along
the pitch axis. After sending those camera-related commands, we will call the ``.wait()`` method
on the "flying action" and then stop the recording.

Create the following python ``asyncaction.py`` script somewhere in your home directory:

.. literalinclude:: ../../examples/asyncaction.py
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

