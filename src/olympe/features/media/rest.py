from typing import Optional

from olympe.messaging.events import EventContext, EventMarker
from olympe.messaging.expectations import Expectation

from .events import MediaCommand


class _RESTExpectation(Expectation):
    always_monitor = True

    def __init__(self, rest_name, rest_method, *, _timeout=None, **rest_args):
        super().__init__()
        self.set_timeout(_timeout)
        self._rest_name = rest_name
        self._rest_method = rest_method
        self._rest_args = rest_args
        self._expected_event = MediaCommand(self._rest_name, rest_args)

    def copy(self):
        return super().base_copy(
            self._rest_name,
            self._rest_method,
            _timeout=self._timeout,
            **self._rest_args,
        )

    def check(self, *args, **kwds):
        return self

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        """
        Returns a collection of events with matched/unmatched markers.
        """
        if self._success:
            return self.expected_events()._set_marker(EventMarker.matched)
        elif self.done() and self.exception():
            error_event = self._expected_event.copy()
            error_event._error = repr(self.exception())
            return EventContext([error_event])._set_marker(default_marked_events)
        else:
            return self.expected_events()._set_marker(default_marked_events)

    def _schedule(self, scheduler):
        if not self._awaited:
            super()._schedule(scheduler)
            media = scheduler.context("olympe.media")
            media._loop.run_async(self._do_request, media)

    async def _do_request(self, media):
        rest_method = getattr(media.factory.ctx_media.rest, self._rest_method)
        try:
            if await rest_method(timeout=self._timeout, **self._rest_args) is True:
                self.set_success()
            else:
                self.cancel()
        except Exception as e:
            self.set_exception(e)


class _RESTDeleteMediaExpectation(_RESTExpectation):
    def __init__(self, media_id: str, _timeout: Optional[float] = None):
        super().__init__(
            "delete_media",
            "_adelete_media",
            media_id=media_id,
            _timeout=_timeout,
        )


class _RESTDeleteAllMediaExpectation(_RESTExpectation):
    def __init__(self, _timeout: Optional[float] = None):
        super().__init__("delete_all_media", "_adelete_all_media", _timeout=_timeout)


class _RESTDeleteResourceExpectation(_RESTExpectation):
    def __init__(self, resource_id: str, _timeout: Optional[float] = None):
        super().__init__(
            "delete_resource",
            "_adelete_resource",
            resource_id=resource_id,
            _timeout=_timeout,
        )

    async def _do_request(self, media):
        rest_method = getattr(media.factory.ctx_media.resource_rest, self._rest_method)
        try:
            if await rest_method(timeout=self._timeout, **self._rest_args) is True:
                self.set_success()
            else:
                self.cancel()
        except Exception as e:
            self.set_exception(e)
