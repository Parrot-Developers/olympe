import logging

from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING

from olympe.concurrent import Future, Loop
from olympe.connection.http import Session
from olympe.controllers.scheduler import AbstractScheduler
from olympe.messaging.expectations import Expectation

if TYPE_CHECKING:
    from olympe.connection import DeviceInfo
    from olympe.controllers.mixins.ipproxy import IpProxy
    from olympe.features.flightplan import Flightplan
    from olympe.features.media import Media
    from olympe.features.mission import MissionController
    from olympe.features.update import Update
    from olympe.features.video.pdraw import Pdraw


class HasLoop(Protocol):
    _loop: Loop


class HasLog(Protocol):
    _name: str
    _device_name: str
    _logger_scope: str
    logger: logging.Logger

    def set_device_name(self, device_name: str) -> None: ...


class HasController(Protocol):
    _is_skyctrl: bool
    _ip_addr: bytes
    _ip_addr_str: str
    _device: "DeviceInfo"
    _device_type: int
    _scheduler: AbstractScheduler
    _http_session: Session

    def get_state(self, Any) -> Dict[str, Any]: ...

    def check_state(self, Any) -> bool: ...

    def __call__(self, expectation: Expectation) -> Expectation: ...

    def _on_skyctrl_connection_changed(self, connected: bool) -> None: ...

    @property
    def connected(self) -> bool: ...

    @property
    def scheduler(self) -> AbstractScheduler: ...

    def destroy(self) -> None: ...

    def _reset_instance(self) -> None: ...


class HasStreaming(Protocol):
    _pdraw: "Pdraw"
    _video_buffer_queue_size: int

    @property
    def streaming(self) -> "Pdraw": ...

    def _create_pdraw_interface(self) -> Optional["Pdraw"]: ...


class HasMedia(Protocol):
    _media: "Media"
    _is_media_custom: bool
    _autoconnect_task: Optional[Future]

    @property
    def media(self) -> "Media": ...


class HasFlightplan(Protocol):
    _flightplan: "Flightplan"

    @property
    def flightplan(self) -> "Flightplan": ...


class HasUpdate(Protocol):
    _update: "Update"

    @property
    def update(self) -> "Update": ...


class HasMission(Protocol):
    _mission: "MissionController"

    @property
    def mission(self) -> "MissionController": ...


class HasIpProxy(Protocol):
    async def aopen_drone_tcp_proxy(self, port: int) -> "IpProxy": ...

    def fopen_drone_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...

    def open_drone_tcp_proxy(self, port: int) -> "IpProxy": ...

    async def aopen_skycontroller_tcp_proxy(self, port: int) -> "IpProxy": ...

    def fopen_skycontroller_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...

    def open_skycontroller_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...


class MixinBase(
    HasLoop,
    HasLog,
    HasController,
    HasStreaming,
    HasMedia,
    HasFlightplan,
    HasUpdate,
    HasMission,
    HasIpProxy,
    Protocol,
):
    """
    An intersection type of "every" mixin protocols in town that should be able to
    provide some useful typehints to the type checker.
    """
