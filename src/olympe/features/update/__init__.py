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

from collections import OrderedDict
from logging import getLogger
from typing import Optional

from olympe.concurrent import (
    Loop,
)
from olympe.connection.http import Session


class Update:
    """
    Drone Update API class

    This class connects to the drone web update interface (REST API) and enable to upload
    a file or request the current device version in a background thread.

    See usage example in examples/update.py
    """

    # HTTP connection or data read timeout
    timeout = 300.0

    def __init__(
        self,
        hostname,
        name=None,
        version=1,
        session: Optional[Session] = None,
    ):
        self._name = name
        if self._name is not None:
            self.logger = getLogger(f"olympe.{self._name}.update")
        else:
            self.logger = getLogger("olympe.update")

        self._version = version

        # The API URLs are defined once the hostname is set
        self._base_url: Optional[str]
        self._api_url: Optional[str]
        self._update_api_url: Optional[str]
        self._version_api_url: Optional[str]
        self.set_hostname(hostname)

        self._flightplans = OrderedDict()

        # max_workers below is set to 1 because the one and only worker is used to
        # sequentially write downloaded flightplans chunks. If more than one worker is used
        # chunks may be written to disk out of order.
        self._loop = Loop(self.logger, max_workers=1)

        # In case no session is transmitted through the mixins, Update builts its own
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
            self._update_api_url = f"{self._api_url}/update/upload"
            self._version_api_url = f"{self._api_url}/update/version"

        # In case hostname is reset to None, reset the API URLs too
        else:
            self._base_url = None
            self._api_url = None
            self._update_api_url = None
            self._version_api_url = None

    def upload(self, to_upload, reboot=True, timeout=None):
        return self._loop.run_async(
            self._upload, to_upload, reboot, timeout
        ).result_or_cancel(timeout)

    def get_current_version(self, timeout=None):
        return self._loop.run_async(
            self._get_current_version, timeout
        ).result_or_cancel(timeout)

    def shutdown(self):
        self.destroy()

    def destroy(self) -> bool:
        """
        Properly close and stop the flightplan API background thread
        """
        self._loop.stop()
        return True

    async def _upload(self, to_upload, reboot=True, timeout=None) -> bool:
        """
        Request the upload of an update file through the REST API
        Reboot is a query parameter to enable or disable automatic reboot
        HTTP PUT /api/v<version>/update/upload[?reboot=no]
        """
        if timeout is None:
            timeout = self.timeout
        if self._update_api_url is None:
            self.logger.error("The upload file API url is not set already.")
            return False
        if not reboot:
            response = await self._session.put(
                os.path.join(self._update_api_url, "?reboot=no"),
                timeout=timeout,
                data=to_upload,
            )
        else:
            response = await self._session.put(
                self._update_api_url, timeout=timeout, data=to_upload
            )
        try:
            response.raise_for_status()
        except Exception as e:
            self.logger.error(str(e))
            return False
        return True

    async def _get_current_version(self, timeout=None) -> str:
        """
        Returns the current device version from the REST API
        HTTP GET /api/v<version>/update/version
        """
        if timeout is None:
            timeout = self.timeout
        if self._version_api_url is None:
            raise ValueError("The get current version API url is not set already.")
        response = await self._session.get(self._version_api_url, timeout=self.timeout)
        try:
            response.raise_for_status()
            version = await response.json()
        except Exception as e:
            raise ValueError(str(e))
        return version
