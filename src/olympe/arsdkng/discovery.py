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
from __future__ import print_function
from __future__ import absolute_import
from future.builtins import str


import concurrent.futures
import ctypes
import olympe_deps as od
import re
import queue
import socket
import time
from collections import OrderedDict
from abc import ABC, abstractmethod
from aenum import Enum
from contextlib import closing


from olympe.arsdkng.backend import Backend
from olympe._private import callback_decorator


_DEFAULT_TIMEOUT = 2.0


DeviceState = Enum(
    "DeviceState",
    {
        re.compile("^ARSDK_DEVICE_STATE_").sub("", v): k
        for k, v in od.arsdk_device_state__enumvalues.items()
    },
)


class Device(object):
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

        def _str_init(_input):
            if isinstance(_input, bytes):
                return _input.decode('utf-8')
            else:
                return _input
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
        return "<ArsdkDevice: serial='{}' ip={}, name='{}', type='{}'>".format(
            self.serial, self.ip_addr, self.name, self.type
        )

    @classmethod
    def from_arsdk_device(cls, backend, device):
        device_info = ctypes.POINTER(od.struct_arsdk_device_info)()
        res = od.arsdk_device_get_info(device, ctypes.pointer(device_info))
        if res != 0:
            raise RuntimeError("ERROR: failed to get device info: {}".format(res))
        return Device(
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


DRONE_DEVICE_TYPE_LIST = []
SKYCTRL_DEVICE_TYPE_LIST = []
for name, value in od.__dict__.items():
    if name.startswith("ARSDK_DEVICE_TYPE_"):
        if name.startswith("ARSDK_DEVICE_TYPE_SKYCTRL"):
            SKYCTRL_DEVICE_TYPE_LIST.append(value)
        else:
            DRONE_DEVICE_TYPE_LIST.append(value)


DEVICE_TYPE_LIST = SKYCTRL_DEVICE_TYPE_LIST + DRONE_DEVICE_TYPE_LIST


class Discovery(ABC):
    def __init__(self, backend):
        self._backend = backend
        self._thread_loop = self._backend._thread_loop
        self.logger = self._backend.logger
        self._devices = OrderedDict()
        self._device_queue = queue.Queue()

        self.userdata = ctypes.c_void_p()
        self.discovery = None
        self._thread_loop.register_cleanup(self._destroy)

    @callback_decorator()
    def _do_start(self):
        if self.discovery is not None:
            self.logger.error("Discovery already running")
            return True
        self.discovery = self._create_discovery()
        if self.discovery is None:
            self.logger.error("Failed to create discovery object")
            return False
        self.logger.debug("Net discovery object has been created")
        res = self._start_discovery()
        if res != 0:
            self.stop()
            self.logger.error("arsdk_discovery_start: {}".format(res))
            return False
        self.logger.debug("Net discovery has been started")
        self._backend.add_device_handler(self)
        return True

    @abstractmethod
    def _create_discovery(self):
        """
        Create the internal arsdk (net, raw, ...) discovery object
        """

    @abstractmethod
    def _start_discovery(self):
        """
        Call the specific internal arsdk discovery object start method
        """

    @abstractmethod
    def _stop_discovery(self):
        """
        Call the specific internal arsdk discovery object stop method
        """

    @abstractmethod
    def _destroy_discovery(self):
        """
        Destroy the internal arsdk (net, raw, ...) discovery object
        """

    def start(self):
        self._devices = OrderedDict()
        self._device_queue = queue.Queue()
        f = self._thread_loop.run_async(self._do_start)
        try:
            return f.result_or_cancel(timeout=_DEFAULT_TIMEOUT)
        except concurrent.futures.TimeoutError:
            self.logger.warning("Discovery start timedout")
            return False

    def stop(self):
        f = self._thread_loop.run_async(self._do_stop)
        try:
            return f.result_or_cancel(timeout=_DEFAULT_TIMEOUT)
        except concurrent.futures.TimeoutError:
            self.logger.warning("Discovery stop timedout")
            return False

    @callback_decorator()
    def _do_stop(self):
        self._backend.remove_device_handler(self)

        if self.discovery is None:
            self.logger.debug("No discovery instance to be stopped")
            return

        # stop currently running discovery
        res = self._stop_discovery()
        if res != 0:
            self.logger.error("Error while stopping discovery: {}".format(res))
        else:
            self.logger.debug("Discovery has been stopped")

        # then, destroy it
        res = self._destroy_discovery()
        if res != 0:
            self.logger.error(
                "Error while destroying discovery object: {}".format(res)
            )
        else:
            self.logger.debug("Discovery object has been destroyed")

        self.discovery = None

    def _device_added_cb(self, arsdk_device, _user_data):
        """
        Callback received when a new device is detected.
        Detected devices depends on discovery parameters
        """
        device = Device.from_arsdk_device(self._backend, arsdk_device)
        self.logger.info("New device has been detected: '{}'".format(device.name))
        self._devices[device.name] = device
        self._device_queue.put_nowait(device)

    def _device_removed_cb(self, arsdk_device, _user_data):
        """
        Callback received when a device disappear from discovery search
        """
        device = Device.from_arsdk_device(self._backend, arsdk_device)
        self.logger.info("Device '{}' has been removed".format(device.name))
        name = device.name
        if name == "__all__":
            device_names = list(self._devices.keys())
        elif name not in self._devices:
            self.logger.error(
                "Error while removing device from discovery: "
                "{} is an unknown device".format(name)
            )
            device_names = []
        else:
            device_names = [name]

        for name in device_names:
            del self._devices[name]

    @callback_decorator()
    def _destroy(self):
        self._thread_loop.unregister_cleanup(self._destroy, ignore_error=True)
        self._do_stop()

    def destroy(self):
        self._thread_loop.run_later(self._destroy)

    def _device_generator(self, timeout=_DEFAULT_TIMEOUT):
        if not self.start():
            return
        deadline = time.time() + timeout
        while deadline > time.time():
            try:
                device = self._device_queue.get(timeout=0.005)
            except queue.Empty:
                device = None
            run = yield device
            if not run:
                break

    def iter_devices(
        self, timeout=_DEFAULT_TIMEOUT, stop_cond=lambda device, count: True
    ):
        count = 1
        generator = self._device_generator(timeout=timeout)
        try:
            device = next(generator)
            while True:
                if device is not None:
                    yield device
                    count += 1
                    device = generator.send(stop_cond(device, count))
                else:
                    device = generator.send(lambda device, count: True)
        except StopIteration:
            pass

    def get_device_count(self, max_count, timeout=_DEFAULT_TIMEOUT):
        return list(
            self.iter_devices(
                timeout=timeout, stop_cond=lambda device, count: count == max_count
            )
        )

    def get_device(self, timeout=_DEFAULT_TIMEOUT):
        devices = self.get_device_count(max_count=1, timeout=timeout)
        if not devices:
            return None
        else:
            return devices[0]


class DiscoveryNet(Discovery):
    def __init__(self, backend, ip_addr, device_types=None):
        super().__init__(backend=backend)
        if device_types is None:
            device_types = DEVICE_TYPE_LIST
        self._device_types = device_types
        ctypes_device_type_list = (ctypes.c_int * len(device_types))(*device_types)
        self.discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(ctypes_device_type_list, od.POINTER_T(od.arsdk_device_type)),
            len(ctypes_device_type_list),
        )
        self.ip_addr = ip_addr

    def _create_discovery(self):
        """
        Start net discovery in order to detect devices
        """
        discovery = od.POINTER_T(od.struct_arsdk_discovery_net)()

        res = od.arsdk_discovery_net_new(
            self._backend._arsdk_ctrl,
            self._backend._backend_net,
            ctypes.pointer(self.discovery_cfg),
            od.char_pointer_cast(self.ip_addr),
            ctypes.byref(discovery),
        )
        if res != 0:
            self.logger.error("arsdk_discovery_net_new: {}".format(res))
            return None
        return discovery

    def _start_discovery(self):
        return od.arsdk_discovery_net_start(self.discovery)

    def _stop_discovery(self):
        return od.arsdk_discovery_net_stop(self.discovery)

    def _destroy_discovery(self):
        return od.arsdk_discovery_net_destroy(self.discovery)


class DiscoveryNetRaw(Discovery):
    """
    NetRaw discovery corresponds to a net discovery without any active
    method to search for devices.
    That means that this discovery type only works when manually
    adding a device.

    This method should be considered as a fallback.
    """

    def __init__(self, backend, check_port=True, *args, **kwds):
        super().__init__(backend=backend)
        self._check_port = check_port
        devices = kwds.pop("devices", None)
        self._raw_devices = []
        if not devices:
            self._raw_devices.append(Device(*args, **kwds))
        else:
            self._raw_devices.extend(devices)
            if args or kwds:
                self._raw_devices.append(Device(*args, **kwds))

        id_ = str(id(self))[:7]
        for i, device in enumerate(self._raw_devices):
            # arsdk will refuse to discover the same device twice
            # so we need to have unique serial for each "discovered" drone
            device.serial = "UNKNOWN_{:02d}_{}".format(i, id_)

            # simple heuristic to identify the device type from the IP address
            if device.type is None or device.type <= 0:
                if device.ip_addr == "192.168.53.1":
                    device.type = od.ARSDK_DEVICE_TYPE_SKYCTRL_3
                    device.name = "Skycontroller 3"
                elif device.ip_addr in (
                    "192.168.42.1",
                    "192.168.43.1",
                ) or device.ip_addr.startswith("10.202.0."):
                    device.type = od.ARSDK_DEVICE_TYPE_ANAFI4K
                    device.name = "ANAFI-{}".format(7 * "X")

    def start(self):
        super().start()
        for device in self._raw_devices:
            self._add_device(device)
        return True

    def _create_discovery(self):
        discovery = od.POINTER_T(od.struct_arsdk_discovery)()

        backendparent = od.arsdkctrl_backend_net_get_parent(self._backend._backend_net)

        res = od.arsdk_discovery_new(
            b"netraw", backendparent, self._backend._arsdk_ctrl, ctypes.byref(discovery)
        )
        if res != 0:
            raise RuntimeError("Unable to create raw discovery:{}".format(res))
        return discovery

    def _start_discovery(self):
        return od.arsdk_discovery_start(self.discovery)

    def _stop_discovery(self):
        return od.arsdk_discovery_stop(self.discovery)

    def _destroy_discovery(self):
        return od.arsdk_discovery_destroy(self.discovery)

    def _add_device(self, device):
        if self._check_port:
            # check that the device port is opened
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                sock.settimeout(_DEFAULT_TIMEOUT)
                try:
                    res = sock.connect_ex((device.ip_addr, device.port))
                except (socket.error, OSError):
                    self.logger.debug("{} is unreachable".format(device.ip_addr))
                    return
                if res != 0:
                    self.logger.debug("{}:{} is closed".format(device.ip_addr, device.port))
                    return
        # add this device to the "discovered" devices
        f = self._thread_loop.run_async(self._do_add_device, device)
        try:
            f.result_or_cancel(timeout=_DEFAULT_TIMEOUT)
        except concurrent.futures.TimeoutError:
            self.logger.error("raw discovery timedout for {}:{}".format(
                device.ip_addr, device.port))

    @callback_decorator()
    def _do_add_device(self, device):
        res = od.arsdk_discovery_add_device(
            self.discovery, device.as_arsdk_discovery_device_info()
        )
        if res != 0:
            self.logger.error("arsdk_discovery_add_device {}".format(res))
        else:
            self.logger.debug(
                "Device '{}'/{} manually added to raw discovery".format(
                    device.name, device.ip_addr
                )
            )


if __name__ == "__main__":

    backend = Backend()

    for ip_addr in ("192.168.42.1", "192.168.43.1", "192.168.53.1"):
        for discovery in (
            DiscoveryNet(
                backend,
                ip_addr=ip_addr,
                # with the net discovery we can actually filter on the
                # discovered device_type
                device_types=[
                    od.ARSDK_DEVICE_TYPE_ANAFI4K,
                    od.ARSDK_DEVICE_TYPE_SKYCTRL_3,
                ],
            ),
            DiscoveryNetRaw(
                backend,
                ip_addr=ip_addr,
                # With the raw discovery we provide the device type we're
                # expecting. Nothing prevents you from providing an incoherent
                # device type...
                # device_type=od.ARSDK_DEVICE_TYPE_ANAFI4K,
            ),
        ):
            device = discovery.get_device()
            print(device)
