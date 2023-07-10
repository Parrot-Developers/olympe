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
from .arsdkng import DRONE_DEVICE_TYPE_LIST, SKYCTRL_DEVICE_TYPE_LIST
from .arsdkng.cmd_itf import Connect, Disconnect, Connected, Disconnected  # noqa
from .arsdkng.controller import ControllerBase as Controller
from .arsdkng.backend import BackendType
from .mixins.streaming import StreamingControllerMixin
from .mixins.media import MediaControllerMixin
from .mixins.mission import MissionControllerMixin
from .mixins.ipproxy import IpProxyMixin
from .mixins.cellular import CellularPairerMixin
from .utils import callback_decorator


class ControllerBase(
    IpProxyMixin,
    StreamingControllerMixin,
    MissionControllerMixin,
    MediaControllerMixin,
    Controller,
):
    pass


class Drone(ControllerBase):
    """
    Generic Parrot drone controller class

    This class should be able to connect to any Parrot ANAFI drone model when connected
    directly to the drone (not through a SkyController).

    For ANAFI Ai, this class is only usable when the "Direct Connection" mode is
    enabled on the drone.

    See :py:class:`~olympe.AnafiAi` and :py:class:`~olympe.SkyController4` for more
    information.
    """
    DEVICE_TYPES = DRONE_DEVICE_TYPE_LIST


class Anafi(Drone):
    """
    ANAFI controller class.

    This class should be used when you're trying to connect to an ANAFI drone
    directly and not through a SkyController.

    When connecting Olympe to an ANAFI through a SkyController 3. You must use
    the :py:class:`~olympe.SkyController3` class instead.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_ANAFI4K]


class AnafiUSA(Drone):
    """
    ANAFI USA controller class.

    This class should be used when you're trying to connect to an ANAFI USA drone
    directly and not through a SkyController.

    When connecting Olympe to an ANAFI USA through a SkyController USA or a
    SkyController 4 Black. You must use the :py:class:`~olympe.SkyControllerUSA`
    or :py:class:`~olympe.SkyController4Black` classes respectively.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_ANAFI_USA]


class AnafiAi(Drone):
    """
    ANAFI Ai controller class.

    This class is only usable when the "Direct Connection" mode is enabled.
    By default, ANAFI Ai is only reachable from Olympe through a SkyController 4.

    When connecting Olympe to an ANAFI Ai through a SkyController 4. You must use
    the :py:class:`~olympe.SkyController4` class instead.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_ANAFI_2]


class SkyControllerBase(ControllerBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, is_skyctrl=True, **kwds)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_device, _arsdk_device_info, status, _user_data):
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


class SkyControllerNet(SkyControllerBase):
    def __init__(self, *args, backend: BackendType = BackendType.Net, **kwds):
        super().__init__(*args, backend=backend, **kwds)


class SkyControllerMux(SkyControllerBase):
    def __init__(self, *args, backend: BackendType = BackendType.MuxIp, **kwds):
        super().__init__(*args, backend=backend, **kwds)


class SkyControllerMuxCellular(CellularPairerMixin, SkyControllerMux):
    pass


class SkyController3(SkyControllerNet):
    """
    SkyController 3 controller class

    This class should be used to connect to ANAFI drones through a SkyController 3.
    Use the :py:class:`~olympe.Anafi` class instead when connecting to a drone
    directly.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_3]


class SkyControllerUSA(SkyControllerNet):
    """
    SkyController USA controller class

    This class should be used to connect to ANAFI USA drones through a SkyController USA.
    Use the :py:class:`~olympe.AnafiUSA` class instead when connecting to a drone
    directly.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_UA]


class SkyController4Black(SkyControllerMux):
    """
    SkyController 4 Black controller class

    This class should be used to connect to ANAFI USA drones through a SkyController 4 Black.
    Use the :py:class:`~olympe.AnafiUSA` class instead when connecting to a drone
    directly.
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_4_BLACK]


class SkyController4(SkyControllerMuxCellular):
    """
    SkyController 4 controller class

    This class should be used to connect to ANAFI Ai drones through a SkyController 4.
    Use the :py:class:`~olympe.AnafiAi` class instead when connecting to a drone
    directly (the `Direct connection` mode should have been enabled on the drone in this
    case).
    """
    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_4]


class SkyController(SkyControllerMuxCellular):
    """
    Generic SkyController controller class

    This class can be used to connect to any SkyController SDK API
    but should be avoided to access other APIs (media, streaming
    and cellular pairing APIs).
    """
    DEVICE_TYPES = SKYCTRL_DEVICE_TYPE_LIST
