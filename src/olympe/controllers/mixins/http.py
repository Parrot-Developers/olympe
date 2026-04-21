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

from typing import Callable, List, TYPE_CHECKING

from olympe.concurrent import Loop
from olympe.connection.http import Session
from olympe.utils import callback_decorator


if TYPE_CHECKING:
    from .ipproxy import IpProxy


class HttpControllerMixin:
    """
    Manages the HTTP session and the TCP proxy to the drone (or drone-behind-skyctrl).

    Other mixins register proxy lifecycle callbacks at init time via
    register_proxy_created_hook() / register_proxy_destroyed_hook().  This
    makes the call graph explicit and removes the risk of a mixin silently
    breaking the chain by forgetting super().
    """

    def __init__(self, *args, http_port: int = 80, **kwds):
        super().__init__(*args, **kwds)
        self._http_port = http_port
        self._http_loop = Loop(self.logger)
        self._http_loop.register_cleanup(self._destroy_http_proxy)
        # Single session shared across all mixins so they reuse the same TCP
        # connection pool (HTTP connections to the drone are limited).
        self._http_session = Session(self._http_loop)
        self._http_proxy = None
        # Populated at init time by the other mixins via register_proxy_*_hook().
        self._proxy_created_hooks: List[Callable] = []
        self._proxy_destroyed_hooks: List[Callable] = []

        self._http_loop.start()

    @property
    def http_session(self) -> Session:
        assert isinstance(self._http_session, Session)
        return self._http_session

    @property
    def http_proxy(self):
        return self._http_proxy

    def register_proxy_created_hook(self, fn: "Callable[[IpProxy], None]") -> None:
        """Register fn to be called with the new proxy when the HTTP proxy is created."""
        self._proxy_created_hooks.append(fn)

    def register_proxy_destroyed_hook(self, fn: "Callable[[], None]") -> None:
        """Register fn to be called (no arguments) when the HTTP proxy is destroyed."""
        self._proxy_destroyed_hooks.append(fn)

    async def _aconnected_cb(self, *args):
        # self._aconnected_cb from ControllerBase class is freed once the _on_connected
        # function resolves and then self._connection_state switches to "connected"
        await super()._aconnected_cb(*args)

        # Once connected to the drone, the HTTP proxy can be created
        if not self._is_skyctrl:
            self._http_loop.run_async(self._create_http_proxy)

    async def _adisconnect(self, *args, **kwds) -> bool:
        # Close all HTTP session connections before arsdk disconnects so that TLS
        # connections shut down cleanly (SSLZeroReturnError) instead of being
        # abruptly closed (SSLEOFError). This covers connections that exist independently
        # of the HTTP proxy (e.g. HTTPS to external Parrot servers via the mux tunnel).
        # _destroy_http_proxy also calls astop() but only if _http_proxy is not None.
        await self._http_loop.run_async(self._http_session.astop)
        if self._http_proxy is not None:
            await self._http_loop.run_async(self._destroy_http_proxy)
        return await super()._adisconnect(*args, **kwds)

    @callback_decorator()
    def _disconnected_cb(self, *args):
        super()._disconnected_cb(*args)
        # If the direct device is disconnected, the proxy is no longer usable
        # Fallback for device-initiated disconnects where _adisconnect was not called.
        if self._http_proxy is not None:
            self._http_loop.run_async(self._destroy_http_proxy)

    @callback_decorator()
    def _on_skyctrl_connection_changed(self, connected):
        # The proxy needs to be created once the SkyController is newly connected to the
        # drone (if a connection has already been made before, without disconnection in
        # between, this newly connection request is ignored) and if the backup link mode
        # is not active.
        # The proxy shall not be created twice every time the drone sends a
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
        # connections. They would be recreated when requested.
        elif not connected or self._is_backup_link_active:
            self._http_loop.run_async(self._destroy_http_proxy)
        # Call the parent function afterwards, because it updates self._already_connected
        # to True in case connected is True. However, we need to know if that is the first
        # reconnection since a disconnection for the first if case.
        super()._on_skyctrl_connection_changed(connected)

    async def _create_http_proxy(self):
        self._http_proxy = await self.aopen_drone_tcp_proxy(self._http_port)
        for hook in self._proxy_created_hooks:
            hook(self._http_proxy)

    async def _destroy_http_proxy(self):
        if self._http_proxy is None:
            return
        # Close all session's connections first, so that any pending websocket/HTTP request
        # fails with ConnectionError rather than TimeoutError
        await self._http_session.astop()
        # When a proxy is destroyed, the other mixins may need to do some actions
        for hook in self._proxy_destroyed_hooks:
            hook()

        self._http_proxy.close()
        self._http_proxy = None

    def destroy(self):
        self._http_loop.stop()
        super().destroy()
