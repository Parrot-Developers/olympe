Olympe API Reference Documentation
----------------------------------

.. autoclass:: olympe.Drone()

    .. automethod:: __init__
    .. automethod:: connect
    .. automethod:: disconnect
    .. automethod:: connection_state
    .. automethod:: __call__
    .. automethod:: get_state
    .. automethod:: check_state
    .. automethod:: query_state
    .. automethod:: subscribe
    .. automethod:: unsubscribe
    .. automethod:: start_piloting
    .. automethod:: piloting(roll, pitch, yaw, gaz, piloting_time)
    .. automethod:: stop_piloting

.. autoclass:: olympe.EventListener()

    .. automethod:: __init__
    .. automethod:: subscribe
    .. automethod:: unsubscribe

.. autoclass:: olympe.Pdraw()

    .. automethod:: __init__
    .. automethod:: play
    .. automethod:: pause
    .. automethod:: resume
    .. automethod:: stop
    .. automethod:: set_output_files
    .. automethod:: set_callbacks
    .. automethod:: get_session_metadata
    .. autoproperty:: state
    .. automethod:: wait
    .. automethod:: close

.. autoclass:: olympe.Media()

    .. automethod:: __init__
    .. automethod:: connect
    .. automethod:: disconnect
    .. automethod:: shutdown
    .. automethod:: media_info
    .. automethod:: resource_info
    .. automethod:: list_media
    .. automethod:: list_resources
    .. autoproperty:: indexing_state
    .. automethod:: __call__
    .. automethod:: subscribe
    .. automethod:: unsubscribe

.. autoclass:: olympe.MediaInfo()

.. autoclass:: olympe.ResourceInfo()

.. autoclass:: olympe.media.MediaType()
    :members: photo, video

.. autoclass:: olympe.media.PhotoMode()
    :members: single, bracketing, burst, panorama, timelapse, gpslapse

.. autoclass:: olympe.media.IndexingState()
    :members: not_indexed, indexing, indexed

.. autoclass:: olympe.media.GPS()
    :members: latitude, longitude, altitude

.. autoclass:: olympe.VideoFrame()

    .. automethod:: ref
    .. automethod:: unref
    .. automethod:: info
    .. automethod:: vmeta
    .. automethod:: as_ctypes_pointer
    .. automethod:: as_ndarray

.. autoclass:: olympe.PdrawState()

    .. autoattribute:: Created
    .. autoattribute:: Closing
    .. autoattribute:: Closed
    .. autoattribute:: Opening
    .. autoattribute:: Opened
    .. autoattribute:: Playing
    .. autoattribute:: Paused
    .. autoattribute:: Error


.. autoclass:: olympe.MissionController()

    .. automethod:: from_path

.. autoclass:: olympe.Mission()

    .. automethod:: install
    .. autoattribute:: messages
    .. autoattribute:: enums
    .. automethod:: wait_ready
    .. automethod:: send
    .. automethod:: subscribe


.. autoclass:: olympe.Expectation

    .. automethod:: received_events
    .. automethod:: matched_events
    .. automethod:: unmatched_events
    .. automethod:: explain

.. autofunction:: olympe.log.update_config
