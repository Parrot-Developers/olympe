#  Copyright (C) 2019-2022 Parrot Drones SAS
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


import ctypes
import errno
import ipaddress
import olympe_deps as od
import os
import socket
import typing

try:
    # Python 3.8+
    from typing import Protocol
except ImportError:
    # Python 3.7
    from typing_extension import Protocol  # type: ignore

from aenum import Enum
from dataclasses import dataclass
from olympe.log import LogMixin
from olympe.concurrent import Loop, Future
from olympe.networking import (
    Buffer,
    Connection,
    ConnectionListener,
    DataListener,
    DNSResolver,
    INADDR_ANY,
    INADDR_NONE,
    SocketContext,
    SocketBase,
    TcpClient,
    TcpServer,
)
from olympe.utils import callback_decorator


class BackendType(Enum):
    Net = object()
    MuxIp = object()


class DeviceHandler(Protocol):
    def _device_added_cb(
        self,
        arsdk_device: "od.POINTER_T[od.struct_arsdk_device]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        pass

    def _device_removed_cb(
        self,
        arsdk_device: "od.POINTER_T[od.struct_arsdk_device]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        pass


@dataclass
class CtrlBackendInfoBase:
    arsdk_ctrl: "od.POINTER_T[od.struct_arsdk_ctrl]"
    arsdk_ctrl_device_cbs: od.struct_arsdk_ctrl_device_cbs


@dataclass
class CtrlBackendNetInfo(CtrlBackendInfoBase):
    backend: "od.POINTER_T[od.struct_arsdkctrl_backend_net]"
    socket_cb: od.arsdkctrl_backend_net_socket_cb_t


@dataclass
class CtrlBackendMuxIpInfo(CtrlBackendInfoBase):
    tcp_client: typing.Optional[TcpClient]
    backend: "typing.Optional[od.POINTER_T[od.struct_arsdkctrl_backend_mux]]"
    mux_ctx: typing.Optional[od.struct_mux_ctx]
    mux_ops: od.struct_mux_ops


@dataclass
class DeviceBackendInfoBase:
    arsdk_mngr: "od.POINTER_T[od.struct_arsdk_mngr]"
    arsdk_mngr_peer_cbs: od.struct_arsdk_mngr_peer_cbs


@dataclass
class DeviceBackendNetInfo(DeviceBackendInfoBase):
    backend: "od.POINTER_T[od.struct_arsdkctrl_backend_net]"
    socket_cb: od.arsdkctrl_backend_net_socket_cb_t


@dataclass
class DeviceBackendMuxIpInfo(DeviceBackendInfoBase):
    tcp_server: TcpServer
    connection: typing.Optional[Connection]
    backend: "od.POINTER_T[od.struct_arsdkctrl_backend_mux]"
    mux_ctx: od.struct_mux_ctx
    mux_ops: od.struct_mux_ops


CtrlBackendInfo = typing.Union[CtrlBackendNetInfo, CtrlBackendMuxIpInfo]
DeviceBackendInfo = typing.Union[DeviceBackendNetInfo, DeviceBackendMuxIpInfo]


class CtrlBackendBase(LogMixin):
    def __init__(
        self,
        name: typing.Optional[str] = None,
        proto_v_min: int = 1,
        proto_v_max: int = 3,
        **kwds,
    ):
        super().__init__(name, None, "backend")
        self._proto_v_min: int = proto_v_min
        self._proto_v_max: int = proto_v_max
        self._device_handler: typing.List[DeviceHandler] = []
        self._thread_loop = Loop(self.logger)
        self._thread_loop.start()
        self._info: CtrlBackendInfo = self._create()
        self._thread_loop.register_cleanup(self._adestroy)

    def add_device_handler(self, device_handler: DeviceHandler) -> None:
        self._device_handler.append(device_handler)

    def remove_device_handler(self, device_handler: DeviceHandler) -> None:
        try:
            self._device_handler.remove(device_handler)
        except ValueError:
            # ignore missing device handler
            pass

    def _create(self) -> CtrlBackendInfo:
        f = self._thread_loop.run_async(self._do_create)
        return f.result_or_cancel(timeout=3.0)

    async def _do_create(
        self,
    ) -> typing.Tuple[
        "od.POINTER_T[od.struct_arsdk_ctrl]", od.struct_arsdk_ctrl_device_cbs
    ]:
        # default userdata callback argument
        self.userdata = ctypes.c_void_p()

        # Create the arsdk_ctrl
        arsdk_ctrl = od.POINTER_T(od.struct_arsdk_ctrl)()
        res = od.arsdk_ctrl_new(self._thread_loop.pomp_loop, ctypes.byref(arsdk_ctrl))

        arsdk_ctrl_device_cbs = od.struct_arsdk_ctrl_device_cbs.bind(
            {"added": self._device_added_cb, "removed": self._device_removed_cb}
        )
        # Send a command to add callbacks to the arsdk_ctrl
        res = od.arsdk_ctrl_set_device_cbs(arsdk_ctrl, arsdk_ctrl_device_cbs)

        if res != 0:
            raise RuntimeError(f"arsdk_ctrl_set_device_cbs: {res}")

        self.logger.info("device callbacks have been added to arsdk_ctrl")
        return arsdk_ctrl, arsdk_ctrl_device_cbs

    def destroy(self) -> None:
        self._thread_loop.stop()

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend done")
        self._thread_loop.unregister_cleanup(self._adestroy, ignore_error=True)
        self._thread_loop.stop()

    @callback_decorator()
    def _device_added_cb(
        self, arsdk_device: DeviceHandler, _user_data: "od.POINTER_T[None]"
    ) -> None:
        for device_handler in self._device_handler:
            device_handler._device_added_cb(arsdk_device, _user_data)

    @callback_decorator()
    def _device_removed_cb(
        self, arsdk_device: DeviceHandler, _user_data: "od.POINTER_T[None]"
    ) -> None:
        for device_handler in self._device_handler:
            device_handler._device_removed_cb(arsdk_device, _user_data)


class CtrlBackendNet(CtrlBackendBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

    async def _do_create(self) -> CtrlBackendNetInfo:
        arsdk_ctrl, arsdk_ctrl_device_cbs = await super()._do_create()

        # Create the net backend
        cfg = od.struct_arsdkctrl_backend_net_cfg.bind(
            {
                "iface": ctypes.create_string_buffer(b"net_config"),
                "stream_supported": 1,
                "proto_v_min": self._proto_v_min,
                "proto_v_max": self._proto_v_max,
            }
        )
        backend_net = od.POINTER_T(od.struct_arsdkctrl_backend_net)()

        res = od.arsdkctrl_backend_net_new(
            arsdk_ctrl, ctypes.pointer(cfg), ctypes.byref(backend_net)
        )

        if res != 0:
            raise RuntimeError(f"arsdkctrl_backend_net_new: {res}")

        self.logger.debug("New net backend has been created")

        backend_net_socket_callback = od.arsdkctrl_backend_net_socket_cb_t(
            lambda *args: self._socket_cb(*args)
        )

        res_set_socket = od.arsdkctrl_backend_net_set_socket_cb(
            backend_net, backend_net_socket_callback, self.userdata
        )
        if res_set_socket != 0:
            raise RuntimeError(f"arsdkctrl_backend_net_set_socket_cb: {res}")

        self.logger.debug("Set backend socket callback OK")

        return CtrlBackendNetInfo(
            arsdk_ctrl,
            arsdk_ctrl_device_cbs,
            backend_net,
            backend_net_socket_callback,
        )

    async def ready(self):
        return True

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend...")
        if self._info.backend is not None:

            res = od.arsdkctrl_backend_net_destroy(self._info.backend)

            if res != 0:
                self.logger.error(f"Error while destroying net backend: {res}")
            else:
                self._info.backend = None
                self.logger.debug("Net backend has been destroyed")
        await super()._adestroy()

    @callback_decorator()
    def _socket_cb(
        self,
        backend_net: "od.POINTER_T[od.struct_arsdkctrl_backend_net]",
        socket_fd: ctypes.c_int32,
        socket_kind: ctypes.c_uint32,
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug(
            "backend_pointer {} socket_fd {} socket_kind {} userdate_pointer {}".format(
                backend_net, socket_fd, socket_kind, userdata
            )
        )


class CtrlBackendMuxIpListener(ConnectionListener, DataListener):

    def __init__(self, backend: "CtrlBackendMuxIp"):
        self._backend = backend

    def data_received(
        self,
        ctx: "SocketContext",
        connection: "SocketBase",
        buffer: "Buffer",
    ):
        od.mux_decode(self._backend._info.mux_ctx, buffer._buf)
        return True


class CtrlBackendMuxIp(CtrlBackendBase):
    def __init__(self, *args, device_addr, **kwds):
        self._device_addr = device_addr
        super().__init__(*args, **kwds)
        self._info.tcp_client.add_data_listener(CtrlBackendMuxIpListener(self))
        self._thread_loop.run_later(self._retry_connect)
        self._ready_fut = Future(self._thread_loop)
        self._resolver = DNSResolver()

    async def _do_create(self) -> CtrlBackendMuxIpInfo:
        arsdk_ctrl, arsdk_ctrl_device_cbs = await super()._do_create()
        mux_ops = od.struct_mux_ops.bind(
            {
                "tx": self._tx_cb,
                "chan_cb": self._chan_cb,
                "fdeof": self._fdeof_cb,
                "release": self._release_cb,
                "resolve": self._resolve_cb,
            }
        )

        tcp_client = TcpClient(self._thread_loop)

        # The remaining backend info attributes will be filled once the backend is connected
        return CtrlBackendMuxIpInfo(
            arsdk_ctrl,
            arsdk_ctrl_device_cbs,
            tcp_client,
            None,
            None,
            mux_ops,
        )

    async def ready(self):
        if self._info.mux_ctx:
            return True
        else:
            return await self._ready_fut

    async def _retry_connect(self):
        while True:
            if not self._info.tcp_client:
                self.logger.warning("Connection attempt aborted")
                return
            if not await self._info.tcp_client.aconnect(self._device_addr, 4321):
                self.logger.info(
                    f"CtrlBackendMuxIp failed to connect to {self._device_addr.decode()}")
                await self._thread_loop.asleep(3)
                continue

            # create the mux context
            mux_ctx = od.mux_new(
                ctypes.c_int(-1),
                self._thread_loop.pomp_loop,
                ctypes.pointer(self._info.mux_ops),
                ctypes.c_uint(0),
            )
            if not mux_ctx:
                self.logger.error("CtrlBackendMuxIp failed to create mux_ctx")
                await self._thread_loop.asleep(3)
                continue

            # Create the mux backend
            backend_mux = od.POINTER_T(od.struct_arsdkctrl_backend_mux)()
            cfg = od.struct_arsdkctrl_backend_mux_cfg.bind(
                {
                    "mux": mux_ctx,
                    "stream_supported": 1,
                    "proto_v_min": self._proto_v_min,
                    "proto_v_max": self._proto_v_max,
                }
            )

            res = od.arsdkctrl_backend_mux_new(
                self._info.arsdk_ctrl, ctypes.pointer(cfg), ctypes.byref(backend_mux)
            )

            if res != 0:
                self.logger.error(f"arsdkctrl_backend_mux_new: {res}")
                await self._thread_loop.asleep(3)
                continue
            break

        self.logger.debug("New mux backend has been created")
        self._info.backend = backend_mux
        self._info.mux_ctx = mux_ctx
        self._ready_fut.set_result(True)
        self._ready_fut = Future(self._thread_loop)

    @callback_decorator()
    def _tx_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        buf: "od.POINTER_T[od.struct_pomp_buffer]",
        userdata: "od.POINTER_T[None]",
    ) -> int:
        if not self._info.tcp_client.connected:
            return -errno.EPIPE
        self._info.tcp_client.write(buf)
        return 0

    @callback_decorator()
    def _chan_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        chanid: ctypes.c_uint32,
        buf: "od.POINTER_T[od.struct_pomp_buffer]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("chan_cb called")

    @callback_decorator()
    def _fdeof_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("fdeof_cb called")

    @callback_decorator()
    def _release_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("release_cb called")

    @callback_decorator()
    def _resolve_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        hostname: "od.POINTER_T[ctypes.c_char]",
        addr: "od.POINTER_T[ctypes.c_uint32]",
        userdata: "od.POINTER_T[None]",
    ) -> int:
        self.logger.debug("resolve_cb called")
        self._thread_loop.run_later(self._aresolve, od.string_cast(hostname))
        addr.contents.value = socket.htonl(int(INADDR_ANY))
        return 0

    async def _aresolve(self, hostname):
        for (_, sockaddr) in await self._resolver.resolve(hostname, 443):
            addr, *_ = sockaddr
            res = od.mux_resolve(
                self._info.mux_ctx,
                od.char_pointer_cast(hostname),
                ctypes.c_uint32(socket.htonl(int(
                    ipaddress.IPv4Address(addr)
                )))
            )
            break
        else:
            res = od.mux_resolve(
                self._info.mux_ctx,
                od.char_pointer_cast(hostname),
                ctypes.c_uint32(socket.htonl(int(INADDR_NONE)))
            )
        if res < 0:
            self.logger.error(f"mux_resolve returned {res}: {os.strerror(-res)}")

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend...")
        if self._info.backend:

            res = od.arsdkctrl_backend_mux_destroy(self._info.backend)

            if res != 0:
                self.logger.error(f"Error while destroying net backend: {res}")
            else:
                self._info.backend = None
                self.logger.debug("Net backend has been destroyed")

        if self._info.mux_ctx:
            od.mux_unref(self._info.mux_ctx)
            self._info.mux_ctx = None

        if self._info.tcp_client:
            await self._info.tcp_client.adisconnect()
            await self._info.tcp_client.adestroy()
            self._info.tcp_client = None
        await super()._adestroy()


CtrlBackend = typing.Union[CtrlBackendNet, CtrlBackendMuxIp]


class DeviceBackendBase(LogMixin):
    def __init__(
        self,
        name: typing.Optional[str] = None,
        proto_v_min: int = 1,
        proto_v_max: int = 3,
        **kwds,
    ):
        super().__init__(name, None, "backend")
        self._proto_v_min: int = proto_v_min
        self._proto_v_max: int = proto_v_max
        self._device_handler: typing.List[DeviceHandler] = []
        self._thread_loop = Loop(self.logger)
        self._thread_loop.start()
        self._info: DeviceBackendInfo = self._create()
        self._thread_loop.register_cleanup(self._adestroy)

    def add_device_handler(self, device_handler: DeviceHandler) -> None:
        self._device_handler.append(device_handler)

    def remove_device_handler(self, device_handler: DeviceHandler) -> None:
        try:
            self._device_handler.remove(device_handler)
        except ValueError:
            # ignore missing device handler
            pass

    def _create(self) -> DeviceBackendInfo:
        f = self._thread_loop.run_async(self._do_create)
        return f.result_or_cancel(timeout=3.0)

    async def _do_create(
        self,
    ) -> typing.Tuple[
        "od.POINTER_T[od.struct_arsdk_mngr]", od.struct_arsdk_mngr_peer_cbs
    ]:
        # default userdata callback argument
        self.userdata = ctypes.c_void_p()

        # Create the arsdk_mngr
        arsdk_mngr = od.POINTER_T(od.struct_arsdk_mngr)()
        res = od.arsdk_mngr_new(self._thread_loop.pomp_loop, ctypes.byref(arsdk_mngr))

        arsdk_mngr_peer_cbs = od.struct_arsdk_mngr_peer_cbs.bind(
            {"added": self._peer_added_cb, "removed": self._peer_removed_cb}
        )
        # Send a command to add callbacks to the arsdk_mngr
        res = od.arsdk_mngr_set_peer_cbs(arsdk_mngr, arsdk_mngr_peer_cbs)

        if res != 0:
            raise RuntimeError(f"arsdk_mngr_set_device_cbs: {res}")

        self.logger.info("device callbacks have been added to arsdk_mngr")

        return arsdk_mngr, arsdk_mngr_peer_cbs

    def destroy(self) -> None:
        self._thread_loop.stop()

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend done")
        self._thread_loop.unregister_cleanup(self._adestroy, ignore_error=True)
        self._thread_loop.stop()

    @callback_decorator()
    def _peer_added_cb(
        self, arsdk_device: DeviceHandler, _user_data: "od.POINTER_T[None]"
    ) -> None:
        for device_handler in self._device_handler:
            device_handler._device_added_cb(arsdk_device, _user_data)

    @callback_decorator()
    def _peer_removed_cb(
        self, arsdk_device: DeviceHandler, _user_data: "od.POINTER_T[None]"
    ) -> None:
        for device_handler in self._device_handler:
            device_handler._device_removed_cb(arsdk_device, _user_data)


class DeviceBackendNet(DeviceBackendBase):
    async def _do_create(self) -> DeviceBackendInfo:
        arsdk_mngr, arsdk_mngr_peer_cbs = await super()._do_create()

        # Create the net backend
        cfg = od.struct_arsdk_backend_net_cfg.bind(
            {
                "iface": od.POINTER_T(ctypes.c_char)(),
                "stream_supported": 1,
                "proto_v_min": self._proto_v_min,
                "proto_v_max": self._proto_v_max,
            }
        )
        backend_net = od.POINTER_T(od.struct_arsdk_backend_net)()

        res = od.arsdk_backend_net_new(
            arsdk_mngr, ctypes.pointer(cfg), ctypes.byref(backend_net)
        )

        if res != 0:
            raise RuntimeError(f"arsdk_backend_net_new: {res}")

        self.logger.debug("New net backend has been created")

        backend_net_socket_callback = od.arsdk_backend_net_socket_cb_t(
            lambda *args: self._socket_cb(*args)
        )

        res_set_socket = od.arsdk_backend_net_set_socket_cb(
            backend_net, backend_net_socket_callback, self.userdata
        )
        if res_set_socket != 0:
            raise RuntimeError(f"arsdk_backend_net_set_socket_cb: {res}")

        self.logger.debug("Set backend socket callback OK")

        return DeviceBackendNetInfo(
            arsdk_mngr,
            arsdk_mngr_peer_cbs,
            backend_net,
            backend_net_socket_callback,
        )

    @callback_decorator()
    def _socket_cb(
        self,
        backend_net: "od.POINTER_T[od.struct_arsdk_backend_net]",
        socket_fd: ctypes.c_int32,
        socket_kind: ctypes.c_uint32,
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug(
            "backend_pointer {} socket_fd {} socket_kind {} userdate_pointer {}".format(
                backend_net, socket_fd, socket_kind, userdata
            )
        )

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend...")
        if self._info.backend:

            res = od.arsdk_backend_net_destroy(self._info.backend)

            if res != 0:
                self.logger.error(f"Error while destroying net backend: {res}")
            else:
                self._info.backend = None
                self.logger.debug("Net backend has been destroyed")
        await super()._adestroy()


class DeviceBackendMuxIpListener(ConnectionListener, DataListener):
    def __init__(self, backend: "DeviceBackendMuxIp"):
        self._backend = backend

    def connected(self, connection: Connection):
        self._backend._info.connection = connection
        # create the mux context
        mux_ops = od.struct_mux_ops.bind(
            {
                "tx": self._backend._tx_cb,
                "chan_cb": self._backend._chan_cb,
                "fdeof": self._backend._fdeof_cb,
                "release": self._backend._release_cb,
                "resolve": self._backend._resolve_cb,
            }
        )
        self._backend._info.mux_ops = mux_ops

        mux_ctx = od.mux_new(
            ctypes.c_int(-1),
            self._backend._thread_loop.pomp_loop,
            ctypes.pointer(mux_ops),
            ctypes.c_uint(0),
        )
        self._backend._info.mux_ctx = mux_ctx

        # Create the mux backend
        backend_mux = od.POINTER_T(od.struct_arsdk_backend_mux)()
        self._backend._info.backend = backend_mux
        cfg = od.struct_arsdk_backend_mux_cfg.bind(
            {
                "mux": mux_ctx,
                "stream_supported": 1,
                "proto_v_min": self._backend._proto_v_min,
                "proto_v_max": self._backend._proto_v_max,
                "proto_v": self._backend._proto_v_max,
            }
        )

        res = od.arsdk_backend_mux_new(
            self._backend._info.arsdk_mngr, ctypes.pointer(cfg), ctypes.byref(backend_mux)
        )

        if res != 0:
            self._backend.logger.error(f"arsdkctrl_backend_mux_new: {res}")
            self._backend._info.connection = None
            self._backend._info.backend = None
            self._backend._info.mux_ctx = None
            self._backend._info.mux_ops = None
            return

        self._backend._ready_fut.set_result(True)
        self._backend._ready_fut = Future(self._backend._thread_loop)

    def disconnected(self, connection: Connection):
        self._backend._info.connection = None

    def data_received(
        self,
        ctx: "SocketContext",
        connection: "SocketBase",
        buffer: "Buffer",
    ):
        if not self._backend._info.connection:
            return False
        od.mux_decode(self._backend._info.mux_ctx, buffer._buf)
        return True


class DeviceBackendMuxIp(DeviceBackendBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self._ready_fut = Future(self._thread_loop)

    async def _do_create(self) -> DeviceBackendInfo:
        arsdk_mngr, arsdk_mngr_peer_cbs = await super()._do_create()

        tcp_server = TcpServer(self._thread_loop)
        await tcp_server.listen("", 4321)
        listener = DeviceBackendMuxIpListener(self)
        tcp_server.add_data_listener(listener)
        tcp_server.add_connection_listener(listener)

        self.logger.debug("New mux backend has been created")

        return DeviceBackendMuxIpInfo(
            arsdk_mngr,
            arsdk_mngr_peer_cbs,
            tcp_server,
            None,
            None,
            None,
            None,
        )

    async def ready(self):
        if self._info.connection:
            return True
        else:
            return await self._ready_fut

    @callback_decorator()
    def _tx_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        buf: "od.POINTER_T[od.struct_pomp_buffer]",
        userdata: "od.POINTER_T[None]",
    ) -> int:
        if not self._info.connection:
            return -errno.EPIPE
        self._info.connection.write(buf)
        return 0

    @callback_decorator()
    def _chan_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        chanid: ctypes.c_uint32,
        buf: "od.POINTER_T[od.struct_pomp_buffer]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("chan_cb called")

    @callback_decorator()
    def _fdeof_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("fdeof_cb called")

    @callback_decorator()
    def _release_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        userdata: "od.POINTER_T[None]",
    ) -> None:
        self.logger.debug("release_cb called")

    @callback_decorator()
    def _resolve_cb(
        self,
        mux_ctx: "od.POINTER_T[od.struct_mux_ctx]",
        hostname: "od.POINTER_T[ctypes.c_char]",
        addr: "od.POINTER_T[ctypes.c_uint32]",
        userdata: "od.POINTER_T[None]",
    ) -> int:
        self.logger.debug("resolve_cb called")
        self._thread_loop.run_later(self._aresolve, od.string_cast(hostname))
        addr.contents.value = socket.htonl(int(INADDR_ANY))
        return 0

    async def _aresolve(self, hostname):
        for (_, sockaddr) in await self._resolver.resolve(hostname, 443):
            addr, *_ = sockaddr
            res = od.mux_resolve(
                self._info.mux_ctx,
                od.char_pointer_cast(hostname),
                ctypes.c_uint32(socket.htonl(int(
                    ipaddress.IPv4Address(addr)
                )))
            )
            break
        else:
            res = od.mux_resolve(
                self._info.mux_ctx,
                od.char_pointer_cast(hostname),
                ctypes.c_uint32(socket.htonl(int(INADDR_NONE)))
            )
        if res < 0:
            self.logger.error(f"mux_resolve returned {res}: {os.strerror(-res)}")

    async def _adestroy(self) -> None:
        self.logger.debug("Destroying backend device mux ip...")

        self._info.connection = None

        if self._info.backend:

            res = od.arsdk_backend_mux_destroy(self._info.backend)

            if res != 0:
                self.logger.error(f"Error while destroying mux backend: {res}")
            else:
                self._info.backend = None
                self.logger.debug("Net backend has been destroyed")

        if self._info.tcp_server:
            await self._info.tcp_server.astop()
            self._info.tcp_server.destroy()
            self._info.tcp_server = None

        if self._info.mux_ctx:
            od.mux_unref(self._info.mux_ctx)
            self._info.mux_ctx = None
        await super()._adestroy()


if __name__ == "__main__":
    device_backend = DeviceBackendMuxIp()
    ctrl_backend = CtrlBackendMuxIp(device_addr="127.0.0.1")
