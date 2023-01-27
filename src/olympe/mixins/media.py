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

from olympe.media import Media
from olympe.utils import callback_decorator
from olympe.enums import drone_manager as drone_manager_enums


class MediaControllerMixin:
    def __init__(
        self, *args, media_autoconnect: bool = True, media_port: int = 80, **kwds
    ):
        self._media = None
        super().__init__(*args, **kwds)
        self._media_autoconnect = media_autoconnect
        self._media_port = media_port
        self._media = Media(
            name=self._name, device_name=self._device_name, scheduler=self._scheduler
        )
        """Proxy through the skyctrl to access to the drone"""
        self._proxy = None

    def destroy(self):
        self._media.shutdown()
        super().destroy()

    @callback_decorator()
    def _connected_cb(self, *args):
        super()._connected_cb(*args)
        if not self._is_skyctrl and self._media_autoconnect:
            media_hostname = self._ip_addr_str + f":{self._media_port}"
            self._media.set_hostname(media_hostname)
            self._media.async_disconnect().then(lambda f: self._media.async_connect())

    @callback_decorator()
    def _disconnected_cb(self, *args):
        super()._disconnected_cb(*args)
        # If the direct device is disconnected, the proxy is no longer unusable
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None

    @callback_decorator()
    def _on_connection_state_changed(self, message_event, _):
        super()._on_connection_state_changed(message_event, _)
        # Handle drone connection_state events
        if self._is_skyctrl:
            if (
                message_event._args["state"]
                == drone_manager_enums.connection_state.connected
            ):
                if self._proxy is not None:
                    self._proxy.close()

                self._thread_loop.run_async(self._create_proxy)
            elif self._proxy is not None:
                self._proxy.close()
                self._proxy = None

    async def _create_proxy(self):
        """
        Creates the proxy to access to the drone
        """
        self._proxy = await self.aopen_tcp_proxy(self._media_port)

        media_hostname = f"{self._proxy.address}:{self._proxy.port}"
        self._media.set_hostname(media_hostname)

        if self._media_autoconnect:
            self._media.async_disconnect().then(lambda f: self._media.async_connect())

    def _reset_instance(self):
        """
        Reset instance attributes
        """
        if self._media is not None:
            self._media._reset_state()
        super()._reset_instance()

    @property
    def media(self) -> Media:
        return self._media

    @property
    def media_autoconnect(self) -> bool:
        return self._media_autoconnect
