# -*- coding: UTF-8 -*-

#  Copyright (C) 2019 Parrot Drones SAS
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


from __future__ import unicode_literals
from __future__ import absolute_import


import functools
import pprint
import time

from abc import ABC, abstractmethod
from aenum import Enum
from boltons.setutils import IndexedSet
from concurrent.futures import Future, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import CancelledError as FutureCancelledError
from collections import OrderedDict, defaultdict, deque
from logging import getLogger
from olympe._private import (
    callback_decorator,
    merge_mapping,
    timestamp_now,
    equals,
    DEFAULT_FLOAT_TOL,
)
from olympe._private.pomp_loop_thread import PompLoopThread
from olympe.arsdkng.events import EventContext, MultipleEventContext, ArsdkMessageEvent
from olympe.arsdkng.event_marker import EventMarker
from olympe.arsdkng.listener import Subscriber
import threading


class AbstractScheduler(ABC):

    __slots__ = ()

    @abstractmethod
    def schedule(self, expectations, **kwds):
        pass

    @abstractmethod
    def subscribe(
        self, callback, expectation=None, queue_size=None, default=None, timeout=None
    ):
        """
        Subscribe a callback to some specific event expectation or to all events
        if no specific event expectation is given in parameter.

        :param callback: a callable object (function, method, function-like object, ...)
            :param expectation: an event expectation object (ex: `FlyingStateChanged()`)
            :param queue_size: this subscriber queue size or None if unbounded (the default)
            :type queue_size: int
            :param timeout: the callback timeout in seconds or None for infinite timeout (the default)

        :rtype: Subscriber
        """

    @abstractmethod
    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by :py:func:`~olympe.Drone.subscribe`
        :type subscriber: Subscriber
        """


class Namespace:
    pass


class DefaultScheduler(AbstractScheduler):

    __slots__ = "_attr"

    def __init__(self, pomp_loop_thread, name=None, device_name=None):
        self._attr = Namespace()
        self._attr.default = Namespace()
        self._attr.default.name = name
        self._attr.default.device_name = device_name
        if self._attr.default.name is not None:
            self._attr.default.logger = getLogger(
                "olympe.{}.scheduler".format(self._attr.default.name)
            )
        elif self._attr.default.device_name is not None:
            self._attr.default.logger = getLogger(
                "olympe.scheduler.{}".format(self._attr.default.device_name)
            )
        else:
            self._attr.default.logger = getLogger("olympe.scheduler")

        # Expectations internal state
        self._attr.default.contexts = OrderedDict()
        self._attr.default.pending_expectations = []
        self._attr.default.pomp_loop_thread = pomp_loop_thread

        # Setup expectations monitoring timer, this is used to detect timedout
        # expectations periodically
        self._attr.default.expectations_timer = self._attr.default.pomp_loop_thread.create_timer(
            lambda timer, userdata: self._garbage_collect()
        )
        if not self._attr.default.pomp_loop_thread.set_timer(
            self._attr.default.expectations_timer, delay=200, period=15
        ):
            error_message = "Unable to launch piloting interface"
            self._attr.default.logger.error(error_message)
            raise RuntimeError(error_message)

        # Subscribers internal state
        self._attr.default.subscribers_lock = threading.Lock()
        self._attr.default.subscribers = []
        self._attr.default.running_subscribers = defaultdict(list)
        self._attr.default.subscribers_thread_loop = PompLoopThread(
            self._attr.default.logger, parent=pomp_loop_thread,
        )
        self._attr.default.subscribers_thread_loop.start()

    def add_context(self, name, context):
        self._attr.default.contexts[name] = context

    def remove_context(self, name):
        return self._attr.default.contexts.pop(name, None) is not None

    def context(self, name):
        return self._attr.default.contexts[name]

    def schedule(self, expectations, **kwds):
        # IMPORTANT note: the schedule method should ideally be called from
        # this scheduler pomp loop thread. This method should not be blocking
        # on any input/output (this is true if all expectations.check/_schedule
        # method are non-blocking).
        # To ensure that `self._schedule()` is called in the right thread we
        # execute it through the pomp loop run_async function. If we are already
        # in the pomp loop thread, this `self._schedule()` is called
        # synchronously
        self._attr.default.pomp_loop_thread.run_async(
            self._schedule, expectations, **kwds
        ).result()

    def run(self, *args, **kwds):
        return self._attr.default.pomp_loop_thread.run_async(
            *args, **kwds
        )

    @callback_decorator()
    def _schedule(self, expectation, **kwds):
        expectation._schedule(self)
        monitor = kwds.get("monitor", True)
        if monitor and not expectation.success():
            self._attr.default.pending_expectations.append(expectation)

    def process_event(self, event):
        self._attr.default.pomp_loop_thread.run_async(self._process_event, event)

    @callback_decorator()
    def _process_event(self, event):
        # For all current pending expectations
        garbage_collected_expectations = []
        for expectation in self._attr.default.pending_expectations:
            if expectation.cancelled() or expectation.timedout():
                # Garbage collect canceled/timedout expectations
                garbage_collected_expectations.append(expectation)
            elif expectation.check(event).success():
                # If an expectation successfully matched a message, signal the expectation
                # and remove it from the currently monitored expectations.
                expectation.set_success()
                garbage_collected_expectations.append(expectation)
        # Remove the garbage collected expectations
        for expectation in garbage_collected_expectations:
            self._attr.default.pending_expectations.remove(expectation)

        # Notify subscribers
        self._attr.default.pomp_loop_thread.run_later(self._notify_subscribers, event)

    @callback_decorator()
    def _garbage_collect(self):
        # For all currently pending expectations
        garbage_collected_expectations = []
        for expectation in self._attr.default.pending_expectations:
            # Collect cancelled or timedout expectation
            # The actual cancel/timeout check is delegated to the expectation
            if expectation.cancelled() or expectation.timedout():
                garbage_collected_expectations.append(expectation)
        # Remove the collected expectations
        for expectation in garbage_collected_expectations:
            self._attr.default.pending_expectations.remove(expectation)

    def stop(self):
        for expectation in self._attr.default.pending_expectations:
            expectation.cancel()
        self._attr.default.pending_expectations = []

    def destroy(self):
        self.stop()
        self._attr.default.subscribers_thread_loop.stop()
        self._attr.default.subscribers_thread_loop.destroy()

    @callback_decorator()
    def _notify_subscribers(self, event):
        with self._attr.default.subscribers_lock:
            defaults = OrderedDict.fromkeys(
                (
                    s._default
                    for s in self._attr.default.subscribers
                    if s._default is not None
                )
            )
            for subscriber in self._attr.default.subscribers:
                checked = subscriber.notify(event)
                if checked:
                    if subscriber._default is not None:
                        defaults.pop(subscriber._default, None)
                    future = self._attr.default.subscribers_thread_loop.run_async(
                        subscriber.process
                    )
                    self._attr.default.running_subscribers[id(subscriber)].append(future)
                    future.add_done_callback(
                        functools.partial(
                            lambda subscriber, future, _: self._attr.default.running_subscribers[
                                id(subscriber)
                            ].remove(future),
                            subscriber, future
                        )
                    )

            for default in defaults:
                default.notify(event)
                self._attr.default.subscribers_thread_loop.run_async(default.process)

    def subscribe(
        self,
        callback,
        expectation=None,
        queue_size=Subscriber.default_queue_size,
        default=None,
        timeout=None,
    ):
        """
        Subscribe a callback to some specific event expectation or to all events
        if no specific event expectation is given in parameter.

        :param callback: a callable object (function, method, function-like object, ...)
        :param expectation: an event expectation object (ex: `FlyingStateChanged()`)
        :param queue_size: this subscriber queue size or None if unbounded (the default)
        :type queue_size: int
        :param timeout: the callback timeout in seconds

        :rtype: Subscriber
        """
        subscriber = Subscriber(
            self,
            callback,
            expectation=expectation,
            queue_size=queue_size,
            default=default,
            timeout=timeout,
        )
        with self._attr.default.subscribers_lock:
            self._attr.default.subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by :py:func:`~olympe.Drone.subscribe`
        :type subscriber: Subscriber
        """
        with self._attr.default.subscribers_lock:
            futures = self._attr.default.running_subscribers.pop(id(subscriber), [])
            for future in futures:
                try:
                    future.result(subscriber.timeout)
                except Exception as e:
                    self._attr.default.logger.exception(e)
            self._attr.default.subscribers.remove(subscriber)

    def _subscriber_overrun(self, subscriber, event):
        self._attr.default.logger.warning(
            "Subscriber {} event queue ({}) is overrun by {}".format(
                subscriber, subscriber.queue_size, event
            )
        )


class StreamSchedulerMixin:
    """
    StreamScheduler is scheduler class decorator (decorator pattern not an
    actual python decorator) that acts as a queuing discipline to limit
    the maximum number of parallelized expectation processing.
    """

    __slots__ = ()

    def __init__(self, *args, stream_timeout=None, max_parallel_processing=1, **kwds):
        """
        :param scheduler: the decorated scheduler
        :param stream_timeout: the default timeout value in seconds used by StreamScheduler.join
        :param max_parallel_processing: the maximum number of parallelized expectation
            processing (defaults to 1)
        """
        queue_size = 1024
        self._attr.stream_scheduler = Namespace()
        self._attr.stream_scheduler.timeout = stream_timeout
        self._attr.stream_scheduler.max_parallel_processing = max_parallel_processing
        self._attr.stream_scheduler.token_count = threading.BoundedSemaphore(
            max_parallel_processing
        )
        self._attr.stream_scheduler.expectation_queue = deque([], queue_size)
        self._attr.stream_scheduler.pending_expectations = set()
        self._attr.stream_scheduler.on_done_condition = threading.Condition()

    @callback_decorator()
    def _schedule(self, expectation, **kwds):
        """
        Schedule one expectation processing if the maximum number of parallel
        processing has not been reached yet. Otherwise, the expectation will
        remain in an internal pending queue until at least one expectation
        processing is done.
        """
        self._attr.stream_scheduler.expectation_queue.append((expectation, kwds))
        self._stream_schedule()

    def _stream_schedule(self):
        # try to schedule expectations from the queue if possible
        # while at least one token in available
        while self._attr.stream_scheduler.expectation_queue and (
            self._attr.stream_scheduler.token_count.acquire(blocking=False)
        ):
            expectation, kwds = self._attr.stream_scheduler.expectation_queue.popleft()
            self._attr.stream_scheduler.pending_expectations.add(expectation)
            expectation.add_done_callback(self._stream_on_done)
            super()._schedule(expectation, **kwds)

    def _stream_on_done(self, expectation):
        # release one token
        self._attr.stream_scheduler.token_count.release()
        self._attr.stream_scheduler.pending_expectations.remove(expectation)

        # try to schedule one expectation
        self._stream_schedule()

        # notify that we're done with one expectation processing
        with self._attr.stream_scheduler.on_done_condition:
            self._attr.stream_scheduler.on_done_condition.notify_all()

    def stream_join(self, timeout=None):
        """
        Wait for all currently pending expectations
        """
        if timeout is None:
            timeout = self._attr.stream_scheduler.timeout
        with self._attr.stream_scheduler.on_done_condition:
            self._attr.stream_scheduler.on_done_condition.wait_for(
                lambda: (
                    not bool(self._attr.stream_scheduler.pending_expectations)
                    and not bool(self._attr.stream_scheduler.expectation_queue)
                ),
                timeout=timeout,
            )


class SchedulerDecoratorContext:
    def __init__(self, decorated):
        self._decorated = decorated

    def __getattr__(self, name):
        return getattr(self._decorated, name)

    def decorate(self, name, decorator, *args, **kwds):
        if issubclass(self._decorated.__class__, decorator):
            # We've already applied this decorator, nothing to be done
            return
        namespace = dict(decorator.__dict__)
        self._decorated.__class__ = type(
            name, (decorator, type(self._decorated)), namespace
        )
        decorator.__init__(self._decorated, *args, **kwds)


class Scheduler(SchedulerDecoratorContext):
    def __init__(self, *args, **kwds):
        super().__init__(DefaultScheduler(*args, **kwds))


class ExpectPolicy(Enum):
    wait, check, check_wait = range(3)


class ExpectationBase(ABC):

    always_monitor = False

    def __init__(self):
        self._future = Future()
        self._awaited = False
        self._scheduler = None
        self._success = False
        self._timeout = None
        self._deadline = None
        self._timedout = False
        # FIXME: float_tol should be moved to ArsdkExpectationBase
        self._float_tol = DEFAULT_FLOAT_TOL

    def _schedule(self, scheduler):
        # This expectation is scheduled on the `scheduler`, subclasses of ExpectationBase can
        # perform some operations on this scheduler: schedule another expectation later or
        # perform an operation on the scheduler object when this expectation is schedule (like
        # sending a message for which this expectation object expect some result).
        # IMPORTANT NOTE: this function (or its overridden versions) should be non-blocking
        self._awaited = True
        self._scheduler = scheduler
        if self._timeout is not None:
            self._deadline = timestamp_now() + self._timeout

    def success(self):
        return self._success

    def wait(self, _timeout=None):
        if self._awaited:
            try:
                self._future.result(timeout=_timeout)
            except FutureTimeoutError:
                self.set_timedout()
            except FutureCancelledError:
                self.cancel()
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
        self._future.cancel()
        return True

    def cancelled(self):
        return self._future.cancelled()

    def timedout(self):
        if self._timedout:
            return True
        if self._success:
            return False
        if self._deadline is not None:
            timedout = timestamp_now() > self._deadline
            if timedout:
                self.set_timedout()
        return self._timedout

    def set_float_tol(self, _float_tol):
        self._float_tol = _float_tol

    def base_copy(self, *args, **kwds):
        other = self.__class__(*args, **kwds)
        ExpectationBase.__init__(other)
        other._timeout = self._timeout
        other._float_tol = self._float_tol
        return other

    @abstractmethod
    def copy(self):
        """
        All expectations sublclasses must implement a shallow copy.
        """
        pass

    def done(self):
        return (self._future.done() or not self._awaited) and self._success

    def __bool__(self):
        return self.done()

    def __or__(self, other):
        return WhenAnyExpectation([self, other])

    def __and__(self, other):
        return WhenAllExpectations([self, other])

    def __rshift__(self, other):
        return WhenSequenceExpectations([self, other])

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
        super().__init__()
        self._future = future
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


class ArsdkExpectationBase(Expectation):
    def __init__(self):
        super().__init__()
        self._deprecated_statedict = False

    def _set_deprecated_statedict(self):
        self._deprecated_statedict = True

    @abstractmethod
    def _fill_default_arguments(self, message, args):
        pass

    @abstractmethod
    def check(self, received_event, *args, **kwds):
        pass


class ArsdkFillDefaultArgsExpectationMixin(object):
    def _fill_default_arguments(self, message, args):
        for argname, argval in self.expected_args.copy().items():
            if callable(argval):
                # command message expectation args mapping
                self.expected_args[argname] = argval(message, args)
            elif argval is None and argname in args:
                # default argument handling
                self.expected_args[argname] = args[argname]


class ArsdkEventExpectation(ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase):
    def __init__(self, expected_message, expected_args):
        super().__init__()
        self.expected_message = expected_message.new()
        self.expected_args = OrderedDict()
        for k, v in expected_args.items():
            self.expected_args[k] = v
        self.received_args = []
        self._received_events = []
        self.matched_args = OrderedDict()

    def copy(self):
        return super().base_copy(
            self.expected_message.copy(), self.expected_args.copy()
        )

    def check(self, received_event, *args, **kwds):
        if not isinstance(received_event, ArsdkMessageEvent):
            return self
        if received_event.message.id != self.expected_message.id:
            return self
        self._received_events.append(received_event)
        self.received_args.append(received_event.args)
        for arg_name, arg_val in self.expected_args.items():
            if arg_val is None:
                continue
            if arg_name not in received_event.args:
                return self
            if not equals(
                received_event.args[arg_name],
                self.expected_args[arg_name],
                float_tol=self._float_tol,
            ):
                return self
        if not self._success:
            self.matched_args = received_event.args.copy()
            self.set_success()
        return self

    def expected_events(self):
        if not self._deprecated_statedict:
            return EventContext(
                [ArsdkMessageEvent(self.expected_message, self.expected_args)]
            )
        else:
            return {
                self.expected_message.FULL_NAME: {
                    k.upper(): v for k, v in self.expected_args.items()
                }
            }

    def received_events(self):
        if not self._deprecated_statedict:
            if not self._received_events:
                return EventContext()
            return EventContext(self._received_events[:])
        else:
            return {
                self.expected_message.FULL_NAME: [
                    {k.upper(): v for k, v in args.items()}
                    for args in self.received_args
                ]
            }

    def matched_events(self):
        if not self._deprecated_statedict:
            if self._success:
                if not self.matched_args:
                    return EventContext()
                return EventContext(
                    [ArsdkMessageEvent(self.expected_message, self.matched_args)]
                )
            else:
                return EventContext()
        else:
            if self._success:
                return {
                    self.expected_message.FULL_NAME: {
                        k.upper(): v for k, v in self.matched_args.items()
                    }
                }
            else:
                return {}

    def unmatched_events(self):
        if not self._deprecated_statedict:
            if not self._success:
                return EventContext(self.expected_events().events())
            else:
                return EventContext()
        else:
            if not self._success:
                return self.expected_events()
            else:
                return {}

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        if not self._deprecated_statedict:
            return super().marked_events(default_marked_events=default_marked_events)
        else:
            if not self._success:
                return self.expected_events()
            else:
                return {}

    @classmethod
    def from_arsdk(cls, messages, ar_expectation):
        expected_message = messages.by_id_name[ar_expectation.id.lstrip("#")]
        # When a list item is expected without arguments
        # expect the last and/or empty element
        if not ar_expectation.arguments and (
            expected_message._is_list_item() or expected_message._is_map_item()
        ):
            expectations = []
            for event in ("Last", "Empty"):
                args = OrderedDict()
                event = expected_message.args_bitfield["list_flags"](event)
                args["list_flags"] = event
                expectations.append(cls(expected_message, args))
            return ArsdkWhenAnyExpectation(expectations)
        args = OrderedDict()
        for arg in ar_expectation.arguments:
            if arg.value.startswith("this."):
                argname = arg.value[5:]
                args[arg.name] = (
                    lambda argname: lambda command_message, command_args: command_args[
                        argname
                    ]
                )(argname)
            elif arg.name in expected_message.args_enum:
                args[arg.name] = expected_message.args_enum[arg.name][arg.value]
            elif arg.name in expected_message.args_bitfield:
                args[arg.name] = expected_message.args_bitfield[arg.name](arg.value)
            else:
                args[arg.name] = int(arg.value)
        return cls(expected_message, args)

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.FullName: self.expected_args})


class ArsdkCheckStateExpectation(
    ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase
):
    def __init__(self, expected_message, expected_args):
        super().__init__()
        self.expected_message = expected_message.new()
        self.expected_args = expected_args
        self.matched_state = None

    def copy(self):
        return super().base_copy(
            self.expected_message.copy(), self.expected_args.copy()
        )

    def check(self, received_event, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        controller = scheduler.context("olympe.controller")
        try:
            if controller.check_state(
                self.expected_message, _float_tol=self._float_tol, **self.expected_args
            ):
                self.matched_state = controller.get_state(self.expected_message)
                self.set_success()
            else:
                self.cancel()
        except KeyError:
            # state not found
            pass

    def expected_events(self):
        if not self._deprecated_statedict:
            return EventContext(
                [ArsdkMessageEvent(self.expected_message, self.expected_args)]
            )
        else:
            return {
                self.expected_message.FULL_NAME: {
                    k.upper(): v for k, v in self.expected_args.items()
                }
            }

    def received_events(self):
        return EventContext() if not self._deprecated_statedict else {}

    def matched_events(self):
        if not self._deprecated_statedict:
            if self._success:
                if not self.matched_state:
                    return EventContext()
                return EventContext(
                    [
                        ArsdkMessageEvent(
                            self.expected_message,
                            self.matched_state,
                            ExpectPolicy.check,
                        )
                    ]
                )
            else:
                return EventContext()
        else:
            if self._success:
                return {
                    self.expected_message.FULL_NAME: {
                        k.upper(): v for k, v in self.matched_state.items()
                    }
                }
            else:
                return {}

    def unmatched_events(self):
        if not self._deprecated_statedict:
            if not self._success:
                return EventContext(
                    [ArsdkMessageEvent(self.expected_message, self.matched_state)]
                )
            else:
                return EventContext()
        else:
            if not self._success:
                return {
                    self.expected_message.FULL_NAME: {
                        k.upper(): v for k, v in self.matched_state.items()
                    }
                }
            else:
                return {}

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.FullName: self.expected_args})


class CheckWaitStateExpectationMixin:
    def __init__(self, check_expectation, wait_expectation):
        super().__init__()
        self._check_expectation = check_expectation
        self._wait_expectation = wait_expectation
        self._checked = False

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        self._check_expectation._schedule(scheduler)
        self._checked = self._check_expectation.success()
        self._success = self._checked
        if not self._success:
            scheduler._schedule(
                self._wait_expectation, monitor=self._wait_expectation.always_monitor
            )
        else:
            self.set_success()

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

    def cancelled(self):
        return self._wait_expectation.cancelled()


class CheckWaitStateExpectation(CheckWaitStateExpectationMixin, Expectation):
    pass


class ArsdkCheckWaitStateExpectation(
    CheckWaitStateExpectationMixin, ArsdkExpectationBase
):
    def _set_deprecated_statedict(self):
        super()._set_deprecated_statedict()
        if hasattr(self._check_expectation, "_set_deprecated_statedict"):
            self._check_expectation._set_deprecated_statedict()
        if hasattr(self._wait_expectation, "_set_deprecated_statedict"):
            self._wait_expectation._set_deprecated_statedict()

    def _fill_default_arguments(self, message, args):
        if hasattr(self._check_expectation, "_fill_default_arguments"):
            self._check_expectation._fill_default_arguments(message, args)
        if hasattr(self._wait_expectation, "_fill_default_arguments"):
            self._wait_expectation._fill_default_arguments(message, args)

    def set_float_tol(self, _float_tol):
        super().set_float_tol(_float_tol)
        self._check_expectation.set_float_tol(_float_tol)
        self._wait_expectation.set_float_tol(_float_tol)


class MultipleExpectationMixin:
    def __init__(self, expectations=None):
        super().__init__()
        if expectations is None:
            self.expectations = []
        else:
            self.expectations = expectations
        self.matched_expectations = IndexedSet()

    def copy(self):
        other = super().base_copy(list(map(lambda e: e.copy(), self.expectations)))
        return other

    def append(self, expectation):
        if not isinstance(expectation, self.__class__):
            self.expectations.append(expectation)
        else:
            self.expectations.extend(expectation.expectations)
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

    def __repr__(self):
        return "<{}: {}>".format(self.__class__.__name__, repr(self.expectations))

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

    def as_completed(self, timeout=None):
        end_time = None
        if timeout is not None:
            end_time = timeout + time.monotonic()
        done = set()
        while end_time is None or end_time > time.monotonic():
            fs = OrderedDict([(e._future, e) for e in self.expectations if e not in done])
            for f in as_completed(fs.keys(), timeout=timeout):
                yield fs[f]
                done.add(fs[f])
            if len(done) == len(self.expectations):
                break


class MultipleExpectation(MultipleExpectationMixin, Expectation):
    pass


class ArsdkMultipleExpectation(MultipleExpectationMixin, ArsdkExpectationBase):
    def _set_deprecated_statedict(self):
        super()._set_deprecated_statedict()
        for expectation in self.expectations:
            if hasattr(expectation, "_set_deprecated_statedict"):
                expectation._set_deprecated_statedict()

    def _fill_default_arguments(self, message, args):
        for expectation in self.expectations:
            if hasattr(expectation, "_fill_default_arguments"):
                expectation._fill_default_arguments(message, args)

    def set_float_tol(self, _float_tol):
        for expectation in self.expectations:
            expectation.set_float_tol(_float_tol)

    @classmethod
    def from_arsdk(cls, messages, ar_expectations):
        expectations = list(
            map(
                lambda e: ArsdkEventExpectation.from_arsdk(messages, e), ar_expectations
            )
        )
        return cls(expectations)

    def expected_events(self):
        if not self._deprecated_statedict:
            return super().expected_events()
        else:
            return merge_mapping(map(lambda e: e.expected_events(), self.expectations))

    def received_events(self):
        if not self._deprecated_statedict:
            return super().received_events()
        else:
            return merge_mapping(map(lambda e: e.received_events(), self.expectations))

    def matched_events(self):
        if not self._deprecated_statedict:
            return super().matched_events()
        else:
            return merge_mapping(
                map(lambda e: e.matched_events(), self.matched_expectations)
            )

    def unmatched_events(self):
        if not self._deprecated_statedict:
            return super().unmatched_events()
        else:
            return merge_mapping(
                map(lambda e: e.unmatched_events(), self.unmatched_expectations())
            )

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        if not self._deprecated_statedict:
            return super().marked_events(default_marked_events=EventMarker.unmatched)
        else:
            if not self._success:
                return self.expected_events()
            else:
                return {}


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
        for expectation in self.expectations:
            if (
                expectation.always_monitor or not expectation.success()
            ) and expectation.check(*args, **kwds).success():
                self.matched_expectations.add(expectation)
                self.set_success()
                return self
        return self

    def __or__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "|"


class WhenAnyExpectation(WhenAnyExpectationMixin, MultipleExpectation):
    pass


class ArsdkWhenAnyExpectation(WhenAnyExpectationMixin, ArsdkMultipleExpectation):
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

    def __and__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "&"


class WhenAllExpectations(WhenAllExpectationsMixin, MultipleExpectation):
    pass


class ArsdkWhenAllExpectations(WhenAllExpectationsMixin, ArsdkMultipleExpectation):
    pass


class ArsdkCommandExpectation(ArsdkMultipleExpectation):
    def __init__(self, command_message, command_args=None, expectations=None):
        super().__init__(expectations)
        self.command_message = command_message.new()
        self.command_args = command_args or []
        self._command_future = None
        self._no_expect = False

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

    def check(self, received_event, *args, **kwds):
        if not isinstance(received_event, ArsdkMessageEvent):
            return self
        if self._command_future is None or (
            not self._command_future.done() or not self._command_future.result()
        ):
            return self
        if self._no_expect:
            self.set_success()
            return self
        for expectation in self.expectations:
            if (
                expectation.always_monitor or not expectation.success()
            ) and expectation.check(received_event).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def _fill_default_arguments(self, message, args):
        super()._fill_default_arguments(message, args)
        if self.command_message.id != message.id:
            raise RuntimeError(
                "Unexpected message {} where {} was expected".format(
                    message.fullName, self.command_message.fullName
                )
            )
        self.command_args = list(args.values())

    def copy(self):
        return super().base_copy(
            self.command_message.copy(),
            self.command_args[:],
            list(map(lambda e: e.copy(), self.expectations)),
        )

    def _schedule(self, scheduler):
        if not self._awaited:
            for expectation in self.expectations:
                scheduler._schedule(expectation, monitor=expectation.always_monitor)
            controller = scheduler.context("olympe.controller")
            self._command_future = controller._send_command_raw(
                self.command_message, *self.command_args
            )
            super()._schedule(scheduler)

    def no_expect(self, value):
        self._no_expect = value

    def _combine_method(self):
        return "&"

    def explain(self):
        if self._command_future is None:
            return "{} has not been sent yet".format(self.command_message.fullName)
        elif not self._command_future.done() or not self._command_future.result():
            return "{} has been sent but hasn't been acknowledged".format(
                self.command_message.fullName
            )
        else:
            ret = "{} has been sent and acknowledged.".format(
                self.command_message.fullName
            )
            if not self._no_expect and self.expectations:
                ret += " Command expectations status :\n{}".format(super().explain())
            return ret


class WhenSequenceExpectationsMixin:
    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        self._do_schedule()

    def _do_schedule(self):
        if self._scheduler is None:
            return

        # Schedule all available expectations in this sequence until we
        # encounter a pending asynchronous expectation
        while self._current_expectation() is not None:
            self._scheduler._schedule(
                self._current_expectation(),
                monitor=self._current_expectation().always_monitor,
            )
            if not self._current_expectation().success():
                break
            self.matched_expectations.add(self._current_expectation())

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        elif any(expectation.cancelled() for expectation in self.expectations):
            self.cancel()

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
            self.expectations[len(self.matched_expectations):]
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
                or not self._current_expectation().success()
            )
            and self._current_expectation().check(*args, **kwds).success()
        ):
            # Consume the current expectation
            self.matched_expectations.add(self._current_expectation())
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


class ArsdkWhenSequenceExpectations(
    WhenSequenceExpectationsMixin, ArsdkMultipleExpectation
):
    pass
