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
import base64
import certifi
import ssl

from typing import Optional

from olympe.concurrent import Loop
from olympe.connection.http import Session
from olympe.log import LogMixin

from olympe.messages import esim
from olympe.enums import esim as esim_enums

class EsimHandler(LogMixin):
    def __init__(self, controller:"LogMixin"):
        super().__init__(controller._name, getattr(controller, "_device_name", None), "esim")

        self._controller=controller
        # dedicated loop and session for http request sent from the drone
        self._loop = Loop(self.logger)

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        # drone esim's servers don't send the full intermediate chain; verification
        # is not the security boundary here (GSMA RSP handles it at application level)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._session = Session(loop=self._loop, ssl_context=ssl_ctx)

        self._get_state_subscriber = self._controller.subscribe(
            self._on_getstate, esim.Event.State(_policy="wait")
        )
        self._http_request_subscriber = self._controller.subscribe(
            self._on_http_request, esim.Event.HttpRequest(_policy="wait")
        )

        self._loop.start()


    def _on_getstate(self, message_event, _):
        sim_status: str = message_event.args["sim_status_value"]["value"]
        if sim_status != esim_enums.SimStatus.ready:
            self.logger.warning(f"eSIM status is not ready : {sim_status=}")
            return

        profiles = message_event.args["profile_list"]["profiles"]

        for iteration, profile in enumerate(profiles):
            self.logger.debug(
                f"ESim profile n{iteration} - iccid: {profile['iccid']}, provider: {profile['provider']}, enabled: {profile['enabled']}"
            )

        profile_operation_status = None
        if "profile_operation_status" in message_event.args:
            profile_operation_status =  message_event.args["profile_operation_status"]

        if profile_operation_status:
            error_code = profile_operation_status["error_code"]
            if profile_operation_status.get("download_profile_status") is not None:
                self.logger.info(
                    f"ESim download profile status - iccid: {profile_operation_status.get('download_profile_status', {}).get('profile',{}).get('iccid', '- No iccid registered')}, provider: {profile_operation_status.get('download_profile_status', {}).get('profile', {}).get('provider', '- No provider registered')}, enabled: {profile_operation_status.get('download_profile_status', {}).get('profile', {}).get('enabled', '')} (error_code {error_code})"
                )
            if profile_operation_status.get("enable_profile_status") is not None:
                self.logger.info(
                    f"ESim enable profile status - iccid: {profile_operation_status.get('enable_profile_status', {}).get('iccid', '- No iccid registered')}, enabled: {profile_operation_status.get('enable_profile_status',{}).get('enabled', '')} (error_code {error_code})"
                )
            if profile_operation_status.get("delete_profile_status") is not None:
                self.logger.info(
                    f"ESim delete profile status - iccid: {profile_operation_status.get('delete_profile_status', {}).get('iccid', '-No iccid registered')} (error_code {error_code})"
                )

    def list_profiles(self) -> bool:
        send_esim_command = self._controller._send_command_raw(
            esim.Command.GetState, dict(include_default_capabilities=True)
        )

        if send_esim_command.done() and not send_esim_command.result():
            self.logger.error("Unable to send a 'GetState' command")
            return False
        return True

    def download_profile(
        self, activation_code: str, confirmation_code: Optional[str]=None
    ) -> bool:
        if confirmation_code:
            send_esim_command = self._controller._send_command_raw(
                esim.Command.DownloadProfile,
                dict(
                    activation_code=activation_code, confirmation_code=confirmation_code
                ),
            )
        else:
            send_esim_command = self._controller._send_command_raw(
                esim.Command.DownloadProfile,
                dict(activation_code=activation_code),
            )

        if send_esim_command.done() and not send_esim_command.result():
            self.logger.error("Unable to send a 'DownloadProfile' command")
            return False
        return True

    def enable_profile(self, iccid: str, enable: bool) -> bool:
        send_esim_command = self._controller._send_command_raw(
            esim.Command.EnableProfile,
            dict(iccid=iccid, enable=enable),
        )

        if send_esim_command.done() and not send_esim_command.result():
            self.logger.error("Unable to send a 'EnableProfile' command")
            return False
        return True

    def delete_profile(self, iccid: str) -> bool:
        send_esim_command = self._controller._send_command_raw(
            esim.Command.DeleteProfile,
            dict(iccid=iccid),
        )

        if send_esim_command.done() and not send_esim_command.result():
            self.logger.error("Unable to send a 'DeleteProfile' command")
            return False
        return True

    def _on_http_request(self, message_event, _):
        self.logger.debug("ESim HttpRequest args: %s", message_event.args)
        id=message_event.args["id"]
        url=message_event.args["url"]
        headers=message_event.args["headers"]
        data=message_event.args["data"]

        self._loop.run_async(self._do_http_request, id, url, headers, data)


    async def _do_http_request(self, id, url, headers, data):
        try:
            # drone sends headers as "Key: Value" strings, parse into dict
            parsed_headers = {}
            for h in headers:
                key, _, value = h.partition(": ")
                parsed_headers[key] = value

            if data:
                if isinstance(data, str):
                    # decode str to bytes before sending
                    data = base64.b64decode(data)
                resp = await self._session.post(url, headers=parsed_headers, data=data)  # POST if data not null
            else:
                resp = await self._session.get(url, headers=parsed_headers)
            code = resp.status_code
            body = await resp.content()
            error_code = 0
        except Exception:
            self.logger.exception("ESim HTTP request failed: %s", url)
            code = 0
            body = b""
            error_code = 1
        self._controller._send_command_raw(
            esim.Command.HttpResponse,
            dict(id=id, code=code, error_code=error_code, message="", data=body),
        )


    def destroy(self):
        self._get_state_subscriber.unsubscribe()
        self._http_request_subscriber.unsubscribe()
        self._session.stop()
        self._loop.stop()