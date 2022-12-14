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


import concurrent.futures
import threading
import time

from abc import ABC, abstractmethod
from aenum import Enum
from boltons.setutils import IndexedSet
from collections import OrderedDict
from concurrent.futures import TimeoutError as FutureTimeoutError
from logging import getLogger
from .concurrent import Future
from .event_marker import EventMarker
from .event import EventContext, MultipleEventContext


class ExpectPolicy(Enum):
    wait, check, check_wait = range(3)


class ExpectationBase(ABC):

    always_monitor = False
    _eventloop_future_blocking = False

    def __init__(self, future=None):
        if future is None:
            future = Future()
        self._future = future
        self._awaited = False
        self._scheduler = None
        self._success = False
        self._timeout = None
        self._deadline = None
        self._timedout = False
        self._scheduled_condition = threading.Condition()

    def _schedule(self, scheduler):
        # This expectation is scheduled on the `scheduler`, subclasses of ExpectationBase can
        # perform some operations on this scheduler: schedule another expectation later or
        # perform an operation on the scheduler object when this expectation is schedule (like
        # sending a message for which this expectation object expect some result).
        # IMPORTANT NOTE: this function (or its overridden versions) should be non-blocking
        self._awaited = True
        self._scheduler = scheduler
        if self._future.loop is None:
            self._future.loop = self._scheduler.expectation_loop
        if self._timeout is not None:
            self._deadline = self._scheduler.time() + self._timeout
        with self._scheduled_condition:
            self._scheduled_condition.notify_all()

    def _await(self, scheduler):
        ret = not self._awaited
        self._awaited = True
        self._scheduler = scheduler
        if self._future.loop is None and ret:
            self._future.loop = self._scheduler.expectation_loop
        return ret

    def success(self):
        return self._success

    def wait(self, _timeout=None):
        if self._awaited:
            self._scheduler.expectation_loop.run_async(
                self._wait_future, _timeout=_timeout
            ).result_or_cancel()
        return self

    async def _wait_future(self, _timeout=None):
        deadline = None
        if _timeout is not None:
            deadline = self._scheduler.time() + _timeout
        while True:
            await self._scheduler.expectation_loop.asleep(0.005)
            if self._future.done():
                if self._future.cancelled():
                    self.cancel()
                return self
            if deadline is not None and self._scheduler.time() > deadline:
                self.set_timedout()
                return self

    def add_done_callback(self, cb):
        self._future.add_done_callback(lambda f: cb(self))

    def set_success(self):
        if not self._future.done():
            self._success = True
            self._future.set_result(self.received_events())
            return True
        return False

    def set_exception(self, exception):
        if not self._future.done():
            self._future.set_exception(exception)

    def set_timeout(self, _timeout):
        self._timeout = _timeout

    def set_timedout(self):
        if self._future.done():
            return False
        if not self._success:
            self._timedout = True
            self.cancel()
            return True
        return False

    def cancel(self):
        if self._future.done():
            return False
        return self._future.cancel()

    def cancelled(self):
        return self._future.cancelled()

    def remaining_time(self):
        remaining = self._deadline - self._scheduler.time()
        return remaining if remaining > 0.0 else 0.0

    def timedout(self):
        if self._timedout:
            return True
        if self._success:
            return False
        if self._deadline is not None:
            timedout = self._scheduler.time() > self._deadline
            if timedout:
                self.set_timedout()
        return self._timedout

    def base_copy(self, *args, **kwds):
        other = self.__class__(*args, **kwds)
        other._timeout = self._timeout
        return other

    @abstractmethod
    def copy(self):
        """
        All expectations sublclasses must implement a shallow copy.
        """
        pass

    def explain(self):
        return f"{self.__class__.__name__} is {bool(self)}"

    def done(self):
        return (self._future.done() or not self._awaited) and self._success or (
            self._future.done() and self.exception() is not None)

    def __await__(self):
        if not self.done():
            self._eventloop_future_blocking = True
            yield self
        if not self.done():
            raise RuntimeError("await wasn't used with future")
        return self

    def result(self, timeout=None):
        return self._future.result(timeout=timeout)

    def exception(self, timeout=None):
        if self._success or not self._awaited:
            return None
        if self._future.cancelled():
            return None
        return self._future.exception(timeout=timeout)

    def __bool__(self):
        return self.done()

    def __or__(self, other):
        return WhenAnyExpectation([self, other])

    def __and__(self, other):
        return WhenAllExpectations([self, other])

    def __rshift__(self, other):
        return WhenSequenceExpectations([self, other])

    def __str__(self):
        return self.explain()

    __repr__ = __str__

    __nonzero__ = __bool__


class SuccessExpectation(ExpectationBase):
    def __init__(self):
        super().__init__()
        self.set_success()

    def copy(self):
        return super().base_copy()

    def received_events(self):
        return None


class FailedExpectation(ExpectationBase):
    def __init__(self, message):
        super().__init__()
        self._message = message
        self.set_exception(RuntimeError(message))

    def copy(self):
        return super().base_copy(self._message)

    def explain(self):
        return self._message


class FutureExpectation(ExpectationBase):
    def __init__(self, future, status_checker=lambda status: True):
        super().__init__(future)
        self._status_checker = status_checker
        self._future.add_done_callback(self._on_done)

    def _on_done(self, f):
        if f.exception() is None:
            self._success = self._status_checker(f.result())

    def check(self, *args, **kwds):
        return self

    def copy(self):
        return super().base_copy(self._future, self._status_checker)


class Expectation(ExpectationBase):
    @abstractmethod
    def check(self, *args, **kwds):
        # IMPORTANT NOTE: this function (or its overridden versions) should be non-blocking
        pass

    @abstractmethod
    def expected_events(self):
        pass

    @abstractmethod
    def received_events(self):
        """
        Returns a collection of events that have matched at least one of the
        messages ID monitored by this expectation.
        """
        pass

    @abstractmethod
    def matched_events(self):
        """
        Returns a collection of events that have matched this expectation
        (or a child expectation)
        """
        pass

    @abstractmethod
    def unmatched_events(self):
        """
        Returns a collection of events object that are still expected
        """
        pass

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        """
        Returns a collection of events with matched/unmatched markers.
        """
        if self._success:
            return self.expected_events()._set_marker(EventMarker.matched)
        else:
            return self.expected_events()._set_marker(default_marked_events)

    def explain(self):
        """
        Returns a debug string that explain this expectation current state.
        """
        try:
            return str(self.marked_events())
        except Exception:
            getLogger("olympe.expectations").exception("")
            return None


class CheckWaitStateExpectationMixin:
    def __init__(self, check_expectation, wait_expectation):
        super().__init__()
        self._check_expectation = check_expectation
        self._wait_expectation = wait_expectation
        self._checked = False

    def _await(self, scheduler):
        ret = all(
            list(
                map(
                    lambda e: e._await(scheduler),
                    (super(), self._check_expectation, self._wait_expectation),
                )
            )
        )
        if not ret:
            return False
        self._checked = self._check_expectation.success()
        if self._checked:
            self._wait_expectation.cancel()
            self.set_success()
        return ret

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        scheduler._schedule(self._check_expectation)
        self._checked = self._check_expectation.success()
        if not self._checked:
            scheduler._schedule(
                self._wait_expectation, monitor=self._wait_expectation.always_monitor
            )
            self._check_expectation.cancel()
        else:
            self.set_success()
            self._wait_expectation.cancel()

    def copy(self):
        other = super().base_copy(
            self._check_expectation.copy(), self._wait_expectation.copy()
        )
        return other

    def check(self, *args, **kwds):
        if not self._checked and self._wait_expectation.check(*args, **kwds).success():
            self.set_success()
        return self

    def expected_events(self):
        if self._checked:
            return EventContext(
                self._check_expectation.expected_events().events(),
                ExpectPolicy.check_wait,
            )
        else:
            return EventContext(
                self._wait_expectation.expected_events().events(),
                ExpectPolicy.check_wait,
            )

    def received_events(self):
        if self._checked:
            return self._check_expectation.received_events()
        else:
            return self._wait_expectation.received_events()

    def matched_events(self):
        if self._checked:
            return EventContext(self._check_expectation.matched_events().events())
        else:
            return EventContext(self._wait_expectation.matched_events().events())

    def unmatched_events(self):
        if self._checked:
            return EventContext(self._check_expectation.unmatched_events().events())
        else:
            return EventContext(self._wait_expectation.unmatched_events().events())

    def set_timeout(self, _timeout):
        super().set_timeout(_timeout)
        self._wait_expectation.set_timeout(_timeout)

    def timedout(self):
        if self._checked:
            return False
        else:
            if self._wait_expectation.timedout():
                self.set_timedout()
            return self._wait_expectation.timedout()

    def cancel(self):
        check_cancelled = self._check_expectation.cancel()
        wait_cancelled = self._wait_expectation.cancel()
        return super().cancel() or check_cancelled or wait_cancelled

    def cancelled(self):
        return self._check_expectation.cancelled() and self._wait_expectation.cancelled()


class CheckWaitStateExpectation(CheckWaitStateExpectationMixin, Expectation):
    pass


class MultipleExpectationMixin(ABC):
    def __init__(self, expectations=None):
        super().__init__()
        if expectations is None:
            self.expectations = []
        else:
            self.expectations = expectations
        self.matched_expectations = IndexedSet()

    def _register_subexpectations(self, *expectations):
        for expectation in expectations:
            expectation.add_done_callback(self.on_subexpectation_done)

    @abstractmethod
    def on_subexpectation_done(self, expectation):
        pass

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        scheduler.expectation_loop.run_async(self._register_subexpectations, *self.expectations)

    def _await(self, scheduler):
        ret = True
        if not super()._await(scheduler):
            ret = False
        if not all(list(map(lambda e: e._await(scheduler), self.expectations))):
            ret = False

        scheduler.expectation_loop.run_async(self._register_subexpectations, *self.expectations)
        return ret

    def copy(self):
        other = super().base_copy(list(map(lambda e: e.copy(), self.expectations)))
        return other

    def append(self, expectation):
        if not isinstance(expectation, self.__class__):
            self.expectations.append(expectation)

            if self._scheduler is not None:
                self._scheduler.expectation_loop.run_async(
                    self._register_subexpectations, expectation)
        else:
            self.expectations.extend(expectation.expectations)
            if self._scheduler is not None:
                self._scheduler.expectation_loop.run_async(
                    self._register_subexpectations, expectation.expectations)
        return self

    def expected_events(self):
        return MultipleEventContext(
            list(map(lambda e: e.expected_events(), self.expectations)),
            self._combine_method(),
        )

    def received_events(self):
        return MultipleEventContext(
            list(map(lambda e: e.received_events(), self.expectations)),
            self._combine_method(),
        )

    def matched_events(self):
        return MultipleEventContext(
            list(map(lambda e: e.matched_events(), self.matched_expectations)),
            self._combine_method(),
        )

    def unmatched_events(self):
        return MultipleEventContext(
            list(map(lambda e: e.unmatched_events(), self.unmatched_expectations())),
            self._combine_method(),
        )

    def unmatched_expectations(self):
        for expectation in self.expectations:
            if expectation not in self.matched_expectations:
                yield expectation

    def __iter__(self):
        return iter(self.expectations)

    def __len__(self):
        return len(self.expectations)

    @abstractmethod
    def _combine_method(self):
        pass

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        if self._success:
            default_marked_events = EventMarker.ignored
        return MultipleEventContext(
            list(
                map(lambda e: e.marked_events(default_marked_events), self.expectations)
            ),
            self._combine_method(),
        )

    def as_completed(self, expected_count=None, timeout=None):
        end_time = None
        if timeout is not None:
            end_time = timeout + time.monotonic()
        with self._scheduled_condition:
            if not self._scheduled_condition.wait_for(
                lambda: self._awaited, timeout=timeout
            ):
                raise FutureTimeoutError()
        done = set()
        if timeout is not None:
            timeout = end_time - time.monotonic()
        while timeout is None or timeout > 0:
            fs = OrderedDict(
                [(e._future, e) for e in self.expectations if e not in done]
            )
            for f in concurrent.futures.as_completed(fs.keys(), timeout=timeout):
                yield fs[f]
                if timeout is not None:
                    timeout = end_time - time.monotonic()
                done.add(fs[f])
            done_count = len(done)
            if expected_count is None:
                if done_count == len(self.expectations):
                    return
            elif done_count == expected_count:
                return
            if timeout is not None:
                timeout = end_time - time.monotonic()
        raise FutureTimeoutError()

    def cancel(self):
        cancelled = False
        for expectation in self.expectations:
            if expectation.cancel():
                cancelled = True
        return super().cancel() or cancelled


class MultipleExpectation(MultipleExpectationMixin, Expectation):
    pass


class WhenAnyExpectationMixin:
    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        for expectation in self.expectations:
            scheduler._schedule(expectation, monitor=expectation.always_monitor)
            if expectation.success():
                self.matched_expectations.add(expectation)
                self.set_success()
                break
        if self.success():
            return
        if all(expectation.cancelled() for expectation in self.expectations):
            self.cancel()

    def timedout(self):
        if super().timedout():
            return True
        elif all(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super().timedout()

    def cancelled(self):
        if super().cancelled():
            return True
        elif all(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, *args, **kwds):
        success = False
        for expectation in self.expectations:
            if (
                expectation.always_monitor or not expectation.success()
            ) and expectation.check(*args, **kwds).success():
                self.matched_expectations.add(expectation)
                success = True
                self.set_success()
                break

        if success:
            # Cancel every non successful expectations
            for expectation in self.expectations:
                if not expectation.success():
                    expectation.cancel()
        return self

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return

        self.matched_expectations.add(expectation)
        self.set_success()

        # Cancel every non successful expectations
        for expectation in self.expectations:
            if not expectation.success():
                expectation.cancel()

    def __or__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "|"


class WhenAnyExpectation(WhenAnyExpectationMixin, MultipleExpectation):
    pass


class WhenAllExpectationsMixin:
    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        for expectation in self.expectations:
            scheduler._schedule(expectation, monitor=expectation.always_monitor)
            if expectation.success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        elif any(expectation.cancelled() for expectation in self.expectations):
            self.cancel()

    def timedout(self):
        if super().timedout():
            return True
        elif any(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super().timedout()

    def cancelled(self):
        if super().cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, *args, **kwds):
        for expectation in self.expectations:
            if (
                expectation.always_monitor or not expectation.success()
            ) and expectation.check(*args, **kwds).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return

        self.matched_expectations.add(expectation)
        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()

    def __and__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "&"


class WhenAllExpectations(WhenAllExpectationsMixin, MultipleExpectation):
    pass


class WhenSequenceExpectationsMixin:
    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        self._do_schedule()

    def _await(self, scheduler):
        ret = super()._await(scheduler)
        # Consume any checked expectation
        while self._current_expectation() is not None:
            if not self._current_expectation().success():
                break
            self.matched_expectations.add(self._current_expectation())
        return ret

    def _do_schedule(self):
        if self._scheduler is None:
            return

        # Schedule all available expectations in this sequence until we
        # encounter a pending asynchronous expectation
        while self._current_expectation() is not None:
            current = self._current_expectation()
            if not current._awaited:
                self._scheduler._schedule(
                    current,
                    monitor=current.always_monitor,
                )
            if not current.success():
                break
            self.matched_expectations.add(current)

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        elif any(expectation.cancelled() for expectation in self.expectations):
            self.cancel()

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return
        self._scheduler.expectation_loop.run_async(self._do_schedule)

    def timedout(self):
        if super().timedout():
            return True
        elif any(map(lambda e: e.timedout(), self._pending_expectations())):
            self.set_timedout()
        return super().timedout()

    def cancelled(self):
        if super().cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self._pending_expectations())):
            self.cancel()
            return True
        else:
            return False

    def _current_expectation(self):
        return (
            self.expectations[len(self.matched_expectations)]
            if len(self.matched_expectations) < len(self.expectations)
            else None
        )

    def _pending_expectations(self):
        return (
            self.expectations[len(self.matched_expectations) :]
            if len(self.matched_expectations) < len(self.expectations)
            else []
        )

    def check(self, *args, **kwds):
        if self._current_expectation() is None:
            self.set_success()
            return self

        # While the current event matches an unmatched expectation
        # in this sequence
        while (
            self._current_expectation() is not None
            and (
                self._current_expectation().always_monitor
                or (not self._current_expectation().success())
            )
            and (self._current_expectation().check(*args, **kwds).success())
        ):
            # Schedule the next expectation(s), if any.
            # This may also consume one or more synchronous expectations
            # (i.e. events with policy="check").
            self._do_schedule()

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def __rshift__(self, other):
        return self.append(other)

    def _combine_method(self):
        return ">>"


class WhenSequenceExpectations(WhenSequenceExpectationsMixin, MultipleExpectation):
    pass
