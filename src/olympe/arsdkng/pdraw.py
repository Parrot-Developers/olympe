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
from aenum import Enum, auto
from collections import defaultdict

from olympe._private import py_object_cast
from olympe._private.pomp_loop_thread import Future

_copyright__ = "Copyright 2018, Parrot"


class State(Enum):
    Created = auto()
    Closing = auto()
    Closed = auto()
    Opening = auto()
    Opened = auto()
    Playing = auto()
    Paused = auto()
    Error = auto()


PDRAW_LOCAL_ADDR = b"0.0.0.0"
PDRAW_LOCAL_STREAM_PORT = 55004
PDRAW_LOCAL_CONTROL_PORT = 55005
PDRAW_REMOTE_STREAM_PORT = 0
PDRAW_REMOTE_CONTROL_PORT = 0
PDRAW_IFACE_ADRR = b""
PDRAW_URL = b"rtsp://%s/live"


class Pdraw(object):

    def __init__(self,
                 logging,
                 thread_loop,
                 streaming_server_addr,
                 legacy=False):

        self.logging = logging
        self.thread_loop = thread_loop
        self.pomp_loop = self.thread_loop.pomp_loop
        self.streaming_server_addr = streaming_server_addr
        self._legacy = legacy

        self._open_resp_future = Future(self.thread_loop)
        self._close_resp_future = Future(self.thread_loop)
        self._play_resp_future = Future(self.thread_loop)
        self._pause_resp_future = Future(self.thread_loop)
        self._state = State.Created

        self.pdraw = od.POINTER_T(od.struct_pdraw)()
        self.streams = defaultdict(lambda: {
            'id': None,
            'type': od.PDRAW_VIDEO_MEDIA_FORMAT_UNKNOWN,
            'info': None,
            'video_sink': od.POINTER_T(od.struct_pdraw_video_sink)(),
            'video_queue': None,
            'video_queue_fd': 0,
            'video_queue_event': None,
        })

        self.outfiles = {
            od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            {
                'data': None,
                'meta': None,
            },
            od.PDRAW_VIDEO_MEDIA_FORMAT_YUV:
            {
                'data': None,
                'meta': None,
            },
        }

        self.callbacks = {
            od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
            {
                'data': None,
                'meta': None,
            },
            od.PDRAW_VIDEO_MEDIA_FORMAT_YUV:
            {
                'data': None,
                'meta': None,
            },
        }

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
        })

        self.video_sink_cb = od.struct_pdraw_video_sink_cbs.bind({
            "flush": self._video_sink_flush
        })

        res = od.pdraw_new(
            self.pomp_loop,
            self.cbs,
            ctypes.cast(ctypes.pointer(ctypes.py_object(self)), ctypes.c_void_p),
            ctypes.byref(self.pdraw)
        )

        if res != 0:
            msg = "Error while creating pdraw interface: {}".format(res)
            self.logging.logE(msg)
            raise RuntimeError("ERROR: {}".format(msg))
        else:
            self.logging.logI("Pdraw interface has been created")

    def dispose(self):
        return self.thread_loop.run_async(
            self._dispose_impl)

    def _dispose_impl(self):
        if not self.pdraw:
            return

        f = self.thread_loop.run_async(self._close_stream).then(
            lambda _: self._destroy(), deferred=True)
        return f

    def _destroy(self):
        res = od.pdraw_destroy(self.pdraw)
        if res != 0:
            raise RuntimeError('Cannot destroy pdraw object')
        self.pdraw = ctypes.c_void_p(0)
        return True

    def _open_single_stream(self):
        """
        Opening pdraw single stream (legacy API)
        """
        res = od.pdraw_open_single_stream(
            self.pdraw,
            PDRAW_LOCAL_ADDR,
            self.local_stream_port,
            self.local_control_port,
            self.streaming_server_addr,
            PDRAW_REMOTE_STREAM_PORT,
            PDRAW_REMOTE_CONTROL_PORT,
            PDRAW_IFACE_ADRR
        )

        if res != 0:
            self.logging.logE(
                "Error while opening pdraw single stream: {}".format(res))
            return False
        else:
            self.logging.logI("Opening pdraw single stream OK")
        return True

    def _open_url(self):
        """
        Opening rtsp streaming url
        """
        url = PDRAW_URL % self.streaming_server_addr
        res = od.pdraw_open_url(
            self.pdraw,
            url
        )

        if res != 0:
            self.logging.logE(
                "Error while opening pdraw url: {} ({})".format(url, res))
            return False
        else:
            self.logging.logI("Opening pdraw url OK: {}".format(url))
        return True

    def _open_stream(self):
        """
        Opening pdraw stream using the appropriate method (legacy or rtsp)
        according to the device type
        """
        self._open_resp_future = Future(self.thread_loop)
        if self._state not in (State.Error, State.Closed, State.Created):
            self.logging.logW("Cannot open stream from {}".format(self._state))
            self._open_resp_future.set_result(False)
            return self._open_resp_future

        self._state = State.Opening
        if not self._legacy:
            ret = self._open_url()
        else:
            ret = self._open_single_stream()

        if not ret:
            self._open_resp_future.set_result(False)

        return self._open_resp_future

    def _close_stream(self):
        """
        Close pdraw stream
        """
        self._close_resp_future = Future(self.thread_loop)
        if self._state in (State.Closed, State.Created):
            self.logging.logW("Cannot close stream from {}".format(self._state))
            self._close_resp_future.set_result(False)
            self._state = State.Error
            return self._close_resp_future

        if not self.pdraw:
            self.logging.logE("Error Pdraw interface seems to be destroyed")
            self._state = State.Error
            self._close_resp_future.set_result(False)
            return self._close_resp_future

        self._state = State.Closing
        if not self._close_stream_impl():
            self._state = State.Error
            self._close_resp_future.set_result(False)

        return self._close_resp_future

    def _close_stream_impl(self):
        res = od.pdraw_close(self.pdraw)

        if res != 0:
            self.logging.logE(
                "Error while closing pdraw stream: {}".format(res))
            self._state = State.Error
            return False
        else:
            self.logging.logI("Closing pdraw stream OK")

        return True

    def _open_resp(self, pdraw, status, userdata):
        self.logging.logD("_open_resp called")
        self.local_stream_port = od.pdraw_get_single_stream_local_stream_port(self.pdraw)

        self.local_control_port = od.pdraw_get_single_stream_local_control_port(self.pdraw)

        if status != 0:
            self._state = State.Error
        else:
            self._state = State.Opened

        self._open_resp_future.set_result(status == 0)

    def _close_resp(self, pdraw, status, userdata):
        self.logging.logD("_close_resp called")
        if status != 0:
            self._close_resp_future.set_result(False)
            self._state = State.Error
        else:
            self._state = State.Closed
            self._close_resp_future.set_result(True)

    def _ready_to_play(self, pdraw, ready, userdata):
        self.logging.logI("_ready_to_play({}) called".format(ready))
        if ready:
            self._play_resp_future = Future(self.thread_loop)
            self._play_impl()

    def _play_resp(self, pdraw, status, timestamp, speed, userdata):
        if status == 0:
            self.logging.logD("_play_resp called {}".format(status))
            self._state = State.Playing
            self._play_resp_future.set_result(True)
        else:
            self.logging.logE("_play_resp called {}".format(status))
            self._state = State.Error
            self._play_resp_future.set_result(False)

    def _pause_resp(self, pdraw, status, timestamp, userdata):
        self._close_output_files()
        if status == 0:
            self.logging.logD("_pause_resp called {}".format(status))
            self._state = State.Paused
            self._pause_resp_future.set_result(True)
        else:
            self.logging.logE("_pause_resp called {}".format(status))
            self._state = State.Error
            self._pause_resp_future.set_result(False)

    def _seek_resp(self, pdraw, status, timestamp, userdata):
        if status == 0:
            self.logging.logD("_seek_resp called {}".format(status))
        else:
            self.logging.logE("_seek_resp called {}".format(status))
            self._state = State.Error

    def _socket_created(self, pdraw, fd, userdata):
        self.logging.logD("_socket_created called")

    def _select_demuxer_media(self, pdraw, medias, count, userdata):
        for idx in range(count):
            self.logging.logI(
                "_select_demuxer_media: "
                "idx={} media_id={} name={} uri={} default={}".format(
                    idx, medias[idx].media_id,
                    od.string_cast(medias[idx].name),
                    od.string_cast(medias[idx].uri),
                    str(bool(medias[idx].is_default)))
            )
        # select the default media
        return 0

    def _media_added(self, pdraw, media_info, userdata):
        id_ = media_info.contents.id
        self.logging.logI("_media_added id : {}".format(id_))

        # store the information if supported media type, otherwise exit
        if (media_info.contents._2.video.format !=
                od.PDRAW_VIDEO_MEDIA_FORMAT_YUV and
                media_info.contents._2.video.format !=
                od.PDRAW_VIDEO_MEDIA_FORMAT_H264):
            self.logging.logW(
                'Ignoring media id {} (type {})'.format(
                    id_, media_info.contents._2.video.format))
            return
        self.streams[id_]['type'] = media_info.contents._2.video.format
        self.streams[id_]['info'] = media_info.contents

        # start a video sink attached to the new media
        video_sink_params = od.struct_pdraw_video_sink_params(
            2,  # buffer queue size
            1,  # drop buffers when the queue is full
        )
        self.streams[id_]['id'] = ctypes.cast(
            ctypes.pointer(ctypes.py_object(id_)), ctypes.c_void_p)

        res = od.pdraw_start_video_sink(
            pdraw,
            id_,
            video_sink_params,
            self.video_sink_cb,
            self.streams[id_]['id'],
            ctypes.byref(self.streams[id_]['video_sink'])
        )
        if res != 0:
            self.logging.logE("Unable to start video sink")
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

        self.streams[id_]['video_queue_fd'] = \
            od.pomp_evt_get_fd(self.streams[id_]['video_queue_event'])

        # add the file description to our pomp loop
        self.thread_loop.add_fd_to_loop(
            self.streams[id_]['video_queue_fd'],
            lambda *args: self._video_sink_queue_event(*args),
            id_
        )

    def _media_removed(self, pdraw, media_info, userdata):
        id_ = media_info.contents.id
        if id_ not in self.streams:
            self.logging.logE(
                'Received removed event from unknown ID {}'.format(id_))
            return

        self.logging.logI("_media_removed called id : {}".format(id_))

        if self.streams[id_]['video_queue_fd']:
            self.thread_loop.remove_fd_from_loop(
                self.streams[id_]['video_queue_fd'])

        res = od.pdraw_stop_video_sink(pdraw, self.streams[id_]['video_sink'])
        if res < 0:
            self.logging.logE('pdraw_stop_video_sink() returned %s' % res)

        self.streams.pop(id_)

    def _video_sink_flush(self, pdraw, videosink, userdata):

        id_ = py_object_cast(userdata)
        if id_ not in self.streams:
            self.logging.logE(
                'Received flush event from unknown ID {}'.format(id_))
            return
        self.thread_loop.run_async(
            self._video_sink_flush_impl, pdraw, videosink, id_)

    def _video_sink_flush_impl(self, pdraw, videosink, id_):

        self.logging.logD('video sink flush from media #%d' % id_)

        res = od.vbuf_queue_flush(self.streams[id_]['video_queue'])
        if res < 0:
            self.logging.logE('vbuf_queue_flush() returned %s' % res)
        else:
            self.logging.logD('vbuf_queue_flush() returned %s' % res)

        res = od.pdraw_video_sink_queue_flushed(pdraw, videosink)
        if res < 0:
            self.logging.logE(
                'pdraw_video_sink_queue_flushed() returned %s' % res)
        else:
            self.logging.logD(
                'pdraw_video_sink_queue_flushed() returned %s' % res)

    def _get_media_info(self, mediatype):
        for s in self.streams.values():
            if s['type'] == mediatype:
                return s['info']
        return None

    def _process_outputs(self, frame, metadata, mediatype):
        """
        @param frame: bytearray containing binary data
        @param metadata: dict containing all metadata
        @param mediatype: enum from PDRAW_VIDEO_MEDIA_FORMAT_xxx
        """
        # handle output files
        files = self.outfiles[mediatype]

        f = files['meta']
        if f and not f.closed:
            files['meta'].write(json.dumps(metadata) + '\n')

        f = files['data']
        if f and not f.closed:
            if mediatype == od.PDRAW_VIDEO_MEDIA_FORMAT_H264:
                if f.tell() == 0:
                    # h264 files need a header to be readable
                    self._write_h264_header(f, self._get_media_info(
                        od.PDRAW_VIDEO_MEDIA_FORMAT_H264))
            f.write(frame)

        # call callbacks when existing
        cbs = self.callbacks[mediatype]
        if cbs['meta']:
            cbs['meta'](metadata)
        if cbs['data']:
            cbs['data'](frame)

    def _video_sink_queue_event(self, fd, revents, userdata):
        id_ = py_object_cast(userdata)
        self.logging.logD('media id = {}'.format(id_))

        if id_ not in self.streams:
            self.logging.logE(
                'Received queue event from unknown ID {}'.format(id_))
            return

        # acknowledge event
        res = od.pomp_evt_clear(self.streams[id_]['video_queue_event'])
        if res != 0:
            self.logging.logE(
                "Unable to clear frame received event ({})".format(res))

        # process all available buffers in the queue
        while self._process_buffer(id_):
            pass

    def _process_buffer(self, id_):
        # get the last buffer in the queue
        buf = od.POINTER_T(od.struct_vbuf_buffer)()
        ret = od.vbuf_queue_pop(
            self.streams[id_]['video_queue'], 0, ctypes.byref(buf)
        )

        if ret < 0:
            if ret != -errno.EAGAIN:
                self.logging.logE('vbuf_queue_pop returned error %d' % ret)
            return False
        if not buf:
            self.logging.logE('vbuf_queue_pop returned NULL')
            return False

        # get the size in bytes of the raw data
        frame_size = od.vbuf_get_size(buf)
        self.logging.logD("Frame of %s bytes received" % frame_size)

        # retrieve the raw data from the buffer
        frame = od.vbuf_get_cdata(buf)
        if not frame:
            self.logging.logW('vbuf_get_cdata returned null pointer')
            self._unref_buffer(buf)
            return False


        # retrieve current frame's metadata
        metabuf = od.POINTER_T(ctypes.c_ubyte)()
        res = od.vbuf_metadata_get(
            buf,
            self.streams[id_]['video_sink'],
            od.POINTER_T(ctypes.c_uint32)(),
            od.POINTER_T(ctypes.c_uint64)(),
            ctypes.byref(metabuf))
        metabuf = ctypes.cast(
            metabuf, od.POINTER_T(od.struct_pdraw_video_frame))

        metadict = {}
        if res < 0:
            self.logging.logE(
                'vbuf_metadata_get returned error {}'.format(res))
        else:
            # convert the binary metadata into json
            jsonbuf = ctypes.create_string_buffer(4096)
            res = od.pdraw_video_frame_to_json_str(metabuf, jsonbuf, ctypes.sizeof(jsonbuf))
            if res < 0:
                self.logging.logE(
                    'pdraw_frame_metadata_to_json returned error {}'.format(res))
            else:
                metadict = json.loads(str(jsonbuf.value, encoding="utf-8"))
                self.logging.logD('metadata: {}'.format(metadict))

        # write and/or send data over the requested channels
        self._process_outputs(frame,
                              metadict,
                              self.streams[id_]['type'])

        # Once we're done with this frame, dispose the associated frame buffer
        self._unref_buffer(buf)

    def _unref_buffer(self, buf):
        od.vbuf_unref(buf)

        return True

    def set_output_files(self,
                         h264_data_file,
                         h264_meta_file,
                         raw_data_file,
                         raw_meta_file):
        if self._state is State.Playing:
            raise RuntimeError(
                'Cannot set video streaming files while streaming is on.')

        for mediatype, datatype, filepath, attrib in (
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'data', h264_data_file, 'wb'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'meta', h264_meta_file, 'w'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'data', raw_data_file,  'wb'),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'meta', raw_meta_file,  'w')):
            if self.outfiles[mediatype][datatype]:
                self.outfiles[mediatype][datatype].close()
                self.outfiles[mediatype][datatype] = None

            if filepath is None:
                continue

            # open and close file to store its filename and attribute
            self.outfiles[mediatype][datatype] = open(filepath, attrib)
            self.outfiles[mediatype][datatype].close()

    def set_callbacks(self,
                      h264_data_cb,
                      h264_meta_cb,
                      raw_data_cb,
                      raw_meta_cb):

        for mediatype, datatype, cb in (
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'data', h264_data_cb),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_H264, 'meta', h264_meta_cb),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'data', raw_data_cb),
                (od.PDRAW_VIDEO_MEDIA_FORMAT_YUV,  'meta', raw_meta_cb)):
            self.callbacks[mediatype][datatype] = cb

    def _open_output_files(self):
        self.logging.logD('opening video output files')
        for mediatype, data in self.outfiles.items():
            for datatype, f in data.items():
                if f and f.closed:
                    self.outfiles[mediatype][datatype] = open(f.name, f.mode)

    def _write_h264_header(self, fobj, media_info):

        start = bytearray([0, 0, 0, 1])
        info = media_info._2.video._2.h264

        self.logging.logD("sps: %s, pps: %s" % (info.spslen, info.ppslen))
        if info.spslen > 0:
            fobj.write(start)
            fobj.write(bytearray(info.sps[:info.spslen]))

        if info.ppslen > 0:
            fobj.write(start)
            fobj.write(bytearray(info.pps[:info.ppslen]))

    def _close_output_files(self):
        self.logging.logD('closing video output files')
        for files in self.outfiles.values():
            for f in files.values():
                if f:
                    f.close()

    def play(self):
        if self._state not in (State.Opened,
                               State.Playing,
                               State.Paused,
                               State.Created):
            self.logging.logW("Cannot play stream from the {} state".format(
                self._state))
            f = Future(self.thread_loop)
            f.set_result(False)
            return f

        self._open_output_files()
        if self._state is State.Created:
            f = self.thread_loop.run_async(self._open_stream)
        else:
            f = self._play_resp_future = Future(self.thread_loop)
            self.thread_loop.run_async(self._play_impl)
        return f

    def _play_impl(self):
        self.logging.logD("play_impl")
        if self._state is State.Playing:
            self._play_resp_future.set_result(True)
            return self._play_resp_future

        res = od.pdraw_play(self.pdraw)
        if res != 0:
            msg = "Unable to start streaming ({})".format(res)
            self.logging.logE(msg)
            self._play_resp_future.set_result(False)

        return self._play_resp_future

    def pause(self):
        self._pause_resp_future = Future(self.thread_loop)
        if self.pdraw is None:
            self.logging.logE("Error Pdraw interface seems to be destroyed")
            self._pause_resp_future.set_result(False)
            return self._pause_resp_future

        res = od.pdraw_pause(self.pdraw)
        if res != 0:
            self.logging.logE("Unable to stop streaming ({})".format(res))
            self._pause_resp_future.set_result(False)
        return self._pause_resp_future

    def is_legacy(self):
        return self._legacy
