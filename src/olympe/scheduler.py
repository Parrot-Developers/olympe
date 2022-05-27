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

from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict, deque
from logging import getLogger
from types import SimpleNamespace

from olympe.expectations import MultipleExpectation
from olympe.subscriber import Subscriber
from olympe.concurrent import Loop
from olympe.utils import callback_decorator, timestamp_now

import functools
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
            :param timeout: the callback timeout in seconds or None for infinite timeout (the
             default)

        :rtype: Subscriber
        """

    @abstractmethod
    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by :py:func:`~olympe.Drone.subscribe`
        :type subscriber: Subscriber
        """


class DefaultScheduler(AbstractScheduler):

    __slots__ = "_attr"

    def __init__(self, pomp_loop_thread, name=None, device_name=None):
        self._attr = SimpleNamespace()
        self._attr.default = SimpleNamespace()
        self._attr.default.name = name
        self._attr.default.device_name = device_name
        self._attr.default.time = timestamp_now
        if self._attr.default.name is not None:
            self._attr.default.logger = getLogger(
                f"olympe.{self._attr.default.name}.scheduler"
            )
        elif self._attr.default.device_name is not None:
            self._attr.default.logger = getLogger(
                f"olympe.scheduler.{self._attr.default.device_name}"
            )
        else:
            self._attr.default.logger = getLogger("olympe.scheduler")

        # Expectations internal state
        self._attr.default.contexts = OrderedDict()
        self._attr.default.pending_expectations = []
        self._attr.default.pomp_loop_thread = pomp_loop_thread

        # Setup expectations timeout monitoring
        self._attr.default.pomp_loop_thread.run_delayed(0.2, self._garbage_collect)

        # Subscribers internal state
        self._attr.default.subscribers_lock = threading.RLock()
        self._attr.default.subscribers = []
        self._attr.default.running_subscribers = defaultdict(set)
        self._attr.default.subscribers_thread_loop = Loop(
            self._attr.default.logger,
            name="subscribers_thread",
            parent=pomp_loop_thread,
        )
        self._attr.default.subscribers_thread_loop.start()

    def set_time_function(self, time_function):
        self._attr.default.time = time_function

    def time(self):
        return self._attr.default.time()

    def add_context(self, name, context):
        self._attr.default.contexts[name] = context

    def remove_context(self, name):
        return self._attr.default.contexts.pop(name, None) is not None

    def context(self, name):
        return self._attr.default.contexts[name]

    def _call_context_schedule_hook(self, expectation, **kwds):
        if isinstance(expectation, MultipleExpectation):
            for e in expectation.expectations:
                ret = self._call_context_schedule_hook(e, **kwds)
                if ret is not None:
                    return ret
            return None
        for context in self._attr.default.contexts.values():
            if hasattr(context, "schedule_hook"):
                ret = context.schedule_hook(expectation, **kwds)
                if ret is not None:
                    return ret
        return None

    def schedule(self, expectations, **kwds):
        # IMPORTANT note: the schedule method should ideally be called from
        # this scheduler pomp loop thread. This method should not be blocking
        # on any input/output (this is true if all expectations.check/_schedule
        # method are non-blocking).
        # To ensure that `self._schedule()` is called in the right thread we
        # execute it through the pomp loop run_async function. If we are already
        # in the pomp loop thread, this `self._schedule()` is called
        # synchronously
        hook_ret = self._call_context_schedule_hook(expectations, **kwds)
        if hook_ret is not None:
            return hook_ret
        self._attr.default.pomp_loop_thread.run_async(
            self._schedule, expectations, **kwds
        ).result()
        return expectations

    def run(self, *args, **kwds):
        return self._attr.default.pomp_loop_thread.run_async(*args, **kwds)

    @property
    def expectation_loop(self):
        return self._attr.default.pomp_loop_thread

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
        garbage_collected_expectations = set()
        for expectation in self._attr.default.pending_expectations:
            if expectation.cancelled() or expectation.timedout():
                # Garbage collect canceled/timedout expectations
                garbage_collected_expectations.add(expectation)
            elif expectation.check(event).success():
                # If an expectation successfully matched a message, signal the expectation
                # and remove it from the currently monitored expectations.
                expectation.set_success()
                garbage_collected_expectations.add(expectation)
        # Remove the garbage collected expectations
        for expectation in garbage_collected_expectations:
            try:
                self._attr.default.pending_expectations.remove(expectation)
            except ValueError:
                pass

        # Notify subscribers
        self._attr.default.pomp_loop_thread.run_later(self._notify_subscribers, event)

    async def _garbage_collect(self):
        while self._attr.default.pomp_loop_thread.running:
            # For all currently pending expectations
            garbage_collected_expectations = []
            for expectation in self._attr.default.pending_expectations:
                # Collect cancelled or timedout expectation
                # The actual cancel/timeout check is delegated to the expectation
                if expectation.cancelled() or expectation.timedout():
                    garbage_collected_expectations.append(expectation)
            # Remove the collected expectations
            for expectation in garbage_collected_expectations:
                try:
                    self._attr.default.pending_expectations.remove(expectation)
                except ValueError:
                    pass
            await self._attr.default.pomp_loop_thread.asleep(0.015)

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
                s._default
                for s in self._attr.default.subscribers
                if s._default is not None
            )
            for subscriber in self._attr.default.subscribers:
                checked = subscriber.notify(event)
                if checked:
                    if subscriber._default is not None:
                        defaults.pop(subscriber._default, None)
                    future = self._attr.default.subscribers_thread_loop.run_async(
                        subscriber.process
                    )
                    self._attr.default.running_subscribers[id(subscriber)].add(
                        future
                    )
                    future.add_done_callback(
                        functools.partial(
                            lambda subscriber, future, _: self._attr.default.running_subscribers[
                                id(subscriber)
                            ].discard(
                                future
                            ),
                            subscriber,
                            future,
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
            futures = self._attr.default.running_subscribers.pop(id(subscriber), set())
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
        self._attr.stream_scheduler = SimpleNamespace()
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
                    and (not bool(self._attr.stream_scheduler.expectation_queue))
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
