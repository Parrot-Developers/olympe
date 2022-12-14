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

import pprint

from abc import abstractmethod
from olympe.concurrent import CancelledError, Future
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from olympe.utils import (
    equals,
    DEFAULT_FLOAT_TOL,
)
from olympe.event import EventContext
from olympe.expectations import (
    ExpectPolicy,
    Expectation,
    CheckWaitStateExpectationMixin,
    MultipleExpectationMixin,
    WhenAnyExpectationMixin,
    WhenAllExpectationsMixin,
    WhenSequenceExpectationsMixin,
)


class ArsdkExpectationBase(Expectation):
    def __init__(self):
        super().__init__()
        self._float_tol = DEFAULT_FLOAT_TOL

    def copy(self):
        other = super().base_copy()
        other._float_tol = self._float_tol
        return other

    @abstractmethod
    def _fill_default_arguments(self, message, args):
        pass

    @abstractmethod
    def check(self, received_event, *args, **kwds):
        pass

    def set_float_tol(self, _float_tol):
        self._float_tol = _float_tol

    def __or__(self, other):
        return ArsdkWhenAnyExpectation([self, other])

    def __and__(self, other):
        return ArsdkWhenAllExpectations([self, other])

    def __rshift__(self, other):
        return ArsdkWhenSequenceExpectations([self, other])


class ArsdkFillDefaultArgsExpectationMixin:
    def _fill_default_arguments(self, message, args):
        for argname, argval in self.expected_args.copy().items():
            if callable(argval):
                # command message expectation args mapping
                self.expected_args[argname] = argval(message, args)
            elif argval is None:
                if argname in args:
                    # default argument handling
                    self.expected_args[argname] = args[argname]
                else:
                    # filter out None value
                    del self.expected_args[argname]


def _match(received, expected, float_tol):
    if isinstance(expected, (bytes, str)):
        if received != expected:
            return False
    elif isinstance(expected, Mapping):
        if not _match_mapping(received, expected, float_tol):
            return False
    elif isinstance(expected, Iterable):
        if not _match_iterable(received, expected, float_tol):
            return False
    else:
        if not equals(received, expected, float_tol=float_tol):
            return False
    return True


def _match_mapping(received_args, expected_args, float_tol):
    for arg_name, arg_val in expected_args.items():
        if arg_val is None:
            continue
        if arg_name not in received_args:
            return False
        if not _match(
            received_args[arg_name], expected_args[arg_name], float_tol=float_tol
        ):
            return False
    return True


def _match_iterable(received_args, expected_args, float_tol):
    if len(received_args) != len(expected_args):
        return False
    for received_arg, expected_arg in zip(received_args, expected_args):
        if not _match(received_arg, expected_arg, float_tol=float_tol):
            return False
    return True


class ArsdkEventExpectation(ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase):
    def __init__(self, expected_message, expected_args):
        super().__init__()
        self.expected_message = expected_message.new()
        self.expected_args = OrderedDict()
        self.expected_event_type = self.expected_message._event_type()
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
        if not isinstance(received_event, self.expected_event_type):
            return self
        if received_event.message.id != self.expected_message.id:
            return self
        self._received_events.append(received_event)
        self.received_args.append(received_event.args)
        if not _match_mapping(received_event.args, self.expected_args, self._float_tol):
            return self
        if not self._success:
            self.matched_args = received_event.args.copy()
            self.set_success()
        return self

    def expected_events(self):
        return EventContext(
            [self.expected_event_type(self.expected_message, self.expected_args)]
        )

    def received_events(self):
        if not self._received_events:
            return EventContext()
        return EventContext(self._received_events[:])

    def matched_events(self):
        if self._success:
            if not self.matched_args:
                return EventContext()
            return EventContext(
                [self.expected_event_type(self.expected_message, self.matched_args)]
            )
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext(self.expected_events().events())
        else:
            return EventContext()

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
                args[arg.name] = expected_message.args_type[arg.name](arg.value)
        return cls(expected_message, args)

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.fullName: self.expected_args})


class ArsdkCheckStateExpectation(
    ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase
):
    def __init__(self, expected_message, expected_args):
        super().__init__()
        self.expected_message = expected_message.new()
        self.expected_event_type = self.expected_message._event_type()
        self.expected_args = expected_args
        self.matched_state = None

    def copy(self):
        return super().base_copy(
            self.expected_message.copy(), self.expected_args.copy()
        )

    def check(self, received_event, *args, **kwds):
        return self

    def _await(self, scheduler):
        if not super()._await(scheduler):
            return False
        self._do_check(scheduler)
        return True

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        self._do_check(scheduler)

    def _do_check(self, scheduler):
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
        return EventContext(
            [self.expected_event_type(self.expected_message, self.expected_args)]
        )

    def received_events(self):
        return EventContext()

    def matched_events(self):
        if self._success:
            if not self.matched_state:
                return EventContext()
            return EventContext(
                [
                    self.expected_event_type(
                        self.expected_message,
                        self.matched_state,
                        ExpectPolicy.check,
                    )
                ]
            )
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext(
                [self.expected_event_type(self.expected_message, self.matched_state)]
            )
        else:
            return EventContext()

    def __iter__(self):
        return iter((self,))

    def __len__(self):
        return 1

    def __repr__(self):
        return pprint.pformat({self.expected_message.fullName: self.expected_args})


class ArsdkCheckWaitStateExpectation(
    CheckWaitStateExpectationMixin, ArsdkExpectationBase
):
    def _fill_default_arguments(self, message, args):
        if hasattr(self._check_expectation, "_fill_default_arguments"):
            self._check_expectation._fill_default_arguments(message, args)
        if hasattr(self._wait_expectation, "_fill_default_arguments"):
            self._wait_expectation._fill_default_arguments(message, args)

    def set_float_tol(self, _float_tol):
        super().set_float_tol(_float_tol)
        self._check_expectation.set_float_tol(_float_tol)
        self._wait_expectation.set_float_tol(_float_tol)

    def __iter__(self):
        yield from self._check_expectation
        yield from self._wait_expectation


class ArsdkMultipleExpectation(MultipleExpectationMixin, ArsdkExpectationBase):
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


class ArsdkWhenAnyExpectation(WhenAnyExpectationMixin, ArsdkMultipleExpectation):
    pass


class ArsdkWhenAllExpectations(WhenAllExpectationsMixin, ArsdkMultipleExpectation):
    pass


class ArsdkCommandExpectation(ArsdkMultipleExpectation):
    def __init__(self, command_message, command_args=None, expectations=None):
        super().__init__(expectations)
        self.command_message = command_message.new()
        self.command_args = command_args or {}
        self._command_future = None
        self._no_expect = False
        self.expected_event_type = self.command_message._event_type()

    def timedout(self):
        if super().timedout():
            return True
        elif any(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super().timedout()

    def cancel(self):
        if self._command_future is not None and not self._command_future.done():
            # Futures associated to running callbacks are non-cancellable
            # We have to use Future.set_exception() here instead.
            self._command_future.set_exception(CancelledError())
        return super().cancel()

    def cancelled(self):
        if super().cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def _check_command_event(self, received_event):
        if self._command_future is not None:
            return
        if not self._awaited:
            return
        if not isinstance(received_event, self.expected_event_type):
            return
        if received_event.message.id != self.command_message.id:
            return
        if not _match_mapping(received_event.args, self.command_args, self._float_tol):
            return
        self._command_future = Future()
        self._command_future.set_result(True)
        return True

    def check(self, received_event, *args, **kwds):
        if self.success():
            return self
        self._check_command_event(received_event)
        command_sent = False
        if self._command_future is not None and self._command_future.done():
            if self._command_future.cancelled():
                return self
            elif self._command_future.exception():
                return self
            elif self._command_future.result():
                command_sent = True
        if self._no_expect:
            if command_sent:
                self.set_success()
            return self
        for expectation in self.expectations:
            if (
                expectation.always_monitor or not expectation.success()
            ) and expectation.check(received_event).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            if command_sent:
                self.set_success()
        return self

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return
        self.matched_expectations.add(expectation)

        if self._command_future is None or not self._command_future.done():
            return
        if self._command_future.cancelled():
            return
        elif self._command_future.exception():
            return

        # the command has been sent, this command is successful if its
        # expectations matched.
        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()

    def _fill_default_arguments(self, message, args):
        super()._fill_default_arguments(message, args)
        if self.command_message.id != message.id:
            raise RuntimeError(
                "Unexpected message {} where {} was expected".format(
                    message.fullName, self.command_message.fullName
                )
            )
        self.command_args = args

    def copy(self):
        return super().base_copy(
            self.command_message.copy(),
            self.command_args.copy(),
            list(map(lambda e: e.copy(), self.expectations)),
        )

    def _schedule(self, scheduler):
        if not self._awaited:
            controller = scheduler.context("olympe.controller")
            self._command_future = controller._send_command_raw(
                self.command_message, self.command_args
            )
            self._command_future.add_done_callback(lambda _: self.check(None))
        super()._schedule(scheduler)
        for expectation in self.expectations:
            scheduler._schedule(expectation, monitor=expectation.always_monitor)

    def no_expect(self, value):
        self._no_expect = value

    def _combine_method(self):
        return "&"

    def explain(self):
        if self._command_future is None:
            return f"{self.command_message.fullName} has not been sent yet"
        elif not self._command_future.done() or not self._command_future.result():
            return "{} has been sent but hasn't been acknowledged".format(
                self.command_message.fullName
            )
        else:
            ret = "{} has been sent and acknowledged.".format(
                self.command_message.fullName
            )
            if not self._no_expect and self.expectations:
                ret += f" Command expectations status:\n{super().explain()}"
            return ret


class ArsdkProtoCommandExpectation(ArsdkExpectationBase):
    def __init__(self, message, command_args=None, expectation=None):
        super().__init__()
        self.command_message = message.new()
        self.command_args = command_args or {}
        self.expectation = expectation
        self._command_future = None
        self._no_expect = False
        self.expected_event_type = self.command_message._event_type()

    def cancel(self):
        if self._command_future is not None and not self._command_future.done():
            # Futures associated to running callbacks are non-cancellable
            # We have to use Future.set_exception() here instead.
            self._command_future.set_exception(CancelledError())
        return super().cancel()

    def _check_command_event(self, received_event):
        if self._command_future is not None:
            return
        if not self._awaited:
            return
        if not isinstance(received_event, self.expected_event_type):
            return
        if received_event.message.id != self.command_message.id:
            return
        if not _match_mapping(received_event.args, self.command_args, self._float_tol):
            return
        self._command_future = Future()
        self._command_future.set_result(True)
        return True

    def check(self, received_event, *args, **kwds):
        if self.success():
            return self
        self._check_command_event(received_event)
        if self.success():
            return self
        command_sent = False
        if self._command_future is not None and self._command_future.done():
            if self._command_future.cancelled():
                return self
            elif self._command_future.exception():
                return self
            elif self._command_future.result():
                command_sent = True
                if self._no_expect:
                    self.set_success()
                elif self.expectation is None or self.expectation.success():
                    self.set_success()
        if (
                self.expectation is not None and (
                    self.expectation.always_monitor or not
                    self.expectation.success()
                ) and self.expectation.check(received_event).success()
        ):
            if command_sent:
                self.set_success()
        return self

    def _fill_default_arguments(self, message, args):
        super()._fill_default_arguments(message, args)
        if hasattr(self.expectation, "_fill_default_arguments"):
            self.expectation._fill_default_arguments(message, args)
        if message.id != self.command_message.id:
            raise RuntimeError(
                f"Unexpected message {message.fullName} where "
                f"{self.command_message.fullName} was expected: "
                f"{message.id} != {self.command_message.id}"
            )
        self.command_args = dict(args)

    def copy(self):
        return super().base_copy(
            self.command_message,
            self.command_args.copy(),
            self.expectation.copy() if self.expectation is not None else None,
        )

    def _schedule(self, scheduler):
        if not self._awaited:
            controller = scheduler.context("olympe.controller")
            self._command_future = controller._send_protobuf_command(
                self.command_message, self.command_args
            )
            self._command_future.add_done_callback(lambda _: self.check(None))
        super()._schedule(scheduler)

    def no_expect(self, value):
        self._no_expect = value

    def expected_events(self):
        if self._no_expect or self.expectation is None:
            return EventContext()
        return self.expectation.expected_events()

    def received_events(self):
        if self.expectation is None:
            return EventContext()
        return self.expectation.received_events()

    def matched_events(self):
        if self._no_expect or self.expectation is None:
            return EventContext()
        return self.expectation.matched_events()

    def unmatched_events(self):
        if self.expectation is None:
            return EventContext()
        return self.expectation.unmatched_events()

    def explain(self):
        if self._command_future is None:
            return f"{self.command_message.fullName} has not been sent yet"
        elif not self._command_future.done() or (not self._command_future.result()):
            return (
                f"{self.command_message.fullName} has been sent "
                f"but hasn't been acknowledged"
            )
        else:
            ret = f"{self.command_message.fullName} has been sent and " f"acknowledged."
            if not self._no_expect and self.expectation is not None:
                ret += (
                    f" Command expectations status:\n" f"{self.expectation.explain()}"
                )
            return ret

    def __iter__(self):
        if self.expectation is None:
            return iter(())
        return iter(self.expectation)

    @property
    def expectations(self):
        # Provided for convenience and to be consistent with ArsdkCommandExpectation
        return self.expectation


class ArsdkWhenSequenceExpectations(
    WhenSequenceExpectationsMixin, ArsdkMultipleExpectation
):
    pass
