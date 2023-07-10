Displaying the video streaming with an HUD
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Some overlays can be enabled with the :py:class:`~olympe.PdrawRenderer` class.
For now, there are three HUD:

* :ref:`piloting_hud` displaying navigation instruments
* :ref:`imaging_hud` for shooting videos and taking pictures
* :ref:`tracking_hud` with tracking box proposals and tracking box target (Only supported with ANAFI Ai.)

See the :py:class:`~olympe.PdrawRenderer` documentation for more information.

.. _piloting_hud:

Piloting HUD
""""""""""""

Displays flight information such as compass, speed, altitude, battery level and
other useful flight information.

The following example is a lite version of the :ref:`video_streaming_example`.
Only the :py:func:`start()`, :py:func:`stop()` and :py:func:`fly()` functions
are kept.

These lines are used to activate the piloting HUD:

.. literalinclude:: ../../examples/piloting_hud.py
    :language: python
    :linenos:
    :lineno-start: 47
    :lines: 47-49
    :emphasize-lines: 3

The full code of this example can be found in
`src/olympe/doc/examples/piloting_hud.py <https://github.com/Parrot-Developers/olympe/blob/master/src/olympe/doc/examples/piloting_hud.py>`_.

.. _imaging_hud:

Imaging HUD
""""""""""""

Displays the photo capture grid and the image histogram.

These lines are used to activate the imaging HUD:

.. code-block:: python

    from olympe_deps import PDRAW_GLES2HUD_TYPE_IMAGING

    # ...

    PdrawRenderer(pdraw=pdraw, hud_type=PDRAW_GLES2HUD_TYPE_IMAGING)

.. _tracking_hud:

Tracking HUD
""""""""""""

Displays the visual tracking proposal box or the target tracking box.

.. Important ::

    This feature is only supported with ANAFI Ai.

The tracking feature needs to be enabled on the drone first. The
:py:class:`olympe.messages.onboard_tracker.start_tracking_engine` command must
be sent to the drone before trying to display the tracking box proposals and/or
the selected target onto the live video stream.

.. literalinclude:: ../../examples/tracking_hud.py
    :emphasize-lines: 31, 35
