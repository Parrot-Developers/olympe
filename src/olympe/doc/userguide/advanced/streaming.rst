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

.. literalinclude:: ../../examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 30
    :lines: 30-44

Our objective is to start the video stream, fly the drone around, perform some
live video processing, stop the video stream and finally perform some video
postprocessing.

.. literalinclude:: ../../examples/streaming.py
    :language: python
    :linenos:
    :lineno-start: 200
    :lines: 200-209

Before we start the video streaming, we must connect to the drone and optionally
register our callback functions and output files for the recorded video stream.

.. literalinclude:: ../../examples/streaming.py
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
