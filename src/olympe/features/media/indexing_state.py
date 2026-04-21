from typing import Optional

from olympe.messaging.events import EventContext
from olympe.messaging.expectations import (
    CheckWaitStateExpectation,
    ExpectPolicy,
    Expectation,
)

from .enums import IndexingState
from .events import MediaEvent
from .expectations import _MediaEventExpectationBase


class _indexing_state_changed_event(_MediaEventExpectationBase):
    def __init__(self, new_state=None, old_state=None, _timeout=None):
        super().__init__("indexing_state_changed", _timeout=_timeout)
        if new_state is not None:
            self._new_state = IndexingState(new_state)
        else:
            self._new_state = None
        if old_state is not None:
            self._old_state = IndexingState(old_state)
        else:
            self._old_state = None

    def check(self, media_event, *args, **kwds):
        if self._success:
            return self
        if not isinstance(media_event, MediaEvent):
            return self
        if media_event.name != self._event_name:
            return self
        if self._new_state is not None and self._new_state is not IndexingState(
            media_event.data["new_state"]
        ):
            return self
        elif self._old_state is not None and self._old_state is not IndexingState(
            media_event.data["old_state"]
        ):
            return self
        else:
            self._success = True
            return self


class _IndexingStateCheck(Expectation):
    def __init__(self, state):
        super().__init__()
        self._expected_state = IndexingState(state)
        self._expected_event = MediaEvent(
            "indexing_state_changed", {"new_state": state}
        )

    def copy(self):
        return super().base_copy(state=self._expected_state)

    def check(self, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media = scheduler.context("olympe.media")
        if media.indexing_state is self._expected_state:
            self.set_success()
        else:
            self.cancel()
        return

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


def indexing_state(
    state, _timeout: Optional[float] = None, _policy: str = "check_wait"
) -> Expectation:
    state = IndexingState(state)
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _IndexingStateCheck(state)
    elif policy == ExpectPolicy.wait:
        return _indexing_state_changed_event(new_state=state, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _IndexingStateCheck(state),
            _indexing_state_changed_event(new_state=state, _timeout=_timeout),
        )
