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
from collections import OrderedDict
from collections.abc import Iterable, Mapping, MutableMapping
from concurrent.futures import CancelledError
from enum import Enum, auto
from typing import Tuple, Type

from olympe.concurrent import Future
from olympe.parser.proto_this import ArsdkProtoThis
from olympe.utils import (
    equals_float,
    equals_int,
    DEFAULT_FLOAT_TOL,
    DEFAULT_INT_TOL,
)
from .arsdk_enums import ArsdkBitfield
from .events import Event, EventContext
from .expectations import (
    ExpectPolicy,
    Expectation,
    CheckWaitStateExpectationMixin,
    MultipleExpectationMixin,
    WhenAnyExpectationMixin,
    WhenAllExpectationsMixin,
    WhenSequenceExpectationsMixin,
)


class MatchingMode(Enum):
    """
    Expectation matching mode enum class

    - `MatchingMode.pattern`: (the default) matches if the expected arguments are present among the received arguments and match some of them.

    - `MatchingMode.exact`: further restrict the matching events to those that don't contain more arguments than the expected arguments.

    - `MatchingMode.negative_pattern`: matches if not all expected arguments are present in the received arguments.

    This enum controls how expectations are matched internally.
    """

    pattern = auto()
    exact = auto()
    negative_pattern = auto()


class ArsdkExpectationBase(Expectation):
    def __init__(
        self,
        float_tol: Tuple[float, float] = DEFAULT_FLOAT_TOL,
        int_tol: int = DEFAULT_INT_TOL,
        matching_mode: MatchingMode = MatchingMode.pattern,
    ):
        super().__init__()
        self._float_tol = float_tol
        self._int_tol = int_tol
        self._matching_mode = matching_mode

    def copy(self):
        return super().base_copy(self._float_tol, self._int_tol, self._matching_mode)

    @abstractmethod
    def _fill_default_arguments(self, message, args):
        pass

    @abstractmethod
    def check(self, received_event, *args, **kwds):
        pass

    def set_float_tol(self, _float_tol):
        self._float_tol = _float_tol

    def set_int_tol(self, _int_tol):
        self._int_tol = _int_tol

    def set_matching_mode(self, _matching_mode: MatchingMode):
        self._matching_mode = _matching_mode

    def __or__(self, other):
        return ArsdkWhenAnyExpectation([self, other])

    def __and__(self, other):
        return ArsdkWhenAllExpectations([self, other])

    def __rshift__(self, other):
        return ArsdkWhenSequenceExpectations([self, other])


class ArsdkFillDefaultArgsExpectationMixin:
    def _fill_default_arguments(self, message, args):
        definition = False
        for argname, argdef in args.items():
            if isinstance(argdef, ArsdkProtoThis):
                definition = True
        if definition:
            self.expected_args = args
            return

        def set_defaults(message, args, expected_args):
            ret = OrderedDict()
            for argname, argval in expected_args.items():
                if callable(argval):
                    # command message expectation args mapping
                    ret[argname] = argval(message, args)
                elif isinstance(argval, MutableMapping):
                    ret[argname] = set_defaults(message, args, argval)
                elif argval is None:
                    if argname in args:
                        # default argument handling
                        ret[argname] = args[argname]
                    else:
                        # filter out None value
                        pass
                else:
                    ret[argname] = argval
            return ret

        self.expected_args = set_defaults(message, args, self.expected_args)


def _match(
    received, expected, float_tol: float, int_tol: int, matching_mode: MatchingMode
):
    if matching_mode is not MatchingMode.negative_pattern:
        if isinstance(expected, (bytes, str)):
            if received != expected:
                return False
        elif isinstance(expected, Mapping):
            if not _match_mapping(
                received, expected, float_tol, int_tol, matching_mode
            ):
                return False
        elif isinstance(expected, ArsdkBitfield):
            if matching_mode is MatchingMode.exact:
                if expected & received != expected or expected & received != received:
                    return False
            else:
                if expected & received != expected:
                    return False
        elif isinstance(expected, Iterable):
            if not _match_iterable(
                received, expected, float_tol, int_tol, matching_mode
            ):
                return False
        elif isinstance(expected, Enum):
            if expected != received:
                return False
        elif isinstance(expected, float):
            if not equals_float(received, expected, float_tol=float_tol):
                return False
        elif isinstance(expected, int):
            if not equals_int(received, expected, int_tol=int_tol):
                return False
        else:
            raise ValueError(
                f"Expectation of type {type(expected)} does not match any of types "
                "[bytes, str, Mapping, bitfield, Iterable, Enum, float, int]"
            )
        return True
    else:
        if isinstance(expected, (bytes, str)):
            if received == expected:
                return False
        elif isinstance(expected, Mapping):
            if not _match_mapping(
                received, expected, float_tol, int_tol, matching_mode
            ):
                return False
        elif isinstance(expected, ArsdkBitfield):
            if expected & received != 0 or expected == received == 0:
                return False
        elif isinstance(expected, Iterable):
            if not _match_iterable(
                received, expected, float_tol, int_tol, matching_mode
            ):
                return False
        elif isinstance(expected, Enum):
            if expected == received:
                return False
        elif isinstance(expected, float):
            if equals_float(received, expected, float_tol=float_tol):
                return False
        elif isinstance(expected, int):
            if equals_int(received, expected, int_tol=int_tol):
                return False
        else:
            raise ValueError(
                f"Expectation of type {type(expected)} does not match any of types "
                "[bytes, str, Mapping, bitfield, Iterable, Enum, float, int]"
            )
        return True


def _match_mapping(
    received_args,
    expected_args,
    float_tol: float,
    int_tol: int,
    matching_mode: MatchingMode,
):
    if matching_mode is MatchingMode.exact and len(received_args) != len(expected_args):
        return False
    for arg_name, arg_val in expected_args.items():
        if arg_val is None:
            # An expected argument to None is supposed not to be in received argument.
            # It is like this argument does not count
            if matching_mode is MatchingMode.exact and arg_name in received_args:
                # We are NOT expecting this argument to be present
                # This is a reason why 'exact' and 'pattern' are not handled the same:
                # their difference resides in the fact that 'exact' requires a strictly exact
                # matching, whereas received arguments can outnumber expected
                # arguments in 'pattern' matching
                return False
            else:
                # The argument may or may not be present in received arguments in case
                # 'pattern' or 'negative': it is ignored so to say
                continue
        if matching_mode is not MatchingMode.negative_pattern:
            # If at least one expected argument key is not present in received arguments keys,
            # then there is no 'exact' or 'pattern' matching
            if arg_name not in received_args:
                return False
            # If at least one expected argument does not match in value with the
            # received arguments it shares key with, then there is no 'exact' or
            # 'pattern' matching
            if not _match(
                received_args[arg_name],
                expected_args[arg_name],
                float_tol=float_tol,
                int_tol=int_tol,
                matching_mode=matching_mode,
            ):
                return False
        else:
            # If an expected argument is not present in received arguments,
            # then there is no issue in 'negative' pattern
            if arg_name not in received_args:
                continue
            # If at least one expected argument matches in value with the
            # received arguments it shares key with, then there is no 'negative'
            # matching. Here the 'not' is essential because the thinking is already
            # switched in the match function concerning 'negative pattern'
            if not _match(
                received_args[arg_name],
                expected_args[arg_name],
                float_tol=float_tol,
                int_tol=int_tol,
                matching_mode=matching_mode,
            ):
                return False
    return True


def _match_iterable(
    received_args,
    expected_args,
    float_tol: float,
    int_tol: int,
    matching_mode: MatchingMode,
):
    # In case 'exact' pattern, every items of the expected arguments must match the
    # received arguments. So the length of each have to match
    if matching_mode is MatchingMode.exact and len(expected_args) != len(received_args):
        return False
    # In case 'exact' or 'pattern', received_args cannot be empty while expected args is
    # not. This means we are expecting arguments that are not received
    if (
        matching_mode is not MatchingMode.negative_pattern
        and expected_args
        and not received_args
    ):
        return False
    # Avoid mutating the user provided received arguments by copying the original iterable.
    # Make the copy mutable by converting it to list because all iterables
    # by default are parsed from protobuf as tuples, and tuples are immutable.
    received_args_copy = list(received_args[:])
    for expected_arg in expected_args:
        counter = 0
        # A copy of the received arguments is used to pop out the arguments that will match the
        # expected arguments in case 'exact'. Otherwise, 1 single received argument 'toto'
        # would match x expected arguments 'toto', whereas we would expect the x expected
        # arguments to match x received arguments
        for received_arg in received_args_copy[:]:
            # The aim is to check that every expected argument will be in received arguments
            if matching_mode is not MatchingMode.negative_pattern:
                # In case of 'pattern' or 'exact', if at least one expected argument
                # does not match any received arguments, there is no matching
                if _match(
                    received_arg,
                    expected_arg,
                    float_tol=float_tol,
                    int_tol=int_tol,
                    matching_mode=matching_mode,
                ):
                    if matching_mode is MatchingMode.exact:
                        # The matching received argument has to be removed, not to match
                        # other expected arguments that would be identical in case 'exact'.
                        # This is why 'received_args_copy' was cast to list a few lines above.
                        received_args_copy.remove(received_arg)
                    break
                else:
                    # Browse all list to seek for the matching argument. Return false
                    # if it has not been found until the end of the list only
                    if counter == len(received_args_copy) - 1:
                        return False
                    else:
                        counter = counter + 1
            else:
                # In case of 'negative' pattern, we do want the item to be present. But
                # because the result has already been switched in match, we have to
                # switch again the result
                if not _match(
                    received_arg,
                    expected_arg,
                    float_tol=float_tol,
                    int_tol=int_tol,
                    matching_mode=matching_mode,
                ):
                    return False
    return True


class ArsdkEventExpectation(ArsdkFillDefaultArgsExpectationMixin, ArsdkExpectationBase):
    def __init__(
        self,
        expected_message,
        expected_args,
        float_tol: Tuple[float, float] = DEFAULT_FLOAT_TOL,
        int_tol: int = DEFAULT_INT_TOL,
        matching_mode: MatchingMode = MatchingMode.pattern,
    ):
        super().__init__(float_tol, int_tol, matching_mode)
        self.expected_message = expected_message.new()
        self.expected_args = OrderedDict()
        self.expected_event_type = self.expected_message._event_type()
        for k, v in expected_args.items():
            self.expected_args[k] = v
        self._received_events = []
        self.matched_args = OrderedDict()

    def copy(self):
        return super().base_copy(
            self.expected_message.copy(),
            self.expected_args.copy(),
            self._float_tol,
            self._int_tol,
            self._matching_mode,
        )

    def check(self, received_event, *args, fallback: bool = True, **kwds):
        assert self._scheduler is not None

        # Check that the type of message is the same ; otherwise they don't match for sure
        if not isinstance(received_event, self.expected_event_type):
            return self
        # Also check that the id of message is the same ; otherwise they don't match either
        if received_event.message.id != self.expected_message.id:
            return self

        self._received_events.append(received_event)
        # Match the event
        if not _match_mapping(
            received_event.args,
            self.expected_args,
            self._float_tol,
            self._int_tol,
            self._matching_mode,
        ):
            # When getting the controller state, this means all the previous received
            # events are retrieved. Thus, a previous received event may make an expectation
            # success, event if it is not the event relied upon to make it success.
            # So that could make an event taken into account for that expectation, thanks
            # to a former received one that matches this expectation.
            # This is why the below fallback shall not be used for certain purposes, such
            # as a subscriber. A subscriber should wait for events to arrive, not check
            # the former received ones
            if not fallback:
                return self
            # If the event does not match, try matching the same previously received
            # state that is remembered
            controller = self._scheduler.context("olympe.controller")
            try:
                state_args = controller.get_state(self.expected_message)
            except ValueError:
                return self
            # If the message is not a map, the two messages can be directly compared,
            # without considering the sub-dict to get rid of the map key they match upon.
            # This case may happen when the user would like to match an event with all
            # the items of the message, and not only the parts that have changed recently.
            from olympe.messaging.arsdk_messages import ArsdkMessageCallbackType

            if self.expected_message.callback_type is not ArsdkMessageCallbackType.MAP:
                if not _match_mapping(
                    state_args,
                    self.expected_args,
                    self._float_tol,
                    self._int_tol,
                    self._matching_mode,
                ):
                    return self
            # If the message is a map, they can't be directly compared, because the received
            # message has an upper-dict, with the map key in question, and probably many other.
            # So this upper-dict has to be browsed according to the map key, so that the
            # right one will be tested, and if both the dict of this map key and the dict
            # of the expected args match, this means the check is successful. If no dict
            # of one of those keys match the expected message, there is no matching
            # This case may happen when the user would like to match a specific dict of
            # a map message, and not the last one received particularly. For instance, we
            # may have received a camera_id=0 event, but we would like to check the state
            # of camera_id=1, that may still be right
            else:
                for value in state_args.values():
                    if _match_mapping(
                        value,
                        self.expected_args,
                        self._float_tol,
                        self._int_tol,
                        self._matching_mode,
                    ):
                        break
                else:
                    return self
        if not self._success:
            # Store the received args that have matched
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
    def __init__(
        self,
        expected_message,
        expected_args,
        float_tol: Tuple[float, float] = DEFAULT_FLOAT_TOL,
        int_tol: int = DEFAULT_INT_TOL,
        matching_mode: MatchingMode = MatchingMode.pattern,
    ):
        super().__init__(float_tol, int_tol, matching_mode)
        self.expected_message = expected_message.new()
        self.expected_event_type = self.expected_message._event_type()
        self.expected_args = expected_args
        self.matched_state = None
        self._received_events = []

    def copy(self):
        return super().base_copy(
            self.expected_message.copy(),
            self.expected_args.copy(),
            self._float_tol,
            self._int_tol,
            self._matching_mode,
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

    def _check_event(self, scheduler):
        expectation = self.expected_message._expectation_from_args(**self.expected_args)
        expectation.set_float_tol(self._float_tol)
        expectation.set_int_tol(self._int_tol)
        expectation._await(scheduler)
        from olympe.messaging.arsdk_messages import ArsdkMessageCallbackType

        if self.expected_message.callback_type is not ArsdkMessageCallbackType.MAP:
            key = None
        else:
            if self.expected_message.key_name not in expectation.expected_args:
                key = None
            else:
                key = expectation.expected_args[self.expected_message.key_name]
        try:
            for last_event in scheduler.context("olympe.controller").get_last_events(
                self.expected_message, key=key
            ):
                self._received_events.append(last_event)
                if expectation.check(last_event).success():
                    return True
        except KeyError:
            return False
        else:
            return False

    def _do_check(self, scheduler):
        controller = scheduler.context("olympe.controller")
        try:
            if self._check_event(scheduler):
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
        if not self._received_events:
            return EventContext()
        return EventContext(self._received_events[:])

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

    def set_int_tol(self, _int_tol):
        super().set_int_tol(_int_tol)
        self._check_expectation.set_int_tol(_int_tol)
        self._wait_expectation.set_int_tol(_int_tol)

    def set_matching_mode(self, _matching_mode):
        super().set_matching_mode(_matching_mode)
        self._check_expectation.set_matching_mode(_matching_mode)
        self._wait_expectation.set_matching_mode(_matching_mode)

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
        super().set_float_tol(_float_tol)

    def set_matching_mode(self, _matching_mode):
        for expectation in self.expectations:
            expectation.set_matching_mode(_matching_mode)
        super().set_matching_mode(_matching_mode)

    def set_int_tol(self, _int_tol):
        for expectation in self.expectations:
            expectation.set_int_tol(_int_tol)

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
        if not _match_mapping(
            received_event.args,
            self.command_args,
            self._float_tol,
            self._int_tol,
            self._matching_mode,
        ):
            return
        self._command_future = Future()
        self._command_future.set_result(True)
        return True

    def check(self, received_event, *args, **kwds):
        assert self._scheduler is not None
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
                self._add_matched_expectation(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            if command_sent:
                self.set_success()
        return self

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return
        self._add_matched_expectation(expectation)

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
        for expectation in self.expectations:
            scheduler._schedule(expectation, monitor=expectation.always_monitor)
        if not self._awaited:
            controller = scheduler.context("olympe.controller")
            self._command_future = controller._send_command_raw(
                self.command_message, self.command_args
            )
            super()._schedule(scheduler)
            self._command_future.add_done_callback(lambda _: self.check(None))
        else:
            super()._schedule(scheduler)

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
    def __init__(
        self,
        message,
        command_args=None,
        expectation=None,
        float_tol: Tuple[float, float] = DEFAULT_FLOAT_TOL,
        int_tol: int = DEFAULT_INT_TOL,
        matching_mode: MatchingMode = MatchingMode.pattern,
    ):
        super().__init__(float_tol, int_tol, matching_mode)
        self.command_message = message.new()
        self.command_args = command_args or {}
        self.expectation = expectation
        self._command_future = None
        self._no_expect = False
        self.expected_event_type = self.command_message._event_type()

    def set_float_tol(self, _float_tol):
        if self.expectation is not None:
            self.expectation.set_float_tol(_float_tol)
        super().set_float_tol(_float_tol)

    def set_matching_mode(self, _matching_mode):
        if self.expectation is not None:
            self.expectation.set_matching_mode(_matching_mode)
        super().set_matching_mode(_matching_mode)

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
        if not _match_mapping(
            received_event.args,
            self.command_args,
            self._float_tol,
            self._int_tol,
            self._matching_mode,
        ):
            return
        self._command_future = Future()
        self._command_future.set_result(True)
        return True

    def check(self, received_event, *args, **kwds):
        assert self._scheduler is not None
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
            self.expectation is not None
            and (self.expectation.always_monitor or not self.expectation.success())
            and self.expectation.check(received_event).success()
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
            self._float_tol,
            self._int_tol,
            self._matching_mode,
        )

    def _schedule(self, scheduler):
        if self.expectation is not None:
            scheduler._schedule(
                self.expectation, monitor=self.expectation.always_monitor
            )
        if not self._awaited:
            controller = scheduler.context("olympe.controller")
            self._command_future = controller._send_protobuf_command(
                self.command_message, self.command_args
            )
            super()._schedule(scheduler)
            self._command_future.add_done_callback(lambda _: self.check(None))
            if self.expectation is not None:
                self.expectation._schedule(scheduler)
        else:
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


class UnknownMessage(Expectation):
    event_type: Type[Event]

    def __init__(self):
        self._received_event = None
        super().__init__()

    def copy(self):
        return self.base_copy()

    def check(self, event, *args, **kwds):
        if not isinstance(event, self.event_type):
            return self
        self._received_event = event
        self.set_success()
        return self

    def expected_events(self):
        if self:
            return EventContext()
        else:
            return EventContext([self.event_type()])

    def received_events(self):
        if not self:
            return EventContext()
        else:
            return EventContext([self._received_event])

    def matched_events(self):
        return self.received_events()

    def unmatched_events(self):
        return self.expected_events()


class UnknownArsdkMessage(UnknownMessage):
    from .arsdk_events import UnknownArsdkMessageEvent

    event_type = UnknownArsdkMessageEvent


class UnknownGenericMessage(UnknownMessage):
    from .arsdk_events import UnknownGenericMessageEvent

    event_type = UnknownGenericMessageEvent


class UnknownMissionMessage(UnknownMessage):
    from .arsdk_events import UnknownMissionMessageEvent

    event_type = UnknownMissionMessageEvent
