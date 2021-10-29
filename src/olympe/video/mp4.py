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
import time

from olympe.log import LogMixin
from olympe.__version__ import __version__
from . import PDRAW_TIMESCALE


class Mp4Mux(LogMixin):
    def __init__(self, filepath, name=None, device_name=None):
        super().__init__(name, device_name, "mp4mux")
        self._filepath = filepath
        self._mux = self._open(filepath)
        self._tracks = dict()
        self._tracks_count = dict()

    @property
    def name(self):
        return self._filepath

    def _open(self, filepath):
        mux = od.POINTER_T(od.struct_mp4_mux)()
        now = ctypes.c_uint64(int(time.time()))
        res = od.mp4_mux_open(
            od.char_pointer_cast(filepath), PDRAW_TIMESCALE, now, now, ctypes.byref(mux)
        )
        if res != 0:
            raise RuntimeError(f"mp4_mux_open returned {res}")
        res = od.mp4_mux_add_file_metadata(
            mux,
            od.char_pointer_cast("com.parrot.olympe.version"),
            od.char_pointer_cast(__version__),
        )
        if res != 0:
            self.logger.error(f"mp4_mux_add_file_metadata returned {res}")
        return mux

    def close(self):
        if not self._mux:
            return
        res = od.mp4_mux_close(self._mux)
        if res < 0:
            self.logger.error(f"mp4_mux_close returned {res}")
        self._mux = None

    @property
    def closed(self):
        return self._mux is None

    def sync(self):
        if not self._mux:
            self.logger.error(f"mp4_mux_sync on closed file '{self._filepath}'")
            return
        res = od.mp4_mux_sync(self._mux)
        if res < 0:
            self.logger.error(f"mp4_mux_sync returned {res}")
            return False
        return True

    def add_track(self, type, name, enabled, in_movie, in_preview):
        now = time.time()
        track = od.struct_mp4_mux_track_params.bind(
            dict(
                type=type,
                name=self._to_LP_c_char(name),
                enabled=ctypes.c_int32(enabled),
                in_movie=ctypes.c_int32(in_movie),
                in_preview=ctypes.c_int32(in_preview),
                timescale=ctypes.c_uint32(PDRAW_TIMESCALE),
                creation_time=ctypes.c_uint64(int(now)),
                modification_time=ctypes.c_uint64(int(now)),
            )
        )
        track_id = od.mp4_mux_add_track(self._mux, ctypes.pointer(track))
        self._tracks[track_id] = track
        self._tracks_count[track_id] = 0
        return track_id

    def ref_to_track(self, track_id, ref_track_id):
        res = od.mp4_mux_add_ref_to_track(self._mux, track_id, ref_track_id)
        if res < 0:
            self.logger.error(f"mp4_mux_add_ref_to_track returned {res}")
        return res

    def add_track_metadata(self, track_id, meta_key, meta_value):
        res = od.mp4_mux_add_track_metadata(
            self._mux,
            track_id,
            self._to_LP_c_char(meta_key),
            self._to_LP_c_char(meta_value),
        )
        if res < 0:
            self.logger.error(f"mp4_mux_add_track_metadata returned {res}")
        return res

    def tell(self, track_id):
        return self._tracks_count[track_id]

    def set_decoder_config(self, track_id, h264_header, width, height):
        cfg = od.struct_mp4_video_decoder_config()
        cfg.codec = od.MP4_VIDEO_CODEC_AVC

        sps_pps_cfg = cfg.mp4_video_decoder_config_0.avc
        sps_cfg = sps_pps_cfg.mp4_video_decoder_config_0_0_0
        sps_cfg.sps = self._to_LP_c_ubyte(h264_header.sps)
        sps_cfg.c_sps = self._to_LP_c_ubyte(h264_header.sps)
        sps_pps_cfg.sps_size = h264_header.spslen
        pps_cfg = sps_pps_cfg.mp4_video_decoder_config_0_0_1
        pps_cfg.pps = self._to_LP_c_ubyte(h264_header.pps)
        pps_cfg.c_pps = self._to_LP_c_ubyte(h264_header.pps)
        sps_pps_cfg.pps_size = h264_header.ppslen
        cfg.width = width
        cfg.height = height
        res = od.mp4_mux_track_set_video_decoder_config(
            self._mux, track_id, ctypes.byref(cfg)
        )
        if res < 0:
            self.logger.error(f"mp4_mux_track_set_video_decoder_config returned {res}")
            return False
        return True

    def add_coded_frame(self, track_id, metadata_track_id, coded_frame):
        nalu = od.struct_vdef_nalu()
        nalu_count = od.mbuf_coded_video_frame_get_nalu_count(
            coded_frame._get_video_frame()
        )
        info = coded_frame._vdef_info()
        if nalu_count < 0:
            self.logger.error(
                f"mbuf_coded_video_frame_get_nalu_count returned {nalu_count}"
            )
            return False
        bufs = (nalu_count * od.POINTER_T(ctypes.c_ubyte))()
        lens = (nalu_count * ctypes.c_size_t)()
        for i in range(nalu_count):
            res = od.mbuf_coded_video_frame_get_nalu(
                coded_frame._get_video_frame(),
                i,
                ctypes.cast(ctypes.pointer(bufs[i]), od.POINTER_T(ctypes.c_void_p)),
                ctypes.byref(nalu),
            )
            if res < 0:
                self.logger.error(f"mbuf_coded_video_frame_get_nalu returned {res}")
                break
            lens[i] = nalu.size
        else:
            coded_sample = od.struct_mp4_mux_scattered_sample.bind(
                {
                    "buffers": bufs,
                    "len": lens,
                    "nbuffers": nalu_count,
                    "sync": (info.type == od.VDEF_CODED_FRAME_TYPE_IDR),
                    "dts": info.info.timestamp,
                }
            )
            res = od.mp4_mux_track_add_scattered_sample(
                self._mux, track_id, ctypes.pointer(coded_sample)
            )
        for i in range(nalu_count):
            od.mbuf_coded_video_frame_release_nalu(
                coded_frame._get_video_frame(),
                i,
                ctypes.cast(bufs[i], od.POINTER_T(ctypes.c_void_p)),
            )
        if res < 0:
            self.logger.error(f"mp4_mux_track_add_scattered_sample returned {res}")
            return False

        self._tracks_count[track_id] += 1

        buf = od.POINTER_T(ctypes.c_ubyte)()
        size = ctypes.c_size_t()
        if coded_frame._vmeta():
            res = od.vmeta_frame_proto_get_buffer(
                coded_frame._vmeta(), ctypes.byref(buf), ctypes.byref(size)
            )

            if res < 0:
                self.logger.error(f"vmeta_frame_proto_get_buffer returned {res}")
                return False

            metadata_sample = od.struct_mp4_mux_sample.bind(
                {
                    "buffer": buf,
                    "len": size,
                    "dts": info.info.timestamp,
                }
            )

            res = od.mp4_mux_track_add_sample(
                self._mux, metadata_track_id, ctypes.pointer(metadata_sample)
            )
            od.vmeta_frame_proto_release_buffer(coded_frame._vmeta(), buf)
            if res < 0:
                self.logger.error(f"mp4_mux_track_add_sample returned {res}")
                return False

        return self.sync()

    def set_metadata_mime_type(self, metadata_track_id, content_encoding, mime_type):
        res = od.mp4_mux_track_set_metadata_mime_type(
            self._mux,
            metadata_track_id,
            self._to_LP_c_char(content_encoding),
            self._to_LP_c_char(mime_type),
        )
        if res < 0:
            self.logger.error(f"mp4_mux_track_set_metadata_mime_type returned {res}")
            return False
        return True

    @classmethod
    def _to_LP_c_char(cls, string):
        if isinstance(string, (bytes, str)):
            return od.char_pointer_cast(string)
        else:
            assert od.POINTER_T(ctypes.c_char) == type(string)
            return string

    @classmethod
    def _to_LP_c_ubyte(cls, buf):
        return ctypes.cast(
            (len(buf) * ctypes.c_ubyte).from_buffer(buf), od.POINTER_T(ctypes.c_ubyte)
        )
