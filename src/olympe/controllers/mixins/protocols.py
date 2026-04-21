import logging

from threading import Event as threading_event
from typing import Any, Callable, Dict, Optional, Protocol, TYPE_CHECKING

from olympe.concurrent import Future, Loop
from olympe.connection.http import Session
from olympe.controllers.scheduler import AbstractScheduler
from olympe.messaging.expectations import Expectation

if TYPE_CHECKING:
    from olympe.connection import DeviceInfo
    from olympe.controllers.mixins.ipproxy import IpProxy
    from olympe.features.esim import EsimHandler
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

    @property
    def http_session(self) -> Session: ...

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


class HasEsim(Protocol):
    _esim: "EsimHandler"
    _loop: "Loop"
    _session: "Session"
    _get_state_subscriber: "AbstractScheduler"
    _received_esim_event: "threading_event"

    @property
    def esim(self) -> "EsimHandler": ...

    def _on_first_getstate(self, message_event, _): ...


class HasMission(Protocol):
    _mission: "MissionController"

    @property
    def mission(self) -> "MissionController": ...

    def _on_mission_proxy_created(self, proxy: "IpProxy") -> None: ...

    def _on_mission_proxy_destroyed(self) -> None: ...


class HasUpdate(Protocol):
    _update: "Update"

    @property
    def update(self) -> "Update": ...


class HasIpProxy(Protocol):
    async def aopen_drone_tcp_proxy(self, port: int) -> "IpProxy": ...

    def fopen_drone_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...

    def open_drone_tcp_proxy(self, port: int) -> "IpProxy": ...

    async def aopen_skycontroller_tcp_proxy(self, port: int) -> "IpProxy": ...

    def fopen_skycontroller_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...

    def open_skycontroller_tcp_proxy(self, port: int) -> Future["IpProxy"]: ...


class HasHttpProxy(Protocol):
    # Mixins call these methods in their __init__ (after super().__init__()) to
    # register a callback that HttpControllerMixin will invoke when the TCP proxy
    # to the drone is opened or closed.  Using explicit registration ensures that no
    # callback is silently dropped if a mixin forgets the super() call.
    def register_proxy_created_hook(self, fn: "Callable[[IpProxy], None]") -> None: ...

    def register_proxy_destroyed_hook(self, fn: "Callable[[], None]") -> None: ...


class MixinBase(
    HasLoop,
    HasLog,
    HasController,
    HasStreaming,
    HasMedia,
    HasFlightplan,
    HasEsim,
    HasUpdate,
    HasMission,
    HasIpProxy,
    HasHttpProxy,
    Protocol,
):
    """
    An intersection type of "every" mixin protocols in town that should be able to
    provide some useful typehints to the type checker.
    """
