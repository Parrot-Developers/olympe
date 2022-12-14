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

from collections import deque


class Subscriber:

    default_queue_size = 1024
    default_timeout = 10

    def __init__(
        self,
        scheduler,
        callback,
        expectation=None,
        queue_size=default_queue_size,
        default=None,
        timeout=None
    ):
        self._scheduler = scheduler
        self._callback = callback
        self._expectation = expectation
        self._default = default
        self._timeout = timeout if timeout is not None else self.default_timeout

        # here we use collections.deque instead of queue.Queue because
        # when a deque reaches its maximum size, it discards the oldest
        # elements as new elements are appended.
        self._event_queue = deque([], queue_size)

    def __enter__(self):
        pass

    def __exit__(self, *args, **kwds):
        if self._expectation is not None:
            self._expectation.cancel()
        self._scheduler.unsubscribe(self)

    def _add_event(self, event):
        if len(self._event_queue) == self._event_queue.maxlen:
            self._scheduler._subscriber_overrun(self, event)
        self._event_queue.append(event)

    def notify(self, event):
        if self._expectation is None:
            self._add_event(event)
            return True
        else:
            # Await the expectation (this is a no-op if already done).
            # This prevent monitored command expectations from sending messages
            self._expectation._await(self._scheduler)
            if self._expectation.success() or self._expectation.cancelled():
                # reset already succeeded or cancelled expectations
                self._expectation = self._expectation.copy()
                self._expectation._await(self._scheduler)
            if not self._expectation.success() and self._expectation.check(event).success():
                self._add_event(event)
                return True
            else:
                return False

    def process(self):
        while len(self._event_queue) > 0:
            event = self._event_queue.popleft()
            self._callback(event, self._scheduler)

    @property
    def queue_size(self):
        return self._event_queue.maxlen

    @property
    def timeout(self):
        return self._timeout

    def unsubscribe(self):
        if self._expectation is not None:
            self._expectation.cancel()
        self._scheduler.unsubscribe(self)
