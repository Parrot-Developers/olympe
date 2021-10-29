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

from .subscriber import Subscriber
import functools


def listen_event(expectation=None, queue_size=None):
    """
    See :py:class:`~olympe.EventListener` documentation.
    """

    def decorator(method):
        if not hasattr(method, "__listento__"):
            method.__listento__ = []
        if expectation is not None:
            method.__listento__.append(expectation)
        previous_queue_size = getattr(method, "__queue_size__", None)
        if previous_queue_size is None:
            method.__queue_size__ = queue_size
        elif queue_size is not None and previous_queue_size != queue_size:
            raise TypeError(
                "Listener method queue size can only be set once per method"
            )
        return method

    return decorator


class _EventListenerMeta(type):
    def __new__(cls, name, bases, ns):
        listener_methods = dict()
        default_queue_size = ns.get("default_queue_size", None)
        if default_queue_size is None:
            for base in bases:
                if hasattr(base, "default_queue_size"):
                    default_queue_size = base.default_queue_size
                    break
            else:
                default_queue_size = Subscriber.default_queue_size
        for k, v in ns.items():
            if hasattr(v, "__listento__"):
                if k != "default":
                    listener_methods[k] = v
                if getattr(v, "__queue_size__", None) is None:
                    v.__queue_size__ = default_queue_size
        if "default" in ns:
            default = ns["default"]
            if not hasattr(default, "__listento__"):
                default.__listento__ = []
            if getattr(default, "__queue_size__", None) is None:
                default.__queue_size__ = default_queue_size
        ns["__listener_methods__"] = listener_methods
        return super().__new__(cls, name, bases, ns)


class EventListener(metaclass=_EventListenerMeta):
    """
    EventListener base class

    This class implements the visitor pattern and is meant to be overridden to
    dispatch drone event messages to the correct class method.

    To start/stop listening to event messages EventListener.subscribe()
    EventListener.unsubscribe() methods should be called. Alternatively,
    this class can be used as a context manager.

    Example:

    .. code-block:: python

        import olympe
        from olympe.messages.ardrone3.Piloting import TakeOff, Landing, moveBy
        from olympe.messages.ardrone3.PilotingState import (
            PositionChanged,
            AlertStateChanged,
            FlyingStateChanged,
            NavigateHomeStateChanged,
        )

        class FlightListener(olympe.EventListener):

            @olympe.listen_event(FlyingStateChanged() | AlertStateChanged() |
                NavigateHomeStateChanged())
            def onStateChanged(self, event, scheduler):
                print("{} = {}".format(event.message.name, event.args["state"]))

            @olympe.listen_event(PositionChanged())
            def onPositionChanged(self, event, scheduler):
                print(
                    "latitude = {latitude} longitude = {longitude} altitude = {altitude}".format(
                        **event.args
                    )
                )


        drone = olympe.Drone("10.202.0.1")
        with FlightListener(drone):
            drone.connect()
            drone(
                FlyingStateChanged(state="hovering")
                | (TakeOff() & FlyingStateChanged(state="hovering"))
            ).wait()
            drone(moveBy(10, 0, 0, 0)).wait()
            drone(Landing()).wait()
            drone(FlyingStateChanged(state="landed")).wait()
            drone.disconnect()
    """

    default_queue_size = Subscriber.default_queue_size
    default_timeout = Subscriber.default_timeout

    def __init__(self, *contexts, timeout=default_timeout):
        """
            :param scheduler: an olympe.Drone or an olympe.expectations.Scheduler
                object for which this listener will subscribe to event messages.
            :param timeout: the listener callbacks timeout in seconds
        """
        self._contexts = contexts
        self._schedulers = [context.scheduler for context in contexts]
        self._subscribers = []
        self._default_subscribers = []
        self._timeout = timeout
        self._expectations = []
        for scheduler in self._schedulers:
            self._default_subscribers.append(
                Subscriber(
                    scheduler,
                    self.default,
                    queue_size=getattr(
                        self.default, "__queue_size__", self.default_queue_size
                    ),
                    timeout=timeout,
                )
            )

    def subscribe(self):
        """
            Start to listen to the scheduler event messages
        """
        for name, method in self.__listener_methods__.items():
            expectations = method.__listento__ or [None]
            for scheduler, default_subscriber in zip(
                self._schedulers, self._default_subscribers
            ):
                for expectation in expectations:
                    self._subscribers.append((
                        scheduler.subscribe(
                            functools.partial(method, self),
                            expectation,
                            queue_size=method.__queue_size__,
                            default=default_subscriber,
                            timeout=self._timeout,
                        ),
                        scheduler,
                    ))

    def unsubscribe(self):
        """
            Stop from listening scheduler event messages
        """
        for subscriber, scheduler in self._subscribers:
            scheduler.unsubscribe(subscriber)

    def __enter__(self):
        self.subscribe()
        return self

    def __exit__(self, *args, **kwds):
        self.unsubscribe()

    @listen_event(queue_size=Subscriber.default_queue_size)
    def default(self, event, scheduler):
        pass

    @property
    def timeout(self):
        return self._timeout
