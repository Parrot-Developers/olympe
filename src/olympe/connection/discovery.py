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
import queue
import time

from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Dict, List, AsyncGenerator, Optional

import olympe_deps as od

from olympe.concurrent import Loop, TimeoutError
from olympe.utils import callback_decorator
from olympe.utils.types import PointerType
from . import DeviceInfo, DEVICE_TYPE_LIST, _DISCOVERY_PORT
from .backend import CtrlBackend, CtrlBackendNet, CtrlBackendMuxIp, DeviceHandler
from .networking import TcpClient

DISCOVERY_TIMEOUT = 3.0


class Discovery(ABC, DeviceHandler):
    def __init__(
        self,
        *,
        discovery_port: int = _DISCOVERY_PORT,
        timeout: float = DISCOVERY_TIMEOUT,
    ):
        self._backend: CtrlBackend
        self.logger: logging.Logger
        self._loop: Loop

        self._devices: Dict[str, DeviceInfo] = OrderedDict()
        self._device_queue: queue.Queue[DeviceInfo] = queue.Queue()

        self._discovery = None
        self._discovery_port = discovery_port

        self.timeout: float = timeout
        self._deadline: float = 0.0

        self._loop.register_cleanup(self._cleanup)

    @property
    def discovery_name(self) -> str:
        return self.__class__.__name__

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

    async def _astart(self) -> bool:
        if self._discovery is not None:
            self.logger.warning(
                f"A discovery '{self.discovery_name}' is already running. Aborting this one..."
            )
            return True

        self._deadline = time.time() + self.timeout

        try:
            self._discovery = self._create_discovery()
        except RuntimeError:
            raise

        self.logger.info(
            f"Discovery '{self.discovery_name}' has successfully been created"
        )

        res = self._start_discovery()
        if res != 0:
            self.logger.error(
                f"Discovery '{self.discovery_name}' failed to start ({res}). Discovery is being stopped..."
            )
            await self._astop()
            return False

        self.logger.info(
            f"Discovery '{self.discovery_name}' has successfully been started"
        )

        self._backend.add_device_handler(self)

        return True

    def start(self) -> bool:
        discovery_start_future = self._loop.run_async(self._astart)

        try:
            res = discovery_start_future.result_or_cancel(timeout=self.timeout)
        except TimeoutError:
            self.logger.warning(f"Discovery '{self.discovery_name}' start timedout")
            return False
        except RuntimeError:
            self.logger.error(f"Discovery '{self.discovery_name}' creation has failed")
            return False
        return res

    async def _astop(self) -> bool:
        if self._discovery is None:
            self.logger.warning(
                "No discovery instance is to be stopped. Aborting this one..."
            )
            return True

        res = self._stop_discovery()
        if res != 0:
            self.logger.error(
                f"Discovery '{self.discovery_name}' failed to stop ({res})"
            )
        else:
            self.logger.info(
                f"Discovery '{self.discovery_name}' has successfully been stopped"
            )

        res = self._destroy_discovery()
        if res != 0:
            self.logger.error(
                f"Discovery '{self.discovery_name}' failed to destroy ({res})"
            )
        else:
            self.logger.info(
                f"Discovery '{self.discovery_name}' has successfully been destroyed"
            )

        self._backend.remove_device_handler(self)
        self._discovery = None

        return True if res == 0 else False

    def stop(self) -> bool:
        discovery_stop_future = self._loop.run_async(self._astop)

        try:
            res = discovery_stop_future.result_or_cancel(timeout=self.timeout)
        except TimeoutError:
            self.logger.warning(f"Discovery '{self.discovery_name}' stop timedout")
            return False
        finally:
            self._devices = OrderedDict()
            self._device_queue = queue.Queue()
        return res

    @callback_decorator()
    def _device_added_cb(
        self,
        arsdk_device: PointerType[od.struct_arsdk_device],
        user_data: ctypes.c_void_p,
    ) -> None:
        """
        Called when a new device is detected.
        Detected devices depends on discovery parameters
        """
        device = DeviceInfo.from_arsdk_device(
            self._backend, arsdk_device, self._discovery_port
        )

        self._devices[device.name] = device
        self._device_queue.put_nowait(device)
        self.logger.info(
            f"Discovery '{self.discovery_name}' has detected a new device '{device.name}'"
        )

    @callback_decorator()
    def _device_removed_cb(
        self,
        arsdk_device: PointerType[od.struct_arsdk_device],
        user_data: ctypes.c_void_p,
    ) -> None:
        """
        Called when a device disappears from the discovery search
        """
        device = DeviceInfo.from_arsdk_device(
            self._backend, arsdk_device, self._discovery_port
        )
        name = device.name
        if name == "__all__":
            device_names = list(self._devices.keys())
        elif name not in self._devices:
            self.logger.error(
                f"Discovery '{self.discovery_name}' failed while removing device "
                f"{name}: unknown device"
            )
            device_names = []
        else:
            device_names = [name]

        for name in device_names:
            del self._devices[name]

        self.logger.info(
            f"Discovery '{self.discovery_name}' has removed device(s) '{device.name}'"
        )

    @callback_decorator()
    def _cleanup(self) -> None:
        if self._discovery is not None:
            self.stop()

    async def _device_generator(self) -> AsyncGenerator[DeviceInfo, None]:
        if not await self._astart():
            self.logger.error(
                "Failed to retrieve devices from a discovery that did not start"
            )
            return
        while time.time() < self._deadline:
            try:
                yield self._device_queue.get_nowait()
            except queue.Empty:
                try:
                    await self._loop.asleep(0.005)
                # if CancelledError is not caught here, coroutine would be interrupted
                # as soon as the task would be cancelled, if it was. self._astop wouldn't
                # be called, which would result in memory leak or a non-ending discovery.
                # This way, the waiting coroutine that is cancelled will raise an
                # CancelledError, caught here and processed properly
                except concurrent.futures.CancelledError:
                    await self._astop()
                    raise

    async def _async_get_devices(self) -> AsyncGenerator[DeviceInfo, None]:
        async for device in self._device_generator():
            yield device

    async def async_get_device(self) -> Optional[DeviceInfo]:
        async for device in self._device_generator():
            return device
        return None

    async def async_get_device_count(self, max_count: int) -> List[DeviceInfo]:
        devices: List[DeviceInfo] = []
        if max_count <= 0:
            return devices
        count = 0
        async for device in self._async_get_devices():
            devices.append(device)
            count += 1
            if count == max_count:
                break
        return devices

    def get_device_count(self, max_count: int) -> Optional[List[DeviceInfo]]:
        t = self._loop.run_async(self.async_get_device_count, max_count)
        try:
            return t.result_or_cancel(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            return None

    def get_device(self) -> Optional[DeviceInfo]:
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
        discovery_port: int = _DISCOVERY_PORT,
        device_types: List[int] = DEVICE_TYPE_LIST,
        timeout: float = DISCOVERY_TIMEOUT,
        **_,
    ):
        self._backend: CtrlBackendNet = backend
        self.logger = self._backend.logger
        self._loop = self._backend._loop
        super().__init__(discovery_port=discovery_port, timeout=timeout)

        self._ip_addr = ip_addr

        self._device_types = device_types
        ctypes_device_type_list = (ctypes.c_int * len(device_types))(*device_types)
        self.discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(ctypes_device_type_list, od.POINTER_T(od.arsdk_device_type)),
            len(ctypes_device_type_list),
        )

    def _create_discovery(self) -> PointerType[od.struct_arsdk_discovery_net]:
        """
        Start net discovery in order to detect devices
        """
        discovery = od.POINTER_T(od.struct_arsdk_discovery_net)()

        res = od.arsdk_discovery_net_new_with_port(
            self._backend._info.arsdk_ctrl,
            self._backend._info.backend,
            ctypes.pointer(self.discovery_cfg),
            od.char_pointer_cast(self._ip_addr),
            self._discovery_port,
            ctypes.byref(discovery),
        )
        if res != 0:
            raise RuntimeError(
                f"Fail to create a NET discovery '{self.discovery_name}' for device '{self._ip_addr}' (arsdk_discovery_net_new: {res})"
            )
        return discovery

    def _start_discovery(self) -> int:
        return od.arsdk_discovery_net_start(self._discovery)

    def _stop_discovery(self) -> int:
        return od.arsdk_discovery_net_stop(self._discovery)

    def _destroy_discovery(self) -> int:
        return od.arsdk_discovery_net_destroy(self._discovery)


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
        check_port: Optional[bool] = True,
        devices: Optional[List[DeviceInfo]] = [],
        timeout: float = DISCOVERY_TIMEOUT,
        **kwds,
    ):
        self._backend: CtrlBackendNet = backend
        self.logger = self._backend.logger
        self._loop = self._backend._loop
        super().__init__(timeout=timeout)

        self._check_port = check_port
        self._raw_devices: List[DeviceInfo] = []
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

    def _create_discovery(self) -> PointerType[od.struct_arsdk_discovery]:
        discovery = od.POINTER_T(od.struct_arsdk_discovery)()

        backendparent = od.arsdkctrl_backend_net_get_parent(self._backend._info.backend)

        res = od.arsdk_discovery_new(
            b"netraw",
            backendparent,
            self._backend._info.arsdk_ctrl,
            ctypes.byref(discovery),
        )
        if res != 0:
            raise RuntimeError(
                f"Fail to create a RAW discovery '{self.discovery_name}' (arsdk_discovery_new: {res})"
            )
        return discovery

    def _start_discovery(self) -> int:
        return od.arsdk_discovery_start(self._discovery)

    def _stop_discovery(self) -> int:
        return od.arsdk_discovery_stop(self._discovery)

    def _destroy_discovery(self) -> int:
        return od.arsdk_discovery_destroy(self._discovery)

    def _do_add_device(self, device: DeviceInfo) -> None:
        res = od.arsdk_discovery_add_device(
            self._discovery, device.as_arsdk_discovery_device_info()
        )
        if res != 0:
            self.logger.error(
                f"Fail to create a RAW discovery '{self.discovery_name}' (arsdk_discovery_add_device: {res})"
            )
        else:
            self.logger.debug(
                f"Device '{device.name}'/{device.ip_addr}"
                f" manually added to RAW discovery '{self.discovery_name}'"
            )

    async def _add_device(self, device: DeviceInfo) -> None:
        if self._check_port:
            client = TcpClient(self._loop)
            try:
                if not await client.aconnect(device.ip_addr, device.port):
                    return
            finally:
                client.destroy()
        # add this device to the "discovered" devices
        self._do_add_device(device)

    async def _astart(self) -> bool:
        if not await super()._astart():
            return False
        for device in self._raw_devices:
            await self._add_device(device)
        return True


class DiscoveryMux(Discovery):
    def __init__(
        self,
        backend: CtrlBackendMuxIp,
        *,
        device_types: List[int] = DEVICE_TYPE_LIST,
        timeout: float = DISCOVERY_TIMEOUT,
        **kwds,
    ):
        self._backend: CtrlBackendMuxIp = backend
        self.logger = self._backend.logger
        self._loop = self._backend._loop
        super().__init__(timeout=timeout)

        self._device_types = device_types
        ctypes_device_type_list = (ctypes.c_int * len(device_types))(*device_types)
        self.discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(ctypes_device_type_list, od.POINTER_T(od.arsdk_device_type)),
            len(ctypes_device_type_list),
        )

    def _create_discovery(self) -> PointerType[od.struct_arsdk_discovery_mux]:
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
            raise RuntimeError(
                f"Fail to create a MUX discovery '{self.discovery_name}' (arsdk_discovery_mux_new: {res})"
            )
        return discovery

    def _start_discovery(self) -> int:
        return od.arsdk_discovery_mux_start(self._discovery)

    def _stop_discovery(self) -> int:
        return od.arsdk_discovery_mux_stop(self._discovery)

    def _destroy_discovery(self) -> int:
        return od.arsdk_discovery_mux_destroy(self._discovery)


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
