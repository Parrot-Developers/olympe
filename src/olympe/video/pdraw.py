#  Copyright (C) 2019-2021 Parrot Drones SAS
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
import olympe_deps as od
import errno
import json
import os
import threading
import time
from aenum import Enum, auto
from collections import defaultdict, namedtuple
from concurrent.futures import TimeoutError as FutureTimeoutError
from olympe.utils import py_object_cast, callback_decorator
from olympe.concurrent import Condition, Loop
from olympe.log import LogMixin
from . import VMetaFrameType, PDRAW_LOCAL_STREAM_PORT, PDRAW_LOCAL_CONTROL_PORT  # noqa
from . import PDRAW_TIMESCALE
from .mp4 import Mp4Mux
from .frame import VideoFrame


class PdrawState(Enum):
    Created = auto()
    Closing = auto()
    Closed = auto()
    Stopped = auto()
    Opening = auto()
    Opened = auto()
    Playing = auto()
    Paused = auto()
    Error = auto()


class H264Header(namedtuple('H264Header', ['sps', 'spslen', 'pps', 'ppslen'])):

    def tofile(self, f):
        start = bytearray([0, 0, 0, 1])
        if self.spslen > 0:
            f.write(start)
            f.write(bytearray(self.sps[:self.spslen]))

        if self.ppslen > 0:
            f.write(start)
            f.write(bytearray(self.pps[:self.ppslen]))


def StreamFactory():
    return {
        'id': None,
        'id_userdata': ctypes.c_void_p(),
        'frame_type': od.VDEF_FRAME_TYPE_UNKNOWN,
        'h264_header': None,
        'track_id': None,
        'metadata_track_id': None,
        'video_sink': None,
        'video_sink_cbs': None,
        'video_sink_lock': threading.RLock(),
        'video_queue': None,
        'video_queue_event': od.POINTER_T(od.struct_pomp_evt)(),
    }


class _CodedVideoSink:
    new = od.pdraw_coded_video_sink_new
    destroy = od.pdraw_coded_video_sink_destroy
    queue_flushed = od.pdraw_coded_video_sink_queue_flushed
    resync = od.pdraw_coded_video_sink_resync
    get_queue = od.pdraw_coded_video_sink_get_queue
    cbs = od.struct_pdraw_coded_video_sink_cbs
    video_sink_type = od.struct_pdraw_coded_video_sink
    queue_get_event = od.mbuf_coded_video_frame_queue_get_event
    queue_pop = od.mbuf_coded_video_frame_queue_pop
    queue_flush = od.mbuf_coded_video_frame_queue_flush
    queue_flushed = od.pdraw_coded_video_sink_queue_flushed
    mbuf_video_frame_type = od.struct_mbuf_coded_video_frame


class _RawVideoSink:
    new = od.pdraw_raw_video_sink_new
    destroy = od.pdraw_raw_video_sink_destroy
    queue_flushed = od.pdraw_raw_video_sink_queue_flushed

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
    mbuf_video_frame_type = od.struct_mbuf_raw_video_frame


class Pdraw(LogMixin):

    def __init__(self,
                 name=None,
                 device_name=None,
                 server_addr=None,
                 buffer_queue_size=8,
                 pdraw_thread_loop=None,
                 ):
        """
        :param name: (optional) pdraw client name (used by Olympe logs)
        :type name: str
        :param device_name: (optional) the drone device name
            (used by Olympe logs)
        :type device_name: str
        :param buffer_queue_size: (optional) video buffer queue size
            (defaults to 8)
        :type buffer_queue_size: int
        """

        super().__init__(name, device_name, "pdraw")

        if pdraw_thread_loop is None:
            self.own_pdraw_thread_loop = True
            self.pdraw_thread_loop = Loop(self.logger)
            self.pdraw_thread_loop.start()
        else:
            self.own_pdraw_thread_loop = False
            self.pdraw_thread_loop = pdraw_thread_loop

        self.callbacks_thread_loop = Loop(
            self.logger, parent=self.pdraw_thread_loop)
        self.callbacks_thread_loop.start()
        self.buffer_queue_size = buffer_queue_size
        self.pomp_loop = self.pdraw_thread_loop.pomp_loop

        self._open_condition = Condition(self.pdraw_thread_loop)
        self._close_condition = Condition(self.pdraw_thread_loop)
        self._play_condition = Condition(self.pdraw_thread_loop)
        self._pause_condition = Condition(self.pdraw_thread_loop)
        self._stop_condition = Condition(loop=self.pdraw_thread_loop)
        self._state = PdrawState.Created
        self._state_lock = threading.Lock()
        self._state_wait_events = {k: list() for k in PdrawState}

        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        self.streams = defaultdict(StreamFactory)
        self.session_metadata = {}

        self.outfiles = {
            od.VDEF_FRAME_TYPE_CODED: {
                'data': None,
                'meta': None,
                'info': None,
            },
            od.VDEF_FRAME_TYPE_RAW: {
                'data': None,
                'meta': None,
                'info': None,
            },
        }

        self.frame_callbacks = {
            (od.VDEF_FRAME_TYPE_CODED, od.VDEF_CODED_DATA_FORMAT_AVCC): None,
            (od.VDEF_FRAME_TYPE_CODED, od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM): None,
            (od.VDEF_FRAME_TYPE_RAW, None): None,
        }
        self.start_callback = None
        self.end_callback = None
        self.flush_callbacks = {
            od.VDEF_FRAME_TYPE_CODED: None,
            od.VDEF_FRAME_TYPE_RAW: None,
        }

        self.url = None
        if server_addr is None:
            server_addr = "192.168.42.1"
        self.server_addr = server_addr
        self.resource_name = "live"
        self.media_name = None

        self.demuxer_cbs = od.struct_pdraw_demuxer_cbs.bind({
            "open_resp": self._open_resp,
            "close_resp": self._close_resp,
            "unrecoverable_error": self._unrecoverable_error,
            "ready_to_play": self._ready_to_play,
            "play_resp": self._play_resp,
            "pause_resp": self._pause_resp,
            "seek_resp": self._seek_resp,
            "select_media": self._select_media,
            "end_of_range": self._end_of_range,
        })
        self.pdraw_cbs = od.struct_pdraw_cbs.bind({
            "socket_created": self._socket_created,
            "media_added": self._media_added,
            "media_removed": self._media_removed,
            "stop_resp": self.stop_resp,
        })

        self.video_sink_vt = {
            od.VDEF_FRAME_TYPE_CODED: _CodedVideoSink,
            od.VDEF_FRAME_TYPE_RAW: _RawVideoSink,
        }

        self.pdraw_thread_loop.register_cleanup(self._adispose)

    @property
    def state(self):
        """
        Return the current Pdraw state

        :rtype: PdrawState
        """
        return self._state

    @state.setter
    def state(self, value):
        with self._state_lock:
            self._state = value
            for event in self._state_wait_events[self._state]:
                event.set()
            self._state_wait_events[self._state] = []

    def wait(self, state, timeout=None):
        """
        Wait for the provided Pdraw state

        This function returns True when the requested state is reached or False
        if the timeout duration is reached.

        If the requested state is already reached, this function returns True
        immediately.

        This function may block indefinitely when called without a timeout
        value.

        :type state: PdrawState
        :param timeout: the timeout duration in seconds or None (the default)
        :type timeout: float
        :rtype: bool
        """
        with self._state_lock:
            if self._state == state:
                return True
            event = threading.Event()
            self._state_wait_events[state].append(event)
        return event.wait(timeout=timeout)

    def dispose(self):
        return self.pdraw_thread_loop.run_async(self._adispose)

    def destroy(self):
        self.callbacks_thread_loop.stop()
        try:
            self.dispose().result_or_cancel(timeout=2.)
        except FutureTimeoutError:
            self.logger.error("Pdraw.destroy() timedout")
        self.pdraw_thread_loop.stop()

    async def _adispose(self):
        self.pdraw_thread_loop.unregister_cleanup(
            self._adispose, ignore_error=True)
        await self.aclose()
        if not self._stop():
            return False
        async with self._stop_condition:
            await self._stop_condition.wait()
        return self.state is PdrawState.Closed

    @callback_decorator()
    def _stop(self):
        if not self.pdraw:
            return False
        res = od.pdraw_stop(self.pdraw)
        if self.callbacks_thread_loop.stop():
            self.logger.info("pdraw callbacks thread loop stopped")

        # cleanup some FDs from the callbacks thread loop that might
        # have been lost
        for stream in self.streams.values():
            if stream['video_queue_event'] is not None:
                self.logger.warning(
                    "cleanup leftover pdraw callbacks eventfds")
                self.callbacks_thread_loop.remove_event_from_loop(
                    stream['video_queue_event'])
                stream['video_queue_event'] = None
        if res != 0:
            self.logger.error(f"cannot stop pdraw session: {os.strerror(-res)}")
            return False
        else:
            self.pdraw_thread_loop.run_delayed(2.0, self._stop_waiter)
        return True

    async def _stop_waiter(self):
        async with self._stop_condition:
            self._stop_condition.notify_all()

    @callback_decorator()
    def stop_resp(self, pdraw, status, userdata):
        self.pdraw_thread_loop.run_async(self._astop_resp, status)

    async def _astop_resp(self, status):
        async with self._stop_condition:
            if status != 0:
                self.logger.error(f"_stop_resp called {status}")
                self.state = PdrawState.Error
            else:
                self.logger.info(f"_stop_resp called {status}")
                self.state = PdrawState.Stopped
            self._stop_condition.notify_all()

    def _destroy_pdraw(self):
        ret = True
        if self.pdraw_demuxer:
            if not self.pdraw:
                self.logger.error(
                    "Cannot destroy pdraw demuxer: a NULL pdraw session")
                return False
            self.logger.info("destroying pdraw demuxer...")
            res = od.pdraw_demuxer_destroy(self.pdraw, self.pdraw_demuxer)
            if res != 0:
                self.logger.error(f"cannot destroy pdraw demuxer: {os.strerror(-res)}")
                ret = False
            else:
                self.logger.info("pdraw demuxer destroyed")
            self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        if self.pdraw:
            self.logger.info("destroying pdraw...")
            res = od.pdraw_destroy(self.pdraw)
            if res != 0:
                self.logger.error(f"cannot destroy pdraw: {os.strerror(-res)}")
                ret = False
            else:
                self.logger.info("pdraw destroyed")
            self.pdraw = od.POINTER_T(od.struct_pdraw)()
        return ret

    def _open_url(self):
        """
        Opening rtsp streaming url
        """
        if self.resource_name.startswith("replay/"):
            if self.media_name is None:
                self.logger.error(
                    "Error media_name should be provided in video stream "
                    "replay mode"
                )
                return False
        res = od.pdraw_demuxer_new_from_url(
            self.pdraw,
            self.url,
            self.demuxer_cbs,
            ctypes.cast(
                ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
            ctypes.byref(self.pdraw_demuxer)
        )

        if res != 0:
            self.logger.error(
                f"Error while opening pdraw url: {self.url} {os.strerror(-res)}")
            return False
        else:
            self.logger.info(f"Opening pdraw url OK: {self.url}")
        return True

    @callback_decorator()
    def _open_stream(self):
        """
        Opening pdraw stream using an rtsp url
        """
        if self.state not in (
                PdrawState.Error,
                PdrawState.Stopped,
                PdrawState.Closed,
                PdrawState.Created):
            self.logger.warning(f"Cannot open stream from {self.state}")
            return False

        self.state = PdrawState.Opening
        if not self.pdraw and not self._pdraw_new():
            return False

        if not self._open_url():
            return False

        return True

    def close(self):
        """
        Close a playing or paused video stream session
        """
        return self.pdraw_thread_loop.run_async(self.aclose).result_or_cancel(5.0)

    async def aclose(self):
        """
        Close a playing or paused video stream session
        """
        if self.state is PdrawState.Closed:
            return True
        elif self.state is PdrawState.Created:
            self.state = PdrawState.Closed
            return True
        if self.state in (
                PdrawState.Opened,
                PdrawState.Paused,
                PdrawState.Playing,
                PdrawState.Error):
            self.logger.debug(f"pdraw closing from the {self.state} state")
            self.state = PdrawState.Closing
            if not self._close_stream():
                return False
        elif self.state is not PdrawState.Closing:
            return False
        self.pdraw_thread_loop.run_delayed(1., self._close_waiter)
        async with self._close_condition:
            await self._close_condition.wait()
        if self.state is not PdrawState.Closed:
            self.logger.warning("Closing pdraw session timedout")
            # FIXME: workaround TRS-1052
            self.state = PdrawState.Closed
        return self.state is PdrawState.Closed

    async def _close_waiter(self):
        async with self._close_condition:
            self._close_condition.notify_all()

    def _close_stream(self):
        """
        Close pdraw stream
        """
        if self.state is PdrawState.Closed:
            self.logger.info("pdraw is already closed")
            return True

        if not self.pdraw:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            self.state = PdrawState.Error
            return False

        if not self._close_stream_impl():
            self.state = PdrawState.Error
            return False

        return True

    def _close_stream_impl(self):
        res = od.pdraw_demuxer_close(self.pdraw, self.pdraw_demuxer)

        if res != 0:
            self.logger.error(f"Error while closing pdraw demuxer: {os.strerror(-res)}")
            self.state = PdrawState.Error
            return False
        else:
            self.logger.info("Closing pdraw demuxer OK")

        return True

    @callback_decorator()
    def _open_resp(self, pdraw, demuxer, status, userdata):
        self.logger.debug("_open_resp called")
        self.pdraw_thread_loop.run_async(self._aopen_resp, status)

    async def _aopen_resp(self, status):
        if status != 0:
            self.state = PdrawState.Error
        else:
            self.state = PdrawState.Opened

        async with self._open_condition:
            self._open_condition.notify_all()

    @callback_decorator()
    def _close_resp(self, pdraw, demuxer, status, userdata):
        self._close_output_files()
        self.pdraw_thread_loop.run_async(self._aclose_resp, pdraw, demuxer, status)

    async def _aclose_resp(self, pdraw, demuxer, status):
        if status != 0:
            self.logger.error(f"_close_resp called {status}")
            self.state = PdrawState.Error
        else:
            self.logger.debug(f"_close_resp called {status}")
            self.state = PdrawState.Closed
        if demuxer == self.pdraw_demuxer:
            self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        res = od.pdraw_demuxer_destroy(pdraw, demuxer)
        if res != 0:
            self.logger.error(f"pdraw_demuxer_destroy: {os.strerror(-res)}")
        else:
            self.logger.debug(f"pdraw_demuxer_destroy: {os.strerror(-res)}")
        async with self._close_condition:
            self._close_condition.notify_all()

    def _pdraw_new(self):
        res = od.pdraw_new(
            self.pomp_loop,
            self.pdraw_cbs,
            ctypes.cast(
                ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
            ctypes.byref(self.pdraw)
        )
        if res != 0:
            self.logger.error(f"Error while creating pdraw interface: {res}")
            self.pdraw = od.POINTER_T(od.struct_pdraw)()
            return False
        else:
            self.logger.info("Pdraw interface has been created")
            return True

    def _unrecoverable_error(self, pdraw, demuxer, userdata):
        self.logger.error("_unrecoverable_error() -> pdraw teardown...")
        # remove every video sinks
        for id_ in self.streams:
            self._video_sink_flush_impl(id_)
            self._media_removed_impl(id_)

        # demuxer.close -> demuxer.destroy
        if self.pdraw and self.pdraw_demuxer:
            od.pdraw_demuxer_close(self.pdraw, self.pdraw_demuxer)
            # the demuxer will be destroyed in close_resp
            self.pdraw_demuxer = od.POINTER_T(od.struct_pdraw_demuxer)()
        self.logger.error("_unrecoverable_error() -> pdraw teardown done")

        # we should be good to go again with a Pdraw.play()
        self._state = PdrawState.Created

    def _ready_to_play(self, pdraw, demuxer, ready, userdata):
        self.logger.info(f"_ready_to_play({ready}) called")
        self._is_ready_to_play = bool(ready)
        if self._is_ready_to_play:
            self._play_impl()
            if self.start_callback is not None:
                self.callbacks_thread_loop.run_async(self.start_callback)
        else:
            if self.end_callback is not None:
                self.callbacks_thread_loop.run_async(self.end_callback)

    def _play_resp(self, pdraw, demuxer, status, timestamp, speed, userdata):
        self.pdraw_thread_loop.run_async(self._aplay_resp, status)

    async def _aplay_resp(self, status):
        if status == 0:
            self.logger.debug(f"_play_resp called {status}")
            self.state = PdrawState.Playing
        else:
            self.logger.error(f"_play_resp called {status}")
            self.state = PdrawState.Error
        async with self._play_condition:
            self._play_condition.notify_all()

    def _pause_resp(self, pdraw, demuxer, status, timestamp, userdata):
        self.pdraw_thread_loop.run_async(self._apause_resp, status)

    async def _apause_resp(self, status):
        if status == 0:
            self.logger.debug(f"_pause_resp called {status}")
            self.state = PdrawState.Paused
        else:
            self.logger.error(f"_pause_resp called {status}")
            self.state = PdrawState.Error
        async with self._pause_condition:
            self._pause_condition.notify_all()

    def _seek_resp(self, pdraw, demuxer, status, timestamp, userdata):
        if status == 0:
            self.logger.debug(f"_seek_resp called {status}")
        else:
            self.logger.error(f"_seek_resp called {status}")
            self.state = PdrawState.Error

    def _socket_created(self, pdraw, fd, userdata):
        self.logger.debug("_socket_created called")

    def _select_media(self, pdraw, demuxer, medias, count, userdata):
        # by default select the default media (media_id=0)
        selected_media_id = 0
        selected_media_idx = 0
        default_media_id = 0
        default_media_idx = 0
        for idx in range(count):
            self.logger.info(
                f"_select_media: "
                f"idx={idx} media_id={medias[idx].media_id} "
                f"name={od.string_cast(medias[idx].name)} "
                f"default={str(bool(medias[idx].is_default))}"
            )
            if bool(medias[idx].is_default):
                default_media_id = medias[idx].media_id
                default_media_idx = idx
            if (self.media_name is not None and
                    self.media_name == od.string_cast(medias[idx].name)):
                selected_media_id = medias[idx].media_id
                selected_media_idx = idx
        if (
            self.media_name is not None and
            od.string_cast(medias[selected_media_idx].name) != self.media_name
        ):
            default_media_name = od.string_cast(medias[default_media_idx].name)
            self.logger.warning(
                f"media_name {self.media_name} is unavailable. "
                f"Selecting the default media instead: {default_media_name}"
            )
            self.session_metadata = od.struct_vmeta_session.as_dict(
                medias[default_media_idx].session_meta)
        else:
            self.session_metadata = od.struct_vmeta_session.as_dict(
                medias[selected_media_idx].session_meta)
        if selected_media_id:
            return 1 << selected_media_id
        elif default_media_id:
            return 1 << default_media_id
        else:
            return 0

    def _media_added(self, pdraw, media_info, userdata):
        id_ = int(media_info.contents.id)
        self.logger.info(f"_media_added id : {id_}")

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

        # store the information if it is supported and requested media
        # otherwise exit
        if (frame_type != od.VDEF_FRAME_TYPE_RAW and
                frame_type != od.VDEF_FRAME_TYPE_CODED):
            self.logger.warning(
                f"Ignoring unsupported media id {id_} "
                f"(type {video_info.format})")
            return

        requested_media = False
        if self.frame_callbacks[media_type] is not None:
            requested_media = True
        elif any(map(lambda f: f is not None, self.outfiles[frame_type])):
            requested_media = True

        if not requested_media:
            self.logger.info(
                f"Skipping non-requested media id {id_} "
                f"(type {video_info.format})")
            return

        self.streams[id_]["media_type"] = media_type
        self.streams[id_]["frame_type"] = frame_type
        self.streams[id_]["vdef_format"] = vdef_format

        if frame_type == od.VDEF_FRAME_TYPE_CODED and (
                od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM):
            outfile = self.outfiles[frame_type]["data"]
            if outfile:
                header = video_info.pdraw_coded_video_info_0.h264
                header = H264Header(
                    bytearray(header.sps),
                    int(header.spslen),
                    bytearray(header.pps),
                    int(header.ppslen),
                )
                self.streams[id_]['h264_header'] = header
                self.streams[id_]['track_id'] = outfile.add_track(
                    type=od.MP4_TRACK_TYPE_VIDEO,
                    name="video",
                    enabled=1,
                    in_movie=1,
                    in_preview=1,
                )
                self.streams[id_]['metadata_track_id'] = outfile.add_track(
                    type=od.MP4_TRACK_TYPE_METADATA,
                    name="metadata",
                    enabled=0,
                    in_movie=0,
                    in_preview=0,
                )

                outfile.ref_to_track(
                    self.streams[id_]['metadata_track_id'],
                    self.streams[id_]['track_id']
                )

        # start a video sink attached to the new media
        video_sink_params = od.struct_pdraw_video_sink_params.bind(dict(
            # drop buffers when the queue is full (buffer_queue_size > 0)
            queue_max_count=self.buffer_queue_size,  # buffer queue size
        ))
        self.streams[id_]['id_userdata'] = ctypes.cast(
            ctypes.pointer(ctypes.py_object(id_)), ctypes.c_void_p)
        self.streams[id_]['id'] = id_
        self.streams[id_]['video_sink_cbs'] = self.video_sink_vt[
            frame_type].cbs.bind({"flush": self._video_sink_flush})
        self.streams[id_]["frame_type"] = frame_type
        self.streams[id_]['video_sink'] = od.POINTER_T(
            self.video_sink_vt[frame_type].video_sink_type)()

        res = self.video_sink_vt[frame_type].new(
            pdraw,
            id_,
            video_sink_params,
            self.streams[id_]['video_sink_cbs'],
            self.streams[id_]['id_userdata'],
            ctypes.byref(self.streams[id_]['video_sink'])
        )
        if res != 0 or not self.streams[id_]['video_sink']:
            self.logger.error("Unable to start video sink")
            return

        # Retrieve the queue belonging to the sink
        queue = self.video_sink_vt[frame_type].get_queue(
            pdraw,
            self.streams[id_]['video_sink'],
        )
        self.streams[id_]['video_queue'] = queue

        # Retrieve event object and related file descriptor
        res = self.video_sink_vt[frame_type].queue_get_event(
            queue, ctypes.byref(self.streams[id_]['video_queue_event']))
        if res < 0 or not self.streams[id_]['video_queue_event']:
            self.logger.error(f"Unable to get video sink queue event: {os.strerror(-res)}")
            return

        # add the file description to our pomp loop
        self.callbacks_thread_loop.add_event_to_loop(
            self.streams[id_]['video_queue_event'],
            lambda *args: self._video_sink_queue_event(*args),
            id_
        )

    def _media_removed(self, pdraw, media_info, userdata):
        id_ = media_info.contents.id
        if id_ not in self.streams:
            self.logger.error(f"Received removed event from unknown ID {id_}")
            return

        self.logger.info(f"_media_removed called id : {id_}")

        # FIXME: Workaround media_removed called with destroyed media
        if not self.pdraw:
            self.logger.error(
                f"_media_removed called with a destroyed pdraw id : {id_}"
            )
            return
        self._media_removed_impl(id_)

    def _media_removed_impl(self, id_):
        frame_type = self.streams[id_]['frame_type']
        with self.streams[id_]['video_sink_lock']:
            if self.streams[id_]['video_queue_event']:
                self.callbacks_thread_loop.remove_event_from_loop(
                    self.streams[id_]['video_queue_event']).result_or_cancel(
                        timeout=5.)
            self.streams[id_]['video_queue_event'] = None

            if not self.streams[id_]['video_sink']:
                self.logger.error(
                    f"pdraw_video_sink for media_id {id_} "
                    f"has already been stopped"
                )
                return
            res = self.video_sink_vt[frame_type].destroy(
                self.pdraw, self.streams[id_]['video_sink'])
            if res < 0:
                self.logger.error(f"pdraw_stop_video_sink() returned {res}")
            else:
                self.logger.debug(
                    f"_media_removed video sink destroyed id : {id_}"
                )
            self.streams[id_]['video_queue'] = None
            self.streams[id_]['video_sink'] = None
            self.streams[id_]['video_sink_cbs'] = None

    def _end_of_range(self, pdraw, demuxer, timestamp, userdata):
        self.logger.info("_end_of_range")
        self.pdraw_thread_loop.run_async(self.aclose)

    def _video_sink_flush(self, pdraw, videosink, userdata):
        id_ = py_object_cast(userdata)
        if id_ not in self.streams:
            self.logger.error(f"Received flush event from unknown ID {id_}")
            return -errno.ENOENT

        return self._video_sink_flush_impl(id_)

    def _video_sink_flush_impl(self, id_):
        # FIXME: Workaround video_sink_flush called with destroyed media
        if not self.pdraw:
            self.logger.error(
                f"_video_sink_flush called with a destroyed pdraw id : {id_}")
            return -errno.EINVAL

        # FIXME: Workaround video_sink_flush called with destroyed video queue
        if not self.streams[id_]['video_queue']:
            self.logger.error(
                f"_video_sink_flush called with a destroyed queue id : {id_}")
            return -errno.EINVAL

        with self.streams[id_]['video_sink_lock']:
            self.logger.debug(f"flush_callback {id_}")

            flush_callback = self.flush_callbacks[self.streams[id_]['frame_type']]
            if flush_callback is not None:
                flushed = self.callbacks_thread_loop.run_async(
                    flush_callback, self.streams[id_])
                try:
                    if not flushed.result_or_cancel(timeout=5.):
                        self.logger.error(f"video sink flush id {id_} error")
                except FutureTimeoutError:
                    self.logger.error(f"video sink flush id {id_} timeout")
                # NOTE: If the user failed to flush its buffer at this point,
                # bad things WILL happen we're acknowledging the buffer flush
                # in all cases...
            frame_type = self.streams[id_]['frame_type']
            res = self.video_sink_vt[frame_type].queue_flush(
                self.streams[id_]['video_queue'])
            if res < 0:
                self.logger.error(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}")
            else:
                self.logger.info(
                    f"mbuf_coded/raw_video_frame_queue_flush(): {os.strerror(-res)}")

            res = self.video_sink_vt[frame_type].queue_flushed(
                self.pdraw, self.streams[id_]['video_sink'])
            if res < 0:
                self.logger.error(
                    f"pdraw_coded/raw_video_sink_queue_flushed() "
                    f"returned {res}"
                )
            else:
                self.logger.debug(
                    f"pdraw_coded/raw_video_sink_queue_flushed() "
                    f"returned {res}"
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
        res = od.pomp_evt_clear(self.streams[id_]['video_queue_event'])
        if res != 0:
            self.logger.error(f"Unable to clear frame received event: {os.strerror(-res)}")

        if not self._is_ready_to_play:
            self.logger.debug("The stream is no longer ready: drop one frame")
            return

        # process all available buffers in the queue
        with self.streams[id_]['video_sink_lock']:
            while self._process_stream(id_):
                pass

    def _pop_stream_buffer(self, id_):
        frame_type = self.streams[id_]['frame_type']
        mbuf_video_frame = od.POINTER_T(
            self.video_sink_vt[frame_type].mbuf_video_frame_type)()
        res = self.video_sink_vt[frame_type].queue_pop(
            self.streams[id_]['video_queue'], ctypes.byref(mbuf_video_frame)
        )
        if res < 0:
            if res not in (-errno.EAGAIN, -errno.ENOENT):
                self.logger.error(
                    f"mbuf_coded_video_frame_queue_pop returned error: {os.strerror(-res)}")
            mbuf_video_frame = od.POINTER_T(
                self.video_sink_vt[frame_type].mbuf_video_frame_type)()
        elif not mbuf_video_frame:
            self.logger.error('mbuf_coded_video_frame_queue_pop returned NULL')
        return mbuf_video_frame

    def _process_stream(self, id_):
        self.logger.debug(f"media id = {id_}")
        mbuf_video_frame = self._pop_stream_buffer(id_)
        if not mbuf_video_frame:
            return False
        video_frame = VideoFrame(
            self.logger,
            mbuf_video_frame,
            id_,
            self.streams[id_],
            self.get_session_metadata()
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

    def _process_stream_buffer(self, id_, video_frame):
        stream = self.streams[id_]
        frame_type = stream['frame_type']
        media_type = stream["media_type"]

        # write and/or send data over the requested channels
        # handle output files
        files = self.outfiles[frame_type]

        f = files['meta']
        if f and not f.closed:
            vmeta_type, vmeta = video_frame.vmeta()
            files['meta'].write(json.dumps({str(vmeta_type): vmeta}) + '\n')

        f = files['info']
        if f and not f.closed:
            info = video_frame.info()
            files['info'].write(json.dumps(info) + '\n')

        f = files['data']

        if f and not f.closed:
            if frame_type == od.VDEF_FRAME_TYPE_CODED:
                if stream["track_id"] is not None:
                    track_id = stream["track_id"]
                    metadata_track_id = stream["metadata_track_id"]
                    h264_header = stream["h264_header"]
                    if f.tell(track_id) == 0:
                        now = time.time()
                        f.set_decoder_config(
                            track_id,
                            h264_header,
                            video_frame.width,
                            video_frame.height
                        )
                        f.set_metadata_mime_type(
                            metadata_track_id,
                            od.VMETA_FRAME_PROTO_CONTENT_ENCODING,
                            od.VMETA_FRAME_PROTO_MIME_TYPE
                        )
                        f.add_track_metadata(
                            track_id,
                            "com.parrot.olympe.first_timestamp",
                            str(now * PDRAW_TIMESCALE)
                        )
                        f.add_track_metadata(
                            track_id,
                            "com.parrot.olympe.resolution",
                            f"{video_frame.width}x{video_frame.height}",
                        )
                    f.add_coded_frame(
                        track_id, metadata_track_id, video_frame)
            else:
                frame_array = video_frame.as_ndarray()
                if frame_array is not None:
                    f.write(ctypes.string_at(
                        frame_array.ctypes.data_as(
                            ctypes.POINTER(ctypes.c_ubyte)),
                        frame_array.size,
                    ))

        # call callbacks when existing
        cb = self.frame_callbacks[media_type]
        if cb is not None:
            cb(video_frame)

    def set_output_files(self,
                         video=None,
                         metadata=None,
                         info=None):
        """
        Records the video stream session to the disk

        - video: path to the video stream mp4 recording file
        - metadata: path to the video stream metadata json output file
        - info: path to video stream frames info json output file

        This function MUST NOT be called when a video streaming session is
        active.
        Setting a file parameter to `None` disables the recording for the
        related stream part.
        """
        if self.state is PdrawState.Playing:
            raise RuntimeError(
                'Cannot set video streaming files while streaming is on.')

        for frame_type, data_type, filepath, attrib in (
                (od.VDEF_FRAME_TYPE_CODED, 'data', video, 'wb'),
                (od.VDEF_FRAME_TYPE_CODED, 'meta', metadata, 'w'),
                (od.VDEF_FRAME_TYPE_CODED, 'info', info, 'w')):
            if self.outfiles[frame_type][data_type]:
                self.outfiles[frame_type][data_type].close()
                self.outfiles[frame_type][data_type] = None

            if filepath is None:
                continue

            # open and close file to store its filename and attribute
            self.outfiles[frame_type][data_type] = open(filepath, attrib)
            self.outfiles[frame_type][data_type].close()

    def set_callbacks(self,
                      h264_cb=None,
                      h264_avcc_cb=None,
                      h264_bytestream_cb=None,
                      raw_cb=None,
                      start_cb=None,
                      end_cb=None,
                      flush_h264_cb=None,
                      flush_raw_cb=None):
        """
        Set the callback functions that will be called when a new video stream
        frame is available, when the video stream starts/ends or when the video
        buffer needs to get flushed.

        **Video frame callbacks**

        - `h264_cb` is associated to the H264 encoded video (AVCC) stream
        - `h264_avcc_cb` is associated to the H264 encoded video (AVCC) stream
        - `h264_bytestream_cb` is associated to the H264 encoded video
            (ByteStream) stream
        - `raw_cb` is associated to the decoded video stream

        Each video frame callback function takes an
        :py:func:`~olympe.VideoFrame` parameter whose lifetime ends after the
        callback execution. If this video frame is passed to another thread,
        its internal reference count need to be incremented first by calling
        :py:func:`~olympe.VideoFrame.ref`. In this case, once the frame is no
        longer needed, its reference count needs to be decremented so that this
        video frame can be returned to memory pool.

        **Video flush callbacks**

        - `flush_h264_cb` is associated to the H264 encoded video stream
        - `flush_raw_cb` is associated to the decoded video stream

        Video flush callback functions are called when a video stream reclaim
        all its associated video buffer. Every frame that has been referenced

        **Start/End callbacks**

        The `start_cb`/`end_cb` callback functions are called when the video
        stream start/ends. They don't accept any parameter.

        The return value of all these callback functions are ignored.
        If a callback is not desired, leave the parameter to its default value
        or set it to `None` explicitly.
        """

        if h264_cb and (h264_avcc_cb or h264_bytestream_cb):
            raise ValueError(
                "Invalid parameters combination: "
                "h264_cb and one of h264_avcc_cb or h264_bytestream_cb have "
                "been set"
            )

        h264_avcc_cb = h264_avcc_cb or h264_cb

        for media_type, cb in (
                ((od.VDEF_FRAME_TYPE_CODED, od.VDEF_CODED_DATA_FORMAT_AVCC),
                 h264_avcc_cb),
                ((od.VDEF_FRAME_TYPE_CODED, od.VDEF_CODED_DATA_FORMAT_BYTE_STREAM),
                 h264_bytestream_cb),
                ((od.VDEF_FRAME_TYPE_RAW, None),
                 raw_cb)):
            self.frame_callbacks[media_type] = callback_decorator(
                logger=self.logger)(cb)
        for frame_type, cb in ((od.VDEF_FRAME_TYPE_CODED, flush_h264_cb),
                               (od.VDEF_FRAME_TYPE_RAW, flush_raw_cb)):
            self.flush_callbacks[frame_type] = callback_decorator(
                logger=self.logger)(cb)
        self.start_callback = callback_decorator(logger=self.logger)(start_cb)
        self.end_callback = callback_decorator(logger=self.logger)(end_cb)

    def _open_output_files(self):
        self.logger.debug('opening video output files')
        for frame_type, data in self.outfiles.items():
            for data_type, f in data.items():
                if f and f.closed:
                    if data_type == "data" and (
                            frame_type == od.VDEF_FRAME_TYPE_CODED):
                        self.outfiles[frame_type][data_type] = Mp4Mux(f.name)
                    else:
                        self.outfiles[frame_type][data_type] = open(f.name, f.mode)

    def _close_output_files(self):
        self.logger.debug('closing video output files')
        for files in self.outfiles.values():
            for f in files.values():
                if f:
                    f.close()

    def start(self, *args, **kwds):
        """
        See :py:func:`~olympe.video.Pdraw.play`
        """
        return self.play(*args, **kwds)

    def stop(self, timeout=5):
        """
        Stops the video stream
        """
        return self.pdraw_thread_loop.run_async(
            self.astop
        ).result_or_cancel(timeout=timeout)

    async def astop(self):
        """
        Stops the video stream
        """
        if not await self.apause():
            return False
        return await self.aclose()

    def play(
            self,
            url=None,
            media_name="DefaultVideo",
            resource_name="live",
            timeout=5):
        """
        Play a video

        By default, open and play a live video streaming session available
        from rtsp://192.168.42.1/live where "192.168.42.1" is the default IP
        address of a physical (Anafi) drone. The default is equivalent to
        `Pdraw.play(url="rtsp://192.168.42.1/live")`

        For a the live video streaming from a **simulated drone**, you have to
        specify the default simulated drone IP address (10.202.0.1) instead:
        `Pdraw.play(url="rtsp://10.202.0.1/live")`.

        The `url` parameter can also point to a local file example:
        `Pdraw.play(url="file://~/Videos/100000010001.MP4")`.

        :param url: rtsp or local file video URL
        :type url: str
        :param media_name: name of the media/track (defaults to "DefaultVideo").
            If the provided media name is not available from the requested video
            stream, the default media is selected instead.
        :type media_name: str

        """
        return self.pdraw_thread_loop.run_async(
            self.aplay,
            url=url,
            media_name=media_name,
            resource_name=resource_name,
            timeout=timeout
        ).result_or_cancel(timeout=timeout)

    async def aplay(
            self,
            url=None,
            media_name="DefaultVideo",
            resource_name="live",
            timeout=5):

        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            return False

        if self.state in (PdrawState.Opening, PdrawState.Closing):
            self.logger.error(
                f"Cannot play stream from the {self.state} state")
            return False

        self.resource_name = resource_name
        self.media_name = media_name

        if url is None:
            self.url = b"rtsp://%s/%s" % (
                self.server_addr.encode(), self.resource_name.encode())
        else:
            if isinstance(url, bytes):
                url = url.decode('utf-8')
            if url.startswith('file://'):
                url = url[7:]
            if url.startswith('~/'):
                url = os.path.expanduser(url)
            url = os.path.expandvars(url)
            url = url.encode('utf-8')
            self.url = url

        # reset session metadata from any previous session
        self.session_metadata = {}
        self.streams = defaultdict(StreamFactory)

        self._open_output_files()
        if self.state in (PdrawState.Created, PdrawState.Closed):
            self.pdraw_thread_loop.run_delayed(timeout, self._open_waiter)
            if not self._open_stream():
                return False
            async with self._open_condition:
                await self._open_condition.wait()
            if self.state is not PdrawState.Opened:
                return False
        self.pdraw_thread_loop.run_delayed(timeout, self._play_waiter)
        async with self._play_condition:
            await self._play_condition.wait()
        return self.state is PdrawState.Playing

    async def _open_waiter(self):
        async with self._open_condition:
            self._open_condition.notify_all()

    async def _play_waiter(self):
        async with self._play_condition:
            self._play_condition.notify_all()

    def _play_impl(self):
        self.logger.debug("play_impl")
        if self.state is PdrawState.Playing:
            return True

        res = od.pdraw_demuxer_play(self.pdraw, self.pdraw_demuxer)
        if res != 0:
            self.logger.error(f"Unable to start streaming: {os.strerror(-res)}")
            return False

        return True

    def pause(self):
        """
        Pause the currently playing video
        """
        return self.pdraw_thread_loop.run_async(self.apause)

    async def apause(self):
        """
        Pause the currently playing video
        """
        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            return False

        if self.state is PdrawState.Playing:
            self.pdraw_thread_loop.run_delayed(2.0, self._pause_waiter)
            if not self._pause_impl():
                return False
        elif self.state is PdrawState.Closed:
            # Pause an closed stream is OK
            return True
        else:
            return False
        async with self._pause_condition:
            await self._pause_condition.wait()

        return self.state in (PdrawState.Closed, PdrawState.Paused)

    async def _pause_waiter(self):
        async with self._pause_condition:
            self._pause_condition.notify_all()

    def _pause_impl(self):
        res = od.pdraw_demuxer_pause(self.pdraw, self.pdraw_demuxer)
        if res != 0:
            self.logger.error(f"Unable to stop streaming: {os.strerror(-res)}")
            return False
        return True

    def get_session_metadata(self):
        """
        Returns a dictionary of video stream session metadata
        """
        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            return None

        return self.session_metadata
