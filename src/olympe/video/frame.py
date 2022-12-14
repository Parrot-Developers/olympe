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
import errno
import json
import numpy as np
import olympe_deps as od
import os
from . import VMetaFrameType
from olympe.utils import hashabledict


class _CodedVideoFrame:
    ref = od.mbuf_coded_video_frame_ref
    unref = od.mbuf_coded_video_frame_unref
    get_metadata = od.mbuf_coded_video_frame_get_metadata
    get_packed_buffer = od.mbuf_coded_video_frame_get_packed_buffer
    get_packed_size = od.mbuf_coded_video_frame_get_packed_size
    copy = od.mbuf_coded_video_frame_copy
    finalize = od.mbuf_coded_video_frame_finalize
    get_frame_info = od.mbuf_coded_video_frame_get_frame_info
    get_ancillary_data = od.mbuf_coded_video_frame_get_ancillary_data
    frame_type = od.struct_mbuf_coded_video_frame
    vdef_frame_type = od.struct_vdef_coded_frame


class _RawVideoFrame:
    ref = od.mbuf_raw_video_frame_ref
    unref = od.mbuf_raw_video_frame_unref
    get_metadata = od.mbuf_raw_video_frame_get_metadata
    get_packed_buffer = od.mbuf_raw_video_frame_get_packed_buffer
    get_packed_size = od.mbuf_raw_video_frame_get_packed_size
    copy = od.mbuf_raw_video_frame_copy
    finalize = od.mbuf_raw_video_frame_finalize
    get_frame_info = od.mbuf_raw_video_frame_get_frame_info
    get_ancillary_data = od.mbuf_raw_video_frame_get_ancillary_data
    frame_type = od.struct_mbuf_raw_video_frame
    vdef_frame_type = od.struct_vdef_raw_frame


class VideoFrame:

    _mbuf_vt = {
        od.VDEF_FRAME_TYPE_CODED: _CodedVideoFrame,
        od.VDEF_FRAME_TYPE_RAW: _RawVideoFrame,
    }

    def __init__(self, logger, mbuf_video_frame, media_id, stream, session_metadata):
        self.logger = logger
        self._mbuf_video_frame = mbuf_video_frame
        self._media_id = media_id
        self._stream = stream
        self._session_metadata = session_metadata
        self._mbuf = self._mbuf_vt[self._stream["frame_type"]]
        self._frame_pointer = ctypes.c_void_p()
        self._frame_size = ctypes.c_size_t()
        self._frame_array = None
        self._packed_buffer = od.POINTER_T(od.struct_mbuf_mem)()
        self._packed_video_frame = od.POINTER_T(self._mbuf.frame_type)()
        self._frame_info = None

        self._vmeta_frame = od.POINTER_T(od.struct_vmeta_frame)()
        self._metadata_pointers = []

        self._pdraw_video_frame_storage = od.struct_pdraw_video_frame()

    def __bool__(self):
        return bool(self._mbuf_video_frame)

    def ref(self):
        """
        This function increments the reference counter of the
        underlying buffer(s)
        """
        try:
            # try to allocate the packed buffer before referencing it
            self._get_video_frame()
        finally:
            if self._packed_video_frame:
                self._mbuf.ref(self._packed_video_frame)
            self._mbuf.ref(self._mbuf_video_frame)

    def unref(self):
        """
        This function decrements the reference counter of the
        underlying buffer(s)
        """
        try:
            if self._stream["frame_type"] == od.VDEF_FRAME_TYPE_CODED and (
                self._frame_pointer
            ):
                res = od.mbuf_coded_video_frame_release_packed_buffer(
                    self._mbuf_video_frame, self._frame_pointer
                )
                if res != 0:
                    self.logger.error(
                        "mbuf_coded_video_frame_release_packed_buffer "
                        f"{self._media_id}: {os.strerror(-res)}"
                    )
            res = self._mbuf.unref(self._mbuf_video_frame)
            if res != 0:
                self.logger.error(
                    f"mbuf_unref unpacked frame error {self._media_id}: "
                    f"{os.strerror(-res)}"
                )
        finally:
            if self._packed_video_frame:
                res = self._mbuf.unref(self._packed_video_frame)
                if res != 0:
                    self.logger.error(
                        f"mbuf_unref packed frame error {self._media_id} "
                        f"{os.strerror(-res)}"
                    )

    def media_id(self):
        return self._media_id

    def _get_video_frame(self):
        if self._stream["frame_type"] == od.VDEF_FRAME_TYPE_CODED:
            return self._mbuf_video_frame

        if self._packed_video_frame:
            return self._packed_video_frame

        if not self._packed_buffer:
            size = self._mbuf.get_packed_size(self._mbuf_video_frame, True)
            if size < 0:
                self.logger.error(
                    f"mbuf_raw_video_frame_get_packed_size returned error {size}"
                )
                return self._packed_video_frame
            res = od.mbuf_mem_generic_new(size, ctypes.byref(self._packed_buffer))
            if res < 0:
                self.logger.error(f"mbuf_generic_mem_new returned error {res}")
                return self._packed_video_frame
        res = self._mbuf.copy(
            self._mbuf_video_frame,
            self._packed_buffer,
            True,
            ctypes.byref(self._packed_video_frame),
        )
        if res < 0:
            self.logger.error(f"mbuf_raw_video_frame_copy returned error {res}")
            res = od.mbuf_mem_unref(self._packed_buffer)
            if res < 0:
                self.logger.error(f"mbuf_mem_unref returned error {res}")
            return self._packed_video_frame
        res = self._mbuf.finalize(self._packed_video_frame)
        if res < 0:
            self.logger.error(f"mbuf_raw_video_frame_finalize returned error {res}")
            res = od.mbuf_mem_unref(self._packed_buffer)
            if res < 0:
                self.logger.error(f"mbuf_mem_unref returned error {res}")
            return self._packed_video_frame
        # Let the frame own the last ref count on this buffer
        res = od.mbuf_mem_unref(self._packed_buffer)
        if res < 0:
            self.logger.error(f"mbuf_mem_unref returned error {res}")
        return self._packed_video_frame

    def as_ctypes_pointer(self):
        """
        This function return a 2-tuple (frame_pointer, frame_size) where
        frame_pointer is a ctypes pointer and frame_size the frame size
        in bytes.

        See: https://docs.python.org/3/library/ctypes.html
        """
        if self._frame_pointer:
            return self._frame_pointer, self._frame_size.value

        # H264 stream
        if self._stream["frame_type"] == od.VDEF_FRAME_TYPE_CODED:
            # get the size in bytes of the raw data
            res = od.mbuf_coded_video_frame_get_packed_buffer(
                self._mbuf_video_frame,
                ctypes.byref(self._frame_pointer),
                ctypes.byref(self._frame_size),
            )
            if res < 0 or not self._frame_pointer:
                self.logger.warning(f"mbuf_mem_get_data failed: {res}")
                return self._frame_pointer, 0
            return self._frame_pointer, self._frame_size.value

        # YUV I420 or NV12 stream
        elif self._stream["frame_type"] == od.VDEF_FRAME_TYPE_RAW:
            # get the size in bytes of the raw data
            res = od.mbuf_mem_get_data(
                self._packed_buffer,
                ctypes.byref(self._frame_pointer),
                ctypes.byref(self._frame_size),
            )
            if res < 0 or not self._frame_pointer:
                self.logger.warning(f"mbuf_mem_get_data failed: {res}")
                return self._frame_pointer, 0
            return self._frame_pointer, self._frame_size.value
        return self._frame_pointer, self._frame_size.value

    def as_ndarray(self):
        """
        This function returns an non-owning numpy 1D (h264) or 2D (YUV) array
        on this video frame
        """
        if self._frame_array is not None:
            return self._frame_array
        frame_pointer, frame_size = self.as_ctypes_pointer()
        if not frame_pointer:
            return self._frame_array
        if self._stream["frame_type"] == od.VDEF_FRAME_TYPE_CODED:
            shape = (frame_size,)
        elif self._stream["frame_type"] == od.VDEF_FRAME_TYPE_RAW:
            frame_info = self.info()
            if not frame_info:
                return self._frame_array
            height = frame_info["raw"]["frame"]["info"]["height"]
            width = frame_info["raw"]["frame"]["info"]["width"]
            # assume I420 or NV12 3/2 ratio
            shape = (int(3 * height / 2), width)
        self._frame_array = np.ctypeslib.as_array(
            ctypes.cast(frame_pointer, od.POINTER_T(ctypes.c_ubyte)), shape=shape
        )
        return self._frame_array

    @property
    def width(self):
        frame_info = self.info()
        return frame_info[frame_info["format"].lower()]["frame"]["info"]["width"]

    @property
    def height(self):
        frame_info = self.info()
        return frame_info[frame_info["format"].lower()]["frame"]["info"]["height"]

    def info(self):
        """
        Returns a dictionary of video frame info
        """
        if self._frame_info is not None:
            return self._frame_info
        frame = self._get_video_frame()
        if not frame:
            return self._frame_info
        ancillary_data = od.POINTER_T(od.struct_mbuf_ancillary_data)()
        self._mbuf.get_ancillary_data(
            frame, od.PDRAW_ANCILLARY_DATA_KEY_VIDEOFRAME, ctypes.byref(ancillary_data)
        )
        pdraw_video_frame_size = ctypes.c_size_t()
        pdraw_video_frame = ctypes.cast(
            od.mbuf_ancillary_data_get_buffer(
                ancillary_data, ctypes.byref(pdraw_video_frame_size)
            ),
            od.POINTER_T(od.struct_pdraw_video_frame),
        )
        assert pdraw_video_frame_size.value == ctypes.sizeof(
            od.struct_pdraw_video_frame
        )

        # FIXME: workaround bug in pdraw ancillary data API
        frame_type = self._stream["frame_type"]
        pdraw_video_frame_copy = ctypes.pointer(self._pdraw_video_frame_storage)
        pdraw_video_frame_copy.contents = pdraw_video_frame.contents
        pdraw_video_frame = pdraw_video_frame_copy
        pdraw_video_frame.contents.format = frame_type
        if frame_type == od.VDEF_FRAME_TYPE_CODED:
            res = self._mbuf_vt[frame_type].get_frame_info(
                self._get_video_frame(),
                pdraw_video_frame.contents.pdraw_video_frame_0.coded,
            )
        else:
            res = self._mbuf_vt[frame_type].get_frame_info(
                self._get_video_frame(),
                pdraw_video_frame.contents.pdraw_video_frame_0.raw,
            )
        if res < 0:
            self.logger.error(
                f"mbuf_raw/coded_video_frame_get_frame_info returned error {res}"
            )
            return self._frame_info

        # convert the binary metadata into json
        self._frame_info = {}
        jsonbuf = ctypes.create_string_buffer(4096)
        res = od.pdraw_video_frame_to_json_str(
            pdraw_video_frame, self._vmeta_frame, jsonbuf, ctypes.sizeof(jsonbuf)
        )
        if res < 0:
            self.logger.error(f"pdraw_frame_metadata_to_json returned error {res}")
        else:
            self._frame_info = json.loads(str(jsonbuf.value, encoding="utf-8"))
        return self._frame_info

    def _vmeta(self):
        frame = self._get_video_frame()
        if not frame:
            return self._vmeta_frame
        if not self._vmeta_frame:
            res = self._mbuf.get_metadata(frame, ctypes.byref(self._vmeta_frame))
            if res < 0 and res != -errno.ENOENT:
                self.logger.error(
                    f"mbuf_{{raw,coded}}_video_frame_get_metadata returned error {res}"
                )
        return self._vmeta_frame

    def vmeta(self):
        """
        Returns a 2-tuple (VMetaFrameType, dictionary of video frame metadata)
        """
        vmeta = {}
        vmeta_type = VMetaFrameType.NONE
        self._vmeta()
        if not self._vmeta_frame:
            return vmeta_type, vmeta
        jsonbuf = ctypes.create_string_buffer(4096)
        res = od.vmeta_frame_to_json_str(
            self._vmeta_frame, jsonbuf, ctypes.sizeof(jsonbuf)
        )
        if res < 0:
            self.logger.error(f"vmeta_frame_to_json_str returned error {res}")
            return vmeta_type, vmeta
        else:
            vmeta = json.loads(str(jsonbuf.value, encoding="utf-8"))
        vmeta_type = VMetaFrameType(self._vmeta_frame.contents.type)
        return vmeta_type, vmeta

    def _vdef_type(self):
        frame = self._get_video_frame()
        if not frame:
            return od.VDEF_FRAME_TYPE_UNKNOWN
        return int(frame.contents.format)

    def _vdef_info(self):
        frame = self._get_video_frame()
        if not frame:
            return 0
        info = self._mbuf.vdef_frame_type()
        res = self._mbuf.get_frame_info(frame, ctypes.byref(info))
        if res < 0:
            self.logger.error(
                f"mbuf_{{raw,coded}}_video_frame_get_frame_info returned error {res}"
            )
            return self._mbuf.vdef_frame_type()
        return info

    def format(self):
        info = self._vdef_info()
        if not info:
            return 0
        return hashabledict(od.struct_vdef_raw_format.as_dict(info.format))

    def userdata_sei(self):
        """
        This returns some additional and optional userdata SEI associated
        to this frame
        """
        sei_data = ctypes.c_void_p()
        sei_size = ctypes.c_size_t()

        frame = self._get_video_frame()
        if not frame:
            return sei_data, sei_size

        ancillary_data = od.POINTER_T(od.struct_mbuf_ancillary_data)()
        self._mbuf.get_ancillary_data(
            frame, od.MBUF_ANCILLARY_KEY_USERDATA_SEI, ctypes.byref(ancillary_data)
        )
        if not ancillary_data:
            return sei_data, sei_size

        sei_data = (
            od.mbuf_ancillary_data_get_buffer(ancillary_data, ctypes.byref(sei_size)),
        )
        return sei_data, sei_size

    def session_metadata(self):
        """
        Returns video stream session metadata
        """
        return self._session_metadata
