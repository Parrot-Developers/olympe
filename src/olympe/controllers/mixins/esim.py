#  Copyright (C) 2019-2021 Parrot Drones SAS
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

from threading import Event as threading_event
from typing import Optional

from olympe.features.esim import EsimHandler
from olympe.utils.errors import UnavailableAttributeError
from .protocols import MixinBase

from olympe.messages import esim
from olympe.enums import esim as esim_enums

ESIM_TIMEOUT = 10

class EsimHandlerMixin:
    def __init__(self: MixinBase, *args, **kwds):
        super().__init__(*args, **kwds)

        self._get_state_subscriber = self.subscribe(
            self._on_first_getstate, esim.Event.State(_policy="wait")
        )

        self._esim:Optional[EsimHandler]=None
        self._received_esim_event=threading_event()

    @property
    def esim(self) -> Optional[EsimHandler]:
        # wait for esim to be automatically defined if the drone has the feature
        if not self._received_esim_event.wait(timeout=ESIM_TIMEOUT):
            raise UnavailableAttributeError("eSim feature needs an eSim compatible drone")
    
        if self._esim is not None:
            return self._esim
        else:
            raise UnavailableAttributeError("eSim feature needs an eSim configuration that has status 'ready'")
    
    def _on_first_getstate(self, message_event,_):
        sim_status: str = message_event.args["sim_status_value"]["value"]
        if sim_status != esim_enums.SimStatus.ready:
            self.logger.info(f"eSim functionality is deactivated ({sim_status=})")
            self._esim =None
        else:
            self.logger.info(f"eSim functionality is activated ({sim_status=})")
            self._esim = EsimHandler(self)
        # Unsubscribing from within a subscriber callback deadlocks (the callback
        # waits for _aunsubscribe to end which waits for the callback's future to
        # complete). Defer it so it runs after this callback returns and the future is done.
        self._loop.run_later(self._get_state_subscriber.unsubscribe)
        # Set the event so that the property knows if there would be an esim property in
        # the drone or not
        self._received_esim_event.set()

    def destroy(self):
        if self._esim is not None:
            self._esim.destroy()
        super().destroy()
