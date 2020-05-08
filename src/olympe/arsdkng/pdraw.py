# -*- coding: UTF-8 -*-

#  Copyright (C) 2019 Parrot Drones SAS
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


from __future__ import unicode_literals
from __future__ import absolute_import
from future.builtins import str

import ctypes
import olympe_deps as od
import errno
import json
import numpy as np
import os
import re
import threading
import traceback
from aenum import Enum, auto
from collections import defaultdict, namedtuple
from concurrent.futures import TimeoutError as FutureTimeoutError
from logging import getLogger

from olympe._private import py_object_cast, callback_decorator
from olympe._private.pomp_loop_thread import Future, PompLoopThread


_copyright__ = "Copyright 2018, Parrot"


class PdrawState(Enum):
    Created = auto()
    Closing = auto()
    Closed = auto()
    Opening = auto()
    Opened = auto()
    Playing = auto()
    Paused = auto()
    Error = auto()


VMetaFrameType = Enum(
    'VMetaFrameType',
    {
        re.compile('^VMETA_FRAME_TYPE_').sub('', v): k
        for k, v in od.vmeta_frame_type__enumvalues.items()
    }
)


PDRAW_LOCAL_ADDR = b"0.0.0.0"
PDRAW_LOCAL_STREAM_PORT = 55004
PDRAW_LOCAL_CONTROL_PORT = 55005
PDRAW_REMOTE_STREAM_PORT = 0
PDRAW_REMOTE_CONTROL_PORT = 0
PDRAW_IFACE_ADRR = b""


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
        'type': od.PDRAW_VIDEO_MEDIA_FORMAT_UNKNOWN,
        'h264_header': None,
        'video_sink': od.POINTER_T(od.struct_pdraw_video_sink)(),
        'video_sink_lock': threading.RLock(),
        'video_queue': None,
        'video_queue_event': None,
    }


class VideoFrame:

    def __init__(self, logger, buf, media_id, stream, yuv_packed_buffer_pool,
                 session_metadata):
        self.logger = logger
        self._buf = buf
        self._media_id = media_id
        self._stream = stream
        self._yuv_packed_buffer_pool = yuv_packed_buffer_pool
        self._session_metadata = session_metadata
        self._pdraw_video_frame = od.POINTER_T(ctypes.c_ubyte)()
        self._frame_pointer = ctypes.POINTER(ctypes.c_ubyte)()
        self._frame_size = 0
        self._frame_array = None
        self._yuv_packed_buffer = od.POINTER_T(od.struct_vbuf_buffer)()
        self._yuv_packed_video_frame_storage = od.struct_pdraw_video_frame()
        self._yuv_packed_video_frame = od.POINTER_T(
            od.struct_pdraw_video_frame)()
        self._frame_info = None
        self._metadata_pointers = []

    def __bool__(self):
        return bool(self._buf)

    __nonzero__ = __bool__

    def ref(self):
        """
        This function increments the reference counter of the underlying buffer(s)
        """
        try:
            # try to allocate the yuv packed buffer before referencing it
            self._get_pdraw_video_frame()
        finally:
            if self._yuv_packed_buffer:
                od.vbuf_ref(self._yuv_packed_buffer)
            od.vbuf_ref(self._buf)

    def unref(self):
        """
        This function decrements the reference counter of the underlying buffer(s)
        """
        try:
            res = od.vbuf_unref(self._buf)
            if res != 0:
                self.logger.error("vbuf_unref unpacked frame error: {} {} {}".format(
                    self._media_id,
                    os.strerror(-res),
                    ctypes.addressof(self._buf.contents)
                ))
        finally:
            if self._yuv_packed_buffer:
                res = od.vbuf_unref(self._yuv_packed_buffer)
                if res != 0:
                    self.logger.error("vbuf_unref packed frame error: {} {} {}".format(
                        self._media_id,
                        os.strerror(-res),
                        ctypes.addressof(self._buf.contents)
                    ))

    def media_id(self):
        return self._media_id

    def _get_pdraw_video_frame(self):
        if not self._pdraw_video_frame:
            res = od.vbuf_metadata_get(
                self._buf,
                self._stream['video_sink'],
                od.POINTER_T(ctypes.c_uint32)(),
                od.POINTER_T(ctypes.c_uint64)(),
                ctypes.byref(self._pdraw_video_frame))

            if res < 0:
                self.logger.error(
                    'vbuf_metadata_get returned error {}'.format(res))
                self._pdraw_video_frame = od.POINTER_T(ctypes.c_ubyte)()
                return self._pdraw_video_frame
            self._pdraw_video_frame = ctypes.cast(
                self._pdraw_video_frame, od.POINTER_T(od.struct_pdraw_video_frame))

        if self._stream['type'] == od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            return self._pdraw_video_frame

        if self._yuv_packed_video_frame:
            return self._yuv_packed_video_frame

        if not self._yuv_packed_buffer:
            res = od.vbuf_pool_get(
                self._yuv_packed_buffer_pool,
                0,
                ctypes.byref(self._yuv_packed_buffer)
            )
            if res < 0:
                self.logger.error(
                    'vbuf_pool_get returned error {}'.format(res))
                return self._yuv_packed_video_frame
        self._yuv_packed_video_frame = ctypes.pointer(
            self._yuv_packed_video_frame_storage)
        res = od.pdraw_pack_yuv_frame(
            self._pdraw_video_frame,
            self._yuv_packed_video_frame,
            self._yuv_packed_buffer)
        if res < 0:
            self._yuv_packed_video_frame = od.POINTER_T(
                od.struct_pdraw_video_frame)()
            self.logger.error(
                'pdraw_pack_yuv_frame returned error {}'.format(res))
        return self._yuv_packed_video_frame

    def as_ctypes_pointer(self):
        """
        This function return a 2-tuple (frame_pointer, frame_size) where
        frame_pointer is a ctypes pointer and frame_size the frame size in bytes.

        See: https://docs.python.org/3/library/ctypes.html
        """
        if self._frame_pointer:
            return self._frame_pointer, self._frame_size

        # H264 stream
        if self._stream['type'] == od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            # get the size in bytes of the raw data
            self._frame_size = od.vbuf_get_size(self._buf)
            self.logger.debug("Frame of {} bytes received".format(self._frame_size))

            # retrieve the raw data from the buffer
            od.vbuf_get_cdata.restype = ctypes.POINTER(ctypes.c_ubyte)
            self._frame_pointer = od.vbuf_get_cdata(self._buf)
            if not self._frame_pointer:
                self.logger.warning('vbuf_get_cdata returned null pointer')
                return self._frame_pointer, 0
            return self._frame_pointer, self._frame_size

        # YUV I420 or NV12 stream
        elif self._stream['type'] == od.PDRAW_VIDEO_MEDIA_FORMAT_YUV:
            frame = self._get_pdraw_video_frame()
            if not frame:
                return self._frame_pointer, self._frame_size
            frame = frame.contents
            self._frame_pointer = ctypes.cast(
                frame.pdraw_video_frame_0.yuv.plane[0],
                ctypes.POINTER(ctypes.c_ubyte)
            )
            # assume I420 or NV12 3/2 ratio
            height = frame.pdraw_video_frame_0.yuv.height
            width = frame.pdraw_video_frame_0.yuv.width
            self._frame_size = int(3 * height * width / 2)
        return self._frame_pointer, self._frame_size

    def as_ndarray(self):
        """
        This function returns an non-owning numpy 1D (h264) or 2D (YUV) array on this video frame
        """
        if self._frame_array is not None:
            return self._frame_array
        frame_pointer, frame_size = self.as_ctypes_pointer()
        if not frame_pointer:
            return self._frame_array
        if self._stream['type'] == od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            shape = (frame_size,)
        elif self._stream['type'] == od.PDRAW_VIDEO_MEDIA_FORMAT_YUV:
            frame = self._get_pdraw_video_frame()
            if not frame:
                return self._frame_array
            frame = frame.contents
            height = frame.pdraw_video_frame_0.yuv.height
            width = frame.pdraw_video_frame_0.yuv.width
            # assume I420 or NV12 3/2 ratio
            shape = (int(3 * height / 2), width)
        self._frame_array = np.ctypeslib.as_array(
            frame_pointer, shape=shape)
        return self._frame_array

    def info(self):
        """
        Returns a dictionary of video frame info
        """
        if self._frame_info is not None:
            return self._frame_info
        frame = self._get_pdraw_video_frame()
        if not frame:
            return self._frame_info
        # convert the binary metadata into json
        self._frame_info = {}
        jsonbuf = ctypes.create_string_buffer(4096)
        res = od.pdraw_video_frame_to_json_str(
            frame, jsonbuf, ctypes.sizeof(jsonbuf))
        if res < 0:
            self.logger.error(
                'pdraw_frame_metadata_to_json returned error {}'.format(res))
        else:
            self._frame_info = json.loads(str(jsonbuf.value, encoding="utf-8"))
        return self._frame_info

    def vmeta(self):
        """
        Returns a 2-tuple (VMetaFrameType, dictionary of video frame metadata)
        """
        vmeta = {}
        vmeta_type = VMetaFrameType.NONE
        frame = self._get_pdraw_video_frame()
        if not frame:
            return vmeta_type, vmeta
        vmeta = self.info().get('metadata')
        vmeta_type = VMetaFrameType(frame.contents.metadata.type)
        return vmeta_type, vmeta

    def vbuf_userdata_ctypes_pointers(self):
        """
        Unstable/experimental API
        This returns some additional and optional SEI metadata
        """
        userata_pointer = od.vbuf_get_cuserdata(self._buf)
        userdata_size = od.vbuf_get_userdata_size(self._buf)
        userata_pointer = ctypes.cast(
            userata_pointer, ctypes.POINTER(ctypes.c_ubyte * userdata_size))
        return userata_pointer, userdata_size

    def session_metadata(self):
        """
        Returns video stream session metadata
        """
        return self._session_metadata


class Pdraw(object):

    def __init__(self,
                 name=None,
                 device_name=None,
                 buffer_queue_size=8,
                 legacy=False,
                 pdraw_thread_loop=None,
                 ):
        """
        :param name: (optional) pdraw client name (used by Olympe logs)
        :type name: str
        :param device_name: (optional) the drone device name (used by Olympe logs)
        :type device_name: str
        :param buffer_queue_size: (optional) video buffer queue size (defaults to 8)
        :type buffer_queue_size: int
        :param legacy: Defaults to False, set this parameter to True for legacy
            drones (Bebop, Disco, ...) streaming support
        :type legacy: bool
        """

        self.name = name
        self.device_name = device_name
        if self.name is not None:
            self.logger = getLogger("olympe.{}.pdraw".format(self.name))
        elif self.device_name is not None:
            self.logger = getLogger("olympe.pdraw.{}".format(self.device_name))
        else:
            self.logger = getLogger("olympe.pdraw")

        if pdraw_thread_loop is None:
            self.own_pdraw_thread_loop = True
            self.pdraw_thread_loop = PompLoopThread(self.logger)
            self.pdraw_thread_loop.start()
        else:
            self.own_pdraw_thread_loop = False
            self.pdraw_thread_loop = pdraw_thread_loop

        self.callbacks_thread_loop = PompLoopThread(self.logger, parent=self.pdraw_thread_loop)
        self.callbacks_thread_loop.start()
        self.buffer_queue_size = buffer_queue_size
        self.pomp_loop = self.pdraw_thread_loop.pomp_loop
        self._legacy = legacy

        self._open_resp_future = Future(self.pdraw_thread_loop)
        self._close_resp_future = Future(self.pdraw_thread_loop)
        self._close_resp_future.add_done_callback(self._on_close_resp_done)
        self._play_resp_future = Future(self.pdraw_thread_loop)
        self._pause_resp_future = Future(self.pdraw_thread_loop)
        self._state = PdrawState.Created
        self._state_lock = threading.Lock()
        self._state_wait_events = {k: list() for k in PdrawState}

        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        self.streams = defaultdict(StreamFactory)
        self.session_metadata = {}

        self.outfiles = {
            od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            {
                'data': None,
                'meta': None,
                'info': None,
            },
            od.PDRAW_VIDEO_MEDIA_FORMAT_YUV:
            {
                'data': None,
                'meta': None,
                'info': None,
            },
        }

        self.frame_callbacks = {
            od.PDRAW_VIDEO_MEDIA_FORMAT_H264: None,
            od.PDRAW_VIDEO_MEDIA_FORMAT_YUV: None,
        }
        self.start_callback = None
        self.end_callback = None
        self.flush_callbacks = {
            od.PDRAW_VIDEO_MEDIA_FORMAT_H264: None,
            od.PDRAW_VIDEO_MEDIA_FORMAT_YUV: None,
        }

        self.url = None
        self.server_addr = None
        self.resource_name = "live"
        self.media_name = None

        self.local_stream_port = PDRAW_LOCAL_STREAM_PORT
        self.local_control_port = PDRAW_LOCAL_CONTROL_PORT

        self.cbs = od.struct_pdraw_cbs.bind({
            "open_resp": self._open_resp,
            "close_resp": self._close_resp,
            "ready_to_play": self._ready_to_play,
            "play_resp": self._play_resp,
            "pause_resp": self._pause_resp,
            "seek_resp": self._seek_resp,
            "socket_created": self._socket_created,
            "select_demuxer_media": self._select_demuxer_media,
            "media_added": self._media_added,
            "media_removed": self._media_removed,
            "end_of_range": self._end_of_range,
        })

        self.video_sink_cb = od.struct_pdraw_video_sink_cbs.bind({
            "flush": self._video_sink_flush
        })

        self.vbuf_cbs = od.struct_vbuf_cbs()
        res = od.vbuf_generic_get_cbs(ctypes.pointer(self.vbuf_cbs))
        if res != 0:
            msg = "Error while creating vbuf generic callbacks {}".format(res)
            self.logger.error(msg)
            raise RuntimeError("ERROR: {}".format(msg))

        self.yuv_packed_buffer_pool = od.POINTER_T(od.struct_vbuf_pool)()
        res = od.vbuf_pool_new(
            self.buffer_queue_size,
            0,
            0,
            self.vbuf_cbs,
            ctypes.byref(self.yuv_packed_buffer_pool)
        )
        if res != 0:
            msg = "Error while creating yuv packged buffer pool {}".format(res)
            self.logger.error(msg)
            raise RuntimeError("ERROR: {}".format(msg))

        self.pdraw_thread_loop.register_cleanup(self.dispose)

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
        self.pdraw_thread_loop.run_later(self._dispose)

    @callback_decorator()
    def _dispose(self):
        self.pdraw_thread_loop.unregister_cleanup(self.dispose, ignore_error=True)
        return self.pdraw_thread_loop.run_async(
            self._dispose_impl)

    @callback_decorator()
    def _dispose_impl(self):
        # cleanup some FDs from the callbacks thread loop that might have been lost
        for stream in self.streams.values():
            if stream['video_queue_event'] is not None:
                self.logger.warning("cleanup leftover pdraw callbacks eventfds")
                self.callbacks_thread_loop.remove_event_from_loop(
                    stream['video_queue_event'])
                stream['video_queue_event'] = None
        f = self.close().then(
            lambda _: self._stop(), deferred=True)
        return f

    @callback_decorator()
    def _stop(self):
        if self.callbacks_thread_loop.stop():
            self.logger.info("pdraw callbacks thread loop stopped")
        # cleanup some FDs from the callbacks thread loop that might have been lost
        for stream in self.streams.values():
            if stream['video_queue_event'] is not None:
                self.logger.warning("cleanup leftover pdraw callbacks eventfds")
                self.callbacks_thread_loop.remove_event_from_loop(
                    stream['video_queue_event'])
                stream['video_queue_event'] = None
        return True

    def _destroy_pdraw(self):
        ret = True
        if self.pdraw:
            self.logger.info("destroying pdraw...")
            res = od.pdraw_destroy(self.pdraw)
            if res != 0:
                self.logger.error("cannot destroy pdraw {}".format(res))
                ret = False
            else:
                self.logger.info("pdraw destroyed")
        if self.yuv_packed_buffer_pool:
            self.logger.info("destroying yuv buffer pool...")
            res = od.vbuf_pool_destroy(self.yuv_packed_buffer_pool)
            if res != 0:
                ret = False
                self.logger.error(
                    "cannot destroy yuv packed buffer pool: {}".format(res))
            else:
                self.logger.info("yuv buffer pool destroyed")
        self.yuv_packed_buffer_pool = od.POINTER_T(od.struct_vbuf_pool)()
        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        return ret

    @callback_decorator()
    def _destroy(self):
        ret = self._destroy_pdraw()
        if self.pdraw_thread_loop.stop():
            self.logger.info("pdraw thread loop stopped")
        if self.own_pdraw_thread_loop:
            self.pdraw_thread_loop.destroy()
        self.callbacks_thread_loop.destroy()
        return ret

    def _open_single_stream(self):
        """
        Opening pdraw single stream (legacy API)
        """
        res = od.pdraw_open_single_stream(
            self.pdraw,
            PDRAW_LOCAL_ADDR,
            self.local_stream_port,
            self.local_control_port,
            self.server_addr,
            PDRAW_REMOTE_STREAM_PORT,
            PDRAW_REMOTE_CONTROL_PORT,
            PDRAW_IFACE_ADRR
        )

        if res != 0:
            self.logger.error(
                "Error while opening pdraw single stream: {}".format(res))
            return False
        else:
            self.logger.info("Opening pdraw single stream OK")
        return True

    def _open_url(self):
        """
        Opening rtsp streaming url
        """
        if self.resource_name.startswith("replay/"):
            if self.media_name is None:
                self.logger.error(
                    "Error media_name should be provided in video stream replay mode")
                return False
        res = od.pdraw_open_url(self.pdraw, self.url)

        if res != 0:
            self.logger.error(
                "Error while opening pdraw url: {} ({})".format(self.url, res))
            return False
        else:
            self.logger.info("Opening pdraw url OK: {}".format(self.url))
        return True

    @callback_decorator()
    def _open_stream(self):
        """
        Opening pdraw stream using the appropriate method (legacy or rtsp)
        according to the device type
        """
        self._open_resp_future = Future(self.pdraw_thread_loop)
        if self.state not in (PdrawState.Error, PdrawState.Closed, PdrawState.Created):
            self.logger.warning("Cannot open stream from {}".format(self.state))
            self._open_resp_future.set_result(False)
            return self._open_resp_future

        self.state = PdrawState.Opening
        if not self._pdraw_new():
            self._open_resp_future.set_result(False)
            return self._open_resp_future

        if not self._legacy:
            ret = self._open_url()
        else:
            ret = self._open_single_stream()

        if not ret:
            self._open_resp_future.set_result(False)

        return self._open_resp_future

    def close(self):
        """
        Close a playing or paused video stream session
        """
        if self.state in (PdrawState.Opened, PdrawState.Paused, PdrawState.Playing, PdrawState.Error):
            self.logger.debug("pdraw closing from the {} state".format(self.state))
            if self._close_resp_future.done():
                self._close_resp_future = Future(self.pdraw_thread_loop)
                self._close_resp_future.add_done_callback(self._on_close_resp_done)
            f = self._close_resp_future
            self.state = PdrawState.Closing
            self.pdraw_thread_loop.run_async(self._close_stream)
        elif self.state is not PdrawState.Closing:
            f = Future(self.pdraw_thread_loop)
            f.set_result(False)
        else:
            f = self._close_resp_future
        return f

    @callback_decorator()
    def _close_stream(self):
        """
        Close pdraw stream
        """
        if self.state is PdrawState.Closed:
            self.logger.info("pdraw is already closed".format(self.state))
            self._close_resp_future.set_result(True)
            return self._close_resp_future

        if not self.pdraw:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            self.state = PdrawState.Error
            self._close_resp_future.set_result(False)
            return self._close_resp_future

        if not self._close_stream_impl():
            self.state = PdrawState.Error
            self._close_resp_future.set_result(False)

        return self._close_resp_future

    def _close_stream_impl(self):
        res = od.pdraw_close(self.pdraw)

        if res != 0:
            self.logger.error(
                "Error while closing pdraw demuxer: {}".format(res))
            self.state = PdrawState.Error
            return False
        else:
            self.logger.info("Closing pdraw demuxer OK")

        return True

    @callback_decorator()
    def _on_close_resp_done(self, close_resp_future):
        if not close_resp_future.cancelled():
            return

        close_resp_future.cancel()
        self.logger.error("Closing pdraw demuxer timedout")

        if self.state not in (PdrawState.Closed, PdrawState.Stopped, PdrawState.Error):
            return

        # FIXME: workaround pdraw closing timeout
        # This random issue is quiet hard to reproduce
        self.logger.error("destroying pdraw...")
        if self.pdraw:
            self.pdraw_thread_loop.run_later(self._destroy)
        self.state = PdrawState.Closed

    def _open_resp(self, pdraw, status, userdata):
        self.logger.debug("_open_resp called")
        self.local_stream_port = od.pdraw_get_single_stream_local_stream_port(self.pdraw)

        self.local_control_port = od.pdraw_get_single_stream_local_control_port(self.pdraw)

        if status != 0:
            self.state = PdrawState.Error
        else:
            self.state = PdrawState.Opened

        self._open_resp_future.set_result(status == 0)

    def _close_resp(self, pdraw, status, userdata):
        self._close_output_files()
        if status != 0:
            self.logger.error("_close_resp called {}".format(status))
            self._close_resp_future.set_result(False)
            self.state = PdrawState.Error
        else:
            self.logger.info("_close_resp called {}".format(status))
            self.state = PdrawState.Closed
            self._close_resp_future.set_result(True)

        if self.pdraw:
            res = od.pdraw_destroy(self.pdraw)
            if res != 0:
                self.logger.error("Cannot destroy pdraw object")
        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        self._close_resp_future.set_result(True)

    def _pdraw_new(self):
        res = od.pdraw_new(
            self.pomp_loop,
            self.cbs,
            ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
            ctypes.byref(self.pdraw)
        )
        if res != 0:
            msg = "Error while creating pdraw interface: {}".format(res)
            self.logger.error(msg)
            self.pdraw = od.POINTER_T(od.struct_pdraw)()
            return False
        else:
            self.logger.info("Pdraw interface has been created")
            return True

    def _ready_to_play(self, pdraw, ready, userdata):
        self.logger.info("_ready_to_play({}) called".format(ready))
        self._is_ready_to_play = bool(ready)
        if self._is_ready_to_play:
            if self._play_resp_future.done():
                self._play_resp_future = Future(self.pdraw_thread_loop)
            self._play_impl()
            if self.start_callback is not None:
                self.callbacks_thread_loop.run_async(self.start_callback)
        else:
            if self.end_callback is not None:
                self.callbacks_thread_loop.run_async(self.end_callback)

    def _play_resp(self, pdraw, status, timestamp, speed, userdata):
        if status == 0:
            self.logger.debug("_play_resp called {}".format(status))
            self.state = PdrawState.Playing
            self._play_resp_future.set_result(True)
        else:
            self.logger.error("_play_resp called {}".format(status))
            self.state = PdrawState.Error
            self._play_resp_future.set_result(False)

    def _pause_resp(self, pdraw, status, timestamp, userdata):
        if status == 0:
            self.logger.debug("_pause_resp called {}".format(status))
            self.state = PdrawState.Paused
            self._pause_resp_future.set_result(True)
        else:
            self.logger.error("_pause_resp called {}".format(status))
            self.state = PdrawState.Error
            self._pause_resp_future.set_result(False)

    def _seek_resp(self, pdraw, status, timestamp, userdata):
        if status == 0:
            self.logger.debug("_seek_resp called {}".format(status))
        else:
            self.logger.error("_seek_resp called {}".format(status))
            self.state = PdrawState.Error

    def _socket_created(self, pdraw, fd, userdata):
        self.logger.debug("_socket_created called")

    def _select_demuxer_media(self, pdraw, media, count, userdata):
        # by default select the default media (media_id=0)
        selected_media_id = 0
        selected_media_idx = 0
        for idx in range(count):
            self.logger.info(
                "_select_demuxer_media: "
                "idx={} media_id={} name={} default={}".format(
                    idx, media[idx].media_id,
                    od.string_cast(media[idx].name),
                    str(bool(media[idx].is_default)))
            )
            if (self.media_name is not None and
                    self.media_name == od.string_cast(media[idx].name)):
                selected_media_id = media[idx].media_id
                selected_media_idx = idx
        if (
            self.media_name is not None and
            od.string_cast(media[selected_media_idx].name) != self.media_name
        ):
            self.logger.warning(
                "media_name {} is unavailable. "
                "Selecting the default media instead".format(self.media_name)
            )
        return selected_media_id

    def _media_added(self, pdraw, media_info, userdata):
        id_ = int(media_info.contents.id)
        self.logger.info("_media_added id : {}".format(id_))

        # store the information if supported media type, otherwise exit
        if (media_info.contents.pdraw_media_info_0.video.format !=
                od.PDRAW_VIDEO_MEDIA_FORMAT_YUV and
                media_info.contents.pdraw_media_info_0.video.format !=
                od.PDRAW_VIDEO_MEDIA_FORMAT_H264):
            self.logger.warning(
                'Ignoring media id {} (type {})'.format(
                    id_, media_info.contents.pdraw_media_info_0.video.format))
            return
        self.streams[id_]['type'] = int(media_info.contents.pdraw_media_info_0.video.format)
        if (media_info.contents.pdraw_media_info_0.video.format ==
                od.PDRAW_VIDEO_MEDIA_FORMAT_H264):
                header = media_info.contents.pdraw_media_info_0.video.pdraw_video_info_0.h264
                header = H264Header(
                    bytearray(header.sps),
                    int(header.spslen),
                    bytearray(header.pps),
                    int(header.ppslen),
                )
                self.streams[id_]['h264_header'] = header

        # start a video sink attached to the new media
        video_sink_params = od.struct_pdraw_video_sink_params(
            self.buffer_queue_size,  # buffer queue size
            1,  # drop buffers when the queue is full
        )
        self.streams[id_]['id_userdata'] = ctypes.cast(
            ctypes.pointer(ctypes.py_object(id_)), ctypes.c_void_p)
        self.streams[id_]['id'] = id_

        res = od.pdraw_start_video_sink(
            pdraw,
            id_,
            video_sink_params,
            self.video_sink_cb,
            self.streams[id_]['id_userdata'],
            ctypes.byref(self.streams[id_]['video_sink'])
        )
        if res != 0 or not self.streams[id_]['video_sink']:
            self.logger.error("Unable to start video sink")
            return

        # Retrieve the queue belonging to the sink
        queue = od.pdraw_get_video_sink_queue(
            pdraw,
            self.streams[id_]['video_sink'],
        )
        self.streams[id_]['video_queue'] = queue

        # Retrieve event object and related file descriptor
        self.streams[id_]['video_queue_event'] = \
            od.vbuf_queue_get_evt(self.streams[id_]['video_queue'])

        # add the file description to our pomp loop
        self.callbacks_thread_loop.add_event_to_loop(
            self.streams[id_]['video_queue_event'],
            lambda *args: self._video_sink_queue_event(*args),
            id_
        )

    def _media_removed(self, pdraw, media_info, userdata):
        id_ = media_info.contents.id
        if id_ not in self.streams:
            self.logger.error(
                'Received removed event from unknown ID {}'.format(id_))
            return

        self.logger.info("_media_removed called id : {}".format(id_))

        # FIXME: Workaround media_removed called with destroyed media
        if not self.pdraw:
            self.logger.error(
                "_media_removed called with a destroyed pdraw id : {}".format(
                    id_)
            )
            return
        with self.streams[id_]['video_sink_lock']:
            if self.streams[id_]['video_queue_event']:
                self.callbacks_thread_loop.remove_event_from_loop(
                    self.streams[id_]['video_queue_event'])
                self.streams[id_]['video_queue_event'] = None

            if not self.streams[id_]['video_sink']:
                self.logger.error(
                    'pdraw_video_sink for media_id {} has already been stopped'.format(id_))
                return
            res = od.pdraw_stop_video_sink(pdraw, self.streams[id_]['video_sink'])
            if res < 0:
                self.logger.error('pdraw_stop_video_sink() returned %s' % res)
            self.streams[id_]['video_queue'] = None
            self.streams[id_]['video_sink'] = od.POINTER_T(od.struct_pdraw_video_sink)()

    def _end_of_range(self, pdraw, timestamp, userdata):
        self.logger.info("_end_of_range")
        self.close()

    def _video_sink_flush(self, pdraw, videosink, userdata):
        id_ = py_object_cast(userdata)
        if id_ not in self.streams:
            self.logger.error(
                'Received flush event from unknown ID {}'.format(id_))
            return -errno.ENOENT

        # FIXME: Workaround video_sink_flush called with destroyed media
        if not self.pdraw:
            self.logger.error(
                "_video_sink_flush called with a destroyed pdraw id : {}".format(
                    id_)
            )
            return -errno.EINVAL

        # FIXME: Workaround video_sink_flush called with destroyed video queue
        if not self.streams[id_]['video_queue']:
            self.logger.error(
                "_video_sink_flush called with a destroyed queue id : {}".format(
                    id_)
            )
            return -errno.EINVAL

        with self.streams[id_]['video_sink_lock']:
            self.logger.debug("flush_callback {}".format(id_))

            flush_callback = self.flush_callbacks[self.streams[id_]['type']]
            if flush_callback is not None:
                flushed = self.callbacks_thread_loop.run_async(flush_callback)
                try:
                    if not flushed.result_or_cancel(timeout=5.):
                        self.logger.error(
                            'video sink flush id {} error'.format(id_))
                except FutureTimeoutError:
                    self.logger.error(
                        'video sink flush id {} timeout'.format(id_))
                # NOTE: If the user failed to flush its buffer at this point,
                # bad things WILL happen we're acknowledging the buffer flush
                # in all cases...
            res = od.vbuf_queue_flush(self.streams[id_]['video_queue'])
            if res < 0:
                self.logger.error('vbuf_queue_flush() returned %s' % res)
            else:
                self.logger.info('vbuf_queue_flush() returned %s' % res)

            res = od.pdraw_video_sink_queue_flushed(pdraw, videosink)
            if res < 0:
                self.logger.error(
                    'pdraw_video_sink_queue_flushed() returned %s' % res)
            else:
                self.logger.debug(
                    'pdraw_video_sink_queue_flushed() returned %s' % res)
            return 0

    @callback_decorator()
    def _video_sink_queue_event(self, pomp_evt, userdata):
        id_ = py_object_cast(userdata)
        self.logger.debug('media id = {}'.format(id_))

        if id_ not in self.streams:
            self.logger.error(
                'Received queue event from unknown ID {}'.format(id_))
            return

        # acknowledge event
        res = od.pomp_evt_clear(self.streams[id_]['video_queue_event'])
        if res != 0:
            self.logger.error(
                "Unable to clear frame received event ({})".format(res))

        if not self._is_ready_to_play:
            self.logger.debug("The stream is no longer ready: drop one frame")
            return

        # process all available buffers in the queue
        with self.streams[id_]['video_sink_lock']:
            while self._process_stream(id_):
                pass

    def _pop_stream_buffer(self, id_):
        buf = od.POINTER_T(od.struct_vbuf_buffer)()
        ret = od.vbuf_queue_pop(
            self.streams[id_]['video_queue'], 0, ctypes.byref(buf)
        )
        if ret < 0:
            if ret != -errno.EAGAIN:
                self.logger.error('vbuf_queue_pop returned error %d' % ret)
            buf = od.POINTER_T(od.struct_vbuf_buffer)()
        elif not buf:
            self.logger.error('vbuf_queue_pop returned NULL')
        return buf

    def _process_stream(self, id_):
        self.logger.debug('media id = {}'.format(id_))
        if od.vbuf_queue_get_count(self.streams[id_]['video_queue']) == 0:
            return False
        buf = self._pop_stream_buffer(id_)
        if not buf:
            return False
        video_frame = VideoFrame(
            self.logger,
            buf,
            id_,
            self.streams[id_],
            self.yuv_packed_buffer_pool,
            self.get_session_metadata()
        )
        try:
            self._process_stream_buffer(id_, video_frame)
            return True
        except Exception:
            self.logger.error('_process_stream_buffer exception:\n{}'.format(
                traceback.format_exc()))
            return False
        finally:
            # Once we're done with this frame, dispose the associated frame buffer
            video_frame.unref()

    def _process_stream_buffer(self, id_, video_frame):
        stream = self.streams[id_]
        mediatype = stream['type']

        # write and/or send data over the requested channels
        # handle output files
        files = self.outfiles[mediatype]

        f = files['meta']
        if f and not f.closed:
            vmeta_type, vmeta = video_frame.vmeta()
            files['meta'].write(json.dumps((str(vmeta_type), vmeta)) + '\n')

        f = files['info']
        if f and not f.closed:
            info = video_frame.info()
            files['info'].write(json.dumps(info) + '\n')

        f = files['data']
        if f and not f.closed:
            if mediatype == od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
                if f.tell() == 0:
                    # h264 files need a header to be readable
                    stream['h264_header'].tofile(f)
            frame_array = video_frame.as_ndarray()
            if frame_array is not None:
                f.write(ctypes.string_at(
                    frame_array.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
                    frame_array.size,
                ))

        # call callbacks when existing
        cb = self.frame_callbacks[mediatype]
        if cb is not None:
            cb(video_frame)

    def set_output_files(self,
                         h264_data_file,
                         h264_meta_file,
                         h264_info_file,
                         raw_data_file,
                         raw_meta_file,
                         raw_info_file):
        """
        Records the video stream session to the disk

        - xxx_meta_file: video stream metadata output files
        - xxx_data_file: video stream frames output files
        - xxx_info_file: video stream frames info files
        - h264_***_file: files associated to the H264 encoded video stream
        - raw_***_file: files associated to the decoded video stream

        This function MUST NOT be called when a video streaming session is
        active.
        Setting a file parameter to `None` disables the recording for the
        related stream part.
        """
        if self.state is PdrawState.Playing:
            raise RuntimeError(
                'Cannot set video streaming files while streaming is on.')

        for mediatype, datatype, filepath, attrib in (
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'data', h264_data_file, 'wb'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'meta', h264_meta_file, 'w'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'info', h264_info_file, 'w'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'data', raw_data_file,  'wb'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'meta', raw_meta_file,  'w'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'info', raw_info_file,  'w')):
            if self.outfiles[mediatype][datatype]:
                self.outfiles[mediatype][datatype].close()
                self.outfiles[mediatype][datatype] = None

            if filepath is None:
                continue

            # open and close file to store its filename and attribute
            self.outfiles[mediatype][datatype] = open(filepath, attrib)
            self.outfiles[mediatype][datatype].close()

    def set_callbacks(self,
                      h264_cb=None,
                      raw_cb=None,
                      start_cb=None,
                      end_cb=None,
                      flush_h264_cb=None,
                      flush_raw_cb=None):
        """
        Set the callback functions that will be called when a new video stream frame is available,
        when the video stream starts/ends or when the video buffer needs to get flushed.

        **Video frame callbacks**

        - `h264_cb` is associated to the H264 encoded video stream
        - `raw_cb` is associated to the decoded video stream

        Each video frame callback function takes an :py:func:`~olympe.VideoFrame` parameter whose
        lifetime ends after the callback execution. If this video frame is passed to another thread,
        its internal reference count need to be incremented first by calling
        :py:func:`~olympe.VideoFrame.ref`. In this case, once the frame is no longer needed, its
        reference count needs to be decremented so that this video frame can be returned to
        memory pool.

        **Video flush callbacks**

        - `flush_h264_cb` is associated to the H264 encoded video stream
        - `flush_raw_cb` is associated to the decoded video stream

        Video flush callback functions are called when a video stream reclaim all its associated
        video buffer. Every frame that has been referenced

        **Start/End callbacks**

        The `start_cb`/`end_cb` callback functions are called when the video stream start/ends.
        They don't accept any parameter.

        The return value of all these callback functions are ignored.
        If a callback is not desired, leave the parameter to its default value or set it to `None`
        explicitly.
        """

        for mediatype, cb in ((od.PDRAW_VIDEO_MEDIA_FORMAT_H264, h264_cb),
                              (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV, raw_cb)):
            self.frame_callbacks[mediatype] = callback_decorator(
                logger=self.logger)(cb)
        for mediatype, cb in ((od.PDRAW_VIDEO_MEDIA_FORMAT_H264, flush_h264_cb),
                              (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV, flush_raw_cb)):
            self.flush_callbacks[mediatype] = callback_decorator(
                logger=self.logger)(cb)
        self.start_callback = callback_decorator(logger=self.logger)(start_cb)
        self.end_callback = callback_decorator(logger=self.logger)(end_cb)

    def _open_output_files(self):
        self.logger.debug('opening video output files')
        for mediatype, data in self.outfiles.items():
            for datatype, f in data.items():
                if f and f.closed:
                    self.outfiles[mediatype][datatype] = open(f.name, f.mode)

    def _close_output_files(self):
        self.logger.debug('closing video output files')
        for files in self.outfiles.values():
            for f in files.values():
                if f:
                    f.close()

    def play(self, url=None, media_name="DefaultVideo", server_addr=None, resource_name="live"):
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
        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            self._play_resp_future.set_result(False)
            return self._pause_resp_future

        if self.state in (PdrawState.Opening, PdrawState.Closing):
            self.logger.warning("Cannot play stream from the {} state".format(
                self.state))
            f = Future(self.pdraw_thread_loop)
            f.set_result(False)
            return f

        self.resource_name = resource_name
        self.media_name = media_name

        if server_addr is None:
            self.server_addr = "192.168.42.1"
        else:
            self.server_addr = server_addr

        if url is None:
            self.url = b"rtsp://%s/%s" % (
                self.server_addr, self.resource_name.encode())
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
            if self.is_legacy():
                self.logger.warning("Cannot open streaming url for legacy drones")

        # reset session metadata from any previous session
        self.session_metadata = {}
        self.streams = defaultdict(StreamFactory)

        self._open_output_files()
        if self.state in (PdrawState.Created, PdrawState.Closed):
            f = self.pdraw_thread_loop.run_async(self._open_stream)
        else:
            if self._play_resp_future.done():
                self._play_resp_future = Future(self.pdraw_thread_loop)
            f = self._play_resp_future
            self.pdraw_thread_loop.run_async(self._play_impl)
        return f

    @callback_decorator()
    def _play_impl(self):
        self.logger.debug("play_impl")
        if self.state is PdrawState.Playing:
            self._play_resp_future.set_result(True)
            return self._play_resp_future

        res = od.pdraw_play(self.pdraw)
        if res != 0:
            msg = "Unable to start streaming ({})".format(res)
            self.logger.error(msg)
            self._play_resp_future.set_result(False)

        return self._play_resp_future

    def pause(self):
        """
        Pause the currently playing video
        """
        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            self._pause_resp_future.set_result(False)
            return self._pause_resp_future

        if self._pause_resp_future.done():
            self._pause_resp_future = Future(self.pdraw_thread_loop)
        if self.state is PdrawState.Playing:
            self.pdraw_thread_loop.run_async(self._pause_impl)
        elif self.state in (PdrawState.Closed, PdrawState.Opened):
            # Pause an opened/closed stream is OK
            self._pause_resp_future.set_result(True)
        else:
            self.logger.warning("Cannot pause stream from the {} state".format(
                self.state))
            self._pause_resp_future.set_result(False)
        return self._pause_resp_future

    @callback_decorator()
    def _pause_impl(self):
        res = od.pdraw_pause(self.pdraw)
        if res != 0:
            self.logger.error("Unable to stop streaming ({})".format(res))
            self._pause_resp_future.set_result(False)
        return self._pause_resp_future

    def get_session_metadata(self):
        """
        Returns a dictionary of video stream session metadata
        """
        if self.pdraw is None:
            self.logger.error("Error Pdraw interface seems to be destroyed")
            return None

        if self.session_metadata:
            return self.session_metadata

        vmeta_session = od.struct_vmeta_session()
        res = od.pdraw_get_peer_session_metadata(
            self.pdraw, ctypes.pointer(vmeta_session))
        if res != 0:
            msg = "Unable to get sessions metata"
            self.logger.error(msg)
            return None
        self.session_metadata = od.struct_vmeta_session.as_dict(
            vmeta_session)
        return self.session_metadata

    def is_legacy(self):
        return self._legacy
