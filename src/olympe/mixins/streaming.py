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

from olympe.video.pdraw import Pdraw
from olympe.utils import callback_decorator


class StreamingControllerMixin:
    def __init__(self, *args, video_buffer_queue_size=8, **kwds):
        super().__init__(*args, **kwds)
        self._pdraw = None
        self._video_buffer_queue_size = video_buffer_queue_size

    def destroy(self):
        self._destroy_pdraw()
        super().destroy()

    def _destroy_pdraw(self):
        if self._pdraw is not None:
            self._pdraw.destroy()
        self._pdraw = None

    def _dispose_pdraw(self):
        if self._pdraw is not None:
            self._pdraw.dispose()
        self._pdraw = None

    def _create_pdraw_interface(self):
        return Pdraw(
            name=self._name,
            server_addr=self._ip_addr.decode(),
            device_name=self._device_name,
            buffer_queue_size=self._video_buffer_queue_size,
        )

    def connect(self, **kwds):
        if not super().connect(**kwds):
            return False
        # Create pdraw video streaming interface
        if self._pdraw is None:
            self._pdraw = self._create_pdraw_interface()
            if self._pdraw is None:
                self.logger.error(
                    f"Unable to create video streaming interface: "
                    f"{self._ip_addr}"
                )
                self.disconnect()
                return False
        return True

    @callback_decorator()
    def _on_device_removed(self):
        if self._pdraw:
            self._destroy_pdraw()
        return super()._on_device_removed()

    def _reset_instance(self):
        """
        Reset instance attributes
        """
        self._pdraw = None
        super()._reset_instance()

    @property
    def streaming(self):
        return self._pdraw
