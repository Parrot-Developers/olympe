#  Copyright (C) 2022 Parrot Drones SAS
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

from abc import ABC, abstractmethod
from array import array
from aenum import IntEnum, IntFlag
from olympe.utils import callback_decorator
from .concurrent import Condition, Semaphore, Future, Loop, TimeoutError

try:
    # Python 3.8+
    from typing import Protocol
except ImportError:
    # Python 3.7
    from typing_extension import Protocol  # type: ignore


import collections
import concurrent.futures
import ctypes
import errno
import ipaddress
import logging
import olympe_deps as od
import os
import ssl
import socket
import typing


logger = logging.getLogger("olympe.networking")


class SocketKind(IntEnum):
    server = od.POMP_SOCKET_KIND_SERVER
    peer = od.POMP_SOCKET_KIND_PEER
    client = od.POMP_SOCKET_KIND_CLIENT
    dgram = od.POMP_SOCKET_KIND_DGRAM


class SendStatus(IntFlag):
    ok = od.POMP_SEND_STATUS_OK
    error = od.POMP_SEND_STATUS_ERROR
    aborted = od.POMP_SEND_STATUS_ABORTED
    queue_empty = od.POMP_SEND_STATUS_QUEUE_EMPTY


class _struct_sockaddr_in(od.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_uint16),
        ("sin_port", ctypes.c_uint16),
        ("sin_addr", ctypes.c_ubyte * 4),
        ("PADDING_0", ctypes.c_ubyte * 8),
    ]


class _struct_sockaddr_in6(od.Structure):
    _fields_ = [
        ("sin6_family", ctypes.c_uint16),
        ("sin6_port", ctypes.c_uint16),
        ("sin6_flowinfo", ctypes.c_uint32),
        ("sin6_addr", ctypes.c_ubyte * 16),
        ("sin6_scope_id", ctypes.c_uint32),
    ]


DEFAULT_READ_BUFFER_SIZE = 2 ** 16
DEFAULT_CONNECTION_TIMEOUT = 2.0


class SocketBase:
    def __init__(
        self,
        ctx: "SocketContext",
        conn: "od.POINTER_T[od.struct_pomp_conn]",
        read_buffer_size=DEFAULT_READ_BUFFER_SIZE,
    ):
        self._ctx = ctx
        self._loop = self._ctx._loop
        self.logger = self._loop.logger
        self._conn = conn
        self._read_buffer_size = read_buffer_size
        if self._conn:
            self._fd = od.pomp_conn_get_fd(self._conn)
            self._set_conn_read_buffer_len()
        else:
            self._fd = -1
        self._read_buffers = collections.deque()
        self._send_futures = dict()
        self._data_waiter = None

    @property
    def fileno(self):
        return self._fd

    def _set_conn(self, conn):
        assert conn
        self._conn = conn
        self._fd = od.pomp_conn_get_fd(self._conn)
        self._set_conn_read_buffer_len()

    def _set_conn_read_buffer_len(self):
        res = od.pomp_conn_set_read_buffer_len(self._conn, self._read_buffer_size)
        if res < 0:
            self.logger.error("Failed to set connection read buffer size")

    def _feed_data(self, buffer: "Buffer"):
        self._feed_data_from(buffer, connection_less=False)

    def _feed_data_from(
        self,
        buffer: "Buffer",
        *,
        connection_less=True,
    ):
        if not connection_less:
            self._read_buffers.append(bytes(buffer.data.contents))
        else:
            self._read_buffers.append(
                (bytes(buffer.data.contents),) + self._get_peer_addr()
            )
        if self._data_waiter is not None:
            waiter = self._data_waiter
            self._data_waiter = None
            waiter.set_result(True)
        if len(self._read_buffers) >= 16:
            res = od.pomp_conn_suspend_read(self._conn)
            if res < 0:
                try:
                    msg = os.strerror(-res)
                except ValueError:
                    msg = "unknown error ({res})"
                self.logger.error(f"Failed to suspend reading connection: {msg}")
                if not connection_less:
                    od.pomp_conn_disconnect(self._conn)

    def _feed_eof(self):
        self._conn = None
        if self._data_waiter is not None:
            waiter = self._data_waiter
            self._data_waiter = None
            waiter.set_result(True)

    def _ack_data(self, buffer: "Buffer", send_status: "SendStatus"):
        future = self._send_futures.pop(buffer, None)
        if future is None:
            self.logger.error("Unknown sent buffer {Buffer._buf}")
            return
        if future.done():
            return
        if SendStatus.ok in send_status:
            future.set_result(True)
        else:
            future.set_exception(
                ConnectionError(f"Buffer sending error: {send_status._name_}")
            )

    def _maybe_resume_reading(self):
        if self._conn and len(self._read_buffers) < 16:
            res = od.pomp_conn_resume_read(self._conn)
            if res < 0:
                try:
                    msg = os.strerror(-res)
                except ValueError:
                    msg = "unknown error ({res})"
                od.pomp_conn_disconnect(self._conn)
                self.logger.error(f"Failed to resume reading connection: {msg}")

    def _pop_read_buffer(self):
        self._read_buffers.popleft()

    def _wait_for_data(self):
        self._maybe_resume_reading()
        if self._data_waiter is None:
            self._data_waiter = Future(self._loop)
        return self._data_waiter

    def get_local_addr(self):
        return self._ctx._loop.run_later(self._get_local_addr)

    def set_read_buffer_size(self, size):
        assert size > 0, "read buffer size should be a strictly positive integer"
        self._read_buffer_size = size
        if not self._conn:
            return
        self._set_conn_read_buffer_len()

    @callback_decorator()
    def _get_local_addr(self):
        addrlen = ctypes.c_uint32()
        sockaddr: "od.POINTER_T[od.struct_sockaddr]" = od.pomp_conn_get_local_addr(
            self._conn, ctypes.byref(addrlen)
        )
        if not sockaddr:
            raise ConnectionError("Cannot retrieve socket local address")

        if sockaddr.contents.sa_family == socket.AF_INET:
            return self._sock_addr(sockaddr)
        elif sockaddr.sa_family == socket.AF_INET6:
            return self._sock_addr6(sockaddr)

    def get_peer_addr(self):
        return self._ctx._loop.run_async(self._get_peer_addr)

    @callback_decorator()
    def _get_peer_addr(self):
        addrlen = ctypes.c_uint32()
        sockaddr: "od.POINTER_T[od.struct_sockaddr]" = od.pomp_conn_get_peer_addr(
            self._conn, ctypes.byref(addrlen)
        )
        if not sockaddr:
            raise ConnectionError("Cannot retrieve socket local address")

        if sockaddr.contents.sa_family == socket.AF_INET:
            return self._sock_addr(sockaddr)
        elif sockaddr.sa_family == socket.AF_INET6:
            return self._sock_addr6(sockaddr)

    @classmethod
    def _sock_addr(self, sockaddr):
        sockaddr_in = ctypes.cast(sockaddr, od.POINTER_T(_struct_sockaddr_in))
        addr = str(ipaddress.IPv4Address(bytes(sockaddr_in.contents.sin_addr)))
        port = socket.htons(int(sockaddr_in.contents.sin_port))
        return (addr, port)

    @classmethod
    def _sock_addr6(self, sockaddr):
        sockaddr_in6 = ctypes.cast(sockaddr, od.POINTER_T(_struct_sockaddr_in6))
        addr = str(ipaddress.IPv6Address(bytes(sockaddr_in6.contents.sin6_addr)))
        port = socket.htons(int(sockaddr_in6.contents.sin6_port))
        return (addr, port)


class ConnectionClosedError(Exception):
    pass


class Connection(SocketBase):
    def disconnect(self):
        return self._ctx._loop.run_async(self.adisconnect)

    async def adisconnect(self):
        if self._conn is None:
            return False
        res = od.pomp_conn_disconnect(self._conn)
        if res < 0:
            raise ConnectionError("Failed to disconnect")
        self._conn = None
        return True

    def read(self, n=-1):
        return self._loop.run_async(self.aread, n)

    async def aread(self, n=-1):
        if n == 0:
            return b""
        if n < 0:
            blocks = []
            while True:
                block = await self.aread(self._read_buffer_size)
                if not block:
                    break
                blocks.append(block)
            return b"".join(blocks)
        if not self._read_buffers:
            if self._conn is None:
                return b""
            else:
                await self._wait_for_data()
                if self._conn is None:
                    return b""
                return await self.aread(n)
        buffer = self._read_buffers.popleft()
        if len(buffer) > n:
            tail = buffer[n:]
            del buffer[:n]
            self._read_buffers.appendleft(tail)
        else:
            self._maybe_resume_reading()
        return buffer

    def write(self, data, timeout=None):
        return self._loop.run_async(self.awrite, data, timeout=timeout)

    async def awrite(self, data, timeout=None):
        fut = Future(self._loop)
        if not self._ctx._ctx:
            fut.cancel()
            return fut
        if isinstance(data, Buffer):
            buffer = data
        elif isinstance(data, od.POINTER_T(od.struct_pomp_buffer)):
            buffer = Buffer._from_pomp(data)
        else:
            buffer = Buffer.from_bytes(data)
        res = od.pomp_conn_send_raw_buf(self._conn, buffer._buf)
        if res < 0:
            fut.set_exception(
                ConnectionError(f"Failed to send data: {os.strerror(-res)}")
            )
        else:
            self._send_futures[buffer] = fut
        if timeout is not None:
            self._loop.run_delayed(timeout, self._send_timeout_waiter, fut)
        return await fut

    async def _send_timeout_waiter(self, fut):
        if not fut.done():
            fut.set_exception(TimeoutError())
            self.logger.debug("Client connection send timedout")


class DatagramSocket(SocketBase):
    def read_from(self, n=None):
        return self._loop.run_async(self.aread_from, n)

    async def aread_from(self, n=None):
        if n is None:
            n = self._read_buffer_size
        if n == 0:
            return (b"", None, None)
        if n < 0:
            raise ValueError("read_from(n) n < 0: Can't wait for ever for datagrams")
        if not self._read_buffers:
            await self._wait_for_data()
            return await self.aread_from(n)
        buffer, host, port = self._read_buffers.popleft()
        if len(buffer) > n:
            tail = buffer[n:]
            del buffer[:n]
            self._read_buffers.appendleft((tail, host, port))
        else:
            self._maybe_resume_reading()
        return (buffer, host, port)

    def write_to(self, data, host, port):
        return self._loop.run_async(self.awrite_to, data, host, port)

    async def awrite_to(self, data, host, port):
        fut = Future(self._loop)
        buffer = Buffer.from_bytes(data)
        sockaddr, addrlen, _ = self._ctx._get_sockaddr(host, port)
        res = od.pomp_ctx_send_raw_buf_to(
            self._ctx._ctx, buffer._buf, sockaddr, addrlen
        )
        if res < 0:
            fut.set_exception(
                ConnectionError(
                    f"Failed to send data to {host}:{port}: {os.strerror(-res)}"
                )
            )
        else:
            self._send_futures[buffer] = fut
        return await fut


class ConnectionListener(Protocol):
    def connected(self, connection: Connection):
        pass

    def disconnected(self, connection: Connection):
        pass


class SocketCreationListener(Protocol):
    def socket_created(self, ctx: "SocketContext", fd: int, kind: SocketKind):
        pass


class DataListener(Protocol):
    def data_sent(
        self,
        ctx: "SocketContext",
        socket: "SocketBase",
        buffer: "Buffer",
        status: SendStatus,
    ):
        pass

    def data_received(
        self,
        ctx: "SocketContext",
        socket: "SocketBase",
        buffer: "Buffer",
    ) -> bool:
        pass


_WritableBuffer = typing.Union[bytearray, memoryview, array]


class Buffer:
    def __init__(self, capacity: int):
        self._buf: "od.POINTER_T[od.struct_pomp_buffer]" = od.pomp_buffer_new(capacity)
        if not self._buf:
            raise RuntimeError("Failed to allocate a pomp buffer")

    @classmethod
    def _from_pomp(cls, buf: "od.POINTER_T[od.struct_pomp_buffer]") -> "Buffer":
        obj = cls.__new__(cls)
        obj._buf = buf
        return obj

    @classmethod
    def from_bytes(cls, data: _WritableBuffer) -> "Buffer":
        obj = cls.__new__(cls)
        size = len(data)  # type: ignore
        byte_array = ctypes.c_ubyte * size
        pointer = ctypes.cast(
            ctypes.pointer(byte_array.from_buffer_copy(data)), ctypes.c_void_p
        )
        obj._buf = od.pomp_buffer_new_with_data(pointer, size)
        if not obj._buf:
            raise RuntimeError("Failed to allocate a pomp buffer")
        return obj

    @property
    def data(self):
        buf = ctypes.c_void_p()
        size = ctypes.c_size_t()
        od.pomp_buffer_get_data(self._buf, ctypes.byref(buf), ctypes.byref(size), None)
        if not buf:
            return ctypes.cast(buf, od.POINTER_T(ctypes.c_ubyte * 0))
        return ctypes.cast(buf, od.POINTER_T(ctypes.c_ubyte * size.value))

    def set_capacity(self, capacity: int) -> None:
        res = od.pomp_buffer_set_capacity(self._buf, capacity)
        if res < 0:
            raise RuntimeError(
                f"Failed to set pomp buffer capacity {self._buf}: {capacity}"
            )

    def set_length(self, length: int) -> None:
        res = od.pomp_buffer_set_len(self._buf, length)
        if res < 0:
            raise RuntimeError(
                f"Failed to set pomp buffer length {self._buf}: {length}"
            )

    def append(self, data: _WritableBuffer):
        size = len(data)  # type: ignore
        byte_array = ctypes.c_ubyte * size
        pointer: ctypes.c_void_p = ctypes.cast(
            byte_array.from_buffer(data), ctypes.c_void_p
        )
        res = od.pomp_buffer_append_data(self._buf, pointer, size)
        if res < 0:
            raise RuntimeError(f"Failed to append data to pomp buffer {self._buf}")

    def __hash__(self):
        return ctypes.cast(self._buf, ctypes.c_void_p).value

    def __eq__(self, other):
        if not isinstance(other, Buffer):
            return False
        return hash(self) == hash(other)

    def __len__(self):
        return len(self.data)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.destroy()

    def destroy(self) -> None:
        if self._buf is None:
            return
        od.pomp_buffer_unref(self._buf)
        self._buf = None


class SocketContext(ABC):
    def __init__(
        self,
        loop: typing.Optional[Loop] = None,
        name: typing.Optional[str] = None,
    ):
        if loop is None:
            loop = Loop(logger, name=name)
            loop.start()
        self._loop: Loop = loop
        self._buffers: typing.List[Buffer] = []
        self.logger = self._loop.logger
        self._callbacks = []
        event_cb = od.pomp_event_cb_t(self._event_cb)
        self._callbacks.append(event_cb)
        self._ctx: "od.POINTER_T[od.pomp_ctx]" = od.pomp_ctx_new_with_loop(
            event_cb, None, self._loop.pomp_loop
        )
        if not self._ctx:
            raise RuntimeError("Failed to create pomp context")
        socket_cb = od.pomp_socket_cb_t(self._socket_cb)
        self._callbacks.append(socket_cb)
        res = od.pomp_ctx_set_socket_cb(self._ctx, socket_cb)
        if res != 0:
            raise RuntimeError("Failed to set pomp context socket callback")
        send_cb = od.pomp_send_cb_t(self._send_cb)
        self._callbacks.append(send_cb)
        res = od.pomp_ctx_set_send_cb(self._ctx, send_cb)
        if res != 0:
            raise RuntimeError("Failed to set pomp context send callback")
        raw_cb = od.pomp_ctx_raw_cb_t(self._raw_cb)
        self._callbacks.append(raw_cb)
        # TODO: implement pomp message API?
        res = od.pomp_ctx_set_raw(self._ctx, raw_cb)
        if res != 0:
            raise RuntimeError("Failed to set pomp context as raw")
        self._loop.register_cleanup(self.adestroy)
        self._destroying = False
        self._socket_creation_listeners: typing.List[SocketCreationListener] = []
        self._data_listeners: typing.List[DataListener] = []

    def add_socket_creation_listener(
        self, socket_creation_listener: SocketCreationListener
    ):
        self._socket_creation_listeners.append(socket_creation_listener)

    def remove_socket_creation_listener(
        self, socket_creation_listener: SocketCreationListener
    ):
        self._socket_creation_listeners.remove(socket_creation_listener)

    def add_data_listener(self, data_listener: DataListener):
        self._data_listeners.append(data_listener)

    def remove_data_listener(self, data_listener: DataListener):
        self._data_listeners.remove(data_listener)

    async def adestroy(self):
        if not self._destroying:
            self._loop.unregister_cleanup(self.adestroy)
            self._destroying = True
        if self._ctx is None:
            return
        await self.astop()
        if self._ctx is None:
            return
        res = od.pomp_ctx_destroy(self._ctx)
        if res != 0:
            if res != -errno.EBUSY:
                self.logger.error(f"Failed to destroy pomp context: {os.strerror(-res)}")
            else:
                # Device or resource busy... The connection is still in use.
                pass
            # Destroying the pomp context is the only way to unregister internal pomp_timer fd,
            # so we have to try harder to prevent unnecessary pomp_loop_destroy errors about
            # unregisted fds.
            self._destroying = False
            self._loop.register_cleanup(self.adestroy)
        else:
            self._ctx = None

    def destroy(self):
        self._loop.run_later(self.adestroy)

    def enable_keepalive(self, idle: int, interval: int, count: int):
        return self._loop.run_async(self._enable_keep_alive, idle, interval, count)

    @callback_decorator()
    def _enable_keepalive(self, idle: int, interval: int, count: int):
        res = od.pomp_ctx_setup_keepalive(self._ctx, 1, idle, interval, count)
        if res != 0:
            raise RuntimeError("Failed to enable socket keepalive")

    def disable_keepalive(self):
        return self._loop.run_async(self._disable_keepalive)

    @callback_decorator()
    def _disable_keepalive(self):
        res = od.pomp_ctx_setup_keepalive(self._ctx, 0, 0, 0)
        if res != 0:
            raise RuntimeError("Failed to enable socket keepalive")

    @callback_decorator()
    def _event_cb(
        self,
        ctx: "od.POINTER_T[od.pomp_ctx]",
        event: od.pomp_event,
        conn: "od.POINTER_T[od.pomp_conn]",
        pomp_msg: "od.POINTER_T[od.pomp_msg]",
        userdata: "od.POINTER_T[None]",
    ):
        if event == od.POMP_EVENT_CONNECTED:
            self._on_connected_cb(conn)
        elif event == od.POMP_EVENT_DISCONNECTED:
            self._on_disconnected_cb(conn)
        elif event == od.POMP_EVENT_MSG:
            # pomp message API is not implemented, so we shouldn't get here
            self.logger.error("Unhandled pomp message event")
        else:
            self.logger.error(f"Unknown pomp event {event}")

    def _on_connected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        raise NotImplementedError(f"Not implemented for {self.__class__.__name__}")

    def _on_disconnected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        raise NotImplementedError(f"Not implemented for {self.__class__.__name__}")

    @callback_decorator()
    def _raw_cb(
        self,
        ctx: "od.POINTER_T[od.pomp_ctx]",
        conn: "od.POINTER_T[od.pomp_conn]",
        pomp_buf: "od.POINTER_T[od.pomp_buf]",
        userdata: "od.POINTER_T[None]",
    ):
        connection = self._get_connection(conn)
        buffer = Buffer._from_pomp(pomp_buf)
        consumed = False
        for data_listener in self._data_listeners:
            consumed |= data_listener.data_received(self, connection, buffer)
        if consumed:
            connection._pop_read_buffer()
            connection._maybe_resume_reading()

    @callback_decorator()
    def _socket_cb(
        self,
        ctx: "od.POINTER_T[od.pomp_ctx]",
        fd: int,
        kind: "od.pomp_socket_kind",
        userdata: "od.POINTER_T[None]",
    ):
        kind = SocketKind(kind)
        for socket_creation_listener in self._socket_creation_listeners:
            socket_creation_listener.socket_created(self, fd, kind)

    @callback_decorator()
    def _send_cb(
        self,
        ctx: "od.POINTER_T[od.pomp_ctx]",
        conn: "od.POINTER_T[od.pomp_conn]",
        buf: "od.POINTER_T[od.pomp_buf]",
        status: ctypes.c_uint32,
        cookie: ctypes.c_void_p,
        userdata: ctypes.c_void_p,
    ):
        buffer = Buffer._from_pomp(buf)
        status = SendStatus(status)
        connection = self._get_connection(conn)

        for data_listener in self._data_listeners:
            data_listener.data_sent(self, connection, buffer, status)

    @abstractmethod
    def _get_connection(self, conn: "od.POINTER_T[od.struct_pomp_conn]"):
        pass

    def _get_sockaddr(self, addr, port):
        if not addr:
            if socket.has_dualstack_ipv6:
                addr = "::"  # IPv4+IPv6 any address
            else:
                addr = 0  # IPv4 any address
        if isinstance(addr, bytes):
            addr = addr.decode()
        addr = ipaddress.ip_address(addr)
        if isinstance(addr, ipaddress.IPv4Address):
            sockaddr_in = _struct_sockaddr_in(
                socket.AF_INET,
                socket.htons(port),
                (ctypes.c_ubyte * 4).from_buffer_copy(addr.packed),
            )
            sockaddr = ctypes.cast(
                ctypes.pointer(sockaddr_in), od.POINTER_T(od.struct_sockaddr)
            )
            addrlen = ctypes.sizeof(sockaddr_in)
            data = sockaddr_in
        elif isinstance(addr, ipaddress.IPv6Address):
            _, _, _, _, sockaddr_ = next(
                iter(
                    socket.getaddrinfo(
                        str(addr),
                        port,
                        family=socket.AF_INET6,
                        type=socket.SocketKind.SOCK_DGRAM,
                    )
                )
            )
            _, _, flowinfo, scope_id = sockaddr_
            sockaddr_in6 = _struct_sockaddr_in6(
                socket.AF_INET6,
                socket.htons(port),
                flowinfo,
                (ctypes.c_ubyte * 16).from_buffer_copy(addr.packed),
                scope_id,
            )
            sockaddr = ctypes.cast(
                ctypes.pointer(sockaddr_in6), od.POINTER_T(od.struct_sockaddr)
            )
            addrlen = ctypes.sizeof(sockaddr_in6)
            data = sockaddr_in6
        else:
            raise ValueError(f"Unsupported address family: {addr}")
        return sockaddr, addrlen, data


class UdpDataListener:
    def data_received(self, udp_context, dgram_socket: DatagramSocket, buffer: Buffer):
        dgram_socket._feed_data_from(buffer)
        return False

    def data_sent(
        self,
        tcp_server,
        dgram_socket: DatagramSocket,
        buffer: Buffer,
        send_status: SendStatus,
    ):
        dgram_socket._ack_data(buffer, send_status)


class UdpContext(SocketContext):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self._bound = False
        self._dgram_socket = DatagramSocket(self, None)
        self.add_data_listener(UdpDataListener())

    def bind(self, addr: typing.Union[str, int], port: int):
        return self._loop.run_async(self._bind, addr, port)

    @callback_decorator()
    def _bind(self, addr: typing.Union[str, int], port: int):
        sockaddr, addrlen, _ = self._get_sockaddr(addr, port)
        res = od.pomp_ctx_bind(self._ctx, sockaddr, addrlen)
        if res < 0:
            raise ConnectionError(f"Failed to bind {addr}:{port} datagram socket")
        self._bound = True
        return True

    def write_to(self, data, addr: typing.Union[str, int], port: int):
        return self._dgram_socket.write_to(data, addr, port)

    def read_from(self, n=-1):
        return self._dgram_socket.read_from(n)

    def stop(self):
        return self._loop.run_async(self.astop)

    async def astop(self):
        if self._bound:
            res = od.pomp_ctx_stop(self._ctx)
            if res < 0:
                self.logger.error("Failed to stop udp bound socket")
            else:
                self._bound = False
        self._dgram_socket = None
        return True

    def _get_connection(self, conn: "od.POINTER_T[od.struct_pomp_conn]"):
        self._dgram_socket._set_conn(conn)
        return self._dgram_socket


class TcpClientListener:
    def data_received(self, client, connection: Connection, buffer: Buffer):
        client._data_received(buffer)
        return False

    def data_sent(
        self, client, connection: Connection, buffer: Buffer, send_status: SendStatus
    ):
        client._data_sent(buffer, send_status)


class TcpClient(SocketContext):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self._connecting = False
        self._disconnecting = False
        self._connected = False
        self._client_connection = None
        self._connection_listeners: typing.List[ConnectionListener] = []
        self._connection_condition = Condition(loop=self._loop)
        self._disconnection_condition = Condition(loop=self._loop)
        self.add_data_listener(TcpClientListener())

    def add_connection_listener(self, connection_listener: ConnectionListener):
        self._connection_listeners.append(connection_listener)

    def remove_connection_listener(self, connection_listener: ConnectionListener):
        self._connection_listeners.remove(connection_listener)

    def connect(self, addr, port, timeout=None):
        return self._loop.run_async(self.aconnect, addr, port, timeout=timeout)

    async def aconnect(self, addr, port, timeout=None):
        if self._client_connection:
            self.logger.debug("Client already connected")
            return True
        if not self._connecting:
            sockaddr, addrlen, _ = self._get_sockaddr(addr, port)
            res = od.pomp_ctx_connect(self._ctx, sockaddr, addrlen)
            if res < 0:
                self.logger.error(f"Failed to connect to {addr.decode()}:{port}: {res}")
                return False
            self._connecting = True

            if timeout is None:
                timeout = DEFAULT_CONNECTION_TIMEOUT
            self._loop.run_delayed(timeout, self._connection_waiter)
        async with self._connection_condition:
            await self._connection_condition.wait()
        self._connected = self._client_connection is not None
        return self._connected

    async def _connection_waiter(self):
        async with self._connection_condition:
            self._connecting = False
            self._connection_condition.notify_all()

    def disconnect(self):
        return self._loop.run_async(self.adisconnect)

    async def adisconnect(self):
        if self._ctx is None:
            return True
        if not self._disconnecting:
            res = od.pomp_ctx_stop(self._ctx)
            if res < 0:
                self.logger.error(f"Failed to disconnect client: {self._ctx} {os.strerror(-res)}")
                return False
            self._disconnecting = True
            if self.connected:
                self._loop.run_delayed(DEFAULT_CONNECTION_TIMEOUT, self._disconnection_waiter)
        if self.connected:
            async with self._disconnection_condition:
                await self._disconnection_condition.wait()
        self._connected = False
        return True

    async def _disconnection_waiter(self):
        async with self._disconnection_condition:
            self._disconnecting = False
            self._disconnection_condition.notify_all()

    async def astop(self):
        return await self.adisconnect()

    def _get_connection(self, conn: "od.POINTER_T[od.struct_pomp_conn]"):
        return self._client_connection

    def _data_received(self, buffer):
        assert self._client_connection is not None
        self._client_connection._feed_data(buffer)

    def _data_sent(self, buffer: Buffer, send_status: SendStatus):
        assert self._client_connection is not None
        self._client_connection._ack_data(buffer, send_status)

    def _on_connected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        self._loop.run_later(self._aon_connected, conn)

    async def _aon_connected(self, conn: "od.POINTER_T[od.pomp_conn]"):
        async with self._connection_condition:
            self._client_connection = Connection(self, conn)
            self._connecting = False
            self._connection_condition.notify_all()
            for connection_listener in self._connection_listeners:
                connection_listener.connected(self._client_connection)

    def _on_disconnected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        assert self._client_connection is not None
        assert od.pomp_conn_get_fd(conn) == self._client_connection.fileno
        self._loop.run_later(self._aon_disconnected, conn)

    async def _aon_disconnected(self, conn: "od.POINTER_T[od.pomp_conn]"):
        async with self._disconnection_condition:
            if self._client_connection is not None:
                self._client_connection._feed_eof()
            self._disconnecting = False
            self._connected = False
            self._disconnection_condition.notify_all()
            if self._client_connection is not None:
                for connection_listener in self._connection_listeners:
                    connection_listener.disconnected(self._client_connection)

    def read(self, n=-1):
        if not self.connected:
            raise ConnectionClosedError()
        return self._client_connection.read(n)

    def write(self, data):
        if not self.connected:
            raise ConnectionClosedError()
        return self._client_connection.write(data)

    async def aread(self, n=-1):
        if not self.connected:
            raise ConnectionClosedError()
        return await self._client_connection.aread(n)

    async def awrite(self, data):
        if not self.connected:
            raise ConnectionClosedError()
        return await self._client_connection.awrite(data)

    @property
    def connected(self):
        return self._connected and self._client_connection is not None

    @property
    def fd(self):
        return self._client_connection.fileno if self._client_connection else -1


class TlsClient(TcpClient):
    def __init__(self, *args, ssl_context=None, **kwds):
        super().__init__(*args, **kwds)
        if ssl_context is None:
            ssl_context = ssl.create_default_context()

        self._ssl_context = ssl_context
        self._read_bio = ssl.MemoryBIO()
        self._write_bio = ssl.MemoryBIO()
        self._ssl_object = None
        self._want_read_sem = Semaphore(value=0)
        self._data_received_sem = Semaphore(value=0)
        self._handshaken = False
        self._processing = False
        self._tls_data_listeners = []

    def _create_ssl_object(self, server_hostname):
        self._ssl_object = self._ssl_context.wrap_bio(
            self._read_bio, self._write_bio, server_hostname=server_hostname, server_side=False)

    def add_data_listener(self, listener):
        if isinstance(listener, TcpClientListener):
            super().add_data_listener(listener)
        else:
            self._tls_data_listeners.append(listener)

    def remove_data_listener(self, listener):
        if isinstance(listener, TcpClientListener):
            super().remove_data_listener(listener)
        else:
            self._tls_data_listeners.remove(listener)

    async def aconnect(self, addr, port, *, server_hostname, timeout=None):
        self._create_ssl_object(server_hostname)
        if not await super().aconnect(addr, port, timeout=timeout):
            return False
        self._processing = True
        self._loop.run_async(self._process_data)
        while True:
            try:
                self._ssl_object.do_handshake()
            except ssl.SSLWantReadError:
                if self._write_bio.pending:
                    await super().awrite(self._write_bio.read())
                await self._want_read_sem.acquire()
            else:
                if self._write_bio.pending:
                    await super().awrite(self._write_bio.read())
                self._handshaken = True
                return True

    def _data_received(self, buffer):
        self._read_bio.write(bytes(buffer.data.contents))
        self._data_received_sem.release()

    async def _process_tls_data(self):
        if not self._handshaken:
            self._want_read_sem.release()
            return None
        try:
            data = self._ssl_object.read()
        except ssl.SSLWantReadError:
            if self._write_bio.pending and self._connected:
                await super().awrite(self._write_bio.read())
            return None
        except ssl.SSLWantWriteError:
            if self._connected:
                await super().awrite(self._write_bio.read())
            return None
        else:
            if self._write_bio.pending and self._connected:
                await super().awrite(self._write_bio.read())
        if not data:
            return None
        return data

    async def _process_plain_data(self, data):
        buffer = Buffer.from_bytes(data)
        assert len(data) == len(buffer.data.contents)
        consumed = False
        super()._data_received(buffer)
        for data_listener in self._tls_data_listeners:
            consumed |= data_listener.data_received(self, self._client_connection, buffer)
        if consumed:
            self._client_connection._pop_read_buffer()
            self._client_connection._maybe_resume_reading()
        self._want_read_sem.release()
        buffer.destroy()

    async def _process_data(self):
        while self._processing:
            await self._data_received_sem.acquire()
            while True:
                try:
                    data = await self._process_tls_data()
                except ssl.SSLZeroReturnError:
                    self._loop.logger.info("Connection closed")
                    return
                if not data:
                    break
                await self._process_plain_data(data)

    async def awrite(self, data):
        while True:
            try:
                self._ssl_object.write(data)
            except ssl.SSLWantWriteError:
                await super().awrite(self._write_bio.read())
            else:
                if self._write_bio.pending:
                    await super().awrite(self._write_bio.read())
                break

    async def adisconnect(self):
        try:
            self._ssl_object.unwrap()
        except ssl.SSLWantReadError:
            if not self._connected:
                return
            if self._write_bio.pending:
                await super().awrite(self._write_bio.read())
        except ssl.SSLWantWriteError:
            if not self._connected:
                return
            await super().awrite(self._write_bio.read())
        except ssl.SSLSyscallError as e:
            self._loop.logger.warning(f"SSL connection shutdown error: {e}")
        else:
            if self._write_bio.pending and self._connected:
                await super().awrite(self._write_bio.read())
            return
        finally:
            self._processing = False
            self._data_received_sem.release()
            self._read_bio.write_eof()
            self._write_bio.write_eof()
            await super().adisconnect()


class TcpServerListener:
    def data_received(self, tcp_server, connection: Connection, buffer: Buffer):
        connection._feed_data(buffer)
        return False

    def data_sent(
        self,
        tcp_server,
        connection: Connection,
        buffer: Buffer,
        send_status: SendStatus,
    ):
        connection._ack_data(buffer, send_status)


class TcpServer(SocketContext):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self._listening = False
        self._connection_listeners: typing.List[ConnectionListener] = []
        self._connections: typing.List[Connection] = []
        self._last_accepted = -1
        self._accept_awaiters: typing.List[Future] = []
        self.add_data_listener(TcpServerListener())

    def listen(self, addr: typing.Union[str, int], port: int):
        return self._loop.run_async(self._listen, addr, port)

    @callback_decorator()
    def _listen(self, addr: typing.Union[str, int], port: int):
        sockaddr, addrlen, _ = self._get_sockaddr(addr, port)
        res = od.pomp_ctx_listen(self._ctx, sockaddr, addrlen)
        if res < 0:
            raise ConnectionError(f"Failed to listen {addr}:{port} tcp socket")
        self._listening = True
        return True

    def accept(self):
        return self._loop.run_async(self._accept)

    @callback_decorator()
    def _accept(self):
        if self._last_accepted + 1 < len(self._connections):
            self._last_accepted += 1
            return self._connections[self._last_accepted]
        else:
            awaiter = Future(self._loop)
            self._accept_awaiters.append(awaiter)
            return awaiter

    def add_connection_listener(self, connection_listener: ConnectionListener):
        self._connection_listeners.append(connection_listener)

    def remove_connection_listener(self, connection_listener: ConnectionListener):
        self._connection_listeners.remove(connection_listener)

    def _on_connected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        connection = Connection(self, conn)
        self._connections.append(connection)
        for connection_listener in self._connection_listeners:
            connection_listener.connected(connection)
        if self._accept_awaiters:
            awaiter = self._accept_awaiters.pop(0)
            self._last_accepted += 1
            awaiter.set_result(connection)

    def _on_disconnected_cb(self, conn: "od.POINTER_T[od.pomp_conn]"):
        for connection in self._connections[:]:
            if connection.fileno == od.pomp_conn_get_fd(conn):
                self._loop.run_later(connection._feed_eof)
                self._loop.run_later(lambda: self._connections.remove(connection))
                break
        else:
            return
        for connection_listener in self._connection_listeners:
            connection_listener.disconnected(connection)

    def stop(self):
        return self._loop.run_async(self.astop)

    async def astop(self):
        if self._listening:
            res = od.pomp_ctx_stop(self._ctx)
            if res < 0:
                self.logger.error("Failed to stop tcp server listening")
            else:
                self._listening = False
        return True

    def _get_connection(self, conn: "od.POINTER_T[od.struct_pomp_conn]"):
        fd = od.pomp_conn_get_fd(conn)
        for connection in self._connections:
            if connection.fileno == fd:
                return connection
        raise ValueError(f"Unknown server connection fd={fd}")


class DNSResolver:
    def __init__(
            self,
            max_workers: typing.Optional[int] = None
    ):
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="olympe.networking.DNSResolver",
        )

    async def resolve(self, host: str, port: int, family: socket.AddressFamily = socket.AF_UNSPEC):
        fut = Future()
        self._executor.submit(self._resolve, host, port).add_done_callback(fut.set_from)
        return await fut

    def _resolve(self, host: str, port: int, family: socket.AddressFamily = socket.AF_UNSPEC):
        resolved = []
        for family, _, _, _, sockaddr in socket.getaddrinfo(
                host, port, family, socket.SOCK_STREAM
        ):
            resolved.append((family, sockaddr))
        return resolved

    def close(self) -> None:
        self._executor.shutdown()


INADDR_ANY = ipaddress.ip_address("0.0.0.0")
INADDR_NONE = ipaddress.ip_address("255.255.255.255")
