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


import json
import logging
import queue
import re
from collections.abc import Mapping

import h11
import wsproto
import wsproto.connection
import wsproto.events
import wsproto.utilities
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

import olympe.networking

from .__version__ import __version__
from .concurrent import Loop, Semaphore, get_running_loop
from .networking import ConnectionClosedError, DNSResolver


class HTTPError(Exception):
    pass


_default_headers = {
    "user-agent": f"parrot-olympe-{__version__}",
    "accept": "*/*",
    "connection": "keep-alive",
}


class Request:
    def __init__(self, url, method=None, params=None, headers=None, data=None):
        url = url.lstrip()
        method = method or "GET"
        self.method = method
        self.url = url
        self.params = params or ""
        if headers:
            if isinstance(headers, Mapping):
                headers = {k.lower(): v for k, v in headers.items()}
            else:
                headers = {k.lower(): v for (k, v) in headers}
        else:
            headers = _default_headers
        self.headers = headers
        self.data = data

        try:
            scheme, auth, host, port, path, query, fragment = parse_url(url)
        except LocationParseError as e:
            raise ValueError(*e.args)

        if not scheme:
            raise ValueError(f"Invalid URL - no scheme in url: {url}")

        if not host:
            raise ValueError(f"Invalid URL - no host in url: {url}")

        if port is None:
            if scheme in ("http", "ws"):
                port = 80
            elif scheme in ("https", "wss"):
                port = 443
        if path is None:
            path = "/"

        if not self.headers.get("host"):
            self.headers["host"] = host

        self.scheme = scheme
        self.auth = auth
        self.host = host
        self.port = port
        self.path = path
        self.query = query
        self.fragment = fragment

    def _h11(self):
        return h11.Request(
            method=self.method, target=self.path, headers=list(self.headers.items())
        )

    def _wsproto(self):
        return wsproto.events.Request(
            host=self.host, target=f"{self.path}?{self.query}"
        )


class Response:
    def __init__(self, connection, request, response_event):
        self._connection = connection
        self.logger = self._connection.logger
        self._request = request
        self._response_event = response_event
        self._headers = None
        self._content_type = None
        self._content_encoding = None

    @property
    def ok(self):
        return self._response_event.status_code < 400

    @property
    def status_code(self):
        return self._response_event.status_code

    @property
    def headers(self):
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

    async def text(self):
        return "".join([chunk.decode(self.content_encoding) async for chunk in self])

    async def json(self):
        return json.loads(await self.text())

    def raise_for_status(self):
        if not self.ok:
            raise HTTPError(
                f"HTTP {self._response_event.status_code}: "
                f"{self._request.url} {self._response_event.reason}"
            )

    async def __aiter__(self):
        self._reading = True
        if self._response_event.status_code in (300, 301, 302):
            return
        while True:
            event = await self._connection._get_next_event()
            if event == h11.EndOfMessage() or event == h11.ConnectionClosed():
                break
            if not isinstance(event, h11.Data):
                self.logger.error(f"Unexpected event {event} for {self._request.url}")
                assert isinstance(event, h11.Data), f"Unexpected event {event}"
            yield bytes(event.data)
        self._reading = False

    def __repr__(self):
        return "<Response [%s]>" % (self.status_code)

    def __bool__(self):
        return self.ok


class WebSocket:
    def __init__(self, connection, request):
        self._connection = connection
        self._request = request

    async def aread(self):
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

    async def awrite(self, data):
        if (
            self._connection._conn.state is not wsproto.connection.ConnectionState.OPEN
            or not self._connection.connected
        ):
            raise ConnectionClosedError()
        data = self._connection._conn.send(wsproto.events.Message(data=data))
        return await self._connection.awrite(data)

    async def aclose(self):
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


class ConnectionListener(olympe.networking.DataListener):
    def __init__(self, connection: "Connection"):
        self._connection = connection

    def data_sent(self, *args, **kwds):
        pass

    def data_received(
        self,
        client,
        connection: "olympe.networking.Connection",
        buffer: "olympe.networking.Buffer",
    ):
        self._connection._feed_data(bytes(buffer.data.contents))
        return True

    def connected(self, connection: "Connection"):
        pass

    def disconnected(self, connection: "Connection"):
        self._connection._feed_eof()


class Connection:
    def __init__(self, loop, session, scheme):
        self._session = session
        self._loop = loop
        self.logger = self._loop.logger
        self._resolver = session._resolver
        self._scheme = scheme
        if scheme in ("http", "https"):
            self._conn = h11.Connection(our_role=h11.CLIENT)
        else:
            self._conn = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
        if scheme in ("http", "ws"):
            self._client = olympe.networking.TcpClient(self._loop)
        else:
            self._client = olympe.networking.TlsClient(self._loop)
        connection_listener = ConnectionListener(self)
        self._client.add_data_listener(connection_listener)
        self._client.add_connection_listener(connection_listener)
        self._events = queue.Queue()
        self._event_sem = Semaphore(value=0)
        self._reusing = False
        self._sending = False
        self._reading = False
        self._endpoint = None
        self._request = None

    async def send(self, request: Request, timeout=None):
        self._sending = True
        self._request = request
        try:
            if self._endpoint is None:
                self._endpoint = (request.host, request.port)
            else:
                assert self._endpoint == (request.host, request.port)
            for (_, sockaddr) in await self._resolver.resolve(
                request.host, request.port
            ):
                addr, *_ = sockaddr
                break
            else:
                raise ConnectionError(f"Cannot resolve {request.host}:{request.port}")
            if self._scheme in ("http", "ws"):
                connected = await self._client.aconnect(
                    addr, request.port, timeout=timeout
                )
            else:
                connected = await self._client.aconnect(
                    addr, request.port, server_hostname=request.host, timeout=timeout
                )
            if not connected:
                raise ConnectionError()
            return await self._do_send(request)
        finally:
            self._sending = False

    async def _do_send(self, request: Request):
        if request.scheme in ("http", "https"):
            return await self._do_send_http(request)
        else:
            return await self._do_send_ws(request)

    async def _do_send_http(self, request: Request):
        data = self._conn.send(request._h11())
        data += self._conn.send(h11.EndOfMessage())
        self._reusing = False
        await self.awrite(data)
        event = await self._get_next_event()
        assert isinstance(event, h11.Response)
        await self._aclosed_http()  # Handle: keep-alive/connection: closed
        return Response(self, request, event)

    async def _do_send_ws(self, request: Request):
        data = self._conn.send(request._wsproto())
        self._reusing = False
        await self.awrite(data)
        event = await self._get_next_event()
        if isinstance(event, wsproto.events.RejectConnection):
            return None
        assert isinstance(event, wsproto.events.AcceptConnection)
        ws = WebSocket(self, request)
        return ws

    async def awrite(self, data):
        return await self._client.awrite(data)

    async def _get_next_event(self):
        await self._event_sem.acquire()
        return self._events.get_nowait()

    def _feed_data(self, data: bytes):
        self._conn.receive_data(data)
        if self._scheme in ("http", "https"):
            while True:
                event = self._conn.next_event()
                if event in (h11.NEED_DATA, h11.PAUSED):
                    return
                if event == h11.ConnectionClosed():
                    self._loop.logger.error(f"unexpected end of request for {self._request.url}")
                    self._client.disconnect()
                    self._client.destroy()
                    return
                self._events.put_nowait(event)
                self._event_sem.release()
                if isinstance(event, h11.EndOfMessage):
                    if self._conn.our_state is h11.MUST_CLOSE:
                        self._feed_eof()
                        self._client.disconnect()
                        self._client.destroy()
                        return
        else:
            for event in self._conn.events():
                if isinstance(event, wsproto.events.Ping):
                    pong = self._conn.send(event.response())
                    self._client.write(pong)
                    continue
                if isinstance(event, wsproto.events.Pong):
                    continue
                if isinstance(event, wsproto.events.CloseConnection):
                    if self._conn.state not in (
                        wsproto.connection.ConnectionState.CLOSED,
                        wsproto.connection.ConnectionState.LOCAL_CLOSING,
                    ):
                        close = self._conn.send(event.response())
                        self._client.write(close)
                        self._client.disconnect()
                        self._client.destroy()
                    continue
                self._events.put_nowait(event)
                self._event_sem.release()

    def _feed_eof(self):
        if self._scheme in ("http", "https"):
            close_event = h11.ConnectionClosed()
            self._conn.send(close_event)
            self._events.put_nowait(close_event)
            self._event_sem.release()
        else:
            close_event = wsproto.events.CloseConnection(0)
            if self._conn.state not in (
                wsproto.connection.ConnectionState.CLOSED,
                wsproto.connection.ConnectionState.LOCAL_CLOSING,
            ):
                try:
                    data = self._conn.send(close_event)
                    if self._client.connected:
                        self._client.write(data)
                except wsproto.utilities.LocalProtocolError:
                    pass
            self._events.put_nowait(close_event)
            self._event_sem.release()
        self._client.disconnect()
        self._client.destroy()

    def reuse(self):
        if self._reusing or self._sending or self._reading:
            return False
        if self._scheme in ("http", "https"):
            return self._reuse_http()
        else:
            return self._reuse_websocket()

    def _reuse_http(self):
        if self._conn.our_state is h11.DONE and self._conn.their_state is h11.DONE:
            self._events = queue.Queue()
            self._event_sem = Semaphore(value=0)
            self._conn.start_next_cycle()
            self._reusing = True
            return True
        elif self._conn.our_state is h11.IDLE and self._conn.their_state is h11.IDLE:
            self._events = queue.Queue()
            self._event_sem = Semaphore(value=0)
            self._reusing = True
            return True
        else:
            return False

    def _reuse_websocket(self):
        # I'm not sure we can actually ever reuse a tcp connection after a websocket is closed
        return False

    async def aclosed(self):
        if self._scheme in ("http", "https"):
            return await self._aclosed_http()
        else:
            return self._closed_websocket()

    async def _aclosed_http(self):
        if self._conn.their_state is h11.CLOSED:
            self._feed_eof()
            await self._client.adisconnect()
            return True
        return self._conn.our_state is h11.CLOSED

    def _closed_websocket(self):
        return self._conn.state is wsproto.connection.ConnectionState.CLOSED

    def disconnect(self):
        return self._client.disconnect()

    async def adisconnect(self):
        return await self._client.adisconnect()

    @property
    def connected(self):
        return self._client.connected

    @property
    def fd(self):
        return self._client.fd


class Session:
    def __init__(self, loop=None):
        if loop is None:
            loop = get_running_loop()
        self._loop = loop
        self._resolver = DNSResolver()
        self._connection_pools = dict()
        self._loop.register_cleanup(self.astop)

    async def _get_connection(self, scheme, host, port):
        loop = get_running_loop()
        if (scheme, host, port) not in self._connection_pools:
            connection = Connection(loop, self, scheme)
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
            pool.remove(connection)

        connection = Connection(loop, self, scheme)
        pool.append(connection)
        return connection

    async def get(self, url, **kwds) -> Response:
        return await self.request("GET", url, **kwds)

    async def head(self, url, **kwds) -> Response:
        return await self.request("HEAD", url, **kwds)

    async def patch(self, url, **kwds) -> Response:
        return await self.request("PATCH", url, **kwds)

    async def post(self, url, **kwds) -> Response:
        return await self.request("POST", url, **kwds)

    async def delete(self, url, **kwds) -> Response:
        return await self.request("DELETE", url, **kwds)

    async def request(
        self, method, url, params=None, data=None, headers=None, timeout=None
    ) -> Response:
        req = Request(url, method=method, params=params, data=data, headers=headers)
        assert req.scheme in ("http", "https")
        connection = await self._get_connection(req.scheme, req.host, req.port)
        return await connection.send(req, timeout=timeout)

    async def websocket(self, url, timeout=None):
        req = Request(url, method="GET")
        assert req.scheme in ("ws", "wss")
        connection = await self._get_connection(req.scheme, req.host, req.port)
        return await connection.send(req, timeout=timeout)

    def stop(self):
        return self._loop.run_async(self.astop)

    async def astop(self):
        self._loop.unregister_cleanup(self.astop)
        for pool in self._connection_pools.values():
            for connection in pool:
                await connection.adisconnect()


async def main():
    global loop
    session = Session(loop)
    response = await session.get("https://www.python.org")
    response.raise_for_status()
    print(response.headers)
    print(await response.text())
    ws = await session.websocket(
        "wss://demo.piesocket.com/v3/channel_1?"
        "api_key=oCdCMcMPQpbvNjUIzqtvF1d2X2okWpDQj4AwARJuAgtjhzKxVEjQU6IdCjwm&notify_self"
    )
    print(json.loads(await ws.aread()))
    await ws.awrite(
        json.dumps(
            {"type": "event", "name": "test-event", "message": "cmd_ping"}
        ).encode()
    )
    print(json.loads(await ws.aread()))
    loop.stop()


if __name__ == "__main__":
    logger = logging.getLogger("olympe.http")
    loop = Loop(logger)
    loop.run_async(main)
    loop.run()
