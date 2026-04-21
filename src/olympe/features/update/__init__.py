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

from logging import getLogger
from typing import BinaryIO, Optional, Union

from olympe.concurrent import Loop
from olympe.connection.http import Session


class Update:
    """Drone Update API class

    This class connects to the drone web Update interface (REST API) and enable to upload
    a file or request the current device version in a background thread.

    See usage example in examples/update.py
    """

    timeout = 300.0

    def __init__(
        self,
        hostname: Optional[str] = None,
        name: Optional[str] = None,
        version: int = 1,
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
        self._api_update_url: Optional[str]
        self._api_version_url: Optional[str]
        self.set_hostname(hostname)

        # max_workers is set to 1 because upload and version requests must be serialized
        self._loop = Loop(self.logger, max_workers=1)

        # In case no session is transmitted through the mixins, Update builds its own
        # with its own loop
        if session is None:
            self._session = Session(self._loop)
        else:
            self._session = session

        # Start the loop at the end of the init
        self._loop.start()

    def set_hostname(self, hostname: Optional[str]):
        """Set hostname to access the REST API urls

        :param hostname: hostname of the device to access
        :type hostname: str, optional
        """
        self._hostname = hostname
        if self._hostname is not None:
            # REST API endpoints
            self._base_url = f"http://{self._hostname}"
            self._api_url = f"{self._base_url}/api/v{self._version}"
            self._api_update_url = f"{self._api_url}/update/upload"
            self._api_version_url = f"{self._api_url}/update/version"

        # In case hostname is reset to None, reset the API URLs too
        else:
            self._base_url = None
            self._api_url = None
            self._api_update_url = None
            self._api_version_url = None

    def upload(
        self,
        file_to_upload: Union[str, BinaryIO],
        reboot: bool = True,
        timeout: Optional[float] = None,
    ) -> bool:
        """Request to upload an update file through the REST API
        Reboot is a query parameter to enable or disable automatic reboot
        HTTP PUT /api/v<version>/update/upload[?reboot=no]

        :param file_to_upload: update file to upload
        :type file_to_upload: Union[str, BinaryIO]
        :param reboot: tag to specify whether a reboot is necessary or not, defaults to True
        :type reboot: bool, optional
        :param timeout: timeout let to the PUT request to upload the file, defaults to None
        :type timeout: float, optional
        :return: True if the file is successfully uploaded, False otherwise
        :rtype: bool
        """
        return self._loop.run_async(
            self._upload, file_to_upload, reboot, timeout
        ).result_or_cancel(timeout or self.timeout)

    async def _upload(
        self,
        file_to_upload: Union[str, BinaryIO],
        reboot: bool,
        timeout: Optional[float],
    ) -> bool:
        # *** Sanity Checks ***
        if timeout is None:
            timeout = self.timeout
        if self._api_update_url is None:
            raise ValueError("The current API update url is not set already.")

        url = self._api_update_url if reboot else f"{self._api_update_url}?reboot=no"
        response = await self._session.put(url, timeout=timeout, data=file_to_upload)

        try:
            response.raise_for_status()
        except Exception as e:
            self.logger.error(
                f"Unable to upload file '{getattr(file_to_upload, 'name', repr(file_to_upload))}' ({str(e)})"
            )
            return False
        return True

    def get_current_version(self, timeout: Optional[float] = None) -> Optional[str]:
        """Return the current device version from the REST API
        HTTP GET /api/v<version>/update/version

        :param timeout: timeout let to the GET request to obtain the current API version,
        defaults to None
        :type timeout: float, optional
        :raises ValueError: if the current API version url is not set already
        :return: current API version
        :rtype: str, optional
        """
        return self._loop.run_async(
            self._get_current_version, timeout
        ).result_or_cancel(timeout or self.timeout)

    async def _get_current_version(self, timeout: Optional[float]) -> Optional[str]:
        # *** Sanity Checks ***
        if timeout is None:
            timeout = self.timeout
        if self._api_version_url is None:
            raise ValueError("The current API version url is not set already.")

        response = await self._session.get(self._api_version_url, timeout=timeout)

        try:
            response.raise_for_status()
        except Exception as e:
            self.logger.error(f"Failed to get current API version ({str(e)})")
            return None

        try:
            version = await response.json()
        except Exception as e:
            self.logger.error(
                f"JSON Response to get current API version is not parsable ({str(e)})"
            )
            return None

        return version

    def destroy(self) -> bool:
        """Properly stop the loop of the Update API background thread

        :return: True if the loop is successfully stopped, False otherwise
        :rtype: bool
        """
        return self._loop.stop()
