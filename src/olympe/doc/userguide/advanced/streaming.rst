.. _video_streaming_example:

Stream a video, and other streaming-related features
----------------------------------------------------

**Objective:**

Start a video streaming with the :py:func:`olympe.Drone.streaming` API function.

Copy paste the following Python ``streaming_single.py`` script somewhere in your
directory:

.. literalinclude:: ../../examples/streaming_single.py
    :language: python
    :linenos:

*A little explanation*
~~~~~~~~~~~~~~~~~~~~~~
This example shows how to get the video stream from the drone using Olympe.
Internally, Olympe leverages Parrot `libpdraw` to:

    - initialize the video streaming from the drone
    - decode the h264 video stream
    - register user provided callback functions that are called for
      each (encoded or decoded) frame with its associated metadata, if desired
    - display the streaming live, if desired
    - record the live video stream from the drone to the disk, if desired


*Body*
~~~~~~

Without output files
^^^^^^^^^^^^^^^^^^^^
The first class streams the video of the drone and displays it thanks to a renderer.
Once `Olympe` is connected to the drone, the stream is started with
:py:func:`olympe.Pdraw.play()`, that you can access with ``olympe.drone.streaming.play()``.

* ``media_name``: name of the media you'd like to stream. This media has to be chosen among the media list the drone makes available [``Front camera``, ``Horizontal stereo left camera``, ``Horizontal stereo right camera``, ``Down stereo left camera``, ``Down stereo right camera``].
    .. note::
        If you need help on how to stream multiple cameras, check ``streaming_multiple.py`` example out!
* ``data_formats``: list of the requested h264 coded data format(s). These formats have to be chosen among the h264 coded data format enum [``avcc``, ``bytestream``]. AVCC data are unsupported by Pdraw ; bytestream data are for the decoder usage: it has to be requested every time you need a decoder. That is the reason why it is the default value.
* ``has_renderer``: boolean value to decide whether or not a renderer of the streaming video is requested (i.e. if you'd like to see the streaming rendering in a pop-up).

Once opened, the stream has to be properly closed with :py:func:`olympe.Pdraw.stop()`,
that you can access with ``olympe.drone.streaming.stop()``, and `Olympe` can eventually
disconnect from the drone.

With output files
^^^^^^^^^^^^^^^^^^^^
The second class demonstrates the same streaming process, but adds the feature of saving
output files in a folder of your choice.

* ``outfiles_path``: :py:class:`olympe.OutfilesPath` dataclass to store the OutfilesPath where to store the json file containing the information of the record, the ``.mp4`` file of the record, and the metadata of this video.

.. note::

    Those are the basic arguments to fill in for a stream. We will fly over some others
    in the next examples analysis. If you want an exhaustive overview, we suggest you
    check the API out: :py:func:`olympe.Pdraw.play`

*Execution*
~~~~~~~~~~~
To execute this script, run the following command from the shell you've just sourced:

.. code-block:: console

    (olympe-python3) $ python ./streaming_single.py


Multiple video streaming
~~~~~~~~~~~~~~~~~~~~~~~~
As mentioned above, you can check ``streaming_multiple.py`` out if you want to stream
several cameras at the same time. The principle (``media_name``, ``data_formats``,
``has_renderer``) is the same aside from a few changes:

* :py:func:`olympe.Pdraw.play_multiple_stream`: is the former :py:func:`olympe.Pdraw.play` to call when streaming multiple cameras
* ``streams_list``: :py:class:`olympe.StreamInfo` dataclass list to register all the cameras context you'd like to stream. Each one contains:
    * ``name``: equivalent to the previous ``media_name``
    * ``callbacks``: :py:class:`olympe.Callbacks` list, to store several information regarding the media requested. Those can be:
        * ``media_type``: tuple of :py:class:`olympe.video_frame_type` and :py:class:`olympe.h264_coded_data_format`. Tuple ``(raw,None)`` is needed for rendering, tuple ``(coded, bytestream)`` is needed for decoding, tuple ``(coded, avcc)`` is anyway not supported, but can be requested if AVCC is not needing and output files have to be registered.
        * callback functions: functions called internally by Pdraw when frames are being processed. You can deal with callbacks called at :py:func:`olympe.Callbacks.flush`, :py:func:`olympe.Callbacks.drain`, :py:func:`olympe.Callbacks.start` and :py:func:`olympe.Callbacks.end`
    * ``has_renderer``: identical
    * ``outfiles_path``: identical

With a SkyController
~~~~~~~~~~~~~~~~~~~~
Stream through a SkyController works the same as without. The only difference resides
in the fact that the class to connect to and speak through is obviously not :py:class:`olympe.Drone`,
but :py:class:`olympe.SkyController` with IP address ``"192.168.53.1"``.

.. note::

    With Anafi Ai, rather use :py:class:`olympe.SkyController4` ; and :py:class:`olympe.SkyControllerUKR`
    with Anafi UKR

Check ``streaming_skycontroller_ukr.py`` out!

Thermal streaming
~~~~~~~~~~~~~~~~~
Look at ``streaming_thermal.py``.
The difference is about the way to switch from visible to thermal mode, and vice versa.

.. literalinclude:: ../../examples/streaming_thermal.py
    :language: python
    :linenos:
    :lineno-start: 39
    :lines: 39-59

Do not forget to import the appropriate ARSDK messages

.. literalinclude:: ../../examples/streaming_thermal.py
    :language: python
    :linenos:
    :lineno-start: 9
    :lines: 9-11
