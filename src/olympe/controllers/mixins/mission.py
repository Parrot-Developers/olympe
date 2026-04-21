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

from olympe.features.mission import MissionController
from .protocols import MixinBase

if TYPE_CHECKING:
    from .ipproxy import IpProxy


class MissionControllerMixin:
    def __init__(self: MixinBase, ip_addr, *args, name=None, **kwds):
        super().__init__(*args, ip_addr=ip_addr, name=name, **kwds)  # type: ignore[call-arg]
        # MissionController has to use a common session shared between all the mixin
        # classes built through a ControllerBase. It enables to share a single proxy.
        # Since HTTP TCP connections are limited on the drone,
        # it is welcome to share a common TCP connection pool.
        self._mission = MissionController(
            self, ip_addr, name=name, session=self.http_session
        )

        self.register_proxy_created_hook(self._on_mission_proxy_created)
        self.register_proxy_destroyed_hook(self._on_mission_proxy_destroyed)

    @property
    def mission(self) -> MissionController:
        return self._mission

    def set_device_name(self: MixinBase, device_name: str) -> None:
        super().set_device_name(device_name)
        self._mission.set_device_name(device_name)

    def _on_mission_proxy_created(self, proxy: "IpProxy") -> None:
        # Once the proxy is created, set all Mission API urls
        self._mission.set_hostname(f"{proxy.address}:{proxy.port}")

    def _on_mission_proxy_destroyed(self) -> None:
        # Once the proxy is destroyed, all Mission API urls are reset
        self._mission.set_hostname(None)

    def destroy(self: MixinBase) -> None:
        self._mission.destroy()
        # Passthrough to let the upper mixins destroy as well
        super().destroy()
