Olympe API Reference Documentation
----------------------------------

.. autoclass:: olympe.Drone()

    .. automethod:: __init__
    .. automethod:: connection
    .. automethod:: disconnection
    .. automethod:: connection_state
    .. automethod:: start_video_streaming
    .. automethod:: stop_video_streaming
    .. automethod:: set_streaming_output_files
    .. automethod:: set_streaming_callbacks
    .. automethod:: __call__
    .. automethod:: help
    .. automethod:: get_state
    .. automethod:: check_state
    .. automethod:: query_state
    .. automethod:: start_piloting
    .. automethod:: piloting_pcmd(roll, pitch, yaw, gaz, piloting_time)
    .. automethod:: stop_piloting

.. autoclass:: olympe.ReturnTuple()

.. autoclass:: olympe.Expectation

    .. automethod:: received_events
    .. automethod:: matched_events
    .. automethod:: unmatched_events
    .. automethod:: explain

.. autoclass:: olympe.tools.logger.level

    .. autoattribute:: debug
    .. autoattribute:: info
    .. autoattribute:: warning
    .. autoattribute:: error
    .. autoattribute:: critical
