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

from typing import TYPE_CHECKING

from olympe.concurrent import Loop
from olympe.connection.http import Session
from olympe.utils import callback_decorator


if TYPE_CHECKING:
    from .ipproxy import IpProxy


# This class is used to handle the proxy between the mixins classes that inherit from it
class HttpControllerMixin:
    def __init__(self, *args, http_port: int = 80, **kwds):
        super().__init__(*args, **kwds)
        self._http_port = http_port
        self._http_loop = Loop(self.logger)
        self._http_loop.register_cleanup(self._destroy_http_proxy)
        # Only here a session is created, so that it is shared among the
        # other mixins classes.
        self._http_session = Session(self._http_loop)
        """Proxy through the skyctrl to access to the drone"""
        self._http_proxy = None

        self._http_loop.start()

    @property
    def http_session(self) -> Session:
        assert isinstance(self._http_session, Session)
        return self._http_session

    @property
    def http_proxy(self):
        return self._http_proxy

    async def _aconnected_cb(self, *args):
        # self._aconnected_cb from ControllerBase class is freed once the _on_connected
        # function resolves and then self._connection_state switches to "connected"
        await super()._aconnected_cb(*args)

        # Once connected to the drone, the HTTP proxy can be created
        if not self._is_skyctrl:
            self._http_loop.run_async(self._create_http_proxy)

    @callback_decorator()
    def _disconnected_cb(self, *args):
        super()._disconnected_cb(*args)
        # If the direct device is disconnected, the proxy is no longer unusable
        if self._http_proxy is not None:
            self._http_loop.run_async(self._destroy_http_proxy)

    @callback_decorator()
    def _on_skyctrl_connection_changed(self, connected):
        # The proxy needs to be created once the SkyController is newly connected to the
        # drone (if a connection has already been made before, without disconnection in
        # between, this newly connection request is ignored) and if the backup link mode
        # is not active
        # The proxy shall not be created twice everytime the drone sends a
        # devicemanager.Event.State(connected=devicemanager.ConnectionState.Connected)
        if (
            connected
            and not self._is_backup_link_active
            and not self._already_connected
        ):
            assert self._http_proxy is None
            # The proxy adjusts its hostname according to the device type, that is why
            # device_type has to be asserted not to be None
            assert self._device_type is not None
            self._http_loop.run_async(self._create_http_proxy)
        # When the connection to a skycontroller has dropped (disconnection or backup
        # link active), it is needed to stop all the connections, not to have zombies
        # connections. They would be recreated when requested
        elif not connected or self._is_backup_link_active:
            self._http_loop.run_async(self._destroy_http_proxy)
        # Call the parent function afterwards, because it updates self._already_connected
        # to True in case connected is True. However, we need to know if that is the first
        # reconnection since a disconnection for the first if case
        super()._on_skyctrl_connection_changed(connected)

    def on_http_proxy_created(self, proxy: "IpProxy"):
        pass

    async def _create_http_proxy(self):
        """
        Creates the http proxy to access the drone
        """
        self._http_proxy = await self.aopen_drone_tcp_proxy(self._http_port)
        # When a proxy is created, the other mixins may need to do some actions. Thus
        # the callback on_http_proxy_created
        self.on_http_proxy_created(self._http_proxy)

    def on_http_proxy_destroyed(self):
        pass

    async def _destroy_http_proxy(self):
        if self._http_proxy is None:
            return
        # When a proxy is destroyed, the other mixins may need to do some actions. Thus
        # the callback on_http_proxy_destroyed
        self.on_http_proxy_destroyed()
        # Close all session's connections
        await self._http_session.astop()
        self._http_proxy.close()
        self._http_proxy = None

    def destroy(self):
        super().destroy()
        self._http_loop.stop()
