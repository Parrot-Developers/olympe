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


import olympe_deps as od
from .arsdkng.cmd_itf import Connect, Disconnect, Connected, Disconnected  # noqa
from .arsdkng.controller import ControllerBase
from .mixins.streaming import StreamingControllerMixin
from .mixins.media import MediaControllerMixin
from .mixins.mission import MissionControllerMixin
from .utils import callback_decorator


class ControllerBase(
        StreamingControllerMixin,
        MissionControllerMixin,
        MediaControllerMixin,
        ControllerBase):
    pass


class Drone(ControllerBase):
    pass


class SkyController(ControllerBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, is_skyctrl=True, **kwds)

    @callback_decorator()
    def _link_status_cb(
            self,
            _arsdk_device,
            _arsdk_device_info,
            status,
            _user_data):
        """
         Notify link status. At connection completion, it is assumed to be
         initially OK. If called with KO, user is responsible to take action.
         It can either wait for link to become OK again or disconnect
         immediately. In this case, call arsdk_device_disconnect and the
         'disconnected' callback will be called.
        """
        self.logger.info(f"Link status: {status}")
        if status == od.ARSDK_LINK_STATUS_KO:
            # FIXME: Link status KO seems to be an unrecoverable
            # random error with a SkyController when `drone_manager.forget`
            # is sent to the SkyController
            self.logger.error("Link status KO")
