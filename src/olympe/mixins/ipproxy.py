#  Copyright (C) 2023 Parrot Drones SAS
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
from olympe.utils import callback_decorator
from olympe.concurrent import Condition, Future
from olympe.arsdkng.controller import ControllerBase
from olympe.messages.drone_manager import connection_state
from olympe.controller import Connected
from typing import Optional


class IpProxy:
    """Drone Ip Proxy"""

    def __init__(self, controller: ControllerBase, device_type: int, remote_port: int):
        """
        Constructor

        :param controller: controller owning the proxy
        :param device_type: type of the device to access
        :param remote_port: port to access
        """
        self._controller = controller
        self._device_type = device_type
        self._remote_port = remote_port
        self._port = None
        self._address = None
        self._proxy = None
        self._resolved = False
        self._open_condition = Condition(self._controller._thread_loop)

    @property
    def port(self):
        """
        Port to connect

        :getter: Returns the port to connect
        :type: int
        """
        return self._port

    @property
    def address(self):
        """
        Address to connect

        :getter: Returns the address to connect
        :type: string
        """
        return self._address

    def close(self):
        """Closes the proxy"""
        self._controller._thread_loop.run_async(self._aclose)

    def _open(self):
        """Opens the proxy"""
        return self._controller._thread_loop.run_async(self._aopen)

    async def _aopen(self):
        """Opens the proxy"""
        self._proxy_cbs = od.struct_arsdk_device_tcp_proxy_cbs.bind(
            {
                "open": self._open_cb,
                "close": self._close_cb,
            }
        )

        proxy = od.POINTER_T(od.struct_arsdk_device_tcp_proxy)()
        res = od.arsdk_device_create_tcp_proxy(
            self._controller._device.arsdk_device,
            self._device_type,
            self._remote_port,
            self._proxy_cbs,
            ctypes.pointer(proxy),
        )

        if res != 0:
            raise RuntimeError(f"Error while opening proxy: {res}.")

        self._proxy = proxy
        self._controller._thread_loop.register_cleanup(self._cleanup)

        async with self._open_condition:
            await self._open_condition.wait()

    async def _cleanup(self):
        """
        Cleans the proxy
        """
        if self._proxy is not None:
           await self._aclose()

    async def _aclose(self):
        """Closes the proxy"""
        if self._proxy is None:
            return

        res = od.arsdk_device_destroy_tcp_proxy(self._proxy)
        if res != 0:
            raise RuntimeError(f"Error while closing proxy: {res}")

        self._proxy = None
        self._controller._thread_loop.unregister_cleanup(self._cleanup)

    @callback_decorator()
    def _open_cb(self, proxy, localport, user_data):
        """
        Called at the local socket opening.
        """

        address_native = od.arsdk_device_tcp_proxy_get_addr(self._proxy)
        self._address = od.string_cast(address_native)
        self._port = od.arsdk_device_tcp_proxy_get_port(self._proxy)

        self._controller._thread_loop.run_async(self._open_condition_notify)

    async def _open_condition_notify(self):
        """Notifies the opening condition"""
        async with self._open_condition:
            self._open_condition.notify_all()

    @callback_decorator()
    def _close_cb(self, proxy, userdata):
        """
        Called at the local socket closing.
        """

        # Do nothing
        # Either the resolution failed and the timeout will be triggered or
        # the proxy has already been opened and the local socket will notify, itself,
        # of its closure.
        pass


class IpProxyMixin:
    """
    Controller mixin providing the Ip proxy creation.
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

    def open_tcp_proxy(self, port: int, timeout: Optional[float] = None) -> IpProxy:
        """Opens a new drone tcp proxy

        :param port: port to access
        :param timeout: the timeout in seconds or None for infinite timeout (the
             default)

        :return: an :py:class:`IpProxy` object open to the drone
        """

        return self.fopen_tcp_proxy(port).result_or_cancel(timeout=timeout)

    def fopen_tcp_proxy(self, port: int) -> Future:
        """
        Retrives a future of :py:func:`open_tcp_proxy`

        :param port: port to access
        """

        return self._thread_loop.run_async(self.aopen_tcp_proxy, port)

    async def aopen_tcp_proxy(self, port: int) -> IpProxy:
        """Opens a new drone tcp proxy

        :param port: port to access

        :return: an :py:class:`IpProxy` object open to the drone

        Should run in the :py:class:`~olympe.arsdkng.controller.ControllerBase` backend loop
        """

        # Wait to be connected to a drone to get it model Id
        if self._is_skyctrl:
            await self(connection_state(state="connected"))
            drone_model = self.get_state(connection_state)["model"]
        else:
            if not self.connected:
                # The connected event is also triggered from the backend loop
                await self(Connected())
            drone_model = self._device_type

        proxy = IpProxy(self, drone_model, port)
        await proxy._open()
        return proxy
