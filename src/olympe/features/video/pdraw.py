#  Copyright (C) 2019-2024 Parrot Drones SAS
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions
#  are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the Parrot Company nor the names
#    of its contributors may be used to endorse or promote products
#    derived from this software without specific prior written
#    permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
#  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#  PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
#  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
#  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
#  OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
#  AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
#  OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.


import ctypes
import errno
import json
import os
import threading
import time
import warnings

from enum import Enum, auto

import olympe_deps as od

from collections import defaultdict, namedtuple
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    TextIO,
    TYPE_CHECKING,
    Tuple,
    Union,
)

from olympe.concurrent import Condition, Loop
from olympe.connection.backend import CtrlBackendMuxIp
from olympe.controllers.mixins.protocols import MixinBase
from olympe.features.video.renderer import PdrawRenderer
from olympe.log import LogMixin
from olympe.utils import py_object_cast, callback_decorator
from olympe.utils.enum import get_enum_value_as_int
from . import VMetaFrameType  # noqa
from . import PDRAW_TIMESCALE
from .frame import VideoFrame
from .mp4 import Mp4Mux


if TYPE_CHECKING:
    from olympe.controllers.mixins.ipproxy import IpProxy

DEFAULT_RTSP_PORT = 554


@dataclass
class OutfilesPath:
    info: Optional[str] = None
    video: Optional[str] = None
    meta: Optional[str] = None


@dataclass
class Outfiles:
    info: Optional[TextIO] = None
    video: Optional[Mp4Mux] = None
    meta: Optional[TextIO] = None

    def __bool__(self):
        return self.video is not None or self.info is not None or self.meta is not None

    def __iter__(self):
        return iter((self.video, self.info, self.meta))


@dataclass
class Callbacks:
    media_type: Tuple[int, Optional[int]]
    video_frame: Optional[Callable[[VideoFrame], None]] = None
    flush: Optional[Callable[[Dict[str, Any]], None]] = None
    drain: Optional[Callable[[Dict[str, Any]], None]] = None
    start: Optional[Callable[[], None]] = None
    end: Optional[Callable[[], None]] = None


@dataclass
class MediaInfo:
    id: int
    frame_type: int
    data_format: Optional[int]
    video_frame_cb: Optional[Callable[[VideoFrame], None]] = None
    flush_cb: Optional[Callable[[Dict[str, Any]], None]] = None
    drain_cb: Optional[Callable[[Dict[str, Any]], None]] = None
    start_cb: Optional[Callable[[], None]] = None
    end_cb: Optional[Callable[[], None]] = None


@dataclass
class _StreamInfoBase:
    has_renderer: bool = False
    renderer: Optional[PdrawRenderer] = field(init=False, default=None)
    outfiles_path: Optional[OutfilesPath] = field(default=None)
    outfiles: Optional[Outfiles] = field(init=False, default=None)
    callbacks: List[Callbacks] = field(default_factory=list)
    media_info: List[MediaInfo] = field(init=False, default_factory=list)
    session_metadata: Dict[str, Any] = field(init=False, default_factory=dict)
    available: bool = field(init=False, default=False)


@dataclass
class _StreamInfoName:
    name: str


@dataclass
class StreamInfo(_StreamInfoBase, _StreamInfoName):
    pass


@dataclass
class _StreamInfoDefaultName:
    name: str = field(init=False, default="OlympeStreamInfoDefault")


@dataclass
class StreamInfoDefault(_StreamInfoBase, _StreamInfoDefaultName):
    pass


@dataclass
class _StreamInfoSingleName:
    name: str = field(init=False, default="OlympeStreamInfoSingle")


@dataclass
class StreamInfoSingle(_StreamInfoBase, _StreamInfoSingleName):
    pass


@dataclass
class PlayData:
    timestamp: int
    speed: float


@dataclass
class ChapterList:
    timestamp: int
    name: str


class video_frame_type(Enum):
    coded = od.VDEF_FRAME_TYPE_CODED
    raw = od.VDEF_FRAME_TYPE_RAW


class h264_coded_data_format(Enum):
    avcc = od.VDEF_CODED_DATA_FORMAT_AVCC
    bytestream = od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM


class PdrawState(Enum):
    Created = auto()
    Open_requested = auto()
    Opened = auto()
    Play_requested = auto()
    Playing = auto()
    Pause_requested = auto()
    Paused = auto()
    Close_requested = auto()
    Closed = auto()
    Cleaned = auto()
    Error = auto()


class H264Header(namedtuple("H264Header", ["sps", "spslen", "pps", "ppslen"])):
    def tofile(self, f):
        start = bytearray([0, 0, 0, 1])
        if self.spslen > 0:
            f.write(start)
            f.write(bytearray(self.sps[: self.spslen]))

        if self.ppslen > 0:
            f.write(start)
            f.write(bytearray(self.pps[: self.ppslen]))


def StreamFactory():
    return {
        "id": None,
        "id_userdata": ctypes.c_void_p(),
        "frame_type": od.VDEF_FRAME_TYPE_UNKNOWN,
        "h264_header": None,
        "track_id": None,
        "metadata_track_id": None,
        "video_sink": None,
        "video_sink_cbs": None,
        "video_sink_lock": threading.RLock(),
        "video_queue": None,
        "video_queue_event": od.POINTER_T(od.struct_pomp_evt)(),
    }


class _CodedVideoSink:
    new = od.pdraw_coded_video_sink_new
    destroy = od.pdraw_coded_video_sink_destroy
    resync = od.pdraw_coded_video_sink_resync
    get_queue = od.pdraw_coded_video_sink_get_queue
    cbs = od.struct_pdraw_coded_video_sink_cbs
    video_sink_type = od.struct_pdraw_coded_video_sink
    queue_get_event = od.mbuf_coded_video_frame_queue_get_event
    queue_pop = od.mbuf_coded_video_frame_queue_pop
    queue_flush = od.mbuf_coded_video_frame_queue_flush
    queue_flushed = od.pdraw_coded_video_sink_queue_flushed
    queue_drained = od.pdraw_coded_video_sink_queue_drained
    mbuf_video_frame_type = od.struct_mbuf_coded_video_frame


class _RawVideoSink:
    new = od.pdraw_raw_video_sink_new
    destroy = od.pdraw_raw_video_sink_destroy

    @staticmethod
    def resync(*args, **kwds):
        return None

    get_queue = od.pdraw_raw_video_sink_get_queue
    cbs = od.struct_pdraw_raw_video_sink_cbs
    video_sink_type = od.struct_pdraw_raw_video_sink
    queue_get_event = od.mbuf_raw_video_frame_queue_get_event
    queue_pop = od.mbuf_raw_video_frame_queue_pop
    queue_flush = od.mbuf_raw_video_frame_queue_flush
    queue_flushed = od.pdraw_raw_video_sink_queue_flushed
    queue_drained = od.pdraw_raw_video_sink_queue_drained
    mbuf_video_frame_type = od.struct_mbuf_raw_video_frame


class Pdraw(LogMixin):
    def __init__(
        self,
        name: Optional[str] = None,
        device_name: Optional[str] = None,
        buffer_queue_size: int = 8,
        loop: Optional[Loop] = None,
        controller: Optional["MixinBase"] = None,
    ):
        """PDrAW Olympe class

        :param name: PDrAW client name (used by Olympe logs), defaults to None
        :type name: Optional[str], optional
        :param device_name: Drone device name (used by Olympe logs), defaults to None
        :type device_name: Optional[str], optional
        :param buffer_queue_size: Video buffer queue size, defaults to 8
        :type buffer_queue_size: int, optional
        :param loop: Thread to run PDrAW. If 'None', a new thread is created, defaults to None
        :type loop: Optional[Loop], optional
        :param controller: Controller owner of the PDrAW instance, defaults to None
        :type controller: Optional["MixinBase"], optional
        """
        if device_name is None and controller:
            device_name = controller._device_name
        self._controller = controller
        super().__init__(name, device_name, "pdraw")

        if loop is None:
            self._own_loop = True
            self._loop = Loop(self.logger)
            self._loop.start()
        else:
            self._own_loop = False
            self._loop = loop

        self._callbacks_loop = Loop(self.logger, parent=self._loop)
        self._callbacks_loop.start()
        self.buffer_queue_size = buffer_queue_size
        self.pomp_loop = self._loop.pomp_loop

        self._open_condition = Condition(self._loop)
        self._close_condition = Condition(self._loop)
        self._play_condition = Condition(self._loop)
        self._pause_condition = Condition(self._loop)
        self._stop_condition = Condition(self._loop)
        self._seek_condition = Condition(self._loop)
        self._state = PdrawState.Created
        self._state_lock = threading.Lock()
        self._state_wait_events = {k: list() for k in PdrawState}
        # Flag to indicate whether a PDrAW session has to be retried in case of unrecoverable error
        self._retrying = False
        # Flag to indicate whether a PDrAW opening session has to be cancelled
        self._open_cancelling = False

        # Flag to indicate whether a cleanup action is already in progress,
        # to prevent cleanup from running twice in parallel
        self._cleanup_ongoing = False

        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        self.pdraw_demuxer_params = od.struct_pdraw_demuxer_params.bind(
            dict(
                autodecoding_mode=od.PDRAW_DEMUXER_AUTODECODING_MODE_DECODE_ALL,
            )
        )
        self.streams = defaultdict(StreamFactory)
        self.session_metadata = {}

        # This variable is used when the user requests itself a default stream
        self._stream_info_default: Optional[StreamInfoDefault] = None

        # This variable is used for deprecated functions set_output_files and set_callbacks
        # It aims to store the output files and the callbacks of a StreamInfo before playing
        # it, when it is the only one played (ie when play() is called, instead of
        # play_multiple_stream())
        self._single_stream: Optional[StreamInfoSingle] = None

        # User-requested streams list
        self.multistreams: List[
            Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]
        ] = []

        self.url: Optional[bytes] = None
        self.remote_address: Optional[str] = None
        self.remote_port: Optional[int] = None
        self.resource_name: Optional[str] = None
        self._proxy: Optional[IpProxy] = None

        # Data related to the play of a stream (live or replayed)
        self._play_data: Optional[PlayData] = None
        # Speed to replay a stream at
        self._requested_speed = None

        self.demuxer_cbs = od.struct_pdraw_demuxer_cbs.bind(
            {
                "open_resp": self._open_resp,
                "close_resp": self._close_resp,
                "ready_to_play": self._ready_to_play,
                "play_resp": self._play_resp,
                "pause_resp": self._pause_resp,
                "unrecoverable_error": self._unrecoverable_error,
                "end_of_range": self._end_of_range,
                "seek_resp": self._seek_resp,
                "select_media": self._select_media,
            }
        )
        self.pdraw_cbs = od.struct_pdraw_cbs.bind(
            {
                "stop_resp": self._stop_resp,
                "socket_created": self._socket_created,
                "media_added": self._media_added,
                "media_removed": self._media_removed,
            }
        )

        self.video_sink_vt = {
            od.VDEF_FRAME_TYPE_CODED: _CodedVideoSink,
            od.VDEF_FRAME_TYPE_RAW: _RawVideoSink,
        }

        self._loop.register_cleanup(self._acleanup)

    @property
    def state(self) -> PdrawState:
        """Return the current state of the PDrAW state machine

        :return: the current PDrAW state self._state
        :rtype: PdrawState
        """
        return self._state

    @state.setter
    def state(self, value: PdrawState) -> None:
        """Set a new state value for the PDrAW state machine

        :param value: new value to set to the PDrAW state machine
        :type value: PdrawState
        """
        with self._state_lock:
            old_state = self._state
            self._state = value
            for event in self._state_wait_events[self._state]:
                event.set()
            self._state_wait_events[self._state] = []
        self.logger.info(
            f"PDrAW State Machine Transition from state {old_state} "
            f"to state {self.state}"
        )

    def _reset_attributes(self) -> None:
        """Reset attributes after a PDrAW close"""
        self.url = None
        self.remote_address = None
        self.remote_port = None
        self.resource_name = None
        self._stream_info_default = None
        self._stream_single = None
        self.multistreams.clear()
        self._open_cancelling = False
        self._play_data = None
        self._requested_speed = None

    def _get_stream_from_multistreams_with_name(
        self, value: Union[str, bytes, None]
    ) -> Optional[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]]:
        """Through multistreams list, look for the stream with the name 'value'

        :param value: Name of the stream to look for
        :type value: Union[str, bytes, None]
        :return: The stream of the list that matches the name 'value'
        :rtype: Optional[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]]
        """
        for stream in self.multistreams:
            if stream.name == value:
                return stream
        return None

    def _get_stream_from_multistreams_with_id(
        self, value: int
    ) -> Optional[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]]:
        """Through multistreams list, look for the stream whose one of its media has the id 'value'

        :param value: Id of the media to look for
        :type value: int
        :return: The stream whose one of its media matches the id 'value'
        :rtype: Optional[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]]
        """
        for stream in self.multistreams:
            for media in stream.media_info:
                if value == media.id:
                    return stream
        return None

    def _get_media_from_multistreams_with_id(self, value: int) -> Optional[MediaInfo]:
        """Through multistreams list, look for the media with the id 'value'

        :param value: Id of the media to look for
        :type value: int
        :return: The media of the list that matches the id 'value'
        :rtype: Optional[MediaInfo]
        """
        for stream in self.multistreams:
            for media in stream.media_info:
                if value == media.id:
                    return media
        return None

    def wait(self, state: PdrawState, timeout: float = 5) -> bool:
        """Wait for the provided PDrAW state

        If the requested state is already reached, this function returns True
        immediately.

        This function may block indefinitely when called with a timeout of 0.

        :param state: PDrAW state to wait for
        :type state: PdrawState
        :param timeout: Timeout duration in seconds, defaults to 5
        :type timeout: float, optional
        :return: True when the requested state is reached or False if the timeout
            duration is reached
        :rtype: bool
        """
        with self._state_lock:
            if self._state == state:
                return True
            event = threading.Event()
            self._state_wait_events[state].append(event)
        return event.wait(timeout=timeout)

    @callback_decorator()
    def _open_resp(self, pdraw, demuxer, status: int, userdata):
        """Callback when libpdraw is requested to open a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer instance, not used here
        :type demuxer: libpdraw type
        :param status: status of the stream opening. 0 means success, negative value means failure
        :type status: int
        :param userdata: userdata, not used here
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the stream is opened.
        # The status indicates if the process failed or succeeded
        self.logger.info(f"_open_resp({status}) called")
        self._loop.run_async(self._aopen_resp, status)

    async def _aopen_resp(self, status: int):
        """Asynchronous response to a streaming opening

        :param status: status of the stream opening. 0 means success, negative value means failure
        :type status: int
        """
        if self.state is not PdrawState.Open_requested:
            self.logger.error(
                "PDrAW interface is not supposed to receive an "
                f"opening request from {self.state} state. State set to 'Error'"
            )
            self.state = PdrawState.Error
        elif status == -errno.ENOENT and self._retrying:
            # While in state Open_requested, in case of error ENOENT (-2), stream opening
            # has failed and needs to be retried
            self.logger.warning(
                "RTSP request response '-2: No such file or directory'. "
                "Retry video streaming replay"
            )
            # The current demuxer that failed playing a streaming has to be closed.
            # Otherwise it will keep on zombying and will consist in a memory
            # leak and mess with the state machine
            self._close_stream()
            async with self._close_condition:
                await self._close_condition.wait()
            # Retry to play the streaming once the former one is properly closed
            self.state = PdrawState.Created
            self._loop.run_delayed(2.0, self._aplay)
        elif status != 0:
            self.logger.error(
                "Stream opening has failed: "
                f"{os.strerror(status)}. State set to 'Error'"
            )
            self.state = PdrawState.Error
        elif status == 0 and self._open_cancelling:
            # In case the opening shall be closed, for example when the drone has no media
            # available that matches the user request
            self.state = PdrawState.Close_requested
            self._loop.run_async(self._aclose)
        else:
            self.state = PdrawState.Opened
            # Stream opening has succeeded in this case, and there is no need
            # to retry anymore
            self._retrying = False
        # Notify all coroutines waiting on _open_condition that they can now resume
        async with self._open_condition:
            self._open_condition.notify_all()

    @callback_decorator()
    def _close_resp(self, pdraw, demuxer, status: int, userdata):
        """Callback when libpdraw is requested to close a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer instance, not used here
        :type demuxer: libpdraw type
        :param status: status of the stream closing. 0 means success, negative value means failure
        :type status: int
        :param userdata: userdata, not used here
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the stream is closed.
        # The status indicates if the process failed or succeeded
        self._close_output_files()
        self.logger.info("_close_resp called")
        self._loop.run_async(self._aclose_resp, pdraw, demuxer, status)

    def _close_output_files(self):
        """Close output files"""
        self.logger.info("Closing video output files")
        for stream in self.multistreams:
            if stream.outfiles is None:
                continue
            for file in (
                stream.outfiles.info,
                stream.outfiles.video,
                stream.outfiles.meta,
            ):
                if file:
                    file.close()

    async def _aclose_resp(self, pdraw, demuxer, status: int):
        """Asynchronous response to a streaming opening

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer instance, not used here
        :type demuxer: libpdraw type
        :param status: status of the stream opening. 0 means success, negative value means failure
        :type status: int
        """
        if self.state not in (PdrawState.Close_requested, PdrawState.Error):
            self.logger.error(
                "PDrAW interface is not supposed to receive a closing request "
                f"from {self.state} state. State set to 'Error'"
            )
            self.state = PdrawState.Error
        elif status != 0:
            self.logger.error(
                "Stream closing has failed: "
                f"{os.strerror(status)}. State set to 'Error'"
            )
            self.state = PdrawState.Error
        else:
            self.state = PdrawState.Closed
            self.logger.info("PDrAW demuxer is successfully closed")
        # Now that it is properly closed, destroy demuxer because a new one will be created
        self._destroy_pdraw_demuxer()
        # Notify all coroutines waiting on _close_condition that they can now resume
        async with self._close_condition:
            self._close_condition.notify_all()

    def _ready_to_play(self, pdraw, demuxer, ready: bool, userdata):
        """Callback when libpdraw is ready to play a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer instance, not used here
        :type demuxer: libpdraw type
        :param ready: readiness of the streaming play. True means the stream is ready to
        play, False otherwise
        :type ready: bool
        :param userdata: userdata, not used here
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the stream is available to play
        self.logger.info(f"_ready_to_play({ready}) called")
        self._is_ready_to_play = ready
        if self._is_ready_to_play and self.state is PdrawState.Play_requested:
            # Ask the demuxer to start streaming
            if not self._play_impl():
                self.logger.error("Streaming has not started")
                return
            for stream in self.multistreams:
                for cb in (cb for cb in stream.callbacks if cb.start is not None):
                    self._callbacks_loop.run_async(cb.start)
        else:
            for stream in self.multistreams:
                for cb in (cb for cb in stream.callbacks if cb.end is not None):
                    self._callbacks_loop.run_async(cb.end)

    def _play_impl(self) -> bool:
        """Play request implementation on the libpdraw side

        :return: True if the demuxer has successfully played the stream, False otherwise
        :rtype: bool
        """
        if self.state is PdrawState.Playing:
            return True
        if self._requested_speed:
            res = od.pdraw_demuxer_play_with_speed(
                self.pdraw, self.pdraw_demuxer, self._requested_speed
            )
        else:
            res = od.pdraw_demuxer_play(self.pdraw, self.pdraw_demuxer)
        if res != 0:
            self.logger.error(f"Unable to start streaming: {os.strerror(-res)}")
            return False
        return True

    def _play_resp(self, pdraw, demuxer, status: int, timestamp, speed, userdata):
        """Callback when libpdraw is requested to close a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer instance
        :type demuxer: libpdraw type
        :param status: status of the stream playing. 0 means success, negative value means failure
        :type status: int
        :param timestamp: timestamp
        :type timestamp: libpdraw type
        :param speed: speed
        :type speed: libpdraw type
        :param userdata: userdata
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the stream is playing.
        # The status indicates if the process failed or succeeded
        self.logger.info("_play_resp called")
        self._loop.run_async(self._aplay_resp, status)

    async def _aplay_resp(self, status: int):
        """Asynchronous response received when libpdraw has delivered its status upon
        the requested stream play

        :param status: status of the stream play. 0 means play is allowed, negative value
        in case of error
        :type status: int
        """
        if self.state is not PdrawState.Play_requested:
            self.logger.error(
                f"PDrAW interface is not supposed to receive a playing request "
                f"from {self.state} state. State set to 'Error'"
            )
            self.state = PdrawState.Error
        elif status != 0:
            self.logger.error(
                f"Stream playing has failed: {os.strerror(status)}. "
                "State set to 'Error'"
            )
            self.state = PdrawState.Error
        else:
            self.state = PdrawState.Playing
        # Notify all coroutines waiting on _play_condition that they can now resume
        async with self._play_condition:
            self._play_condition.notify_all()

    def _pause_resp(self, pdraw, demuxer, status, timestamp, userdata):
        """Callback when libpdraw is requested to pause a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer
        :type demuxer: libpdraw type
        :param status: status of the stream pause. 0 means success, negative value means failure
        :type status: libpdraw type
        :param timestamp: timestamp
        :type timestamp: libpdraw type
        :param userdata: additional userdata
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the stream is paused.
        # The status indicates if the process failed or succeeded
        self.logger.info("_pause_resp called")
        self._loop.run_async(self._apause_resp, status)

    async def _apause_resp(self, status):
        """Asynchronous response received when libpdraw has delivered its status upon
        the requested stream pause

        :param status: status of the stream pause. 0 means pause is allowed, negative value
        in case of error
        :type status: int
        """
        if self.state is not PdrawState.Pause_requested:
            self.logger.error(
                "PDrAW interface is not supposed to receive a pausing request "
                f"from {self.state} state. State set to 'Error'"
            )
            self.state = PdrawState.Error
        elif status != 0:
            self.logger.error(
                f"Stream pause has failed: {os.strerror(status)}. State set to 'Error'"
            )
            self.state = PdrawState.Error
        else:
            self.state = PdrawState.Paused
        # Notify all coroutines waiting on _pause_condition that they can now resume
        async with self._pause_condition:
            self._pause_condition.notify_all()

    def _unrecoverable_error(self, pdraw, demuxer, userdata):
        """Callback when libpdraw raises an error on its side

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param demuxer: demuxer
        :type demuxer: libpdraw type
        :param userdata: userdata
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when pdraw has encountered
        # an unrecoverable error
        self.logger.info("_unrecoverable_error called")
        self._loop.run_async(self._aunrecoverable_error)

    async def _aunrecoverable_error(self):
        """Unrecoverable error callback function, called when a previously
        opened session is no longer running. When this function is called,
        the demuxer session is no longer running; pdraw_demuxer_close() must
        be called and one must wait for the close_resp callback function to
        be issued prior to calling pdraw_demuxer_destroy()
        """
        self.logger.warning(
            "Unrecoverable error from PDrAW. Restarting the stream session..."
        )
        # Flush every video sinks and remove media
        for id_ in self.streams:
            self._callbacks_loop.run_async(self._video_sink_flush_impl, id_).result()
            await self._media_removed_impl(id_)

        # The error comes from the pdraw library and streaming needs to be retry
        # until it works
        self._retrying = True
        # Before replaying streaming, it has to be closed properly
        self.state = PdrawState.Close_requested
        await self._aclose()

    def _end_of_range(self, pdraw, demuxer, timestamp, userdata):
        self.logger.info("_end_of_range called")
        self.state = PdrawState.Close_requested
        self._loop.run_async(self._aclose)

    def _seek_resp(
        self, pdraw, demuxer, status: int, timestamp: int, speed: float, userdata
    ):
        # Callback awaken by pdraw library when the demuxer is requested to lead a seek
        # operation.
        # The status indicates if the process failed or succeeded
        # Timestamp is the current play time in microseconds after seeking. The speed
        # parameter is the current playback speed (negative value means playing backward)
        self._loop.run_async(
            self._aseek_resp, status=status, timestamp=timestamp, speed=speed
        )

    async def _aseek_resp(self, status: int, timestamp: int, speed: float):
        if status == 0:
            self.logger.info(f"_seek_resp called {status}")
        else:
            self.logger.error(f"_seek_resp called {status}")
            self.state = PdrawState.Error

        self._play_data = PlayData(timestamp=timestamp, speed=speed)
        # Notify all coroutines waiting on _seek_condition that they can now resume
        async with self._seek_condition:
            self._seek_condition.notify_all()

    @callback_decorator()
    def _stop_resp(self, pdraw, status: int, userdata):
        """Callback when libpdraw is requested to pause a stream

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param status: status of the stream stop. 0 means success, negative value means failure
        :type status: int
        :param userdata: userdata
        :type userdata: libpdraw type
        """
        # Callback awaken by pdraw library when the demuxer is stopped.
        # The status indicates if the process failed or succeeded
        self._loop.run_async(self._astop_resp, status)

    async def _astop_resp(self, status):
        if status != 0:
            self.logger.error(f"_stop_resp called {status}")
            self.state = PdrawState.Error
        else:
            self.logger.info(f"_stop_resp called {status}")
        # Notify all coroutines waiting on _stop_condition that they can now resume
        async with self._stop_condition:
            self._stop_condition.notify_all()

    def _socket_created(self, pdraw, fd, userdata):
        self.logger.debug("_socket_created called")

    def _select_media(self, pdraw, demuxer, medias, count, selected_medias, userdata):
        """Select the media that crossed the available cameras of the drone (thanks to
        PDrAW library) and the streams requested by the user

        :param pdraw: PDrAW instance, not used here
        :type pdraw: libpraw type
        :param demuxer: demuxer, not used here
        :type demuxer: libpdraw type
        :param medias: dist of the drone available streams, according to PDrAW library
        :type medias: libpdraw type
        :param count: number of the drone available streams
        :type count: int
        :param selected_medias: bitfield of the identifiers of the currently selected medias
        :type selected_medias: int
        :param userdata: additional data, not used here
        :type userdata: libpdraw type
        :return: Bitfield of the selected media, to transfer back to PDrAW library
        :rtype: bitfield
        """
        # By default select the default media (media_id=0)
        default_media_id = 0
        default_media_idx = 0
        # Bitfield of the selected media to return to PDrAW library
        selected_media_bitfield = 0
        # All the media available from PDrAW library are being browsed
        for idx in range(count):
            self.logger.info(
                f"_select_media: "
                f"idx={idx} media_id={medias[idx].media_id} "
                f"name={od.string_cast(medias[idx].name)} "
                f"default={str(bool(medias[idx].is_default))} "
                f"selected={str(bool(selected_medias & (1 << medias[idx].media_id)))}"
            )
            # It is necessary to store the id and index of the default media, so that
            # there is still a media available for frames processing in case the
            # user-requested media are not matched with the PDrAW API list
            # Only the first default tagged media is considered default for Olympe
            if bool(medias[idx].is_default) and default_media_id == 0:
                default_media_id = medias[idx].media_id
                default_media_idx = idx

            # The current media is looked for in the self.multistreams list of the
            # user-expected media. If the user does not want this media, there is no
            # need to select
            stream = self._get_stream_from_multistreams_with_name(
                od.string_cast(medias[idx].name)
            )
            if stream is not None:
                # If the media is indeed user-requested, the bitfield is incremented
                # with its id
                selected_media_bitfield |= 1 << medias[idx].media_id
                stream.available = True

            # In case of replay, only one stream can be replayed at once
            if (
                self.resource_name is not None
                and self.resource_name.startswith("replay/")
                and len(self.multistreams) == 1
            ):
                selected_media_bitfield = 1 << medias[idx].media_id
                self.multistreams[0].available = True
                # Get the real name of the stream to replay, to replace the one given by
                # default at the initialization of the StreamInfoSingle
                self.multistreams[0].name = str(od.string_cast(medias[idx].name))
                self._stream_info_default = self.multistreams[0]

        # Warn the user if he requested some media that were not available from the drone
        for stream in self.multistreams:
            if not stream.available:
                self.logger.warning(
                    f"The video stream {stream.name} is not available from the drone"
                )

        if selected_media_bitfield == 0:
            if self._stream_info_default is not None:
                # In case no user-requested media has matched the available media proposed
                # by PDrAW library, but the user has registered itself a default media,
                # it may be useful to warn that this registered default media will be used
                default_media_name = od.string_cast(medias[default_media_idx].name)
                self.logger.warning(
                    "No media from the requested list "
                    f"({', '.join(stream.name for stream in self.multistreams)}) are available. "
                    f"Selecting the default media instead: {default_media_name}"
                )
            else:
                # If the user itself has not registered any default media, then no media
                # will be played
                self.logger.error(
                    "No default media is requested. "
                    "No stream will be played and PDrAW streaming will closed"
                )
                self._open_cancelling = True

        if selected_media_bitfield:
            return selected_media_bitfield
        elif self._stream_info_default is None:
            return -errno.ECANCELED
        elif default_media_id:
            return 1 << default_media_id
        else:
            return 0

    def _media_removed(self, pdraw, media_info, element_userdata, userdata):
        """Callback when a media is removed

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param media_info: media to remove
        :type media_info: libpdraw type
        :param element_userdata: additional data, not used here
        :type element_userdata: libpdraw type
        :param userdata: additional data, not used here
        :type userdata: libpdraw type
        """
        id_ = media_info.contents.id
        if id_ not in self.streams:
            self.logger.warning(f"Received removed event from unknown ID {id_}")
            return

        self.logger.info(f"_media_removed called id : {id_}")

        # FIXME: Workaround media_removed called with destroyed media
        if not self.pdraw:
            self.logger.error(
                f"_media_removed called with a destroyed pdraw id : {id_}"
            )
            return
        self._loop.run_later(self._media_removed_impl, id_)

    async def _media_removed_impl(self, id_: int):
        """Process to execute when a media is to be removed

        :param id_: Media id to remove
        :type id_: int
        """
        frame_type = self.streams[id_]["frame_type"]
        with self.streams[id_]["video_sink_lock"]:
            if self.streams[id_]["video_queue_event"]:
                self._callbacks_loop.remove_event_from_loop(
                    self.streams[id_]["video_queue_event"]
                )
            self.streams[id_]["video_queue_event"] = None

            if not self.streams[id_]["video_sink"]:
                self.logger.error(
                    f"pdraw_video_sink for media_id {id_} " f"has already been stopped"
                )
                return
            res = self.video_sink_vt[frame_type].destroy(
                self.pdraw, self.streams[id_]["video_sink"]
            )
            if res < 0:
                self.logger.error(f"pdraw_stop_video_sink() returned {res}")
            else:
                self.logger.debug(f"_media_removed video sink destroyed id : {id_}")

            self.streams[id_]["video_queue"] = None
            self.streams[id_]["video_sink"] = None
            self.streams[id_]["video_sink_cbs"] = None

            # When removing a media, it is necessary to remove this media from the
            # multistreams list and remove its associated callbacks if PDrAW streaming
            # is requested to stop.
            for stream in self.multistreams[:]:
                for media in stream.media_info[:]:
                    if id_ == media.id:
                        if self.state is not PdrawState.Playing:
                            data_format = self.streams[id_]["data_format"]
                            callback = self._get_stream_callbacks_for_a_media_type(
                                stream, frame_type, data_format
                            )
                            if callback:
                                stream.callbacks.remove(callback)
                        stream.media_info.remove(media)
                        return

            self.logger.error(f"Failed to remove media {id_} from multistreams list")

    def _set_renderer(
        self,
        stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle],
        frame_type: int,
        media_id: int,
    ) -> bool:
        """Create or update the renderer of a stream

        :param stream: Stream whose renderer has to be created or updated with the new
        media id
        :type stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]
        :param frame_type: Frame type of the current media being processed. A renderer
        is only setable for raw frames
        :type frame_type: int
        :param media_id: Id of the media whose renderer has to be created or updated
        :type media_id: int
        :return: True in case of a renderer successfully created or updated, False otherwise
        :rtype: bool
        """
        # In case this media is of type 'raw', and is requested to have a renderer by
        # the user, a new renderer will be assigned to it if it was not already
        # existing ; or the actual renderer will be updated with the new media id. This
        # case may happen if there is a disconnection for example, and the PDrAW API
        # changes the media id but keep it existing.
        if frame_type == od.VDEF_FRAME_TYPE_RAW and stream.has_renderer:
            if stream.renderer is None:
                self.logger.info(f"Create a renderer for media id '{media_id}'")
                stream.renderer = PdrawRenderer(pdraw=self, media_id=media_id)
            else:
                self.logger.info(f"Reset the renderer for media id '{media_id}'")
                if (
                    stream.renderer.update_renderer_with_new_media_id(
                        pdraw=self.pdraw, media_id=media_id
                    )
                    < 0
                ):
                    self.logger.error(
                        f"Failed to reset the renderer and update it with the new id "
                        f"'{media_id}' bound to this media"
                    )
                    return False
        return True

    def _get_stream_callbacks_for_a_media_type(
        self,
        stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle],
        frame_type: int,
        data_format: Optional[int],
    ) -> Optional[Callbacks]:
        """Get the callbacks of a stream for a certain media type

        :param stream: Stream whose callbacks are to be retrieved
        :type stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]
        :param frame_type: First part of the media type
        :type frame_type: int
        :param data_format: Second part of the media type
        :type data_format: Optional[int]
        :return: a collection of callbacks associated to this media id, for this media
        type (frame_type, data_format)
        :rtype: Optional[Callbacks]
        """
        for idx in range(len(stream.callbacks)):
            if isinstance(stream.callbacks[idx].media_type[0], int):
                if stream.callbacks[idx].media_type[0] == frame_type:
                    if (
                        stream.callbacks[idx].media_type[1] is not None
                        and stream.callbacks[idx].media_type[1] == data_format
                    ):
                        return stream.callbacks[idx]
                    elif stream.callbacks[idx].media_type[1] is None:
                        return stream.callbacks[idx]
            else:
                if (
                    stream.callbacks[idx].media_type[0] is not None
                    and stream.callbacks[idx].media_type[0].value is frame_type
                ):
                    if (
                        stream.callbacks[idx].media_type[1] is not None
                        and stream.callbacks[idx].media_type[1].value is data_format
                    ):
                        return stream.callbacks[idx]
                    elif stream.callbacks[idx].media_type[1] is None:
                        return stream.callbacks[idx]
        else:
            self.logger.debug(
                f"There is no callback registered in stream '{stream.name}' that "
                f"fits with a media type '({frame_type, data_format})'"
            )
            return None

    def _set_media_info(
        self,
        stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle],
        frame_type: int,
        data_format: Optional[int],
        media_id: int,
    ) -> bool:
        """Centralize the information of a media to add it to its stream


        :param stream: Stream to add this media to
        :type stream: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]
        :param frame_type: Frame type of the media to add
        :type frame_type: int
        :param data_format:  Data format of the media to add
        :type data_format: Optional[int]
        :param media_id: Id of the media to add
        :type media_id: int
        :return: True if the media is correctly added, False otherwise
        :rtype: bool
        """
        idx_callbacks = self._get_stream_callbacks_for_a_media_type(
            stream=stream, frame_type=frame_type, data_format=data_format
        )
        if not idx_callbacks:
            return False

        media = MediaInfo(
            id=media_id,
            frame_type=frame_type,
            data_format=data_format,
            video_frame_cb=idx_callbacks.video_frame,
            flush_cb=idx_callbacks.flush,
            drain_cb=idx_callbacks.drain,
            start_cb=idx_callbacks.start,
            end_cb=idx_callbacks.end,
        )

        stream.media_info.append(media)
        self.logger.info(f"Media id '{media_id}' added")

        return True

    def _media_added(self, pdraw, media_info, element_userdata, userdata):
        """Callback when a media is added by the PDrAW library

        :param pdraw: PDrAW instance
        :type pdraw: libpdraw type
        :param media_info: information of the added media
        :type media_info: libpdraw type
        :param element_userdata: additional data, not used here
        :type element_userdata: libpdraw type
        :param userdata: additional data, not used here
        :type userdata: libpdraw type
        """
        id_ = int(media_info.contents.id)
        self.logger.info(f"Media id offered to add: {id_}")

        video_info = media_info.contents.pdraw_media_info_0.video
        frame_type = video_info.format

        if frame_type == od.VDEF_FRAME_TYPE_CODED:
            video_info = video_info.pdraw_video_info_0.coded
            vdef_format = video_info.format
            data_format = vdef_format.data_format
        else:
            video_info = video_info.pdraw_video_info_0.raw
            vdef_format = video_info.format
            data_format = None
        media_type = (frame_type, data_format)

        # Sanity checks
        if (
            frame_type != od.VDEF_FRAME_TYPE_RAW
            and frame_type != od.VDEF_FRAME_TYPE_CODED
        ):
            self.logger.warning(
                f"Ignoring unsupported media id {id_} " f"(type {video_info.format})"
            )
            return

        stream_name = od.string_cast(
            media_info.contents.pdraw_media_info_0.video.session_meta.contents.title
        )
        stream_info = self._get_stream_from_multistreams_with_name(stream_name)
        if stream_info is None:
            if self._stream_info_default is not None:
                # If the user has registered itself a default stream with specific callbacks,
                # this stream will be played
                stream_info = self._stream_info_default
            else:
                self.logger.warning(
                    f"Provided media {stream_name} - with id {id_} - does not match any "
                    f"user-requested media from list {self.multistreams}, and no "
                    f"default stream media has been registered. This media will not be processed."
                )
                return

        # Determine if the media is requested by the user and deserves to be processed any further
        requested_media = False
        idx_callbacks = self._get_stream_callbacks_for_a_media_type(
            stream=stream_info, frame_type=frame_type, data_format=data_format
        )
        if not idx_callbacks:
            return
        if idx_callbacks.video_frame:
            requested_media = True
        if (
            stream_info.outfiles is not None
            and stream_info.outfiles.video
            and data_format == od.VDEF_CODED_DATA_FORMAT_AVCC
        ):
            requested_media = True
        if stream_info.has_renderer:
            requested_media = True

        if not requested_media:
            self.logger.warning(
                f"Skipping non-requested media {stream_name} - id {id_} "
                f"(type {media_type})"
            )
            return

        stream_info.session_metadata = od.struct_vmeta_session.as_dict(
            media_info.contents.pdraw_media_info_0.video.session_meta.contents
        )

        self._set_media_info(
            stream=stream_info,
            media_id=id_,
            frame_type=frame_type,
            data_format=data_format,
        )

        assert self._set_renderer(
            stream=stream_info, frame_type=frame_type, media_id=id_
        )

        self.streams[id_]["media_type"] = media_type
        self.streams[id_]["frame_type"] = frame_type
        self.streams[id_]["vdef_format"] = vdef_format
        self.streams[id_]["data_format"] = data_format

        if frame_type == od.VDEF_FRAME_TYPE_CODED and (
            data_format == od.VDEF_CODED_DATA_FORMAT_AVCC
        ):
            outfile = None
            if stream_info.outfiles is not None:
                outfile = stream_info.outfiles.video
            if outfile:
                header = video_info.pdraw_coded_video_info_0.h264
                header = H264Header(
                    bytearray(header.sps),
                    int(header.spslen),
                    bytearray(header.pps),
                    int(header.ppslen),
                )
                self.streams[id_]["h264_header"] = header
                self.streams[id_]["track_id"] = outfile.add_track(
                    type=od.MP4_TRACK_TYPE_VIDEO,
                    name="video",
                    enabled=1,
                    in_movie=1,
                    in_preview=1,
                )
                self.streams[id_]["metadata_track_id"] = outfile.add_track(
                    type=od.MP4_TRACK_TYPE_METADATA,
                    name="metadata",
                    enabled=0,
                    in_movie=0,
                    in_preview=0,
                )

                outfile.ref_to_track(
                    self.streams[id_]["metadata_track_id"],
                    self.streams[id_]["track_id"],
                )

        # start a video sink attached to the new media
        video_sink_params = od.struct_pdraw_video_sink_params.bind(
            dict(
                # drop buffers when the queue is full (buffer_queue_size > 0)
                queue_max_count=self.buffer_queue_size,  # buffer queue size
            )
        )
        self.streams[id_]["id_userdata"] = ctypes.cast(
            ctypes.pointer(ctypes.py_object(id_)), ctypes.c_void_p
        )
        self.streams[id_]["id"] = id_
        self.streams[id_]["video_sink_cbs"] = self.video_sink_vt[frame_type].cbs.bind(
            {
                "flush": self._video_sink_flush,
                "drain": self._video_sink_drain,
            }
        )
        self.streams[id_]["frame_type"] = frame_type
        self.streams[id_]["video_sink"] = od.POINTER_T(
            self.video_sink_vt[frame_type].video_sink_type
        )()

        res = self.video_sink_vt[frame_type].new(
            pdraw,
            id_,
            video_sink_params,
            self.streams[id_]["video_sink_cbs"],
            self.streams[id_]["id_userdata"],
            ctypes.byref(self.streams[id_]["video_sink"]),
        )
        if res != 0 or not self.streams[id_]["video_sink"]:
            self.logger.error("Unable to start video sink")
            return

        # Retrieve the queue belonging to the sink
        queue = self.video_sink_vt[frame_type].get_queue(
            pdraw,
            self.streams[id_]["video_sink"],
        )
        self.streams[id_]["video_queue"] = queue

        # Retrieve event object and related file descriptor
        res = self.video_sink_vt[frame_type].queue_get_event(
            queue, ctypes.byref(self.streams[id_]["video_queue_event"])
        )
        if res < 0 or not self.streams[id_]["video_queue_event"]:
            self.logger.error(
                f"Unable to get video sink queue event: {os.strerror(-res)}"
            )
            return

        # add the file description to our pomp loop
        self._callbacks_loop.add_event_to_loop(
            self.streams[id_]["video_queue_event"],
            lambda *args: self._video_sink_queue_event(*args),
            id_,
        )

    def _video_sink_flush(self, pdraw, videosink, userdata):
        id_ = py_object_cast(userdata)
        return self._callbacks_loop.run_async(self._video_sink_flush_impl, id_).result()

    def _video_sink_flush_impl(self, id_):
        if id_ not in self.streams:
            self.logger.error(f"Received flush event from unknown ID {id_}")
            return -errno.ENOENT

        # FIXME: Workaround video_sink_flush called with destroyed media
        if not self.pdraw:
            self.logger.error(
                f"_video_sink_flush called with a destroyed pdraw id : {id_}"
            )
            return -errno.EINVAL

        # FIXME: Workaround video_sink_flush called with destroyed video queue
        if not self.streams[id_]["video_queue"]:
            self.logger.error(
                f"_video_sink_flush called with a destroyed queue id : {id_}"
            )
            return -errno.EINVAL

        with self.streams[id_]["video_sink_lock"]:
            self.logger.debug(f"flush_callback {id_}")

            frame_type = self.streams[id_]["frame_type"]

            media_by_id = self._get_media_from_multistreams_with_id(id_)
            if media_by_id is not None and media_by_id.flush_cb is not None:
                media_by_id.flush_cb(self.streams[id_])
            # NOTE: If the user failed to flush its buffer at this point,
            # bad things WILL happen we're acknowledging the buffer flush
            # in all cases..

            res = self.video_sink_vt[frame_type].queue_flush(
                self.streams[id_]["video_queue"]
            )
            if res < 0:
                self.logger.error(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}"
                )
            else:
                self.logger.info(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}"
                )

            res = self.video_sink_vt[frame_type].queue_flushed(
                self.pdraw, self.streams[id_]["video_sink"]
            )
            if res < 0:
                self.logger.error(
                    f"pdraw_coded/raw_video_sink_queue_flushed() " f"returned {res}"
                )
            else:
                self.logger.debug(
                    f"pdraw_coded/raw_video_sink_queue_flushed() " f"returned {res}"
                )
        return 0

    def _video_sink_drain(self, pdraw, videosink, userdata):
        id_ = py_object_cast(userdata)
        return self._callbacks_loop.run_async(self._video_sink_drain_impl, id_).result()

    def _video_sink_drain_impl(self, id_):
        if id_ not in self.streams:
            self.logger.error(f"Received drain event from unknown ID {id_}")
            return -errno.ENOENT

        # FIXME: Workaround video_sink_drain called with destroyed media
        if not self.pdraw:
            self.logger.error(
                f"_video_sink_drain called with a destroyed pdraw id : {id_}"
            )
            return -errno.EINVAL

        # FIXME: Workaround video_sink_drain called with destroyed video queue
        if not self.streams[id_]["video_queue"]:
            self.logger.error(
                f"_video_sink_drain called with a destroyed queue id : {id_}"
            )
            return -errno.EINVAL

        with self.streams[id_]["video_sink_lock"]:
            self.logger.debug(f"drain_callback {id_}")

            frame_type = self.streams[id_]["frame_type"]

            media_by_id = self._get_media_from_multistreams_with_id(id_)
            if media_by_id is not None and media_by_id.drain_cb is not None:
                media_by_id.drain_cb(self.streams[id_])
            # NOTE: If the user failed to drain its buffer at this point,
            # bad things WILL happen we're acknowledging the buffer drain
            # in all cases..

            res = self.video_sink_vt[frame_type].queue_flush(
                self.streams[id_]["video_queue"]
            )
            if res < 0:
                self.logger.error(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}"
                )
            else:
                self.logger.info(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}"
                )

            res = self.video_sink_vt[frame_type].queue_drained(
                self.pdraw, self.streams[id_]["video_sink"]
            )
            if res < 0:
                self.logger.error(
                    f"pdraw_coded/raw_video_sink_queue_drained() " f"returned {res}"
                )
            else:
                self.logger.debug(
                    f"pdraw_coded/raw_video_sink_queue_drained() " f"returned {res}"
                )
        return 0

    @callback_decorator()
    def _video_sink_queue_event(self, pomp_evt, userdata):
        id_ = py_object_cast(userdata)
        self.logger.debug(f"media id = {id_}")

        if id_ not in self.streams:
            self.logger.error(f"Received queue event from unknown ID {id_}")
            return

        # acknowledge event
        res = od.pomp_evt_clear(self.streams[id_]["video_queue_event"])
        if res != 0:
            self.logger.error(
                f"Unable to clear frame received event: {os.strerror(-res)}"
            )

        if not self._is_ready_to_play:
            self.logger.debug("The stream is no longer ready: drop one frame")
            return

        # process all available buffers in the queue
        with self.streams[id_]["video_sink_lock"]:
            while self._process_stream(id_):
                pass

    def _process_stream(self, id_: int) -> bool:
        """Callback to process frames from a stream

        :param id_: Id of the media whose frames are available to process
        :type id_: int
        :return: True if frames have correctly been processed, False otherwise
        :rtype: bool
        """
        self.logger.debug(f"media id = {id_}")
        mbuf_video_frame = self._pop_stream_buffer(id_)
        if not mbuf_video_frame:
            return False
        video_frame = VideoFrame(
            self.logger,
            mbuf_video_frame,
            id_,
            self.streams[id_],
            self.get_session_metadata(),
        )
        try:
            self._process_stream_buffer(id_, video_frame)
            return True
        except Exception:
            self.logger.exception("_process_stream_buffer exception")
            return False
        finally:
            # Once we're done with this frame, dispose the
            # associated frame buffer
            video_frame.unref()

    def get_session_metadata(self) -> Optional[Dict[str, Any]]:
        """Returns video stream session metadata

        :return: Video stream session metadata
        :rtype: Dict[str, Any]
        """
        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return None

        return self.session_metadata

    def get_session_metadata_dict(self) -> Dict[str, Dict[str, Any]]:
        """Returns a dictionnary of video stream session metadata

        :return: a dictionnary of video stream session metadata
        :rtype: Dict[str, Dict[str, Any]]
        """
        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            return dict()

        return {stream.name: stream.session_metadata for stream in self.multistreams}

    def _process_stream_buffer(self, id_: int, video_frame: VideoFrame):
        """Process a video frame

        :param id_: media id whose frame is to be processed
        :type id_: int
        :param video_frame: video frame to process
        :type video_frame: VideoFrame
        """
        stream = self.streams[id_]
        stream_info = self._get_stream_from_multistreams_with_id(id_)

        if stream_info is None:
            self.logger.error(
                f"Failed to retrieve stream info {id_} while processing a frame"
            )
            return

        # write and/or send data over the requested channels
        # handle output files

        fmeta = finfo = fvideo = None
        if stream_info.outfiles is not None:
            fmeta = stream_info.outfiles.meta
            finfo = stream_info.outfiles.info
            fvideo = stream_info.outfiles.video

        if fmeta and not fmeta.closed:
            vmeta_type, vmeta = video_frame.vmeta()
            fmeta.write(json.dumps({str(vmeta_type): vmeta}) + "\n")

        if finfo and not finfo.closed:
            info = video_frame.info()
            finfo.write(json.dumps(info) + "\n")

        # In case of raw frames, there is no output file created because there has not
        # been any need in having yuv recording so far. Thus the stream frames won't be
        # processed
        if (
            stream["frame_type"] == od.VDEF_FRAME_TYPE_CODED
            and fvideo
            and not fvideo.closed
        ):
            if (
                stream["track_id"] is not None
                and stream["data_format"] == od.VDEF_CODED_DATA_FORMAT_AVCC
            ):
                track_id = stream["track_id"]
                metadata_track_id = stream["metadata_track_id"]
                h264_header = stream["h264_header"]
                if fvideo.tell(track_id) == 0:
                    now = time.time()
                    fvideo.set_decoder_config(
                        track_id, h264_header, video_frame.width, video_frame.height
                    )
                    fvideo.set_metadata_mime_type(
                        metadata_track_id,
                        od.VMETA_FRAME_PROTO_CONTENT_ENCODING,
                        od.VMETA_FRAME_PROTO_MIME_TYPE,
                    )
                    fvideo.add_track_metadata(
                        track_id,
                        "com.parrot.olympe.first_timestamp",
                        str(now * PDRAW_TIMESCALE),
                    )
                    fvideo.add_track_metadata(
                        track_id,
                        "com.parrot.olympe.resolution",
                        f"{video_frame.width}x{video_frame.height}",
                    )
                fvideo.add_coded_frame(track_id, metadata_track_id, video_frame)

        # call callbacks when existing
        media_by_id = self._get_media_from_multistreams_with_id(id_)
        if media_by_id is not None and media_by_id.video_frame_cb is not None:
            media_by_id.video_frame_cb(video_frame)

    def _pop_stream_buffer(self, id_):
        frame_type = self.streams[id_]["frame_type"]
        mbuf_video_frame = od.POINTER_T(
            self.video_sink_vt[frame_type].mbuf_video_frame_type
        )()
        res = self.video_sink_vt[frame_type].queue_pop(
            self.streams[id_]["video_queue"], ctypes.byref(mbuf_video_frame)
        )
        if res < 0:
            if res not in (-errno.EAGAIN, -errno.ENOENT):
                self.logger.error(
                    "mbuf_coded_video_frame_queue_pop failed: "
                    f"{res} ({os.strerror(-res)})"
                )
            else:
                self.logger.debug(
                    "There is no frame available right now. Keeping on processing..."
                )
            mbuf_video_frame = od.POINTER_T(
                self.video_sink_vt[frame_type].mbuf_video_frame_type
            )()
        elif not mbuf_video_frame:
            self.logger.error("mbuf_coded_video_frame_queue_pop returned NULL")
        return mbuf_video_frame

    def seek_next_frame(self, timeout: float = 5) -> Tuple[bool, Optional[PlayData]]:
        """Go to next frame in frame-by-frame playback.
        This function plays the next frame while the playback is paused. If the
        playback is not currently paused an error is returned.
        Frame-by-frame is only available on local replays (MP4 records).

        :param timeout: time block let to perform next frame seeking
        :type timeout: float
        """
        next_or_previous = True
        return self._loop.run_async(
            self._aseek_next_or_previous_frame, next_or_previous
        ).result_or_cancel(timeout=timeout)

    def seek_previous_frame(
        self, timeout: float = 5
    ) -> Tuple[bool, Optional[PlayData]]:
        """Go to previous frame in frame-by-frame playback.
        This function plays the previous frame while the playback is paused. If the
        playback is not currently paused an error is returned.
        Frame-by-frame is only available on local replays (MP4 records).

        :param timeout: time block let to perform next frame seeking
        :type timeout: float
        """
        next_or_previous = False
        return self._loop.run_async(
            self._aseek_next_or_previous_frame, next_or_previous
        ).result_or_cancel(timeout=timeout)

    async def _aseek_next_or_previous_frame(
        self, next_or_previous: bool
    ) -> Tuple[bool, Optional[PlayData]]:
        if not self.resource_name.startswith("replay/"):
            self.logger.warning(
                "Cannot seek a frame into a live stream. Only replayed streams can be sought to"
            )
            return False, None

        if self.state is not PdrawState.Paused:
            self.logger.warning(
                f"Cannot seek {'next' if next_or_previous else 'previous'} frame in a replay that is not paused. The stream should be paused before seeking {'next' if next_or_previous else 'previous'} frame"
            )
            return False, None

        if self.pdraw and self.pdraw_demuxer:
            if next_or_previous:
                res = od.pdraw_demuxer_next_frame(self.pdraw, self.pdraw_demuxer)
            else:
                res = od.pdraw_demuxer_previous_frame(self.pdraw, self.pdraw_demuxer)
            if res != 0:
                self.logger.error(f"Unable to go frame-by-frame: {os.strerror(-res)}")
                self.state = PdrawState.Error
                return False, None
            else:
                self.logger.info(
                    f"PDrAW demuxer is requested to go to {'next' if next_or_previous else 'previous'} frame"
                )
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False, None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return False, None

        async with self._seek_condition:
            await self._seek_condition.wait()

        return True, self._play_data

    def seek_from(
        self, delta: int, exact: int = 1, timeout: float = 5
    ) -> Tuple[bool, Optional[PlayData]]:
        """Seek a frame from a given delta of time
        Seeking is only available on replays (local or streamed), but not on live streams

        :param delta: delta to seek the frame from, in microseconds. Positive delta seeks
            forward, negative delta seeks backward
        :type delta: int
        :param exact: flag set to 0 to seek to the nearest synchronization sample
            preceeding the timestamp, flag set to 1 to seek to the nearest sample to the
            timestamp
        :type exact: bool
        :param timeout: time block let to perform seeking from
        :type timeout: float
        """
        return self._loop.run_async(
            self._aseek_from, delta=delta, exact=exact
        ).result_or_cancel(timeout=timeout)

    async def _aseek_from(
        self, delta: int, exact: int = 1
    ) -> Tuple[bool, Optional[PlayData]]:
        if not self.resource_name.startswith("replay/"):
            self.logger.warning(
                "Cannot seek a frame into a live stream. Only replayed streams can be sought to"
            )
            return False, None

        if self.state is not PdrawState.Playing:
            self.logger.warning(
                "Cannot seek a frame into a stream that is not playing. The stream should be playing before seeking a frame"
            )
            return False, None

        if self.pdraw and self.pdraw_demuxer:
            res = od.pdraw_demuxer_seek(self.pdraw, self.pdraw_demuxer, delta, exact)
            if res != 0:
                self.logger.error(f"Unable to seek from frame: {os.strerror(-res)}")
                self.state = PdrawState.Error
                return False, None
            else:
                self.logger.info(
                    f"PDrAW demuxer is requested to seek to frame from '{delta} microseconds'"
                )
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False, None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return False, None

        async with self._seek_condition:
            await self._seek_condition.wait()

        return True, self._play_data

    def seek_to(
        self, timestamp: int, exact: int = 1, timeout: float = 5
    ) -> Tuple[bool, Optional[PlayData]]:
        """Seek a frame to a given time
        Seeking is only available on replays (local or streamed), but not on live streams

        :param timestamp: timestamp to seek the frame to, in microseconds
        :type timestamp: int
        :param exact: flag set to 0 to seek to the nearest synchronization sample
            preceeding the timestamp, flag set to 1 to seek to the nearest sample to the
            timestamp
        :type exact: bool
        :param timeout: time block let to perform seeking to
        :type timeout: float
        """
        return self._loop.run_async(
            self._aseek_to, timestamp=timestamp, exact=exact
        ).result_or_cancel(timeout=timeout)

    async def _aseek_to(
        self, timestamp: int, exact: int = 1
    ) -> Tuple[bool, Optional[PlayData]]:
        if not self.resource_name.startswith("replay/"):
            self.logger.warning(
                "Cannot seek a frame into a live stream. Only replayed streams can be sought to"
            )
            return False, None

        if self.state is not PdrawState.Playing:
            self.logger.warning(
                "Cannot seek a frame into a stream that is not playing. The stream should be playing before seeking a frame"
            )
            return False, None

        if self.pdraw and self.pdraw_demuxer:
            res = od.pdraw_demuxer_seek_to(
                self.pdraw, self.pdraw_demuxer, timestamp, exact
            )
            if res != 0:
                self.logger.error(f"Unable to seek to frame: {os.strerror(-res)}")
                self.state = PdrawState.Error
                return False, None
            else:
                self.logger.info(
                    f"PDrAW demuxer is requested to seek to frame at timestamp '{timestamp}'"
                )
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False, None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return False, None

        async with self._seek_condition:
            await self._seek_condition.wait()

        return True, self._play_data

    def get_chapter_list(self) -> Optional[List[ChapterList]]:
        """Get the video chapter list

        This function returns the video chapter list if available. If the video does
        not contain any chapter, None is returned
        The chapter timestamps can be used to seek to the desired
        chapter. This function is available on a record demuxer only; on any other
        type of muxer None is returned.

        :return: list of chapters
        :rtype: List[Chapter]
        """
        if self.pdraw and self.pdraw_demuxer:
            c_chapter_ptr_type = od.POINTER_T(od.struct_pdraw_chapter)
            c_chapter_list = c_chapter_ptr_type()

            c_chapter_count = ctypes.c_size_t()

            res = od.pdraw_demuxer_get_chapter_list(
                self.pdraw,
                self.pdraw_demuxer,
                ctypes.byref(c_chapter_list),
                ctypes.byref(c_chapter_count),
            )
            if res != 0:
                self.logger.error(
                    "Unable to get chapter list or the list is not available"
                )
                return None
            else:
                chapter_list = list()
                array_type = od.struct_pdraw_chapter * c_chapter_count.value
                chapters_array = ctypes.cast(
                    c_chapter_list, od.POINTER_T(array_type)
                ).contents
                for i in range(c_chapter_count.value):
                    chapter = chapters_array[i]
                    py_chapter = ChapterList(
                        timestamp=chapter.ts_us,
                        name=(ctypes.string_at(chapter.name).decode("utf-8")),
                    )
                    chapter_list.append(py_chapter)
                self.logger.info(f"Chapter list is '{chapter_list}'")
                c_chapter_list = od.POINTER_T(od.struct_pdraw_chapter)()
                c_chapter_count = od.POINTER_T(ctypes.c_uint64)()
                return chapter_list
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return None

    def get_replay_duration(self) -> Optional[float]:
        """Get the playback duration

        The duration is only available on replays (either local or streamed), not on
        live streams

        :return: the duration in microseconds on success, 0 in case of error
        :rtype: float
        """
        if not self.resource_name.startswith("replay/"):
            self.logger.warning("Cannot get duration of a stream which is not a replay")
            return None

        if self.pdraw and self.pdraw_demuxer:
            duration_res = od.pdraw_demuxer_get_duration(
                self.pdraw,
                self.pdraw_demuxer,
            )
            if duration_res == 0:
                self.logger.error("Unable to get replay duration")
                self.state = PdrawState.Error
                return None
            else:
                self.logger.info(f"Replay duration is '{duration_res}'")
                return duration_res
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return None

    def get_replay_current_time(self) -> Optional[float]:
        """Get the playback current time

        On replays (either local or streamed) this is the position between 0 and
        the duration; on live streams this is the time since the start of the
        stream session.

        :return: the current time in microseconds on success, 0 in case of error
        :rtype: float
        """
        if self.pdraw and self.pdraw_demuxer:
            current_time_res = od.pdraw_demuxer_get_current_time(
                self.pdraw,
                self.pdraw_demuxer,
            )
            if current_time_res == 0:
                self.logger.error("Unable to get current_time")
                self.state = PdrawState.Error
                return None
            else:
                self.logger.info(f"Stream current time is '{current_time_res}'")
                return current_time_res
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return None
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return None

    def stop(self, timeout: float = 5):
        """Stop the video streaming

        :param timeout: beyong this timeout, the task to stop the streaming is canceled.
            Closing is therefore probably not correctly done, defaults to 5
        :type timeout: float, optional
        :return: A Future task, done or canceled
        :rtype: Future
        """
        return self._loop.run_async(self._astop).result_or_cancel(timeout=timeout)

    async def _astop(self) -> bool:
        """Stop a PDrAW session

        _astop() calls aclose()

        :return: True if the session has stopped properly, False otherwise
        :rtype: bool
        """
        # In case a stop is requested before streaming has been played,
        # and pdraw hasn't been instantiated
        if self.state in (PdrawState.Created, PdrawState.Closed):
            self.logger.info(
                f"There's no need to stop PDrAW from the {self.state} state"
            )
            return True

        # When request a stop once PDrAW has been launched, the pdraw interface is
        # not supposed to disappear
        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False

        # The stop() API is called whenever a streaming is requested to stop.

        if self.state is PdrawState.Close_requested:
            self.logger.warning("PDrAW stop is already ongoing")
            return True

        if self.state is PdrawState.Playing:
            if not await self._apause():
                return False

        self.state = PdrawState.Close_requested
        return await self._aclose()

    async def _aclose(self) -> bool:
        """Close a PDrAW session

        Called by astop()

        :return: True if the session has closed properly, False otherwise
        :rtype: bool
        """
        if self.state is PdrawState.Created:
            self.logger.info(
                f"PDrAW streaming is in {self.state} and does not need to be closed."
            )
            return True

        if self.state not in (PdrawState.Close_requested, PdrawState.Error):
            self.logger.error(f"Unable to close stream from the {self.state} state")
            return False

        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False

        # Close all remaining renderers if existing
        for stream in self.multistreams:
            if stream.renderer is not None:
                stream.renderer.stop()
                stream.renderer = None

        # Close the pdraw demuxer
        if not self._close_stream():
            return False
        # Wait for pdraw library to close the stream
        async with self._close_condition:
            await self._close_condition.wait()
        # When pdraw library adverts stream closing result, callback _close_resp
        # is called and changes state to Closed in case of success. In case of failure,
        # session timesout and state sets to Closed anyway
        if self.state is not PdrawState.Closed:
            self.logger.warning("PDrAW session timedout while closing")
            self.state = PdrawState.Closed

        # Reset Pdraw attributes, so that the next session has its own or uses defaults
        self._reset_attributes()

        # In case the streaming is closing because of an unrecoverable error,
        # the streaming has to be retried
        if self._retrying:
            self.state = PdrawState.Created
            await self._aplay()
            return True

        # Otherwise, close the session proxy
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        return self.state is PdrawState.Closed

    def _close_stream(self) -> bool:
        """Close the pdraw demuxer
        The demuxer will be destroyed in _close_resp

        :return: True if the demuxer has correctly been closed, False otherwise
        :rtype: bool
        """
        if self.pdraw and self.pdraw_demuxer:
            res = od.pdraw_demuxer_close(self.pdraw, self.pdraw_demuxer)
            if res != 0:
                self.logger.error(f"Unable to close PDrAW demuxer: {os.strerror(-res)}")
                self.state = PdrawState.Error
                return False
            else:
                self.logger.info("PDrAW demuxer is requested to close")
        elif not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False
        elif not self.pdraw_demuxer:
            self.logger.error("PDrAW demuxer seems to be destroyed")
            self.state = PdrawState.Error
            return False

        return True

    def play_multiple_stream(
        self,
        url: Optional[str] = None,
        *,
        address: Optional[str] = None,
        port: Optional[int] = None,
        streams_list: List[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]] = [],
        resource_name: str = "live",
        timeout: float = 5,
        speed: Optional[float] = None,
    ) -> bool:
        """Play multiple videostreams

        By default, open and play a live video streaming session available
        from rtsp://192.168.42.1/live where "192.168.42.1" is the default IP
        address of a physical (Anafi) drone. The default is equivalent to
        `Pdraw.play(url="rtsp://192.168.42.1/live")`

        For a live video streaming from a simulated drone, the default simulated drone
        IP address has to be specified (10.202.0.1) instead:
        `Pdraw.play(url="rtsp://10.202.0.1/live")`.

        The `url` parameter can also point to a local file example:
        `Pdraw.play(url="file://~/Videos/100000010001.MP4")`.

        :param url: rtsp or local file video URL, defaults to None
        :type url: Optional[str], optional
        :param address: address of the rtsp URL, defaults to None
        :type address: Optional[str], optional
        :param port: port of the rtsp URL, defaults to None
        :type port: Optional[int], optional
        :param streams_list: Union[StreamInfo, StreamInfoDefault, StreamInfoSingle] of
            the streams to play, defaults to []
        :type streams_list: List[Union[StreamInfo, StreamInfoDefault, StreamInfoSingle]], optional
        :param resource_name: resource name, defaults to "live"
        :type resource_name: str, optional
        :param timeout: beyond this timeout, the task to play the streams is canceled.
            Playing all streams is therefore probably not done, defaults to 5
        :type timeout: float, optional
        :param speed: play the playback of a video at the requested speed, defaults to
            None. If the speed parameter is negative, the video is played backward. If the
            speed is greater than or equal to PDRAW_PLAY_SPEED_MAX, the speed is ignored and
            the video is played at the maximum speed achievable. If the speed is less than
            or equal to -PDRAW_PLAY_SPEED_MAX, the speed is ignored and the video is played
            backward at the maximum speed achievable. Speed cannot be null, if requested. On
            a live stream, the speed parameter has no effect.
        :type speed: float, optional
        :raises ValueError: if multiple Default Streams are provided. There must be only
            one default stream provided by the user
        :return: Future task, done or canceled
        :rtype: Future
        """
        for stream in streams_list:
            # If the user registers a default stream, it has to be stored to be used if
            # any of the other registered stream aren't not proposed by PDrAW library
            if isinstance(stream, StreamInfoDefault):
                if self._stream_info_default is not None:
                    self._stream_info_default = None
                    raise ValueError(
                        "Multiple default streams provided. One only expected"
                    )
                self._stream_info_default = stream

            self.multistreams.append(stream)

        # In case there is no raw_cb registered by the user for any media, deactivate
        # the raw frames encoding for the whole PDrAW instance
        deactivate_decoder = True
        for stream in self.multistreams:
            if stream.has_renderer is not None:
                deactivate_decoder = False
                break
            if [
                cb
                for cb in stream.callbacks
                if cb.media_type[0] == od.VDEF_FRAME_TYPE_RAW
            ]:
                deactivate_decoder = False
                break
        if deactivate_decoder:
            self.pdraw_demuxer_params = od.struct_pdraw_demuxer_params.bind(
                dict(
                    autodecoding_mode=od.PDRAW_DEMUXER_AUTODECODING_MODE_DECODE_NONE,
                )
            )

        # Store if a particular speed is requested. Speed cannot be null (if so, it would
        # pause the stream and the play API is not supposed to handle a pause process)
        if speed and speed != 0:
            self._requested_speed = speed

        return self._loop.run_async(
            self._aplay,
            url=url,
            address=address,
            port=port,
            resource_name=resource_name,
        ).result_or_cancel(timeout=timeout)

    def play(
        self,
        url: Optional[str] = None,
        *,
        address: Optional[str] = None,
        port: Optional[int] = None,
        media_name: str = "Front camera",
        resource_name: str = "live",
        speed: Optional[float] = None,
        timeout: float = 5,
        data_formats: List[int] = list([h264_coded_data_format.bytestream]),
        raw_cb: Optional[Callable[[VideoFrame], None]] = None,
        coded_cb: Optional[Callable[[VideoFrame], None]] = None,
        coded_avcc_cb: Optional[Callable[[VideoFrame], None]] = None,
        coded_bytestream_cb: Optional[Callable[[VideoFrame], None]] = None,
        start_cb: Optional[Callable[[], None]] = None,
        start_raw_cb: Optional[Callable[[], None]] = None,
        start_coded_avcc_cb: Optional[Callable[[], None]] = None,
        start_coded_bytestream_cb: Optional[Callable[[], None]] = None,
        end_cb: Optional[Callable[[], None]] = None,
        end_raw_cb: Optional[Callable[[], None]] = None,
        end_coded_avcc_cb: Optional[Callable[[], None]] = None,
        end_coded_bytestream_cb: Optional[Callable[[], None]] = None,
        flush_raw_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        flush_coded_avcc_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        flush_coded_bytestream_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        drain_raw_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        drain_coded_avcc_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        drain_coded_bytestream_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        has_renderer: bool = False,
        outfiles_path: Optional[OutfilesPath] = None,
    ) -> bool:
        """Play a videostream

        By default, open and play a live video streaming session available
        from rtsp://192.168.42.1/live where "192.168.42.1" is the default IP
        address of a physical (Anafi) drone. The default is equivalent to
        `Pdraw.play(url="rtsp://192.168.42.1/live")`

        For a live video streaming from a simulated drone, the default simulated drone
        IP address has to be specified (10.202.0.1) instead:
        `Pdraw.play(url="rtsp://10.202.0.1/live")`.

        The `url` parameter can also point to a local file example:
        `Pdraw.play(url="file://~/Videos/100000010001.MP4")`.


        :param url: rtsp or local file video URL, defaults to None
        :type url: Optional[str], optional
        :param address: address of the rtsp URL, defaults to None
        :type address: Optional[str], optional
        :param port: port of the rtsp URL, defaults to None
        :type port: Optional[int], optional
        :param media_name: name of the stream/track, defaults to "Front camera"
        :type media_name: str, optional
        :param resource_name: resource name, defaults to "live"
        :type resource_name: str, optional
        :param timeout: beyond this timeout, the task to play the stream is canceled.
            Playing the stream is therefore probably not done, defaults to 5
        :type timeout: float, optional
        :param speed: play the playback of a video at the requested speed, defaults to
            None. If the speed parameter is negative, the video is played backward. If the
            speed is greater than or equal to PDRAW_PLAY_SPEED_MAX, the speed is ignored and
            the video is played at the maximum speed achievable. If the speed is less than
            or equal to -PDRAW_PLAY_SPEED_MAX, the speed is ignored and the video is played
            backward at the maximum speed achievable. Speed cannot be null, if requested. On
            a live stream, the speed parameter has no effect.
        :type speed: float, optional
        :param data_formats: Coded video frame formats requested, defaults to
            list([h264_coded_data_format.bytestream])
        :type data_formats: List[int], optional
        :param raw_cb: callback to process raw frames, defaults to None
        :type raw_cb: Optional[Callable[[VideoFrame], None]], optional
        :param coded_cb: callback to process encoded frames (whether they are bytestream
            or avcc), defaults to None
        :type coded_cb: Optional[Callable[[VideoFrame], None]], optional
        :param coded_avcc_cb: callback to process encoded frames for avcc type, defaults
            to None
        :type coded_avcc_cb: Optional[Callable[[VideoFrame], None]], optional
        :param coded_bytestream_cb: callback to process encoded frames for bytestream
            type, defaults to None
        :type coded_bytestream_cb: Optional[Callable[[VideoFrame], None]], optional
        :param start_cb: callback to call at the beginning of the
            streaming, for retrocompatibility. Linked to bytestream frames by default,
            defaults to None
        :type start_cb: Optional[Callable[[], None]], optional
        :param start_raw_cb: callback to call at the beginning of the
            streaming, for raw frames, defaults to None
        :type start_raw_cb: Optional[Callable[[], None]], optional
        :param start_coded_avcc_cb: callback to call at the beginning of the streaming,
            or avcc frames, defaults to None
        :type start_coded_avcc_cb: Optional[Callable[[], None]], optional
        :param start_coded_bytestream_cb: callback to call at the beginning of the
            streaming, for bytestream frames, defaults to None
        :type start_coded_bytestream_cb: Optional[Callable[[], None]], optional
        :param end_cb: callback to call at the end of the streaming, for retrocompatibility.
            Linked to bytestream frames by default, defaults to None
        :type end_cb: Optional[Callable[[], None]], optional
        :param end_raw_cb: callback to call at the beginning of the streaming, for raw
            frames, defaults to None
        :type end_raw_cb: Optional[Callable[[], None]], optional
        :param end_coded_avcc_cb: callback to call at the end of the streaming,
            for avcc frames, defaults to None
        :type end_coded_avcc_cb: Optional[Callable[[], None]], optional
        :param end_coded_bytestream_cb: callback to call at the end of the
            streaming, for bytestream frames, defaults to None
        :type end_coded_bytestream_cb: Optional[Callable[[], None]], optional
        :param flush_raw_cb: callback to call when flushing raw frames, defaults to None
        :type flush_raw_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param flush_coded_avcc_cb: callback to call when flushing avcc frames, defaults
            to None
        :type flush_coded_avcc_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param flush_coded_bytestream_cb: callback to call when flushing bytestream
            frames, defaults to None
        :type flush_coded_bytestream_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param drain_raw_cb: callback to call when draining raw frames, defaults to None
        :type drain_raw_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param drain_coded_avcc_cb: callback to call when draining avcc frames, defaults
            to None
        :type drain_coded_avcc_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param drain_coded_bytestream_cb: callback to call when draining bytestream
            frames, defaults to None
        :type drain_coded_bytestream_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param has_renderer: indicates if the streaming needs a renderer, defaults to False
        :type has_renderer: bool, optional
        :param outfiles_path: paths of the output files, defaults to None
        :type outfiles_path: Optional[OutfilesPath], optional
        :return: Future task, done or canceled
        :rtype: Future
        """
        if self._single_stream is None:
            self._single_stream = StreamInfoSingle()
        self._single_stream.name = media_name
        self._single_stream.has_renderer = has_renderer
        self._single_stream.outfiles_path = outfiles_path

        if raw_cb or has_renderer:
            media_callback = Callbacks(media_type=(od.VDEF_FRAME_TYPE_RAW, None))
            media_callback.video_frame = raw_cb
            media_callback.flush = flush_raw_cb
            media_callback.drain = drain_raw_cb
            media_callback.start = start_raw_cb
            media_callback.end = end_raw_cb
            self._single_stream.callbacks.append(media_callback)
        if h264_coded_data_format.avcc in data_formats or coded_avcc_cb:
            media_callback = Callbacks(
                media_type=(od.VDEF_FRAME_TYPE_CODED, od.VDEF_CODED_DATA_FORMAT_AVCC)
            )
            media_callback.video_frame = coded_avcc_cb
            media_callback.flush = flush_coded_avcc_cb
            media_callback.drain = drain_coded_avcc_cb
            media_callback.start = start_coded_avcc_cb
            media_callback.end = end_coded_avcc_cb
            self._single_stream.callbacks.append(media_callback)
        if (
            h264_coded_data_format.bytestream in data_formats
            or coded_bytestream_cb
            or coded_cb
        ):
            media_callback = Callbacks(
                media_type=(
                    od.VDEF_FRAME_TYPE_CODED,
                    od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM,
                )
            )
            media_callback.video_frame = coded_bytestream_cb or coded_cb
            media_callback.flush = flush_coded_bytestream_cb
            media_callback.drain = drain_coded_bytestream_cb
            media_callback.start = start_coded_bytestream_cb or start_cb
            media_callback.end = end_coded_bytestream_cb or end_cb
            self._single_stream.callbacks.append(media_callback)

        return self.play_multiple_stream(
            url,
            address=address,
            port=port,
            streams_list=[self._single_stream],
            resource_name=resource_name,
            timeout=timeout,
            speed=speed,
        )

    async def _aplay(
        self,
        url: Optional[str] = None,
        *,
        address: Optional[str] = None,
        port: Optional[int] = None,
        resource_name: str = "live",
    ) -> bool:
        """Asynchronous play of a stream

        :param url: stream location url, defaults to None
        :type url: Optional[str], optional
        :param address: sddress of the stream, defaults to None
        :type address: Optional[str], optional
        :param port: port to access the stream at the given address, defaults to None
        :type port: Optional[int], optional
        :param resource_name: type of the stream. It can be "live" or "replay", defaults to "live"
        :type resource_name: str, optional
        :return: True if the stream has successfully played, False otherwise
        :rtype: bool
        """
        if self.state not in (PdrawState.Created, PdrawState.Paused, PdrawState.Closed):
            self.logger.error(f"Unable to play stream from the {self.state} state")
            self.state = PdrawState.Error
            return False

        # The play() API is called whenever a stream video is requested to
        # (open and) play. The workflow is different whether the play() API
        # is called from a 'Created' state or a 'Paused' state

        # If the current state is Created, the stream has to be first opened.
        # Only pdraw object has been created so far and variables instantiated
        # in the init of the Pdraw class
        if self.state in (PdrawState.Created, PdrawState.Closed):
            self.remote_address = address
            self.remote_port = port
            self.resource_name = resource_name

            await self._format_url(url)

            # Reset session metadata from any previous session
            self.session_metadata = {}
            self.streams = defaultdict(StreamFactory)

            self._open_output_files()
            # From now on, the stream is ready to be opened
            self.state = PdrawState.Open_requested

            # Request stream opening
            if not self._open_stream():
                return False
            # Wait for pdraw library to open the stream and flag result
            async with self._open_condition:
                await self._open_condition.wait()
            # When pdraw library adverts stream opening result,
            # callback _open_resp is called and changes state to Opened
            # in case of success. In case of failure, the state is not
            # set to Opened
            if self.state is not PdrawState.Opened:
                return False
        elif self.state is PdrawState.Paused:
            self.state = PdrawState.Play_requested
            self._ready_to_play(self.pdraw, self.pdraw_demuxer, True, None)

        # Whether the stream has just be opened or was paused, it is now
        # available and requested to be played.
        self.state = PdrawState.Play_requested

        # Check that pdraw interface has correctly been created
        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False

        # When pdraw library adverts playing result, callback _play_resp is called and changes
        # state to Playing in case of success. In case of failure, the state is not set
        # to Playing and the play API returns false : the stream has failed in playing a stream
        async with self._play_condition:
            await self._play_condition.wait()
        return self.state is PdrawState.Playing

    async def _format_url(self, url: Optional[Union[str, bytes]]):
        """Format the url with given parameters if it has not been done yet, and decode
        with utf-8

        :param url: url to decode
        :type url: Optional[Union[str,bytes]]
        """
        if url is None:
            assert self.resource_name is not None
            if self._controller is None or not self._controller.is_skyctrl():
                remote_address = self.remote_address or self._controller._ip_addr_str
                remote_port = self.remote_port or DEFAULT_RTSP_PORT
                self.url = b"rtsp://%s:%d/%s" % (
                    remote_address.encode(),
                    remote_port,
                    self.resource_name.encode(),
                )
            else:
                self._proxy = await self._controller.fopen_skycontroller_tcp_proxy(
                    DEFAULT_RTSP_PORT
                )
                assert self.remote_address is None
                assert self.remote_port is None
                self.url = b"rtsp://%s:%d/%s" % (
                    self._proxy.address.encode(),
                    self._proxy.port,
                    self.resource_name.encode(),
                )
        else:
            if isinstance(url, bytes):
                url = url.decode("utf-8")
            if url.startswith("file://"):
                url = url[7:]
            if url.startswith("~/"):
                url = os.path.expanduser(url)
            url = os.path.expandvars(url)
            url = url.encode("utf-8")
            self.url = url

    def _open_output_files(self):
        """Open the output files of the stream"""
        for stream in self.multistreams:
            # If the user has not requested AVCC frames, there's no point in creating
            # output files to store the recorded stream and replay it later
            if not any(
                callback.media_type[1] is not None
                and get_enum_value_as_int(callback.media_type[1])
                == od.VDEF_CODED_DATA_FORMAT_AVCC
                for callback in stream.callbacks
            ):
                self.logger.warning(
                    f"Video output files won't open for stream {stream.name} because "
                    "the user has not requested AVCC frames "
                    "(which are necessary for video recording)"
                )
                continue
            else:
                # If paths are registered by the user, this means it is requested to store
                # the info, video data and metadata into files
                if not stream.outfiles_path:
                    self.logger.error(
                        f"No path has been provided to open output files for stream {stream.name}"
                    )
                    continue
                stream.outfiles = Outfiles()
                if stream.outfiles_path.info is not None:
                    stream.outfiles.info = open(stream.outfiles_path.info, "w")

                if stream.outfiles_path.video is not None:
                    stream.outfiles.video = Mp4Mux(stream.outfiles_path.video)
                if stream.outfiles_path.meta is not None:
                    stream.outfiles.meta = open(stream.outfiles_path.meta, "w")
                self.logger.info(
                    f"Video output files successfully opened for stream {stream.name}"
                )

    def _open_stream(self) -> bool:
        """Instantiate a PDrAW instance if it doesn't exist yet and open a PDrAW stream
        using an RTSP url

        :return: True if the stream has correctly been opened, False otherwise
        :rtype: bool
        """
        # A Pdraw interface is necessary before going any further
        if not self.pdraw and not self._pdraw_new():
            return False

        if self.state is not PdrawState.Open_requested:
            self.logger.error(f"Unable to open stream from the {self.state} state")
            return False

        # Create demuxer to RTSP streaming url
        if not self._open_url():
            return False

        return True

    def _pdraw_new(self) -> bool:
        """Create a new PDrAW instance

        :return: True if the PDrAW instance has correctly been created, False otherwise
        :rtype: bool
        """
        res = od.pdraw_new(
            self.pomp_loop,
            self.pdraw_cbs,
            ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
            ctypes.byref(self.pdraw),
        )
        if res != 0:
            self.logger.error(f"Unable to create a PDrAW interface: {res}")
            # Free allocated memory
            self.pdraw = od.POINTER_T(od.struct_pdraw)()
            return False
        else:
            self.logger.info("A PDrAW interface has been created")
            return True

    def _open_url(self) -> bool:
        """Open an RTSP streaming URL

        :return: True if the streaming URL has correctly been opened, False otherwise
        :rtype: bool
        """
        assert self.resource_name is not None
        if self.resource_name.startswith("replay/"):
            for _ in (stream for stream in self.multistreams if stream.name is None):
                self.logger.error(
                    f"Error: resource name {self.resource_name} should be provided in 'replay' mode"
                )
                return False

        # Create a pdraw demuxer to open from the url
        if self._controller and isinstance(self._controller._backend, CtrlBackendMuxIp):
            res = od.pdraw_demuxer_new_from_url_on_mux(
                self.pdraw,
                self.url,
                self._controller._backend._info.mux_ctx,
                self.pdraw_demuxer_params,
                self.demuxer_cbs,
                ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
                ctypes.byref(self.pdraw_demuxer),
            )
        else:
            res = od.pdraw_demuxer_new_from_url(
                self.pdraw,
                self.url,
                self.pdraw_demuxer_params,
                self.demuxer_cbs,
                ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
                ctypes.byref(self.pdraw_demuxer),
            )

        if res != 0:
            self.logger.error(
                f"Unable to open a PDrAW url: {self.url} {os.strerror(-res)}"
            )
            return False
        else:
            self.logger.info(f"A PDrAW url is opened : {self.url}")
        return True

    def pause(self, timeout: float = 5):
        """Pause the current playing streaming

        :param timeout: beyond this timeout, the task to pause the stream will be canceled.
            The stream will therefore probably not be paused, defaults to 5
        :type timeout: float, optional
        :return: A Future task, done or canceled
        :rtype: Future
        """
        return self._loop.run_async(self._apause).result_or_cancel(timeout=timeout)

    async def _apause(self) -> bool:
        """Asynchronous pause of a PDrAW streaming

        :return: True if the stream has successfully paused, False otherwise
        :rtype: bool
        """
        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            self.state = PdrawState.Error
            return False

        if self.state is not PdrawState.Playing:
            self.logger.error(f"Unable to pause stream from the {self.state} state")
            return False

        # The pause() API is called whenever the stream is requested
        # to pause while playing

        # Pause the demuxer
        if not self._pause_impl():
            return False
        # The stream is requested to pause via the pdraw demuxer
        self.state = PdrawState.Pause_requested
        # When pdraw library adverts pause result, callback _pause_resp is called
        # and changes state to Paused in case of success. In case of failure,
        # the state is not set to Pause and the play API returns false:
        # the stream has failed in pausing a stream
        async with self._pause_condition:
            await self._pause_condition.wait()
        return self.state is PdrawState.Paused

    def _pause_impl(self) -> bool:
        """Pause request implementation on the libpdraw side

        :return: True if the demuxer has successfully paused the streaming, False otherwise
        :rtype: bool
        """
        res = od.pdraw_demuxer_pause(self.pdraw, self.pdraw_demuxer)
        if res != 0:
            self.logger.error(f"Unable to pause streaming: {os.strerror(-res)}")
            return False
        return True

    async def _acleanup(self) -> bool:
        """Asynchronous cleanup, for a proper clean (Stop streaming, loop callbacks, clear
        multistreams info and attributes of the session, stop the PDrAW session properly,
        close the PDrAW state machine, destroy PDrAW instance)

        :return: True in case of success, False otherwise
        :rtype: bool
        """
        # If the cleanup is already done, there is no need to do it all over again
        if self.state is PdrawState.Cleaned:
            self.logger.info("PDrAW process is already cleaned")
            return True

        if self._cleanup_ongoing:
            self.logger.info("A PDrAW cleanup process is already ongoing")
            return True

        # Notify that a cleanup process is ongoing, and prevent it to run twice in parallel
        self._cleanup_ongoing = True

        try:
            # Stop streaming first
            if not await self._astop():
                return False

            res = self._callbacks_loop.stop()
            if res is False:
                return res

            # Clean the multistreams info
            self.multistreams.clear()

            # Then, stop the pdraw session
            if not await self._astop_pdraw():
                return False

            # And eventually destroy the object
            res = self._destroy_pdraw()
            if res is False:
                return res

        finally:
            self._cleanup_ongoing = False

        self.state = PdrawState.Cleaned

        return True

    async def _astop_pdraw(self) -> bool:
        """Asynchronous stop of the PDrAW instance

        :return: True in case of success, False otherwise
        :rtype: bool
        """
        if self.state is PdrawState.Created:
            self.logger.info(
                f"PDrAW streaming is in {self.state} and does not need to be stopped"
            )
            return True

        if not self.pdraw:
            self.logger.error("PDrAW interface seems to be destroyed")
            return False

        res = od.pdraw_stop(self.pdraw)

        # Cleanup some FDs from the callbacks thread loop that might
        # have been lost
        for stream in self.streams.values():
            if stream["video_queue_event"] is not None:
                self.logger.warning("cleanup leftover PDrAW callbacks eventfds")
                self._callbacks_loop.remove_event_from_loop(stream["video_queue_event"])
                stream["video_queue_event"] = None

        if res != 0:
            self.logger.error(f"Unable to stop PDrAW session: {os.strerror(-res)}")
            return False

        # When pdraw library adverts stop result, callback _stop_resp is called
        async with self._stop_condition:
            await self._stop_condition.wait()

        self.logger.info("PDrAW session is successfully stopped")

        return True

    def cleanup(self, timeout: float = 5):
        """Clean up function, for a proper clean (Stop streaming, loop callbacks, clear
        multistreams info and attributes of the session, stop the PDrAW session properly,
        close the PDrAW state machine, destroy PDrAW instance)

        :param timeout: not used anymore, DEPRECATED
        :type timeout: float, optional
        """
        self._loop.run_async(self._acleanup)
        if self._own_loop:
            self._loop.stop_later()

    def destroy(self):
        """Stop streaming, loop callbacks, clear multistreams info and attributes of the
        session, stop the PDrAW session properly, close the PDrAW state machine, destroy
        PDrAW instance
        """
        try:
            self.cleanup()
        except FutureTimeoutError:
            self.logger.error("Pdraw.destroy() timedout")

    def _destroy_pdraw(self) -> bool:
        """Destroy PDrAW instance

        :return: True if PDrAW has correctly been destroyed, False otherwise
        :rtype: bool
        """
        ret = True
        if self.pdraw:
            self.logger.info("Destroying PDrAW...")
            res = od.pdraw_destroy(self.pdraw)
            if res != 0:
                self.logger.error(f"Unable to destroy PDrAW: {os.strerror(-res)}")
                ret = False
            else:
                self.logger.info("PDrAW is successfully destroyed")
            self.pdraw = od.POINTER_T(od.struct_pdraw)()
        return ret

    def _destroy_pdraw_demuxer(self) -> bool:
        """Destroy PDrAW demuxer

        :return: True if PDrAW demuxer has correctly been destroyed, False otherwise
        :rtype: bool
        """
        ret = True
        if self.pdraw_demuxer:
            if not self.pdraw:
                self.logger.error(
                    "Unable to destroy PDrAW demuxer: PDrAW object does not exist"
                )
                return False
            res = od.pdraw_demuxer_destroy(self.pdraw, self.pdraw_demuxer)
            if res != 0:
                self.logger.error(
                    f"Unable to destroy PDrAW demuxer: {os.strerror(-res)}"
                )
                ret = False
            else:
                self.logger.info("PDrAW demuxer is successfully destroyed")
            self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        return ret

    def set_output_files(
        self,
        video: Optional[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]] = None,
        metadata: Optional[
            Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]
        ] = None,
        info: Optional[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]] = None,
    ):
        """This function is DEPRECATED
        Records the video stream session to the disk

        This function should rather not be called before a first play session has been
        done.
        Setting a file parameter to `None` disables the recording for the related stream
        part.

        :param video: path to the video stream mp4 recording file, defaults to None
        :type video: Optional[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]], optional
        :param metadata: path to the video stream metadata json output file, defaults to None
        :type metadata: Optional[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]], optional
        :param info: path to video stream frames info json output file, defaults to None
        :type info: Optional[Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]], optional
        :raises RuntimeError:  This function MUST NOT be called when a video streaming session is
        active
        """
        warnings.warn("deprecated", DeprecationWarning)

        if self.state is PdrawState.Playing:
            raise RuntimeError(
                "Cannot set video streaming files while streaming is on."
            )

        if len(self.multistreams) > 1:
            raise ValueError(
                "Can't set new path to output files if there is more than one stream"
            )

        if self._single_stream is None:
            self._single_stream = StreamInfoSingle()

        stream = (
            self.multistreams[0] if len(self.multistreams) == 1 else self._single_stream
        )

        if not any(
            callback.media_type[1] is not None
            and get_enum_value_as_int(callback.media_type[1])
            == od.VDEF_CODED_DATA_FORMAT_AVCC
            for callback in stream.callbacks
        ):
            self.logger.warning(
                "Video output files won't open because the user has "
                "not requested AVCC frames "
                "(which are necessary for video recording)"
            )
            return

        if stream.outfiles is None:
            stream.outfiles = Outfiles()
        if stream.outfiles.video:
            stream.outfiles.video.close()
            stream.outfiles.video = None
        if video is not None:
            stream.outfiles.video = Mp4Mux(video)
            stream.outfiles.video.close()

        if stream.outfiles.meta:
            stream.outfiles.meta.close()
            stream.outfiles.meta = None
        if metadata is not None:
            stream.outfiles.meta = open(metadata, "w")
            stream.outfiles.meta.close()

        if stream.outfiles.info:
            stream.outfiles.info.close()
            stream.outfiles.info = None
        if info is not None:
            stream.outfiles.info = open(info, "w")
            stream.outfiles.info.close()

    def set_callbacks(
        self,
        h264_cb: Optional[Callable[[VideoFrame], None]] = None,
        h264_avcc_cb: Optional[Callable[[VideoFrame], None]] = None,
        h264_bytestream_cb: Optional[Callable[[VideoFrame], None]] = None,
        raw_cb: Optional[Callable[[VideoFrame], None]] = None,
        start_cb: Optional[Callable[[], None]] = None,
        end_cb: Optional[Callable[[], None]] = None,
        flush_h264_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        flush_raw_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        drain_h264_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        drain_raw_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """This function is DEPRECATED
        Set the callback functions that will be called when a new video stream
        frame is available, when the video stream starts/ends or when the video
        buffer needs to get flushed/drained.

        Each video frame callback function takes an
        :py:func:`~olympe.VideoFrame` parameter whose lifetime ends after the
        callback execution. If this video frame is passed to another thread,
        its internal reference count need to be incremented first by calling
        :py:func:`~olympe.VideoFrame.ref`. In this case, once the frame is no
        longer needed, its reference count needs to be decremented so that this
        video frame can be returned to memory pool.

        :param h264_cb: callback called for the H264 encoded video stream, defaults to
        None
        :type h264_cb:  Optional[Callable[[VideoFrame], None]], optional
        :param h264_avcc_cb: callback called for the H264 encoded video (AVCC) stream,
        defaults to None
        :type h264_avcc_cb: Optional[Callable[[VideoFrame], None]], optional
        :param h264_bytestream_cb: callback called for the H264 encoded video
        ByteStream) stream, defaults to None
        :type h264_bytestream_cb: Optional[Callable[[VideoFrame], None]], optional
        :param raw_cb: callback called for the decoded video stream, defaults to None
        :type raw_cb: Optional[Callable[[VideoFrame], None]], optional
        :param start_cb: callback called when the video stream starts. Do not accept any
        parameters, defaults to None
        :type start_cb: Optional[Callable[[], None]], optional
        :param end_cb: callback called when the video stream ends. Do not accept any
        parameters, defaults to None
        :type end_cb: Optional[Callable[[], None]], optional
        :param flush_h264_cb: callback called when flushing encoded frames, defaults to
        None
        :type flush_h264_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param flush_raw_cb: callback called when flushing raw frames, defaults to None
        :type flush_raw_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param drain_h264_cb: callback called when flushing encoded frames, defaults to
        None
        :type drain_h264_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        :param drain_raw_cb: callback called when draining raw frames, defaults to None
        :type drain_raw_cb: Optional[Callable[[Dict[str, Any]], None]], optional
        Flush/drain callback functions are called when a video stream reclaim all its
        associated video buffer.
        :raises ValueError: In case both bytestream and AVCC encoded modes have been set
        for a single stream
        """
        warnings.warn("deprecated", DeprecationWarning)

        if h264_cb and (h264_avcc_cb or h264_bytestream_cb):
            raise ValueError(
                "Invalid parameters combination: "
                "h264_cb and one of h264_avcc_cb or h264_bytestream_cb have "
                "been set"
            )

        if len(self.multistreams) > 1:
            raise ValueError(
                "Can't set new callbacks to stream if there is more than one stream"
            )

        if self._single_stream is None:
            self._single_stream = StreamInfoSingle()

        stream = (
            self.multistreams[0] if len(self.multistreams) == 1 else self._single_stream
        )

        for media in stream.media_info:
            media.start_cb = start_cb
            media.end_cb = end_cb
            if media.frame_type == od.VDEF_FRAME_TYPE_CODED:
                media.flush_cb = flush_h264_cb
                media.drain_cb = drain_h264_cb
                if media.data_format == od.VDEF_CODED_DATA_FORMAT_AVCC:
                    media.video_frame_cb = h264_avcc_cb or h264_cb
                elif media.data_format == od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM:
                    media.video_frame_cb = h264_bytestream_cb or h264_cb
            elif media.frame_type == od.VDEF_FRAME_TYPE_RAW:
                media.video_frame_cb = raw_cb
                media.flush_cb = flush_raw_cb
                media.drain_cb = drain_raw_cb
