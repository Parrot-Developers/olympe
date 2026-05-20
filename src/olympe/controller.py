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


import warnings

import olympe_deps as od

from olympe.connection import DRONE_DEVICE_TYPE_LIST, SKYCTRL_DEVICE_TYPE_LIST
from olympe.connection.backend import BackendType
from olympe.controllers.cmd_itf import Connected, Disconnected  # noqa
from olympe.controllers.controller import ControllerBase as Controller

try:
    from olympe.controllers.mixins.streaming import StreamingControllerMixin
except ImportError:
    StreamingControllerMixin = None
from olympe.controllers.mixins.cellular import CellularPairerMixin
from olympe.controllers.mixins.flightplan import FlightplanControllerMixin
from olympe.controllers.mixins.http import HttpControllerMixin
from olympe.controllers.mixins.ipproxy import IpProxyMixin
from olympe.controllers.mixins.mission import MissionControllerMixin
from olympe.controllers.mixins.media import MediaControllerMixin
from olympe.controllers.mixins.update import UpdateControllerMixin


# ControllerBase is built with mixin classes that enable to construct the drone and
# skycontroller devices below, used in Olympe scripts. Each mixin is responsible for a
# functionality of the device. Thanks to the fact they inherit from each other, once
# the first one is called, all its parents are as well, ending with the greatest parent
# Controller class of arsdk-ng
if StreamingControllerMixin is not None:
    # Controller with video streaming support
    class ControllerBase(
        IpProxyMixin,
        StreamingControllerMixin,
        MissionControllerMixin,
        MediaControllerMixin,
        FlightplanControllerMixin,
        UpdateControllerMixin,
        HttpControllerMixin,
        Controller,
    ):
        pass

else:
    # Controller without video streaming support
    class ControllerBase(
        IpProxyMixin,
        MissionControllerMixin,
        MediaControllerMixin,
        FlightplanControllerMixin,
        UpdateControllerMixin,
        HttpControllerMixin,
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


class LocalController(ControllerBase):
    """
    Generic Parrot drone local controller class

    This class is to be used to connect to the local controller interface onboard a
    drone (e.g. from an Air SDK mission).
    """

    def __init__(self, *, name=None, backend=BackendType.Net, discovery_port=44447):
        super().__init__(
            "127.0.0.1",
            name=name,
            backend=backend,
            discovery_port=discovery_port,
        )

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


class AnafiUKR(Drone):
    """
    ANAFI UKR controller class.

    This class cannot be used to connect directly to an ANAFI UKR without a SkyController.
    The MARS radio transceiver provided by a SkyController UKR is the only mean
    to connect to an ANAFI UKR.

    When connecting Olympe to an ANAFI UKR through a SkyController UKR. You must use
    the :py:class:`~olympe.SkyControllerUKR` class instead.
    """

    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_ANAFI_3_MIL]

    def __init__(self, *args, **kwds):
        warnings.warn(
            "olympe.AnafiUKR cannot be used to connect directly to an ANAFI UKR drone. "
            "You should probably use the olympe.SkyControllerUKR class instead. "
            "The MARS radio transceiver provided by a SkyController UKR is the only mean "
            "to connect to an ANAFI UKR.\n"
            "When connecting Olympe to an ANAFI UKR through a SkyController UKR. You must use "
            "the ~olympe.SkyControllerUKR class instead."
        )
        super().__init__(*args, **kwds)


class Chuck(Drone):
    """
    Chuck controller class.

    This class should only be used to connect to a Chuck.
    """

    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_CHUCK_3]


class SkyControllerBase(ControllerBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, is_skyctrl=True, **kwds)


class SkyControllerNet(SkyControllerBase):
    def __init__(self, *args, backend: BackendType = BackendType.Net, **kwds):
        super().__init__(*args, backend=backend, **kwds)


class SkyControllerMux(SkyControllerBase):
    def __init__(self, *args, backend: BackendType = BackendType.MuxIp, **kwds):
        super().__init__(*args, backend=backend, **kwds)


class SkyControllerMuxCellular(CellularPairerMixin, SkyControllerMux):
    pass


class SkyController(SkyControllerMuxCellular):
    """
    Generic SkyController controller class

    This class can be used to connect to any SkyController SDK API
    but should be avoided to access other APIs (media, streaming
    and cellular pairing APIs).
    """

    DEVICE_TYPES = SKYCTRL_DEVICE_TYPE_LIST


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


class SkyControllerUKR(SkyControllerMux):
    """
    SkyController UKR controller class

    This class should be used to connect to ANAFI UKR drones through a SkyController UKR.
    """

    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_UA_2]


class SkyControllerUKR5G(SkyControllerMuxCellular):
    """
    SkyController UKR controller class

    This class should be used to connect to ANAFI UKR drones with 5G, through a
    SkyController UKR.
    """

    DEVICE_TYPES = [od.ARSDK_DEVICE_TYPE_SKYCTRL_UA_2]
