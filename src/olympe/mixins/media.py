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


class MediaControllerMixin:
    def __init__(self, *args, media_autoconnect=True, media_port=80, **kwds):
        self._media = None
        super().__init__(*args, **kwds)
        self._media_autoconnect = media_autoconnect
        self._media_port = media_port
        media_hostname = self._ip_addr_str + f":{self._media_port}"
        self._media = Media(
            name=self._name,
            hostname=media_hostname,
            device_name=self._device_name,
            scheduler=self._scheduler
        )

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
    def _on_connection_state_changed(self, message_event, _):
        super()._on_connection_state_changed(message_event, _)
        # Handle drone connection_state events
        if self._is_skyctrl:
            # The SkyController forwards port tcp/180 to the drone tcp/80
            # for the web API endpoints
            if self._media_autoconnect:
                media_hostname = self._ip_addr_str + ":180"
                self._media.set_hostname(media_hostname)
                self._media.async_disconnect().then(lambda f: self._media.async_connect())

    def _reset_instance(self):
        """
        Reset instance attributes
        """
        if self._media is not None:
            self._media._reset_state()
        super()._reset_instance()

    @property
    def media(self):
        return self._media

    @property
    def media_autoconnect(self):
        return self._media_autoconnect
