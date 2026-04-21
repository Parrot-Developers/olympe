#  Copyright (C) 2020 Parrot Drones SAS
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

import os

from collections import namedtuple, OrderedDict
from collections.abc import Mapping
from concurrent.futures import CancelledError
from logging import getLogger
from typing import Optional, Tuple

from olympe.concurrent import (
    Loop,
    get_running_loop,
)
from olympe.connection.http import Session, HTTPError

GET_REQUEST_MAX_RETRIES = 3

FlightplanInfo = namedtuple(
    "FlightplanInfo",
    [
        "uid",
        "date",
    ],
)
FlightplanInfo.__new__.__defaults__ = (None,) * len(FlightplanInfo._fields)
FlightplanInfo.__doc__ = (
    "Namedtuple class "
    + FlightplanInfo.__doc__
    + """

  - uid (str): unique id of the flightplan
  - date (str) :iso8601 datetime of the flightplan

"""
)


def _get_flightplan_tuple(flightplan) -> Tuple[Optional[str], FlightplanInfo]:
    # Retrieve the uid and the flightplan
    if isinstance(flightplan, Mapping):
        flightplan = _get_namedtuple_from_mapping(flightplan, FlightplanInfo)
    if not flightplan.uid:
        return None, flightplan
    return flightplan.uid, flightplan


class Flightplan:
    """
    Drone Flightplan API class

    This class connects to the drone web flightplan interface (REST API) and synchronizes
    the drone flightplan information in a background thread.

    Flightplan info:
      - :py:func:`~olympe.flightplan.download_all`

    See usage example in examples/flightplan.py
    """

    # HTTP connection or data read timeout
    timeout = 5.0

    def __init__(
        self,
        hostname,
        name=None,
        version=1,
        session: Optional[Session] = None,
    ):
        self._name = name
        if self._name is not None:
            self.logger = getLogger(f"olympe.{self._name}.flightplan")
        else:
            self.logger = getLogger("olympe.flightplan")

        self._version = version

        # The API URLs are defined once the hostname is set
        self._base_url: Optional[str]
        self._api_url: Optional[str]
        self._flightplan_api_url: Optional[str]
        self._flightplans_api_url: Optional[str]
        self.set_hostname(hostname)

        self._flightplans = OrderedDict()

        # max_workers below is set to 1 because the one and only worker is used to
        # sequentially write downloaded flightplans chunks. If more than one worker is used
        # chunks may be written to disk out of order.
        self._loop = Loop(self.logger, max_workers=1)

        # In case no session is transmitted through the mixins, Flightplan builts its own
        # with its own loop.
        if session is None:
            self._session = Session(self._loop)
        else:
            self._session = session

        self._loop.start()

    def set_hostname(self, hostname: Optional[str]):
        self._hostname = hostname
        if self._hostname is not None:
            # REST API endpoints
            self._base_url = f"http://{self._hostname}"
            self._api_url = f"{self._base_url}/api/v{self._version}"
            self._flightplan_api_url = f"{self._api_url}/upload/flightplan"
            self._flightplans_api_url = f"{self._api_url}/upload/flightplans"

        # In case hostname is reset to None, reset the API URLs too
        else:
            self._base_url = None
            self._api_url = None
            self._flightplan_api_url = None
            self._flightplans_api_url = None

    def upload(self, flightplan_data, timeout=None) -> str:
        return self._loop.run_async(
            self._upload, flightplan_data, timeout
        ).result_or_cancel(timeout)

    def download_all(self, timeout=None) -> Tuple[Optional[str], FlightplanInfo]:
        return self._loop.run_async(self._download_all, timeout).result_or_cancel(
            timeout
        )

    def download(self, flightplan_id, timeout=None):
        return self._loop.run_async(
            self._download, flightplan_id, timeout
        ).result_or_cancel(timeout)

    def remove(self, flightplan_id, timeout=None):
        return self._loop.run_async(
            self._remove, flightplan_id, timeout
        ).result_or_cancel(timeout)

    def shutdown(self):
        self.destroy()

    def destroy(self) -> bool:
        """
        Properly close and stop the flightplan API background thread
        """
        self._loop.stop()
        return True

    async def _upload(self, flightplan_data, timeout=None) -> Optional[str]:
        """
        Request the upload of a single flightplan through the REST API
        HTTP PUT /api/v<version>/upload/flightplan/<uid>
        """
        if timeout is None:
            timeout = self.timeout
        if self._flightplan_api_url is None:
            self.logger.error("The flightplan API url is not set already.")
            return None
        response = await self._session.put(
            self._flightplan_api_url, timeout=timeout, data=flightplan_data
        )
        response.raise_for_status()
        flightplan_id = await response.json()
        return flightplan_id

    async def _download_all(self, timeout=None) -> Optional[OrderedDict]:
        """
        Returns an array of flightplan objects from the REST API
        HTTP GET /api/v<version>/upload/flightplans
        """
        if timeout is None:
            timeout = self.timeout
        if self._flightplans_api_url is None:
            self.logger.error("The flightplans API url is not set yet.")
            return None
        response = await self._session.get(
            self._flightplans_api_url, timeout=self.timeout
        )
        for _ in range(GET_REQUEST_MAX_RETRIES):
            try:
                response.raise_for_status()
                data = await response.json()
            except HTTPError as e:
                self.logger.warning(str(e))
                await get_running_loop().asleep(1.0)
                continue
            except CancelledError:
                return None
            except Exception:
                self.logger.exception("Unhandled exception")
                return None
            else:
                break
        else:
            self.logger.error("The webserver is unavailable")
            return None
        for flightplan in data:
            flightplan_id, flightplan = _get_flightplan_tuple(flightplan)
            if not flightplan_id:
                self.logger.error("Missing flightplan_id in webserver response")
                continue
            self._flightplans[flightplan_id] = flightplan
        return self._flightplans

    async def _download(self, flightplan_id, timeout=None) -> Optional[FlightplanInfo]:
        """
        Returns a single flightplan object from the REST API
        HTTP GET /api/v<version>/upload/flightplan/<uid>
        """
        if timeout is None:
            timeout = self.timeout
        if self._flightplan_api_url is None:
            self.logger.error("The flightplan API url is not set already.")
            return None
        if flightplan_id in self._flightplans:
            # If the flightplan has already been uploaded, there is no need to download it again
            return self._flightplans[flightplan_id]
        self.logger.info(
            f"Missing flightplan_id '{flightplan_id}' in olympe flightplan database. It has to be download."
        )
        response = await self._session.get(
            os.path.join(self._flightplan_api_url, flightplan_id), timeout=timeout
        )
        response.raise_for_status()
        data = await response.json()
        flightplan_id, flightplan = _get_flightplan_tuple(data)
        if not flightplan_id:
            self.logger.error(
                f"Missing flightplan_id {flightplan_id} in webserver response."
            )
            return None
        self._flightplans[flightplan_id] = flightplan
        return flightplan
