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


import concurrent.futures
import ctypes
import olympe_deps as od
import queue
import socket
import time

from . import DeviceInfo, DEVICE_TYPE_LIST
from .backend import CtrlBackend
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import closing
from olympe.utils import callback_decorator


class Discovery(ABC):

    timeout = 3.0

    def __init__(self, backend):
        self._backend = backend
        self._thread_loop = self._backend._thread_loop
        self.logger = self._backend.logger
        self._devices = OrderedDict()
        self._device_queue = queue.Queue()

        self.userdata = ctypes.c_void_p()
        self.discovery = None
        self._thread_loop.register_cleanup(self._destroy)

    @property
    def discovery_name(self):
        return self.__class__.__name__

    @callback_decorator()
    def _do_start(self):
        if self.discovery is not None:
            self.logger.error(f"{self.discovery_name}: already running")
            return True
        self.discovery = self._create_discovery()
        if self.discovery is None:
            self.logger.error(
                f"{self.discovery_name}: Failed to create discovery object"
            )
            return False
        self.logger.debug(
            f"{self.discovery_name}: Net discovery object has been created"
        )
        res = self._start_discovery()
        if res != 0:
            self.stop()
            self.logger.error(f"{self.discovery_name}: arsdk_discovery_start: {res}")
            return False
        self.logger.debug(f"{self.discovery_name}: Net discovery has been started")
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
        f = self._thread_loop.run_async(self._do_start)
        try:
            return f.result_or_cancel(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            self.logger.warning(f"{self.discovery_name}: Discovery start timedout")
            return False

    def stop(self):
        f = self._thread_loop.run_async(self._do_stop)
        try:
            return f.result_or_cancel(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            self.logger.warning(f"{self.discovery_name}: Discovery stop timedout")
            return False
        finally:
            self._devices = OrderedDict()
            self._device_queue = queue.Queue()

    @callback_decorator()
    def _do_stop(self):
        ret = True
        self._backend.remove_device_handler(self)

        if self.discovery is None:
            self.logger.debug(
                f"{self.discovery_name}: No discovery instance to be stopped"
            )
            return True

        # stop currently running discovery
        res = self._stop_discovery()
        if res != 0:
            self.logger.error(
                f"{self.discovery_name}: Error while stopping discovery: {res}"
            )
            ret = False
        else:
            self.logger.debug(f"{self.discovery_name}: Discovery has been stopped")

        # then, destroy it
        res = self._destroy_discovery()
        if res != 0:
            ret = False
            self.logger.error(
                f"{self.discovery_name}: Error while destroying discovery object: {res}"
            )
        else:
            self.logger.debug(
                f"{self.discovery_name}: Discovery object has been destroyed"
            )

        self.discovery = None
        return ret

    def _device_added_cb(self, arsdk_device, _user_data):
        """
        Called when a new device is detected.
        Detected devices depends on discovery parameters
        """
        device = DeviceInfo.from_arsdk_device(self._backend, arsdk_device)
        self.logger.info(
            f"{self.discovery_name}: New device has been detected: '{device.name}'"
        )
        self._devices[device.name] = device
        self._device_queue.put_nowait(device)

    def _device_removed_cb(self, arsdk_device, _user_data):
        """
        Called when a device disappear from the discovery search
        """
        device = DeviceInfo.from_arsdk_device(self._backend, arsdk_device)
        self.logger.info(
            f"{self.discovery_name}: Device '{device.name}' has been removed"
        )
        name = device.name
        if name == "__all__":
            device_names = list(self._devices.keys())
        elif name not in self._devices:
            self.logger.error(
                f"{self.discovery_name}: Error while removing device from discovery: "
                f"{name} is an unknown device"
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

    async def async_devices(self, timeout=None):
        if not self.start():
            return
        if timeout is None:
            timeout = self.timeout
        deadline = time.time() + timeout
        while deadline > time.time():
            try:
                yield self._device_queue.get_nowait()
            except queue.Empty:
                await self._thread_loop.asleep(0.005)

    async def async_get_device_count(self, max_count, timeout=None):
        devices = []
        if timeout is None:
            timeout = self.timeout
        if max_count <= 0:
            return devices
        count = 0
        async for device in self.async_devices(timeout=timeout):
            devices.append(device)
            count += 1
            if count == max_count:
                break
        return devices

    async def async_get_device(self, timeout=None):
        if timeout is None:
            timeout = self.timeout
        async for device in self.async_devices(timeout=timeout):
            return device
        return None

    def get_device_count(self, max_count, timeout=None):
        if timeout is None:
            timeout = self.timeout
        t = self._thread_loop.run_async(
            self.async_get_device_count, max_count, timeout=timeout
        )
        try:
            return t.result_or_cancel(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return None

    def get_device(self, timeout=None):
        if timeout is None:
            timeout = self.timeout
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
            self.logger.error(f"arsdk_discovery_net_new: {res}")
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
            self._raw_devices.append(DeviceInfo(*args, **kwds))
        else:
            self._raw_devices.extend(devices)
            if args or kwds:
                self._raw_devices.append(DeviceInfo(*args, **kwds))

        id_ = str(id(self))[:7]
        for i, device in enumerate(self._raw_devices):
            # arsdk will refuse to discover the same device twice
            # so we need to have unique serial for each "discovered" drone
            device.serial = f"UNKNOWN_{i:02d}_{id_}"

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
            raise RuntimeError(f"Unable to create raw discovery:{res}")
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
                sock.settimeout(self.timeout)
                try:
                    res = sock.connect_ex((device.ip_addr, device.port))
                except (socket.error, OSError):
                    self.logger.debug(
                        f"{self.discovery_name}: {device.ip_addr} is unreachable"
                    )
                    return
                if res != 0:
                    self.logger.debug(
                        f"{self.discovery_name}: {device.ip_addr}:{device.port} is"
                        " closed"
                    )
                    return
        # add this device to the "discovered" devices
        f = self._thread_loop.run_async(self._do_add_device, device)
        try:
            f.result_or_cancel(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            self.logger.error(
                f"{self.discovery_name}: timedout for {device.ip_addr}:{device.port}"
            )

    @callback_decorator()
    def _do_add_device(self, device):
        res = od.arsdk_discovery_add_device(
            self.discovery, device.as_arsdk_discovery_device_info()
        )
        if res != 0:
            self.logger.error(
                f"{self.discovery_name}: arsdk_discovery_add_device {res}"
            )
        else:
            self.logger.debug(
                f"{self.discovery_name}: Device '{device.name}'/{device.ip_addr}"
                " manually added to raw discovery"
            )


if __name__ == "__main__":

    backend = CtrlBackend()

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
