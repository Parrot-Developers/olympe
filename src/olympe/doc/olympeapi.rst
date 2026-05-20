Olympe API Reference Documentation
----------------------------------

.. autoclass:: olympe.Drone()

    .. automethod:: __init__
    .. automethod:: __call__
    .. automethod:: __enter__
    .. automethod:: __exit__
    .. automethod:: is_skyctrl
    .. automethod:: start_piloting
    .. automethod:: piloting
    .. automethod:: stop_piloting
    .. automethod:: connect
    .. automethod:: disconnect
    .. automethod:: destroy
    .. automethod:: get_last_event
    .. automethod:: get_last_events
    .. automethod:: get_state
    .. automethod:: check_state
    .. automethod:: query_state
    .. automethod:: drone_connection_state
    .. automethod:: schedule
    .. automethod:: subscribe
    .. automethod:: unsubscribe
    .. autoproperty:: connection_state
    .. autoproperty:: connected
    .. autoproperty:: connecting
    .. autoproperty:: disconnected
    .. autoproperty:: disconnecting
    .. autoproperty:: created
    .. autoproperty:: scheduler
    .. autoproperty:: flightplan
    .. autoproperty:: media
    .. autoproperty:: mission
    .. autoproperty:: streaming
    .. autoproperty:: update

.. autoclass:: olympe.LocalController()
   :show-inheritance:

.. autoclass:: olympe.Anafi()
   :show-inheritance:

.. autoclass:: olympe.AnafiUSA()
   :show-inheritance:

.. autoclass:: olympe.AnafiAi()
   :show-inheritance:

.. autoclass:: olympe.controller.AnafiUKR()
   :show-inheritance:

.. autoclass:: olympe.controller.Chuck()
   :show-inheritance:

.. autoclass:: olympe.SkyController()

    .. automethod:: __init__
    .. automethod:: __call__
    .. automethod:: __enter__
    .. automethod:: __exit__
    .. automethod:: is_skyctrl
    .. automethod:: start_piloting
    .. automethod:: piloting
    .. automethod:: stop_piloting
    .. automethod:: connect
    .. automethod:: disconnect
    .. automethod:: destroy
    .. automethod:: get_last_event
    .. automethod:: get_last_events
    .. automethod:: get_state
    .. automethod:: check_state
    .. automethod:: query_state
    .. automethod:: drone_connection_state
    .. automethod:: schedule
    .. automethod:: subscribe
    .. automethod:: unsubscribe
    .. autoproperty:: connection_state
    .. autoproperty:: connected
    .. autoproperty:: connecting
    .. autoproperty:: disconnected
    .. autoproperty:: disconnecting
    .. autoproperty:: created
    .. autoproperty:: scheduler
    .. autoproperty:: cellular
    .. autoproperty:: flightplan
    .. autoproperty:: media
    .. autoproperty:: mission
    .. autoproperty:: streaming
    .. autoproperty:: update

.. autoclass:: olympe.SkyController3()
   :show-inheritance:

.. autoclass:: olympe.SkyControllerUSA()
   :show-inheritance:

.. autoclass:: olympe.SkyController4Black()
   :show-inheritance:

.. autoclass:: olympe.SkyController4()
   :show-inheritance:

.. autoclass:: olympe.SkyControllerUKR()
   :show-inheritance:

.. autoclass:: olympe.controller.SkyControllerUKR5G()
   :show-inheritance:

.. autoclass:: olympe.controllers.cmd_itf.ConnectionState()

    .. autoattribute:: Created
    .. autoattribute:: Connecting
    .. autoattribute:: Connected
    .. autoattribute:: Disconnecting
    .. autoattribute:: Disconnected
    .. autoattribute:: Timeout
    .. autoattribute:: Error

.. autoclass:: olympe.video.pdraw.OutfilesPath()

.. autoclass:: olympe.video.pdraw.Outfiles()

.. autoclass:: olympe.video.pdraw.Callbacks()

.. autoclass:: olympe.video.pdraw.StreamInfo()

.. autoclass:: olympe.video.pdraw.video_frame_type()

.. autoclass:: olympe.video.pdraw.h264_coded_data_format()

.. autoclass:: olympe.video.pdraw.Pdraw()

    .. automethod:: __init__
    .. automethod:: wait
    .. automethod:: get_session_metadata
    .. automethod:: get_session_metadata_dict
    .. automethod:: seek_next_frame
    .. automethod:: seek_previous_frame
    .. automethod:: seek_from
    .. automethod:: seek_to
    .. automethod:: get_chapter_list
    .. automethod:: get_replay_duration
    .. automethod:: get_replay_current_time
    .. automethod:: stop
    .. automethod:: play_multiple_stream
    .. automethod:: play
    .. automethod:: pause
    .. automethod:: cleanup
    .. automethod:: destroy
    .. autoproperty:: state

.. autoclass:: olympe.video.pdraw.PdrawState()

    .. autoattribute:: Created
    .. autoattribute:: Open_requested
    .. autoattribute:: Opened
    .. autoattribute:: Play_requested
    .. autoattribute:: Playing
    .. autoattribute:: Pause_requested
    .. autoattribute:: Paused
    .. autoattribute:: Close_requested
    .. autoattribute:: Closed
    .. autoattribute:: Cleaned
    .. autoattribute:: Error

.. autoclass:: olympe.video.frame.VideoFrame()

    .. automethod:: __init__
    .. automethod:: __bool__
    .. automethod:: ref
    .. automethod:: unref
    .. automethod:: media_id
    .. automethod:: as_ctypes_pointer
    .. automethod:: as_ndarray
    .. automethod:: info
    .. automethod:: vmeta
    .. automethod:: format
    .. automethod:: userdata_sei
    .. automethod:: session_metadata
    .. autoproperty:: frame_type
    .. autoproperty:: width
    .. autoproperty:: height

.. autoclass:: olympe.Cellular()

    .. automethod:: __init__
    .. automethod:: pair
    .. automethod:: configure
    .. autoproperty:: autoconfigure
    .. autoproperty:: user_apc_token

.. autoclass:: olympe.Flightplan()

    .. automethod:: __init__
    .. automethod:: set_hostname
    .. automethod:: upload
    .. automethod:: download_all
    .. automethod:: download
    .. automethod:: remove
    .. automethod:: shutdown
    .. automethod:: destroy

.. autoclass:: olympe.Media()

    .. automethod:: __init__
    .. automethod:: get_hostname
    .. automethod:: set_hostname
    .. automethod:: connect
    .. automethod:: disconnect
    .. automethod:: shutdown
    .. automethod:: destroy
    .. automethod:: wait_for_pending_downloads
    .. automethod:: schedule
    .. automethod:: resource_info
    .. automethod:: media_info
    .. automethod:: list_media
    .. automethod:: list_resources
    .. automethod:: last_media
    .. automethod:: last_media_id
    .. automethod:: next_media_id
    .. automethod:: subscribe
    .. automethod:: unsubscribe
    .. autoproperty:: download_dir
    .. autoproperty:: integrity_check
    .. autoproperty:: indexing_state
    .. autoproperty:: scheduler

.. autoclass:: olympe.MediaInfo()

.. autoclass:: olympe.ResourceInfo()

.. autoclass:: olympe.media.GPS()

.. autoclass:: olympe.MissionController()

    .. automethod:: __init__
    .. automethod:: stop
    .. automethod:: set_hostname
    .. automethod:: set_device_name
    .. automethod:: list_remote
    .. automethod:: from_path
    .. autoproperty:: mission_dir

.. autoclass:: olympe.Mission()

    .. automethod:: __init__
    .. automethod:: __enter__
    .. automethod:: __exit__
    .. automethod:: __bool__
    .. automethod:: open
    .. automethod:: close
    .. automethod:: install
    .. automethod:: hash
    .. automethod:: verify
    .. automethod:: wait_ready
    .. automethod:: send
    .. automethod:: subscribe
    .. autoproperty:: resource
    .. autoproperty:: filepath
    .. autoproperty:: state
    .. autoproperty:: recipient_id
    .. autoproperty:: ready
    .. autoproperty:: messages
    .. autoproperty:: enums
    .. autoproperty:: modules
    .. autoproperty:: signature

.. autoclass:: olympe.Expectation()

    .. automethod:: __bool__
    .. automethod:: __or__
    .. automethod:: __and__
    .. automethod:: __rshift__
    .. automethod:: __str__
    .. automethod:: check
    .. automethod:: expected_events
    .. automethod:: received_events
    .. automethod:: matched_events
    .. automethod:: unmatched_events
    .. automethod:: marked_events
    .. automethod:: explain
    .. automethod:: success
    .. automethod:: wait
    .. automethod:: cancel
    .. automethod:: cancelled
    .. automethod:: set_timeout
    .. automethod:: timedout
    .. automethod:: copy
    .. automethod:: done
    .. automethod:: result

.. autoclass:: olympe.messaging.expectations.SuccessExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.FailedExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.FutureExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.CheckWaitStateExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.MultipleExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.WhenAnyExpectation()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.WhenAllExpectations()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.WhenSequenceExpectations()
   :show-inheritance:

.. autoclass:: olympe.messaging.expectations.ExpectPolicy()
   :members: wait, check, check_wait

.. autoclass:: olympe.messaging.arsdk_expectations.MatchingMode()
   :members: exact, pattern, negative_pattern

.. autoclass:: olympe.Event()

    .. autoproperty:: policy
    .. autoproperty:: uuid
    .. autoproperty:: date
    .. autoproperty:: id

.. autoclass:: olympe.EventListener()

    .. automethod:: __init__
    .. automethod:: __enter__
    .. automethod:: __exit__
    .. automethod:: subscribe
    .. automethod:: unsubscribe
    .. autoproperty:: timeout

.. autofunction:: olympe.listen_event

.. autofunction:: olympe.log.update_config
