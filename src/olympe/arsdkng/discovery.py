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
import logging
import olympe_deps as od
import queue
import time
import typing

from . import DeviceInfo, DEVICE_TYPE_LIST
from .backend import CtrlBackend, CtrlBackendNet, CtrlBackendMuxIp, DeviceHandler
from olympe.concurrent import Future, Loop, TimeoutError, CancelledError
from olympe.networking import TcpClient
from abc import ABC, abstractmethod
from collections import OrderedDict
from olympe.utils import callback_decorator


class Discovery(ABC, DeviceHandler):

    timeout = 3.0

    def __init__(self, *, timeout: typing.Optional[float] = None):
        self._backend: CtrlBackend
        self._thread_loop: Loop
        self.logger: logging.Logger
        self._devices: typing.Dict[str, DeviceInfo] = OrderedDict()
        self._device_queue: "queue.Queue[DeviceInfo]" = queue.Queue()

        self.userdata = ctypes.c_void_p()
        self.discovery = None
        if timeout is None:
            timeout = Discovery.timeout
        self.timeout: float = timeout
        self.deadline: float = 0.0

    @property
    def discovery_name(self) -> str:
        return self.__class__.__name__

    async def _do_start(self) -> bool:
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
            await self.async_stop()
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

    def start(self) -> bool:
        f = self.async_start()
        try:
            return f.result_or_cancel(timeout=self.timeout)
        except TimeoutError:
            self.logger.warning(f"{self.discovery_name}: Discovery start timedout")
            return False

    def stop(self) -> bool:
        f = self.async_stop()
        try:
            return f.result_or_cancel(timeout=self.timeout)
        except TimeoutError:
            self.logger.warning(f"{self.discovery_name}: Discovery stop timedout")
            return False
        finally:
            self._devices = OrderedDict()
            self._device_queue = queue.Queue()

    def async_start(self) -> "Future":
        self.deadline = time.time() + self.timeout
        return self._thread_loop.run_async(self._do_start)

    def async_stop(self) -> "Future":
        return self._thread_loop.run_async(self._do_stop)

    @callback_decorator()
    def _do_stop(self) -> bool:
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

    @callback_decorator()
    def _device_added_cb(
        self,
        arsdk_device: "od.POINTER_T[od.struct_arsdk_device]",
        _user_data: "od.POINTER_T[None]",
    ) -> None:
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

    @callback_decorator()
    def _device_removed_cb(
        self,
        arsdk_device: "od.POINTER_T[od.struct_arsdk_device]",
        _user_data: "od.POINTER_T[None]",
    ) -> None:
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
    def _destroy(self) -> None:
        self._thread_loop.unregister_cleanup(self._destroy, ignore_error=True)
        self._do_stop()

    def destroy(self) -> None:
        self._thread_loop.run_later(self._destroy)

    async def async_devices(self) -> typing.AsyncGenerator[DeviceInfo, None]:
        if not await self.async_start():
            self.logger.error("async_start false")
            return
        while self.deadline > time.time():
            try:
                yield self._device_queue.get_nowait()
            except queue.Empty:
                try:
                    await self._thread_loop.asleep(0.005)
                except CancelledError:
                    await self.async_stop()
                    raise

    async def async_get_device_count(self, max_count: int) -> typing.List[DeviceInfo]:
        devices: typing.List[DeviceInfo] = []
        if max_count <= 0:
            return devices
        count = 0
        async for device in self.async_devices():
            devices.append(device)
            count += 1
            if count == max_count:
                break
        return devices

    async def async_get_device(self) -> typing.Optional[DeviceInfo]:
        async for device in self.async_devices():
            return device
        return None

    def get_device_count(
        self, max_count: int
    ) -> typing.Optional[typing.List[DeviceInfo]]:
        t = self._thread_loop.run_async(self.async_get_device_count, max_count)
        try:
            return t.result_or_cancel(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            return None

    def get_device(self) -> typing.Optional[DeviceInfo]:
        devices = self.get_device_count(max_count=1)
        if not devices:
            return None
        else:
            return devices[0]


class DiscoveryNet(Discovery):
    def __init__(
        self,
        backend: CtrlBackendNet,
        *,
        ip_addr: str,
        device_types: typing.Optional[typing.List[int]] = None,
        timeout: typing.Optional[float] = None,
        **kwds,
    ):
        self._backend: CtrlBackendNet = backend
        self._thread_loop = self._backend._thread_loop
        self._thread_loop.register_cleanup(self._destroy)
        self.logger = self._backend.logger
        super().__init__(timeout=timeout)
        if device_types is None:
            device_types = DEVICE_TYPE_LIST
        self._device_types = device_types
        ctypes_device_type_list = (ctypes.c_int * len(device_types))(*device_types)
        self.discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(ctypes_device_type_list, od.POINTER_T(od.arsdk_device_type)),
            len(ctypes_device_type_list),
        )
        self.ip_addr = ip_addr

    def _create_discovery(self) -> "od.POINTER_T[od.struct_arsdk_discovery_net]":
        """
        Start net discovery in order to detect devices
        """
        discovery = od.POINTER_T(od.struct_arsdk_discovery_net)()

        res = od.arsdk_discovery_net_new(
            self._backend._info.arsdk_ctrl,
            self._backend._info.backend,
            ctypes.pointer(self.discovery_cfg),
            od.char_pointer_cast(self.ip_addr),
            ctypes.byref(discovery),
        )
        if res != 0:
            self.logger.error(f"arsdk_discovery_net_new: {res}")
            return None
        return discovery

    def _start_discovery(self) -> None:
        return od.arsdk_discovery_net_start(self.discovery)

    def _stop_discovery(self) -> None:
        return od.arsdk_discovery_net_stop(self.discovery)

    def _destroy_discovery(self) -> None:
        return od.arsdk_discovery_net_destroy(self.discovery)


class DiscoveryNetRaw(Discovery):
    """
    NetRaw discovery corresponds to a net discovery without any active
    method to search for devices.
    That means that this discovery type only works when manually
    adding a device.

    This method should be considered as a fallback.
    """

    def __init__(
        self,
        backend: CtrlBackendNet,
        *,
        ip_addr: str,
        check_port: typing.Optional[bool] = True,
        timeout: typing.Optional[float] = None,
        **kwds,
    ):
        self._backend: CtrlBackendNet = backend
        self._thread_loop = self._backend._thread_loop
        self._thread_loop.register_cleanup(self._destroy)
        self.logger = self._backend.logger
        super().__init__(timeout=timeout)
        self._check_port = check_port
        devices = kwds.pop("devices", None)
        self._raw_devices = []
        if not devices:
            self._raw_devices.append(DeviceInfo(ip_addr, **kwds))
        else:
            self._raw_devices.extend(devices)
            if kwds:
                self._raw_devices.append(DeviceInfo(ip_addr, **kwds))

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

    async def _do_start(self) -> bool:
        if not await super()._do_start():
            return False
        for device in self._raw_devices:
            await self._add_device(device)
        return True

    def _create_discovery(self) -> "od.POINTER_T[od.struct_arsdk_discovery]":
        discovery = od.POINTER_T(od.struct_arsdk_discovery)()

        backendparent = od.arsdkctrl_backend_net_get_parent(self._backend._info.backend)

        res = od.arsdk_discovery_new(
            b"netraw",
            backendparent,
            self._backend._info.arsdk_ctrl,
            ctypes.byref(discovery),
        )
        if res != 0:
            raise RuntimeError(f"Unable to create raw discovery:{res}")
        return discovery

    def _start_discovery(self) -> int:
        return od.arsdk_discovery_start(self.discovery)

    def _stop_discovery(self) -> int:
        return od.arsdk_discovery_stop(self.discovery)

    def _destroy_discovery(self) -> int:
        return od.arsdk_discovery_destroy(self.discovery)

    async def _add_device(self, device: DeviceInfo) -> None:
        if self._check_port:
            timeout = self.deadline - time.time()
            client = TcpClient(self._thread_loop)
            try:
                if not await client.aconnect(
                    device.ip_addr, device.port, timeout=timeout
                ):
                    return
            finally:
                await client.adestroy()
        # add this device to the "discovered" devices
        self._do_add_device(device)

    @callback_decorator()
    def _do_add_device(self, device: DeviceInfo) -> None:
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


class DiscoveryMux(Discovery):
    def __init__(
        self,
        backend: CtrlBackendMuxIp,
        device_types: typing.Optional[typing.List[int]] = None,
        timeout: typing.Optional[float] = None,
        **kwds,
    ):
        self._backend: CtrlBackendMuxIp = backend
        self._thread_loop = self._backend._thread_loop
        self._thread_loop.register_cleanup(self._destroy)
        self.logger = self._backend.logger
        super().__init__(timeout=timeout)
        if device_types is None:
            device_types = DEVICE_TYPE_LIST
        self._device_types = device_types
        ctypes_device_type_list = (ctypes.c_int * len(device_types))(*device_types)
        self.discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(ctypes_device_type_list, od.POINTER_T(od.arsdk_device_type)),
            len(ctypes_device_type_list),
        )

    def _create_discovery(self) -> "od.POINTER_T[od.struct_arsdk_discovery_mux]":
        """
        Start mux discovery in order to detect devices
        """
        discovery = od.POINTER_T(od.struct_arsdk_discovery_mux)()

        res = od.arsdk_discovery_mux_new(
            self._backend._info.arsdk_ctrl,
            self._backend._info.backend,
            ctypes.pointer(self.discovery_cfg),
            self._backend._info.mux_ctx,
            ctypes.byref(discovery),
        )
        if res != 0:
            self.logger.error(f"arsdk_discovery_mux_new: {res}")
            return None
        return discovery

    def _start_discovery(self) -> int:
        return od.arsdk_discovery_mux_start(self.discovery)

    def _stop_discovery(self) -> int:
        return od.arsdk_discovery_mux_stop(self.discovery)

    def _destroy_discovery(self) -> int:
        return od.arsdk_discovery_mux_destroy(self.discovery)


if __name__ == "__main__":

    backend = CtrlBackendNet()

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
