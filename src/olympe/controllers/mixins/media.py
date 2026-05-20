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
from typing import Optional

from olympe.concurrent.sync import Event
from olympe.features.media import Media
from .ipproxy import IpProxy
from .protocols import MixinBase


class MediaControllerMixin:
    def __init__(self: MixinBase, *args, media: Optional[Media] = None, **kwds):
        super().__init__(*args, **kwds)
        self._is_media_custom = False
        self._autoconnect_task = None
        if media:
            self._is_media_custom = True
            self._media = media

        # If a media is provided, thus is_media_custom is set to True while instancing a
        # controller object, and the controller won't create one itself
        if not self._is_media_custom:
            # Media has to use a common session shared between all the mixin classes built
            # through a ControllerBase. It enables to share a single proxy between Media and
            # Mission. Since HTTP TCP connections are limited on the drone, it is welcome to
            # share a common TCP connection pool.
            self._media = Media(
                name=self._name,
                device_name=self._device_name,
                scheduler=self._scheduler,
                session=self.http_session,
            )

    @property
    def media(self: MixinBase) -> Media:
        return self._media

    @property
    def is_media_custom(self) -> bool:
        return self._is_media_custom

    def destroy(self: MixinBase):
        if self._media is not None:
            self._media.destroy()
        super().destroy()

    def _reset_instance(self):
        if self._media is not None:
            self._media._reset_state()
        else:
            # called from __init__ before attribute definition
            pass
        super()._reset_instance()

    async def _autoconnect(self: MixinBase):
        await self._media._adisconnect()
        if self._media._loop.running:
            if await self._media._aconnect():
                # when _autoconnect is running, self._autoconnect_task has necessarily
                # returned of the run_async called on _autoconnect
                assert self._autoconnect_task
                self._autoconnect_task.cancel()
                self._autoconnect_task = None

    def on_http_proxy_created(self: MixinBase, proxy: IpProxy):
        super().on_http_proxy_created(proxy)

        # Once the proxy is created, hostname for media API urls is provided
        media_hostname = f"{proxy.address}:{proxy.port}"
        self._media.set_hostname(media_hostname)
        # Wake up media async connection now that the hostname is set
        Event.release_event(self._media.get_set_hostname_event())

        # self._is_media_custom is let to False when the user won't connect Media oneself
        if not self._is_media_custom:
            # Assert the internal controller has created its own Media instance
            assert self._media
            # It is necessary to automatically connect to a Media instance if it is
            # not user-custom
            self._autoconnect_task = self._media._loop.run_async(self._autoconnect)

    def on_http_proxy_destroyed(self: MixinBase):
        if not self._is_media_custom and self._autoconnect_task is not None:
            self._autoconnect_task.cancel()
            self._autoconnect_task = None
        if self._media:
            self._media.disconnect()
        super().on_http_proxy_destroyed()
        # Once the proxy is destroyed, all Media API urls are reset
        self._media.set_hostname(None)
