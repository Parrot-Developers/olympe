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

import pprint
import textwrap

from abc import ABCMeta, abstractmethod
from aenum import Enum
from boltons.setutils import IndexedSet
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import CancelledError as FutureCancelledError
from collections import OrderedDict

from olympe._private import merge_mapping, timestamp_now, equals, DEFAULT_FLOAT_TOL
from olympe.arsdkng.events import ArsdkEventContext, ArsdkMultipleEventContext, ArsdkMessageEvent
from olympe.arsdkng.event_marker import EventMarker


class ExpectPolicy(Enum):
    wait, check, check_wait = range(3)


class ExpectationBase(object):

    __metaclass__ = ABCMeta

    def __init__(self):
        self._future = Future()
        self._awaited = False
        self._scheduler = None
        self._success = False
        self._timeout = None
        self._deadline = None
        self._timedout = False
        self._float_tol = DEFAULT_FLOAT_TOL

    def _schedule(self, scheduler):
        # This expectation is scheduled on the `scheduler`, subclasses of ExpectationBase can
        # perform some operations on this scheduler: schedule another expectation later or
        # perform an operation on the scheduler object when this expectation is schedule (like
        # sending a message for which this expectation object expect some result).
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

    def set_result(self):
        self._success = True
        return self._future.set_result(self.received_events())

    def set_exception(self, exception):
        return self._future.set_exception(exception)

    def set_timeout(self, _timeout):
        self._timeout = _timeout

    def set_timedout(self):
        if not self._success:
            self._timedout = True
            self.cancel()

    def cancel(self):
        return self._future.cancel()

    def cancelled(self):
        return self._future.cancelled()

    def timedout(self):
        if self._timedout:
            return True
        if self._success:
            return False
        if self._deadline is not None:
            self._timedout = (timestamp_now() > self._deadline)
            if self._timedout:
                self.cancel()
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
        return ArsdkWhenAnyExpectation([self, other])

    def __and__(self, other):
        return ArsdkWhenAllExpectations([self, other])

    def __rshift__(self, other):
        return ArsdkWhenSequenceExpectations([self, other])

    __nonzero__ = __bool__


class SuccessExpectation(ExpectationBase):

    def __init__(self):
        super(SuccessExpectation, self).__init__()
        self.set_result()

    def copy(self):
        return super(SuccessExpectation, self).base_copy()

    def received_events(self):
        return None


class FailedExpectation(ExpectationBase):

    def __init__(self, message):
        super(FailedExpectation, self).__init__()
        self._message = message
        self.set_exception(RuntimeError(message))

    def copy(self):
        return super(FailedExpectation, self).base_copy(self._message)


class FutureExpectation(ExpectationBase):

    def __init__(self, future, status_checker=lambda status: True):
        super(FutureExpectation, self).__init__()
        self._future = future
        self._future.add_done_callback(self._on_done)
        self._status_checker = status_checker

    def _on_done(self, f):
        if f.exception() is None:
            self._success = self._status_checker(f.result())

    def copy(self):
        return super(FutureExpectation, self).base_copy(
            self._future,
            self.status_checker
        )


class ArsdkExpectationBase(ExpectationBase):

    def __init__(self):
        super(ArsdkExpectationBase, self).__init__()
        self._deprecated_statedict = False

    def _set_deprecated_statedict(self):
        self._deprecated_statedict = True

    @abstractmethod
    def _fill_default_arguments(self, message, args):
        pass

    @abstractmethod
    def check(self, received_event):
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
        if not self._deprecated_statedict:
            if self._success:
                return self.expected_events()._set_marker(EventMarker.matched)
            else:
                return self.expected_events()._set_marker(default_marked_events)
        else:
            raise NotImplementedError("marked_event when _set_deprecated_statedict() has been called")

    def explain(self):
        """
        Returns a debug string that explain this expectation current state.
        """
        if not self._deprecated_statedict:
            try:
                return str(self.marked_events())
            except SyntaxError:
                import logging
                logging.exception("")
                raise
        else:
            return textwrap.dedent("""
                ExpectationBase.explain() is not available when _set_deprecated_statedict() is called.
                Please don't call it or use the following functions to get an idea of what is going
                on with this expectation:
                 - ExpectationBase.expected_events() -> events that are expected
                 - ExpectationBase.received_events() -> received events matching an expected event
                   message id but not necessarily its argument
                 - ExpectationBase.matched_events() -> received events matching an expected event
                   message id and its arguments
                 - ExpectationBase.unmatched_events() -> received events that match an expected
                   message id and but not its arguments
                """)


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
        super(ArsdkEventExpectation, self).__init__()
        self.expected_message = expected_message.new()
        self.expected_args = OrderedDict()
        for k, v in expected_args.items():
            self.expected_args[k] = v
        self.received_args = []
        self._received_events = []
        self.matched_args = OrderedDict()

    def copy(self):
        return super(ArsdkEventExpectation, self).base_copy(
            self.expected_message.copy(),
            self.expected_args.copy()
        )

    def check(self, received_event):
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
                    float_tol=self._float_tol):
                return self
        if not self._success:
            self.matched_args = received_event.args.copy()
            self._success = True
        return self

    def expected_events(self):
        if not self._deprecated_statedict:
            return ArsdkEventContext([ArsdkMessageEvent(
                self.expected_message,
                self.expected_args)])
        else:
            return {
                self.expected_message.FULL_NAME:
                {k.upper(): v for k, v in self.expected_args.items()}
            }

    def received_events(self):
        if not self._deprecated_statedict:
            if not self._received_events:
                return ArsdkEventContext()
            return ArsdkEventContext(self._received_events[:])
        else:
            return {
                self.expected_message.FULL_NAME:
                [{k.upper(): v for k, v in args.items()} for args in self.received_args]
            }

    def matched_events(self):
        if not self._deprecated_statedict:
            if self._success:
                if not self.matched_args:
                    return ArsdkEventContext()
                return ArsdkEventContext([ArsdkMessageEvent(
                    self.expected_message,
                    self.matched_args)]
                )
            else:
                return ArsdkEventContext()
        else:
            if self._success:
                return {
                    self.expected_message.FULL_NAME:
                    {k.upper(): v for k, v in self.matched_args.items()}
                }
            else:
                return {}

    def unmatched_events(self):
        if not self._deprecated_statedict:
            if not self._success:
                return ArsdkEventContext(
                    self.expected_events().events()
                )
            else:
                return ArsdkEventContext()
        else:
            if not self._success:
                return self.expected_events()
            else:
                return {}

    @classmethod
    def from_arsdk(cls, messages, ar_expectation):
        expected_message = messages.by_id_name[ar_expectation.id.lstrip("#")]
        args = OrderedDict()
        for arg in ar_expectation.arguments:
            if arg.value.startswith("this."):
                argname = arg.value[5:]
                args[arg.name] = (
                    lambda argname:
                    lambda command_message, command_args:
                    command_args[argname]
                )(argname)
            else:
                args[arg.name] = expected_message.args_enum[arg.name][arg.value]
        return cls(expected_message, args)

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.FullName: self.expected_args})


class ArsdkCheckStateExpectation(ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase):

    def __init__(self, expected_message, expected_args):
        super(ArsdkCheckStateExpectation, self).__init__()
        self.expected_message = expected_message.new()
        self.expected_args = expected_args
        self.matched_state = None

    def copy(self):
        return super(ArsdkCheckStateExpectation, self).base_copy(
            self.expected_message.copy(),
            self.expected_args.copy()
        )

    def check(self, received_event):
        return self

    def _schedule(self, drone):
        super(ArsdkCheckStateExpectation, self)._schedule(drone)
        try:
            if drone.check_state(self.expected_message, **self.expected_args):
                self.matched_state = drone.get_state(self.expected_message)
                self._success = True
                self.set_result()
        except KeyError:
            # state not found
            pass

    def expected_events(self):
        if not self._deprecated_statedict:
            return ArsdkEventContext([ArsdkMessageEvent(
                self.expected_message,
                self.expected_args)])
        else:
            return {
                self.expected_message.FULL_NAME:
                {k.upper(): v for k, v in self.expected_args.items()}
            }

    def received_events(self):
        return ArsdkEventContext() if not self._deprecated_statedict else {}

    def matched_events(self):
        if not self._deprecated_statedict:
            if self._success:
                if not self.matched_state:
                    return ArsdkEventContext()
                return ArsdkEventContext([ArsdkMessageEvent(
                    self.expected_message,
                    self.matched_state,
                    ExpectPolicy.check)]
                )
            else:
                return ArsdkEventContext()
        else:
            if self._success:
                return {
                    self.expected_message.FULL_NAME:
                    {k.upper(): v for k, v in self.matched_state.items()}
                }
            else:
                return {}

    def unmatched_events(self):
        if not self._deprecated_statedict:
            if not self._success:
                return ArsdkEventContext([ArsdkMessageEvent(
                    self.expected_message,
                    self.matched_state)]
                )
            else:
                return ArsdkEventContext()
        else:
            if not self._success:
                return {
                    self.expected_message.FULL_NAME:
                    {k.upper(): v for k, v in self.matched_state.items()}
                }
            else:
                return {}

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.FullName: self.expected_args})


class ArsdkCheckWaitStateExpectation(ArsdkExpectationBase):

    def __init__(self, check_expectation, wait_expectation):
        super(ArsdkCheckWaitStateExpectation, self).__init__()
        self._check_expectation = check_expectation
        self._wait_expectation = wait_expectation
        self._checked = False

    def _schedule(self, drone):
        super(ArsdkCheckWaitStateExpectation, self)._schedule(drone)
        self._check_expectation._schedule(drone)
        self._checked = self._check_expectation.success()
        self._success = self._checked
        if not self._success:
            self._wait_expectation._schedule(drone)
        else:
            self.set_result()

    def copy(self):
        other = super(ArsdkCheckWaitStateExpectation, self).base_copy(
            self._check_expectation.copy(), self._wait_expectation.copy()
        )
        return other

    def _set_deprecated_statedict(self):
        super(ArsdkCheckWaitStateExpectation, self)._set_deprecated_statedict()
        self._check_expectation._set_deprecated_statedict()
        self._wait_expectation._set_deprecated_statedict()

    def _fill_default_arguments(self, message, args):
        self._check_expectation._fill_default_arguments(message, args)
        self._wait_expectation._fill_default_arguments(message, args)

    def check(self, received_event):
        if not self._checked and self._wait_expectation.check(received_event).success():
            self._success = True
            self.set_result()
        return self

    def expected_events(self):
        if self._checked:
            return ArsdkEventContext(
                self._check_expectation.expected_events().events(), ExpectPolicy.check_wait)
        else:
            return ArsdkEventContext(
                self._wait_expectation.expected_events().events(), ExpectPolicy.check_wait)

    def received_events(self):
        if self._checked:
            return self._check_expectation.received_events()
        else:
            return self._wait_expectation.received_events()

    def matched_events(self):
        if self._checked:
            return ArsdkEventContext(
                self._check_expectation.matched_events().events())
        else:
            return ArsdkEventContext(
                self._wait_expectation.matched_events().events())

    def unmatched_events(self):
        if self._checked:
            return ArsdkEventContext(
                self._check_expectation.unmatched_events().events())
        else:
            return ArsdkEventContext(
                self._wait_expectation.unmatched_events().events())

    def set_timeout(self, _timeout):
        super(ArsdkCheckWaitStateExpectation, self).set_timeout(_timeout)
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

    def set_float_tol(self, _float_tol):
        super(ArsdkCheckWaitStateExpectation, self).set_float_tol(_float_tol)
        self._check_expectation.set_float_tol(_float_tol)
        self._wait_expectation.set_float_tol(_float_tol)


class ArsdkMultipleExpectation(ArsdkExpectationBase):

    def __init__(self, expectations=None):
        super(ArsdkMultipleExpectation, self).__init__()
        if expectations is None:
            self.expectations = []
        else:
            self.expectations = expectations
        self.matched_expectations = IndexedSet()

    def copy(self):
        other = super(ArsdkMultipleExpectation, self).base_copy(
            list(map(lambda e: e.copy(), self.expectations))
        )
        return other

    def _set_deprecated_statedict(self):
        super(ArsdkMultipleExpectation, self)._set_deprecated_statedict()
        for expectation in self.expectations:
            expectation._set_deprecated_statedict()

    def _fill_default_arguments(self, message, args):
        for expectation in self.expectations:
            expectation._fill_default_arguments(message, args)

    def set_float_tol(self, _float_tol):
        for expectation in self.expectations:
            expectation.set_float_tol(_float_tol)

    def append(self, expectation):
        if not isinstance(expectation, self.__class__):
            self.expectations.append(expectation)
        else:
            self.expectations.extend(expectation.expectations)
        return self

    def expected_events(self):
        if not self._deprecated_statedict:
            return ArsdkMultipleEventContext(
                list(map(lambda e: e.expected_events(), self.expectations)),
                self._combine_method()
            )
        else:
            return merge_mapping(map(lambda e: e.expected_events(), self.expectations))

    def received_events(self):
        if not self._deprecated_statedict:
            return ArsdkMultipleEventContext(
                list(map(lambda e: e.received_events(), self.expectations)),
                self._combine_method()
            )
        else:
            return merge_mapping(map(lambda e: e.received_events(), self.expectations))

    def matched_events(self):
        if not self._deprecated_statedict:
            return ArsdkMultipleEventContext(
                list(map(lambda e: e.matched_events(), self.matched_expectations)),
                self._combine_method()
            )
        else:
            return merge_mapping(
                map(lambda e: e.matched_events(), self.matched_expectations))

    def unmatched_events(self):
        if not self._deprecated_statedict:
            return ArsdkMultipleEventContext(
                list(map(lambda e: e.unmatched_events(), self.unmatched_expectations())),
                self._combine_method()
            )
        else:
            return merge_mapping(
                map(lambda e: e.unmatched_events(), self.unmatched_expectations()))

    def unmatched_expectations(self):
        for expectation in self.expectations:
            if expectation not in self.matched_expectations:
                yield expectation

    @classmethod
    def from_arsdk(cls, messages, ar_expectations):
        expectations = list(map(
            lambda e: ArsdkEventExpectation.from_arsdk(messages, e),
            ar_expectations))
        return cls(expectations)

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
        if not self._deprecated_statedict:
            if self._success:
                default_marked_events = EventMarker.ignored
            return ArsdkMultipleEventContext(
                list(map(lambda e: e.marked_events(default_marked_events), self.expectations)),
                self._combine_method(),
            )
        else:
            raise NotImplementedError("marked_event when _set_deprecated_statedict() has been called")


class ArsdkWhenAnyExpectation(ArsdkMultipleExpectation):

    def _schedule(self, drone):
        super(ArsdkWhenAnyExpectation, self)._schedule(drone)
        for expectation in self.expectations:
            expectation._schedule(drone)
            if expectation.success():
                self.matched_expectations.add(expectation)
                self._success = True
                self.set_result()
                break

    def timedout(self):
        if super(ArsdkWhenAnyExpectation, self).timedout():
            return True
        elif all(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super(ArsdkWhenAnyExpectation, self).timedout()

    def cancelled(self):
        if super(ArsdkWhenAnyExpectation, self).cancelled():
            return True
        elif all(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, received_event):
        for expectation in self.expectations:
            if not expectation.success() and expectation.check(received_event).success():
                self.matched_expectations.add(expectation)
                self._success = True
                return self
        return self

    def __or__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "|"


class ArsdkWhenAllExpectations(ArsdkMultipleExpectation):

    def _schedule(self, drone):
        super(ArsdkWhenAllExpectations, self)._schedule(drone)
        for expectation in self.expectations:
            expectation._schedule(drone)
            if expectation.success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self._success = True
            self.set_result()

    def timedout(self):
        if super(ArsdkWhenAllExpectations, self).timedout():
            return True
        elif any(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super(ArsdkWhenAllExpectations, self).timedout()

    def cancelled(self):
        if super(ArsdkWhenAllExpectations, self).cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, received_event):
        for expectation in self.expectations:
            if not expectation.success() and expectation.check(received_event).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self._success = True
        return self

    def __and__(self, other):
        return self.append(other)

    def _combine_method(self):
        return "&"


class ArsdkCommandExpectation(ArsdkMultipleExpectation):

    def __init__(self, command_message, command_args=None, expectations=None):
        super(ArsdkCommandExpectation, self).__init__(expectations)
        self.command_message = command_message.new()
        self.command_args = command_args or []
        self._command_expectation = None
        self._no_expect = False

    def timedout(self):
        if super(ArsdkCommandExpectation, self).timedout():
            return True
        elif any(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super(ArsdkCommandExpectation, self).timedout()

    def cancelled(self):
        if super(ArsdkCommandExpectation, self).cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, received_event):
        if self._command_expectation is None or not self._command_expectation.success():
            return self
        if self._no_expect:
            self._success = True
            return self
        for expectation in self.expectations:
            if not expectation.success() and expectation.check(received_event).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self._success = True
        return self

    def _fill_default_arguments(self, message, args):
        super(ArsdkCommandExpectation, self)._fill_default_arguments(
            message, args)
        if self.command_message.id != message.id:
            raise RuntimeError("Unexpected message {} where {} was expected".format(
                message.fullName, self.command_message.fullName))
        self.command_args = list(args.values())

    def copy(self):
        return super(ArsdkCommandExpectation, self).base_copy(
            self.command_message.copy(), self.command_args[:],
            list(map(lambda e: e.copy(), self.expectations))
        )

    def _schedule(self, drone):
        if not self._awaited:
            for expectation in self.expectations:
                expectation._schedule(drone)
            self._command_expectation = (
                drone._send_command_raw(self.command_message, *self.command_args))
            super(ArsdkCommandExpectation, self)._schedule(drone)

    def no_expect(self, value):
        self._no_expect = value

    def _combine_method(self):
        return "&"


class ArsdkWhenSequenceExpectations(ArsdkMultipleExpectation):

    def _schedule(self, drone):
        super(ArsdkWhenSequenceExpectations, self)._schedule(drone)
        self._do_schedule()

    def _do_schedule(self):
        if self._scheduler is None:
            return

        # Schedule all available expectations in this sequence until we
        # encounter a pending asynchronous expectation
        while self._current_expectation() is not None:
            self._current_expectation()._schedule(self._scheduler)
            if not self._current_expectation().success():
                break
            self.matched_expectations.add(self._current_expectation())

        if len(self.expectations) == len(self.matched_expectations):
            self._success = True
            self.set_result()

    def timedout(self):
        if super(ArsdkWhenSequenceExpectations, self).timedout():
            return True
        elif any(map(lambda e: e.timedout(), self._pending_expectations())):
            self.set_timedout()
        return super(ArsdkWhenSequenceExpectations, self).timedout()

    def cancelled(self):
        if super(ArsdkWhenSequenceExpectations, self).cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self._pending_expectations())):
            self.cancel()
            return True
        else:
            return False

    def _current_expectation(self):
        return (self.expectations[len(self.matched_expectations)]
                if len(self.matched_expectations) < len(self.expectations)
                else None)

    def _pending_expectations(self):
        return (self.expectations[len(self.matched_expectations):]
                if len(self.matched_expectations) < len(self.expectations)
                else [])

    def check(self, received_event):
        if self._current_expectation() is None:
            self._success = True
            return self

        # While the current event matches an unmatched expectation
        # in this sequence
        while (self._current_expectation() is not None and
               not self._current_expectation().success() and
               self._current_expectation().check(received_event).success()):
            # Consume the current expectation
            self.matched_expectations.add(self._current_expectation())
            # Schedule the next expectation(s), if any.
            # This may also consume one or more synchronous expectations
            # (i.e. events with policy="check").
            self._do_schedule()

        if len(self.expectations) == len(self.matched_expectations):
            self._success = True
        return self

    def __rshift__(self, other):
        return self.append(other)

    def _combine_method(self):
        return ">>"
