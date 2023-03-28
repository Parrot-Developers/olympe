#  Copyright (C) 2023 Parrot Drones SAS
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

from olympe.concurrent import Loop, Future, TimeoutError
from olympe.log import LogMixin
from olympe.arsdkng.controller import ControllerBase
from olympe.utils import callback_decorator
from olympe.enums import drone_manager as drone_manager_enums
from olympe.http import Session, HTTPError
from olympe.messages.security import Command
from olympe.controller import Disconnected, Connected
from olympe.event import Event, EventContext
from olympe.expectations import Expectation

from logging import getLogger
from typing import Dict, Optional

import hashlib
import time


class HeaderField:
    authorization = "Authorization"
    contentType = "Content-Type"
    xApiKey = "x-api-key"
    callerId = "X-callerId"
    userAgent = "User-Agent"


class HeaderValue:
    appJson = "application/json"


class Cellular(LogMixin):
    """
    Controller Cellular API class
    Controller mixin providing the cellular pairing.
    """

    """Request timeout"""
    _TIMEOUT = 60

    """Parrot accounts URL."""
    _APC_API_BASE_URL = "https://accounts-api.parrot.com"
    """Parrot Academy URL."""
    _ACADEMY_BASE_URL = "https://academy.parrot.com"

    """APC secret key."""
    _APC_SECRECT_KEY = "g%2SW+m,cc9|eDQBgK:qTS2l=;[O~f@W"
    """Caller ID associated with the APC secret key."""
    _CALLER_ID = "OpenFlight"
    """Academy secret key."""
    _ACADEMY_SECRECT_KEY = "cd7oG8K9h86oCya0u5C0H7mphOuu8LU91o1hBLiG"
    """User agent."""
    _USER_AGENT = "Olympe"
    """Drone WEB API port."""
    _DRONE_WEB_API_PORT = 80

    @classmethod
    def _format_acp_query_items(
        cls, apc_key: str, params: Dict[str, str] = {}
    ) -> Dict[str, str]:
        """
        Formats APC query items.

        :param apc_key: APC signature token.
        :param params: query parameters.

        :return: formatted query item dictionary.
        """
        ts = int(time.time())
        pre_str = ""
        keys_lst = params.keys()
        sorted(keys_lst)
        for key in keys_lst:
            pre_str += f"{params[key]}"
        pre_str += f"{ts}"
        pre_str += apc_key

        token = hashlib.md5(pre_str.encode()).hexdigest()
        return {"ts": f"{ts}", "token": f"{token}"}

    def __init__(
        self,
        controller: "ControllerBase",
        autoconfigure: bool = False,
        user_apc_token: Optional[str] = None,
    ):
        """
        :param autoconfigure: `True` to run :py:func:`configure` automatically when
            :py:attr:`user_apc_token` is set and the SkyController is connected to
            a drone, `False` otherwise. (defaults to `False`)
        :param user_apc_token: User APC token to use for the cellular configuration;
            set :py:attr:`user_apc_token`. If `None` and `autoconfigure` is `True`,
            the drone will be paired with a new anonymous APC token automatically
            assigned to :py:attr:`user_apc_token` when the SkyController is connected
            to the drone. If not `None` force `autoconfigure`
            to `True` and sets :py:attr:`user_apc_token`. (defaults to `None`)
        """

        super().__init__(controller._name, controller._device_name, "Cellular")

        if controller._device_name is not None:
            self.logger = getLogger(f"olympe.cellular.{controller._device_name}")
        else:
            self.logger = getLogger("olympe.cellular")

        self._controller = controller
        self._loop = Loop(self.logger)
        self._session = Session(loop=self._loop)
        self._loop.start()
        self._proxy = None
        self._drone_http_url = None
        self._autoconfigure = autoconfigure
        self._user_apc_token = user_apc_token
        self._connect_subscriber = self._controller.subscribe(
            self._on_connection, Connected()
        )
        self._disconnect_subscriber = self._controller.subscribe(
            self._on_disconnection, Disconnected()
        )

    @property
    def autoconfigure(self) -> bool:
        """
        `True` if the automatic cellular configuration is enabled, `False` otherwise.
        """
        return self._autoconfigure

    @property
    def user_apc_token(self) -> Optional[str]:
        """Current user APC token used."""
        return self._user_apc_token

    def pair(
        self, user_apc_token: Optional[str] = None, timeout: Optional[float] = None
    ) -> str:
        """
        Pairs a user APC token with the currently connected Drone.

        :param user_apc_token: User APC token to pair with the drone.
            If `None`, another anonymous APC token will be generated. (defaults
            to `None`)
        :param timeout: the timeout in seconds or None for infinite timeout
            (the default)

        :raises HTTPError: in case of failure.
        :raises TimeoutError: in case of timeout.

        :return: the user APC token paired with the Drone.
        """

        return self._fpair(user_apc_token=user_apc_token).result_or_cancel(
            timeout=timeout
        )

    def _fpair(self, user_apc_token: Optional[str] = None) -> Future:
        """
        Retrives a future of :py:func:`pair`

        :param user_apc_token: User APC token to pair with the drone.
            if `None`, another anonymous APC token will be generated. (defaults
            to `None`)
        """

        return self._loop.run_async(self._apair, user_apc_token)

    def configure(
        self, user_apc_token: Optional[str] = None, timeout: Optional[float] = None
    ):
        """Configures the cellular connection using a user APC token.

        :param user_apc_token: User APC token to used for the cellular connection.
            If not `None`, user_apc_token is set as :py:attr:`user_apc_token`.
            If `None`, :py:attr:`user_apc_token` will be used (the default).
        :param timeout: the timeout in seconds or None for infinite timeout
            (the default)

        :raises HTTPError: in case of failure.
        :raises TimeoutError: in case of timeout.
        """

        self._fconfigure(user_apc_token=user_apc_token).result_or_cancel(timeout=timeout)

    def _fconfigure(self, user_apc_token: Optional[str] = None) -> Future:
        """Retrives a future of :py:func:`configure`

        :param user_apc_token: User APC token to used to connect.
            if not `None`, user_apc_token is set as :py:attr:`user_apc_token`.
            if `None`, :py:attr:`user_apc_token` will be used (the default).
        """

        return self._loop.run_async(self._aconfigure, user_apc_token)

    def _destroy(self):
        """
        Destructor
        """

        self._disconnect_subscriber.unsubscribe()

        self._session.stop()
        self._loop.stop()

    def _on_connection(self, *_):
        """
        Called at the controller connection.
        """

        if self._user_apc_token is not None:
            # run asynchronous auto configure.
            self._controller._thread_loop.run_async(self._autoconfigure_run)

    async def _autoconfigure_run(self):
        """
        Configures the skycontroller to use the apc token to the cellular connection.
        """
        self.logger.info("cellular auto configuration")
        try:
            # configure the SkyCtrl to use this apc token for the cellular connection.
            await self._aconfigure()
        except (HTTPError, TimeoutError, RuntimeError) as e:
            # raises an autoconfigure failure Event
            event = CellularAutoconfigureFailureEvent(exception=e)
            self.logger.info(str(event))
            self._controller.scheduler.process_event(event)

    def _on_disconnection(self, *_):
        """
        Called at the controller disconnection.
        """

        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None

    async def _aconfigure(self, user_apc_token: Optional[str] = None):
        """
        Configures cellular connection using an user APC token.

        :param user_apc_token: User APC token to used to connect.
            if not `None`, user_apc_token is set as :py:attr:`user_apc_token`.
            if `None`, :py:attr:`user_apc_token` will be used (the default).
        :param timeout: the timeout in seconds or None for infinite timeout
            (the default)

        :raises HTTPError: in case of failure.
        :raises TimeoutError: in case of timeout.
        """

        # use given APC token otherwise use the current.
        _user_apc_token = (
            user_apc_token if user_apc_token is not None else self.user_apc_token
        )

        # get the drone list paired with the user APC token (raise HTTPError
        # in failure)
        drone_list = await self._get_drone_list(_user_apc_token)

        # send the user APC token to the skycontroller
        self.logger.info("send the user APC token to the skycontroller")
        if not await self._controller(Command.RegisterApcToken(token=_user_apc_token)):
            raise TimeoutError

        # send drone list to the skycontroller
        self.logger.info("send the drone list to the skycontroller")
        if not await self._controller(Command.RegisterApcDroneList(list=drone_list)):
            raise TimeoutError

        # Update the current user APC token
        self._user_apc_token = _user_apc_token

    async def _get_anonymous_token(self) -> str:
        """
        Retrieves an anonymous APC token.

        :raises HTTPError: in case of failure.

        :return: an anonymous APC token.
        """

        url = f"{Cellular._APC_API_BASE_URL}/V4/account/tmp/create"

        headers = {
            HeaderField.callerId: Cellular._CALLER_ID,
            HeaderField.contentType: HeaderValue.appJson,
        }

        self.logger.info("get an anonymous token")

        apc_query_items = Cellular._format_acp_query_items(Cellular._APC_SECRECT_KEY)

        response = await self._session.post(
            url,
            headers=headers,
            params=apc_query_items,
            timeout=Cellular._TIMEOUT,
        )

        response.raise_for_status()
        data = await response.json()

        apc_token = data.get("apcToken")
        return apc_token

    async def _get_association_challenge(self, apc_token: str) -> str:
        """
        Retrieves a drone association challenge.

        :param apc_token: user authentication APC token.

        :raises HTTPError: in case of failure.

        :return: a drone association challenge.
        """

        url = f"{Cellular._ACADEMY_BASE_URL}/apiv1/4g/pairing/challenge"
        headers = {
            HeaderField.authorization: f"Bearer {apc_token}",
            HeaderField.contentType: HeaderValue.appJson,
            HeaderField.xApiKey: Cellular._ACADEMY_SECRECT_KEY,
            HeaderField.userAgent: Cellular._USER_AGENT,
        }

        params = {
            "operation": "associate",
        }

        self.logger.info("get the challenge association")

        response = await self._session.get(
            url,
            headers=headers,
            params=params,
            timeout=Cellular._TIMEOUT,
        )

        response.raise_for_status()
        challenge = await response.text()

        return challenge

    async def _sign_challenge_by_drone(self, challenge: str) -> bytes:
        """
        Signs an association challenge by the connected drone.

        :param challenge: drone challenge association to sign.

        :raises HTTPError: in case of failure.

        :return: a message containing the signed drone association challenge.
        """

        url = f"{self._drone_http_url}/api/v1/secure-element/sign_challenge"
        queryItems = {"operation": "associate", "challenge": challenge}

        self.logger.info("sign the challenge by the drone")

        response = await self._session.get(
            url, params=queryItems, timeout=Cellular._TIMEOUT
        )
        response.raise_for_status()
        drone_signed_challenge = await response.content()

        return drone_signed_challenge

    async def _associate_user_drone(self, apc_token, drone_signed_challenge: bytes):
        """
        Associates a user and a drone.

        :param apc_token: authentication APC token of the user to associate with the
            drone.
        :param drone_signed_challenge: message containing the association challenge
            signed by drone to associate with the drone.

        :raises HTTPError: in case of failure.
        """

        url = f"{Cellular._ACADEMY_BASE_URL}/apiv1/4g/pairing"
        headers = {
            HeaderField.authorization: f"Bearer {apc_token}",
            HeaderField.contentType: HeaderValue.appJson,
            HeaderField.xApiKey: Cellular._ACADEMY_SECRECT_KEY,
            HeaderField.userAgent: Cellular._USER_AGENT,
        }

        self.logger.info("associate the user APC token and the drone")

        response = await self._session.post(
            url,
            timeout=Cellular._TIMEOUT,
            headers=headers,
            data=drone_signed_challenge,
        )
        response.raise_for_status()

    async def _get_drone_list(self, apc_token: str) -> str:
        """
        Retrieves the drone list paired with this an APC token.

        :param apc_token: user authentication APC token.

        :return: the drone list paired with this APC token.
        """
        url = f"{Cellular._ACADEMY_BASE_URL}/apiv1/drone/list"
        headers = {
            HeaderField.authorization: f"Bearer {apc_token}",
            HeaderField.contentType: HeaderValue.appJson,
            HeaderField.xApiKey: Cellular._ACADEMY_SECRECT_KEY,
            HeaderField.userAgent: Cellular._USER_AGENT,
        }

        self.logger.info("get paired drone list")
        response = await self._session.get(
            url,
            headers=headers,
            timeout=Cellular._TIMEOUT,
        )
        response.raise_for_status()
        drone_list = await response.text()

        return drone_list

    async def _apair(self, user_apc_token: Optional[str] = None) -> str:
        """
        Pairs an user APC token with the Drone currently connected.

        :param user_apc_token: User APC token to pair with the drone.
            if `None`, another anonymous APC token will be generated. (defaults
            to `None`)

        :raises HTTPError: in case of failure.

        :return: the user APC token paired with the Drone.
        """

        if user_apc_token is not None:
            # use given user apc token.
            token = user_apc_token
        else:
            # get anonymous user apc token. (raise HTTPError in failure)
            token = await self._get_anonymous_token()

        # get challenge association. (raise HTTPError in failure)
        challenge = await self._get_association_challenge(token)

        # Sign the challenge association by the drone. (raise HTTPError in failure)
        drone_signed_challenge = await self._sign_challenge_by_drone(challenge)

        # associate the user and the drone. (raise HTTPError in failure)
        await self._associate_user_drone(token, drone_signed_challenge)

        return token

    async def _create_proxy(self):
        """
        Creates the proxy to access to the drone.
        """
        self._proxy = await self._controller.fopen_tcp_proxy(
            Cellular._DRONE_WEB_API_PORT
        )

        self._drone_http_url = f"http://{self._proxy.address}:{self._proxy.port}"

        if self._autoconfigure and self._user_apc_token is None:
            self.logger.info("cellular auto pairing and configuration")
            # generate a new anonymous user APC token and configure the cellular.
            self._fautoconfigure_with_new_token()

    def _fautoconfigure_with_new_token(self) -> Future:
        """
        Retrives a future of :py:func:`_autoconfigure_with_new_token`
        """

        return self._controller._thread_loop.run_async(
            self._autoconfigure_with_new_token
        )

    async def _autoconfigure_with_new_token(self) -> Future:
        """
        Configures cellular connection with a new anonymous user APC token generated.
        """
        try:
            self._fpair().then(self._autopaired)
        except (HTTPError, TimeoutError, RuntimeError) as e:
            # raise an autoconfigure failure Event
            event = CellularAutoconfigureFailureEvent(exception=e)
            self.logger.info(str(event))
            self._controller.scheduler.process_event(event)

    async def _autopaired(self, new_user_apc_token: str):
        """
        Called at the auto pairing result.

        :param new_user_apc_token: new user authentication APC token result of the
            auto pairing.
        """
        self._user_apc_token = new_user_apc_token
        self._fconfigure()

    @callback_decorator()
    def _on_drone_connection_state_change(
        self, state: drone_manager_enums.connection_state
    ):
        """
        Called at the change of the drone connection state.
        """
        if state == drone_manager_enums.connection_state.connected:
            if self._proxy is not None:
                self._proxy.close()
                self._proxy = None

            self._controller._thread_loop.run_async(self._create_proxy)
        elif self._proxy is not None:
            self._proxy.close()
            self._proxy = None


class CellularPairerMixin:
    """
    Controller mixin providing the cellular API.
    """

    def __init__(
        self,
        *args,
        cellular_autoconfigure: bool = False,
        user_apc_token: Optional[str] = None,
        **kwds,
    ):
        """
        :param cellular_autoconfigure: `True` to run :py:func:`Cellular.configure`
            automatically when :py:attr:`Cellular.user_apc_token` is set and the
            SkyController is connected, `False` otherwise. (defaults to `False`)
        :param user_apc_token: User APC token to use for the cellular configuration;
            set :py:attr:`Cellular.user_apc_token`. If `None` and
            `cellular_autoconfigure` is `True`, the drone will be paired with a new
            anonymous APC token that will be automatically assigned to
            :py:attr:`Cellular.user_apc_token` when the SkyController is connected
            to the drone. If not `None` force
            `cellular_autoconfigure` to `True` and sets
            :py:attr:`Cellular.user_apc_token`. (defaults to `None`)

        .. seealso::
            :py:func:`Cellular.pair` to pair the drone with an user APC token.
            :py:func:`Cellular.configure` to configure the cellular.
        """
        super().__init__(*args, **kwds)
        self._cellular = Cellular(
            self, autoconfigure=cellular_autoconfigure, user_apc_token=user_apc_token
        )

    def destroy(self):
        """
        Destructor
        """
        self._cellular._destroy()
        super().destroy()

    @property
    def cellular(self) -> Cellular:
        """Cellular API."""
        return self._cellular

    @callback_decorator()
    def _on_connection_state_changed(self, message_event, _):
        super()._on_connection_state_changed(message_event, _)

        # Handle drone connection_state events
        self._cellular._on_drone_connection_state_change(message_event._args["state"])

    def set_device_name(self, device_name):
        super().set_device_name(device_name)
        self._cellular.set_device_name(device_name)


class CellularAutoconfigureFailureEvent(Event):
    def __init__(self, policy=None, exception: Exception = None):
        super().__init__(policy)
        self._exception = exception

    @property
    def exception(self) -> Exception:
        """The exception raising the event."""
        return self._exception


class CellularAutoconfigureFailure(Expectation):
    def __init__(self):
        self._received_event = None
        super().__init__()

    def copy(self):
        return self.base_copy()

    def check(self, event, *args, **kwds):
        if not isinstance(event, CellularAutoconfigureFailureEvent):
            return self
        self._received_event = event
        self.set_success()
        return self

    def expected_events(self):
        if self:
            return EventContext()
        else:
            return EventContext([CellularAutoconfigureFailureEvent()])

    def received_events(self):
        if not self:
            return EventContext()
        else:
            return EventContext([self._received_event])

    def matched_events(self):
        return self.received_events()

    def unmatched_events(self):
        return self.expected_events()
