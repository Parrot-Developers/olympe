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

import h11
import json
import logging
import os
import queue
import re
import wsproto
import wsproto.connection
import wsproto.events
import wsproto.utilities

from collections import deque
from collections.abc import Mapping as AbstractMapping
from typing import (
    cast,
    BinaryIO,
    Dict,
    Generator,
    Iterable,
    Mapping,
    Optional,
    Tuple,
    Union,
)
from urllib.parse import urlencode, urlsplit


from olympe.__version__ import __version__
from olympe.concurrent import Loop, Semaphore, get_running_loop
from .networking import (
    Buffer,
    Connection as NetworkingConnection,
    ConnectionClosedError,
    DataListener,
    DNSResolver,
    TcpClient,
    TlsClient,
)


DEFAULT_RECONNECTION_ATTEMPTS = 1
DEFAULT_REQUEST_TIMEOUT = 2.0


class HTTPError(Exception):
    def __init__(self, message: str, response: Optional["ResponseBase"] = None):
        super().__init__(message)
        self.response = response


class HTTPConnectionError(HTTPError, ConnectionError):
    pass


class HTTPWebsocketRejected(HTTPError):
    pass


_default_headers = {
    "user-agent": bytes(f"parrot-olympe-{__version__}", encoding="utf-8"),
    "accept": b"*/*",
    "connection": b"keep-alive",
}


class Request:
    def __init__(
        self,
        url: str,
        method: Optional[str] = None,
        params: Optional[Mapping[str, str]] = None,
        headers: Optional[
            Union[Mapping[str, bytes], Iterable[Tuple[str, bytes]]]
        ] = None,
        data: Union[bytes, BinaryIO, None] = None,
    ):
        url = url.lstrip()
        method = method or "GET"
        self.method: str = method
        self.url: str = url
        self.params: Optional[Mapping[str, str]] = params
        if headers:
            if isinstance(headers, AbstractMapping):
                headers = {
                    k.lower(): v for k, v in cast(Mapping[str, bytes], headers).items()
                }
            else:
                headers = {k.lower(): v for (k, v) in headers}
        else:
            headers = _default_headers.copy()
        self.headers: Dict[str, bytes] = headers
        self.data: Union[bytes, BinaryIO, None] = data

        split_result = urlsplit(url)
        scheme = split_result.scheme
        host = split_result.hostname
        port = split_result.port
        path = split_result.path
        query = split_result.query
        username = split_result.username
        password = split_result.password
        fragment = split_result.fragment

        if not scheme:
            raise ValueError(f"Invalid URL - no scheme in url: {url}")

        if not host:
            raise ValueError(f"Invalid URL - no host in url: {url}")

        if port is None:
            if scheme in ("http", "ws"):
                port = 80
            elif scheme in ("https", "wss"):
                port = 443
            else:
                port = 80
        else:
            port = int(port)
        if path is None:
            path = "/"

        if username:
            if not password:
                auth = username
            else:
                auth = f"{username}:{password}"
        else:
            auth = None

        if not self.headers.get("host"):
            self.headers["host"] = bytes(host, encoding="utf-8")

        self.scheme: str = scheme
        self.auth: Optional[str] = auth
        self.host: str = host
        self.port: int = port
        self.path: str = path
        self.query: Optional[str] = query
        self.fragment: Optional[str] = fragment

    def _h11(self) -> Generator[h11.Event, None, None]:
        if self.data is not None and "content-length" not in self.headers:
            # Compute the length content of the data that are requested to upload
            # through the PUT request
            if isinstance(self.data, bytes):
                self.headers["content-length"] = bytes(
                    str(len(self.data)), encoding="utf-8"
                )
            # In case data are stream objects
            elif hasattr(self.data, "seek"):
                self.data = cast(BinaryIO, self.data)
                self.data.seek(0, os.SEEK_END)
                self.headers["content-length"] = bytes(
                    str(self.data.tell()), encoding="utf-8"
                )
                self.data.seek(0, os.SEEK_SET)
            # Any other types is not handled
            else:
                raise ValueError(
                    f"Unexpected data type '{type(self.data)}' "
                    "instead of bytes|BinaryIO|None"
                )
        if self.params is None:
            target = self.path
        else:
            target = f"{self.path}?{urlencode(self.params)}"
        yield h11.Request(
            method=self.method,
            target=target,
            headers=list((k.encode(), v) for k, v in self.headers.items()),
        )
        if self.data is not None:
            # According to the data type, the way to send the request differs
            if isinstance(self.data, bytes):
                yield h11.Data(data=self.data)
            else:
                while True:
                    chunk = self.data.read(4096 << 10)
                    if not chunk:
                        break
                    yield h11.Data(data=chunk)
        yield h11.EndOfMessage()

    def _wsproto(self):
        return wsproto.events.Request(
            host=self.host, target=f"{self.path}?{self.query}"
        )


class ResponseBase:
    def __init__(self, request, response_event):
        self._request = request
        self._response_event = response_event
        self._headers: Optional[Dict[str, bytes]] = None
        self._content_type = None
        self._content_encoding = None

    @property
    def ok(self):
        return self._response_event.status_code < 400

    @property
    def status_code(self):
        return self._response_event.status_code

    @property
    def headers(self) -> Dict[str, bytes]:
        if self._headers is None:
            self._headers = {h.decode(): v for (h, v) in self._response_event.headers}
        return self._headers

    @property
    def content_type(self):
        if self._content_type is not None:
            return self._content_type
        content_type = self.headers.get("content-type")
        if content_type is not None:
            self._content_type = content_type.decode()
        return self._content_type

    @property
    def content_encoding(self):
        if self._content_encoding is not None:
            return self._content_encoding
        if self.content_type is None:
            self._content_encoding = "utf-8"
            return self._content_encoding
        m = re.search(r"charset=([^;]+)", self.content_type)
        if not m:
            self._content_encoding = "utf-8"
            return self._content_encoding
        self._content_encoding = m.group(1).strip()
        return self._content_encoding

    def raise_for_status(self):
        if not self.ok:
            raise HTTPError(
                f"HTTP {self._response_event.status_code}: "
                f"{self._request.url} {self._response_event.reason}",
                self,
            )

    def __repr__(self):
        return "<Response [%s]>" % (self.status_code)

    def __bool__(self):
        return self.ok


class ContentGenerator:
    def __init__(self, loop):
        self._loop = loop
        self._timeout = None
        self._chunk_sem = Semaphore(loop=loop, value=0)
        self._chunks = deque()

    def set_timeout(self, timeout: Optional[float]):
        self._timeout = timeout

    def __iter__(self):
        while True:
            self._loop.run_async(self._chunk_sem.acquire).result_or_cancel(
                self._timeout
            )
            try:
                yield self._chunks.popleft()
            except IndexError:
                return

    async def _afeed(self, chunk):
        self._chunks.append(chunk)
        self._chunk_sem.release()

    async def _afeed_eof(self):
        self._chunk_sem.release()


class Response(ResponseBase):
    def __init__(self, connection, request, response_event, stream=False):
        super().__init__(request, response_event)
        self._connection = connection
        self._stream = stream
        self._generator = None
        self._generator_task = None
        if self._stream:
            self._generator = ContentGenerator(self._connection._loop)
            self._generator_task = self._connection._loop.run_async(
                self._generator_feed
            )
            self._connection._loop.register_cleanup(self._generator_cleanup)
        self.logger = self._connection.logger

    async def text(self):
        return "".join([chunk.decode(self.content_encoding) async for chunk in self])

    async def json(self):
        return json.loads(await self.text())

    async def content(self):
        data = b""
        async for chunk in self:
            data += chunk
        return data

    async def __aiter__(self):
        self._connection._reading = True
        if self._response_event.status_code in (300, 301, 302):
            return
        while True:
            event = await self._connection._loop.run_async(
                self._connection._get_next_event
            )
            if event == h11.EndOfMessage() or event == h11.ConnectionClosed():
                break
            if not isinstance(event, h11.Data):
                self.logger.error(f"Unexpected event {event} for {self._request.url}")
            assert isinstance(event, h11.Data), f"Unexpected event {event}"
            yield bytes(event.data)
        self._connection._reading = False

    async def _generator_feed(self):
        assert self._generator is not None
        async for chunk in self:
            await self._generator._afeed(chunk)
        await self._generator._afeed_eof()
        await self._generator_cleanup()

    async def _generator_cleanup(self):
        if self._generator_task is not None:
            self._generator_task.cancel()
            self._generator_task = None


class SyncResponse(ResponseBase):
    def __init__(
        self,
        request,
        response_event,
        content: Optional[bytes] = None,
        content_generator: Optional[ContentGenerator] = None,
    ):
        super().__init__(request, response_event)
        self._content = content
        self._content_generator = content_generator

    def text(self):
        if self._content is None:
            return None
        return self._content.decode(self.content_encoding)

    def json(self):
        text = self.text()
        if text is None:
            return None
        return json.loads(text)

    def content(self):
        return self._content

    def iter_content(self, timeout=None):
        if self._content_generator is None:
            yield self._content
        else:
            self._content_generator.set_timeout(timeout)
            yield from iter(self._content_generator)


class WebSocket:
    def __init__(self, connection, request):
        self._connection = connection
        self._request = request

    # Because WebSocket is supposed to run within the process' loop, its methods have to
    # be launched calling the so-called loop

    async def aread(self, timeout=None) -> Union[str, bytes, None]:
        return await self._connection._loop.await_for(timeout, self._aread)

    async def _aread(self) -> Union[str, bytes, None]:
        if (
            self._connection._conn.state is wsproto.connection.ConnectionState.CLOSED
            and self._connection._events.empty()
        ):
            return None
        event = await self._connection._get_next_event()
        if isinstance(event, wsproto.events.CloseConnection):
            return None
        assert isinstance(
            event,
            (
                wsproto.events.Message,
                wsproto.events.TextMessage,
                wsproto.events.BytesMessage,
            ),
        )
        return event.data

    async def awrite(self, data: bytes) -> bool:
        return await self._connection._loop.run_async(self._awrite, data)

    async def _awrite(self, data: bytes) -> bool:
        if (
            self._connection._conn.state is not wsproto.connection.ConnectionState.OPEN
            or not self._connection.connected
        ):
            raise ConnectionClosedError()
        data = self._connection._conn.send(wsproto.events.Message(data=data))
        return await self._connection.awrite(data)

    async def aclose(self) -> bool:
        return await self._connection._loop.run_async(self._aclose)

    async def _aclose(self) -> bool:
        if not self._connection.connected:
            return True
        if self._connection._conn.state in (
            wsproto.connection.ConnectionState.CLOSED,
            wsproto.connection.ConnectionState.LOCAL_CLOSING,
        ):
            return True
        close_event = wsproto.events.CloseConnection(0)
        data = self._connection._conn.send(close_event)
        self._connection._events.put_nowait(close_event)
        self._connection._event_sem.release()
        ws_closed = False
        try:
            ws_closed = await self._connection.awrite(data)
        finally:
            ws_closed = await self._connection.adisconnect() and ws_closed
        return ws_closed


class ConnectionListener(DataListener):
    def __init__(self, connection: "Connection"):
        self._connection = connection

    def data_sent(self, *args, **kwds):
        pass

    def data_received(
        self,
        client,
        connection: "NetworkingConnection",
        buffer: "Buffer",
    ):
        self._connection._feed_data(bytes(buffer.data.contents))
        return True

    def connected(self, connection: "Connection"):
        pass

    def disconnected(self, connection: "Connection"):
        if self._connection._client is not None:
            self._connection._feed_eof()


class Connection:
    def __init__(self, loop, session, scheme):
        self._session = session
        self._loop = loop
        self.logger = self._loop.logger
        self._resolver = session._resolver
        self._scheme = scheme
        if scheme in ("http", "https"):
            self._conn_http = h11.Connection(our_role=h11.CLIENT)
            self._conn = self._conn_http
        else:
            self._conn_ws = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
            self._conn = self._conn_ws
        if scheme in ("http", "ws"):
            self._client = TcpClient(self._loop)
        else:
            self._client = TlsClient(self._loop)
        connection_listener = ConnectionListener(self)
        self._client.add_data_listener(connection_listener)
        self._client.add_connection_listener(connection_listener)
        self._events = queue.Queue()
        self._event_sem = Semaphore(value=0, loop=loop)
        self._reusing = False
        self._sending = False
        self._reading = False
        self._endpoint = None
        self._request = None

    async def connect_and_send(
        self,
        request: Request,
        timeout: float,
        stream: bool = False,
    ) -> Optional[Union[Response, WebSocket]]:
        self._sending = True
        self._request = request

        connected = await self._loop.await_for(timeout, self.connect, request)
        if not connected:
            raise HTTPConnectionError(
                "Connection to {request.host}:{request.port} failed"
            )
        try:
            return await self._loop.run_async(self.send, request, stream)
        # Whether the connection attempt has failed or succeeded, the sending
        # process is declared over
        finally:
            self._sending = False

    async def connect(self, request: Request) -> bool:
        self._request = request
        if self._endpoint is None:
            self._endpoint = (request.host, request.port)
        else:
            assert self._endpoint == (request.host, request.port)
        # self._client refers to the TCP (or TLS) connection remote. If the
        # connection to that server is already established, there is no need to connect.
        # Otherwise, a connection to that remote on the specific address and port is
        # requested
        if self._client.connected:
            connected = True
        else:
            for _, sockaddr in await self._resolver.resolve(request.host, request.port):
                addr, *_ = sockaddr
                break
            else:
                raise HTTPConnectionError(
                    f"Cannot resolve {request.host}:{request.port}"
                )
            if self._scheme in ("http", "ws"):
                connected = await self._client.aconnect(addr, request.port)
            else:
                connected = await self._client.aconnect(
                    addr, request.port, server_hostname=request.host
                )
        return connected

    async def send(
        self, request: Request, stream: bool
    ) -> Optional[Union[Response, WebSocket]]:
        try:
            if request.scheme in ("http", "https"):
                return await self._send_http(request, stream)
            else:
                return await self._send_ws(request)
        except HTTPError:
            self.logger.error("Unable to send a request to a closed connection")

    async def _send_http(self, request: Request, stream: bool) -> Response:
        data = bytes()
        for h11_event in request._h11():
            chunk = self._conn_http.send(h11_event)
            if chunk is not None:
                data += chunk
        self._reusing = False
        await self.awrite(data)
        event = await self._get_next_event()
        # If a closed connection is detected, there is no need to send an HTTP response
        if isinstance(event, h11.ConnectionClosed):
            raise HTTPConnectionError("Connection closed")
        assert isinstance(event, h11.Response)
        await self._aclosed_http()  # Handle: keep-alive/connection: closed
        return Response(self, request, event, stream)

    async def _send_ws(self, request: Request) -> WebSocket:
        # Send the HTTP to Websocket connection upgrade request
        data = self._conn_ws.send(request._wsproto())
        self._reusing = False
        await self.awrite(data)
        event = await self._get_next_event()
        if isinstance(event, wsproto.events.CloseConnection):
            raise HTTPConnectionError("Websocket connection closed")
        if isinstance(event, wsproto.events.RejectConnection):
            raise HTTPWebsocketRejected("Websocket connection rejected")
        # If the connection upgrade is accepted, a websocket is instantiated
        assert isinstance(event, wsproto.events.AcceptConnection)
        return WebSocket(self, request)

    async def awrite(self, data: bytes):
        assert self._client is not None
        return await self._client.awrite(data)

    async def _get_next_event(self):
        await self._event_sem.acquire()
        return self._events.get_nowait()

    def _disconnect_and_destroy_client(self):
        if not self._client.stopped:
            self._client.disconnect()
        self._client.destroy()
        self._client = None

    def _feed_data(self, data: bytes):
        self._conn.receive_data(data)
        assert self._client is not None
        if self._scheme in ("http", "https"):
            while True:
                event = self._conn_http.next_event()
                if event in (h11.NEED_DATA, h11.PAUSED):
                    return
                if event == h11.ConnectionClosed():
                    assert self._request is not None
                    self._loop.logger.error(
                        f"unexpected end of request for {self._request.url}"
                    )
                    self._disconnect_and_destroy_client()
                    return
                self._events.put_nowait(event)
                self._event_sem.release()
                if isinstance(event, h11.EndOfMessage):
                    if self._conn_http.our_state is h11.MUST_CLOSE:
                        self._feed_eof()
                        return
                    elif (
                        self._conn_http.our_state is h11.DONE
                        and self._conn_http.their_state is h11.DONE
                    ):
                        self._conn_http.start_next_cycle()
                        return
        else:
            for event in self._conn_ws.events():
                assert self._client is not None
                if isinstance(event, wsproto.events.Ping):
                    pong = self._conn_ws.send(event.response())
                    self._client.write(pong)
                    continue
                if isinstance(event, wsproto.events.Pong):
                    continue
                if isinstance(event, wsproto.events.CloseConnection):
                    if self._conn_ws.state not in (
                        wsproto.connection.ConnectionState.CLOSED,
                        wsproto.connection.ConnectionState.LOCAL_CLOSING,
                    ):
                        close = self._conn_ws.send(event.response())
                        if not self._client.stopped:
                            self._client.write(close)
                        self._disconnect_and_destroy_client()
                    continue
                self._events.put_nowait(event)
                self._event_sem.release()

    def _feed_eof(self):
        assert self._client is not None
        if self._scheme in ("http", "https"):
            close_event = h11.ConnectionClosed()
            self._conn_http.send(close_event)
            self._events.put_nowait(close_event)
            self._event_sem.release()
        else:
            close_event = wsproto.events.CloseConnection(0)
            if self._conn_ws.state not in (
                wsproto.connection.ConnectionState.CLOSED,
                wsproto.connection.ConnectionState.LOCAL_CLOSING,
            ):
                try:
                    data = self._conn_ws.send(close_event)
                    if self._client.connected:
                        self._client.write(data)
                except wsproto.utilities.LocalProtocolError:
                    pass
            self._events.put_nowait(close_event)
            self._event_sem.release()
            self._disconnect_and_destroy_client()

    def destroy(self):
        if self._client is not None:
            self._client.destroy()
            self._client = None

    def reuse(self):
        if self._reusing or self._sending or self._reading:
            return False
        if self._scheme in ("http", "https"):
            return self._reuse_http()
        else:
            return self._reuse_websocket()

    def _reuse_http(self):
        if (
            self._conn_http.our_state is h11.IDLE
            and self._conn_http.their_state is h11.IDLE
            and self._events.empty()
            and self._event_sem.locked()
        ):
            self._reusing = True
            return True
        else:
            return False

    def _reuse_websocket(self):
        # I'm not sure we can actually ever reuse a tcp connection after a websocket is
        # closed
        return False

    async def aclosed(self):
        if self._scheme in ("http", "https"):
            return await self._aclosed_http()
        else:
            return self._closed_websocket()

    async def _aclosed_http(self):
        if self._conn_http.their_state is h11.CLOSED:
            self._feed_eof()
            return True
        elif self._conn_http.our_state is h11.CLOSED:
            self.destroy()
            return True
        elif self._conn_http.our_state is h11.MUST_CLOSE:
            # Cannot shutdown our send stream without closing the socket (both-ways)
            pass
        return False

    def _closed_websocket(self):
        return self._conn_ws.state is wsproto.connection.ConnectionState.CLOSED

    def disconnect(self):
        if self._client is None:
            return
        if self._client.stopped:
            return True
        return self._client.disconnect()

    async def adisconnect(self):
        if self._client is None:
            return
        if self._client.stopped:
            return
        return await self._client.adisconnect()

    @property
    def connected(self):
        return self._client is not None and self._client.connected

    @property
    def fd(self):
        if self._client is None:
            return -1
        return self._client.fd


class Session:
    def __init__(self, loop=None):
        if loop is None:
            loop = get_running_loop()
        self._loop = loop
        self._resolver = DNSResolver()
        self._connection_pools = dict()
        self._loop.register_cleanup(self._acleanup)

    async def _acleanup(self):
        if self._connection_pools:
            await self.astop()

    def stop(self, timeout=5):
        return self._loop.run_async(self.astop).result_or_cancel(timeout=timeout)

    async def astop(self):
        for pool in self._connection_pools.values():
            for connection in pool:
                await connection.adisconnect()
        self._connection_pools = dict()

    async def get(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("GET", url, **kwds))

    async def head(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("HEAD", url, **kwds))

    async def patch(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("PATCH", url, **kwds))

    async def put(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("PUT", url, **kwds))

    async def post(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("POST", url, **kwds))

    async def delete(self, url: str, **kwds) -> Response:
        return cast(Response, await self.request("DELETE", url, **kwds))

    async def request(
        self,
        method: str,
        url: str,
        params: Optional[Mapping[str, str]] = None,
        data: Optional[Union[bytes, BinaryIO]] = None,
        headers: Optional[
            Union[Mapping[str, bytes], Iterable[Tuple[str, bytes]]]
        ] = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        retry: int = DEFAULT_RECONNECTION_ATTEMPTS,
        stream: bool = False,
    ) -> Optional[Response]:
        req = Request(url, method=method, params=params, data=data, headers=headers)
        assert req.scheme in ("http", "https")

        response = cast(
            Optional[Response],
            await self._get_url_response(
                req, timeout=timeout, retry_attempts=retry, stream=stream
            ),
        )
        if response is None:
            return None
        if response.status_code not in (301, 307, 308):
            return response
        else:
            bytes_url = response.headers.get("location")
            assert bytes_url is not None
            url = bytes_url.decode()
            req = Request(url, method=method, params=params, data=data, headers=headers)
            assert req.scheme in ("http", "https")

            return cast(
                Response,
                await self._get_url_response(
                    req, timeout=timeout, retry_attempts=retry, stream=stream
                ),
            )

    async def websocket(
        self,
        url,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        retry: int = DEFAULT_RECONNECTION_ATTEMPTS,
    ) -> Optional[WebSocket]:
        req = Request(url, method="GET")
        assert req.scheme in ("ws", "wss")

        return cast(
            Optional[WebSocket],
            await self._get_url_response(req, timeout=timeout, retry_attempts=retry),
        )

    async def _get_connection(self, scheme: str, host: str, port: int):
        if (scheme, host, port) not in self._connection_pools:
            connection = Connection(self._loop, self, scheme)
            self._connection_pools[(scheme, host, port)] = [connection]
            return connection
        pool = self._connection_pools[(scheme, host, port)]
        garbage_collected = []
        for connection in pool:
            if connection.reuse():
                return connection
            if await connection.aclosed():
                garbage_collected.append(connection)
        for connection in garbage_collected:
            try:
                pool.remove(connection)
                connection.destroy()
            except ValueError:
                pass

        connection = Connection(self._loop, self, scheme)
        pool.append(connection)
        return connection

    async def _get_url_response(
        self,
        req: Request,
        timeout: float,
        retry_attempts: int,
        stream: bool = False,
    ) -> Optional[Union[Response, WebSocket]]:
        connection = await self._get_connection(req.scheme, req.host, req.port)
        # Try to reconnect a TCP connection 'retry_attempts' times before aborting. Each
        # connection and request sending will wait for 'timeout' before considering its
        # connection/sending aborted
        for i in range(retry_attempts):
            try:
                response = await connection.connect_and_send(
                    req, timeout=timeout, stream=stream
                )
            except HTTPError as e:
                connection.logger.error(f"{e}: Unable to get an URL response")
                return None
            if response is None:
                if i < retry_attempts - 1:
                    # As long as there are retry_attempts left, try to reconnect
                    connection = await self._get_connection(
                        req.scheme, req.host, req.port
                    )
                    continue
                else:
                    # There is no retry_attempts left
                    connection.logger.error(
                        f"Connection to '{req.url}' failed after {retry_attempts} attempts"
                    )
            return response


_logger = logging.getLogger("olympe.http")


async def _arequest(method: str, url: str, **kwds):
    session = Session(get_running_loop())
    resp = await session.request(method, url, **kwds)
    stream = kwds.pop("stream", False)
    assert type(resp) is Response
    content = None
    content_generator = None
    if not stream:
        content = await resp.content()
    else:
        content_generator = resp._generator
    return SyncResponse(resp._request, resp._response_event, content, content_generator)


def request(method, url: str, **kwds):
    loop = Loop(_logger)
    timeout = kwds.get("timeout")
    resp = loop.run_until_complete(_arequest, method, url, **kwds)
    return resp.result_or_cancel(timeout=timeout)


def get(url: str, **kwds):
    return request("GET", url, **kwds)


def head(url: str, **kwds):
    return request("HEAD", url, **kwds)


def post(url: str, **kwds):
    return request("POST", url, **kwds)


def patch(url: str, **kwds):
    return request("PATCH", url, **kwds)


def put(url: str, **kwds):
    return request("PUT", url, **kwds)


def delete(url: str, **kwds):
    return request("DELETE", url, **kwds)


async def main():
    global loop
    session = Session(loop)
    response = await session.get("https://www.python.org")
    response.raise_for_status()
    print(response.headers)
    print(await response.text())
    ws = await session.websocket(
        "wss://demo.piesocket.com/v3/channel_1?"
        "api_key=VCXCEuvhGcBDP7XhiJJUDvR1e1D3eiVjgZ9VRiaV&notify_self"
    )
    if ws is None:
        print("Failed to establish websocket connection")
        return
    data = await ws.aread()
    if data is None:
        print("Failed to read data from websocket")
        return
    print(json.loads(data))
    await ws.awrite(
        json.dumps(
            {"type": "event", "name": "test-event", "message": "cmd_ping"}
        ).encode()
    )
    data = await ws.aread()
    if data is None:
        print("Failed to read data from websocket")
        return
    print(json.loads(data))
    loop.stop()


if __name__ == "__main__":
    logger = logging.getLogger("olympe.http")
    loop = Loop(logger)
    loop.run_async(main)
    loop.run()
    print(get("https://www.python.org", timeout=1.0).text())
