Opt for asynchronous Olympe
^^^^^^^^^^^^^^^^^^^^^^^^^^^

In the basic examples above we've always performed actions `synchronously` because we
were always immediately waiting for an action to complete with the :py:meth:`olympe.Expectation.wait` method.

**Objective:**

Send flying commands to the drone asynchronously. While the drone executes those commands,
we start a video recording and change the gimbal velocity target along the pitch axis.
After sending those camera-related commands, we call the :py:meth:`olympe.Expectation.wait` method on the
flying expectation and stop the recording.

First, reset the simulation:
``sphinx-cli action -m world fwman world_reset_all`` in a terminal.

Copy paste the following Python ``asyncaction.py`` script somewhere in your directory:

.. literalinclude:: ../../examples/asyncaction.py
    :language: python
    :linenos:

*Body*
~~~~~~
The :py:meth:`olympe.Drone()` class processes commands and events asynchronously so that
multiple commands can be sent to the drone and processed concurrently.
The events associated with asynchronous actions are interleaved in an undefined order. That
is the reason why we have to wait for each of them.

*Execution*
~~~~~~~~~~~
To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./asyncaction.py

