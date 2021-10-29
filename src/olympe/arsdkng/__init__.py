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
import re
from aenum import Enum


DeviceState = Enum(
    "DeviceState",
    {
        re.compile("^ARSDK_DEVICE_STATE_").sub("", v): k
        for k, v in od.arsdk_device_state__enumvalues.items()
    },
)


DRONE_DEVICE_TYPE_LIST = []
SKYCTRL_DEVICE_TYPE_LIST = []
for name, value in od.__dict__.items():
    if name.startswith("ARSDK_DEVICE_TYPE_"):
        if name.startswith("ARSDK_DEVICE_TYPE_SKYCTRL"):
            SKYCTRL_DEVICE_TYPE_LIST.append(value)
        else:
            DRONE_DEVICE_TYPE_LIST.append(value)


DEVICE_TYPE_LIST = SKYCTRL_DEVICE_TYPE_LIST + DRONE_DEVICE_TYPE_LIST


def _str_init(_input):
    if isinstance(_input, bytes):
        return _input.decode('utf-8')
    else:
        return _input


class DeviceInfo:
    def __init__(
        self,
        ip_addr,
        serial="000000000000000000",
        name="",
        device_type=int(od.ARSDK_DEVICE_TYPE_UNKNOWN),
        port=44444,
        proto_v=1,  # ARSDK_PROTOCOL_VERSION_1 is the safe default
        state=None,
        json="",
        arsdk_device=None,
        backend=None,
    ):

        self.serial = _str_init(serial)
        self.name = _str_init(name)
        self.type = device_type
        self.ip_addr = _str_init(ip_addr)
        self.port = port
        self.proto_v = proto_v
        self.state = state
        self.json = _str_init(json)
        self.arsdk_device = arsdk_device
        self.backend = backend

    def __repr__(self):
        return (f"<ArsdkDevice: serial='{self.serial}' ip={self.ip_addr} "
                f"name='{self.name}', type='{self.type}'>")

    @classmethod
    def from_arsdk_device(cls, backend, device):
        device_info = ctypes.POINTER(od.struct_arsdk_device_info)()
        res = od.arsdk_device_get_info(device, ctypes.pointer(device_info))
        if res != 0:
            raise RuntimeError(f"Failed to get device info: {res}")
        return DeviceInfo(
            serial=od.string_cast(device_info.contents.id) or "",
            name=od.string_cast(device_info.contents.name) or "",
            device_type=int(device_info.contents.type),
            ip_addr=od.string_cast(device_info.contents.addr) or "",
            port=int(device_info.contents.port),
            proto_v=int(getattr(device_info.contents, "proto_v", 1)),
            state=DeviceState(device_info.contents.state),
            json=od.string_cast(device_info.contents.json) or "",
            arsdk_device=device,
            backend=backend,
        )

    def as_arsdk_discovery_device_info(self):
        return od.struct_arsdk_discovery_device_info(
            od.char_pointer_cast(self.name),
            od.arsdk_device_type(self.type),
            od.char_pointer_cast(self.ip_addr),
            ctypes.c_uint16(self.port),
            od.char_pointer_cast(self.serial),
            ctypes.c_uint32(self.proto_v),
        )


class PeerInfo:
    def __init__(
        self,
        ctrl_name="",
        ctrl_type="",
        ctrl_addr="",
        device_id="",
        proto_v=1,  # ARSDK_PROTOCOL_VERSION_1 is the safe default
        json="",
        arsdk_peer=None,
        backend_type=od.ARSDK_BACKEND_TYPE_UNKNOWN,
    ):
        self.ctrl_name = _str_init(ctrl_name)
        self.ctrl_type = _str_init(ctrl_type)
        self.ctrl_addr = _str_init(ctrl_addr)
        self.device_id = _str_init(device_id)
        self.proto_v = int(proto_v)
        self.json = _str_init(json)
        if arsdk_peer is None:
            arsdk_peer = od.POINTER_T(od.struct_arsdk_peer)()
        self.arsdk_peer = arsdk_peer
        self.backend_type = backend_type

    def __repr__(self):
        return (f"<ArsdkPeer: ctrl_name='{self.ctrl_name}' "
                f"ctrl_type='{self.ctrl_type}' ctrl_addr='{self.ctrl_addr}' "
                f"device_id='{self.device_id}'>")

    @classmethod
    def from_arsdk_peer(cls, arsdk_peer):
        peer_info = od.POINTER_T(od.struct_arsdk_peer_info)()
        res = od.arsdk_peer_get_info(
            arsdk_peer, ctypes.byref(peer_info))
        if res < 0 or not peer_info:
            raise RuntimeError(f"Failed to get peer info: {res}")
        peer_info = peer_info.contents
        return cls(
            ctrl_name=od.string_cast(peer_info.ctrl_name),
            ctrl_type=od.string_cast(peer_info.ctrl_type),
            ctrl_addr=od.string_cast(peer_info.ctrl_addr),
            device_id=od.string_cast(peer_info.device_id),
            proto_v=int(peer_info.proto_v),
            json=od.string_cast(peer_info.json),
            arsdk_peer=arsdk_peer,
        )
