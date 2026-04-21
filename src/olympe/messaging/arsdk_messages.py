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

import arsdkparser
import ctypes
import google.protobuf.empty_pb2
import math

from builtins import str as builtin_str
from collections import OrderedDict
from collections.abc import Iterable, Mapping, MutableMapping
from enum import Enum
from itertools import starmap
from typing import Optional

import olympe_deps as od

from olympe.messaging.arsdk_enums import (
    ArsdkEnums,
    ArsdkEnum,
    list_flags,
    ArsdkProtoEnum,
)
from olympe.messaging.arsdk_events import (
    ArsdkMessageEvent,
    ArsdkProtoMessageEvent,
    ArsdkMessageArgs,
)
from olympe.messaging.arsdk_expectations import (
    ArsdkEventExpectation,
    ArsdkCommandExpectation,
    ArsdkProtoCommandExpectation,
    ArsdkWhenAnyExpectation,
    ArsdkWhenAllExpectations,
    ArsdkCheckStateExpectation,
    ArsdkCheckWaitStateExpectation,
    ExpectPolicy,
    MatchingMode,
)
from olympe.parser import json_format as protobuf_json_format
from olympe.parser.proto import ArsdkProto, ProtoFieldLabel, proto_type_to_python
from olympe.parser.proto_this import ArsdkProtoThis
from olympe.parser.xml import ArsdkXml
from olympe.utils import (
    update_mapping,
    get_mapping,
    set_mapping,
    has_mapping,
    remove_from_collection,
)

# ---------------------------------------------------------------------------
# Helper modules extracted from this file
#
# _xml_message_helpers  — init helpers for XML-based message classes
#                         (identity, type attrs, args, ctypes, tolerances)
#                         and _create_callable (shared with proto pipeline).
# _proto_message_helpers — init helpers for protobuf-based message classes.
# doc                    — RST docstring builders for both pipelines.
#
# These are imported at module level so that ArsdkMessageMeta.__new__ and
# ArsdkProtoMessageMeta.__new__ stay readable.  The helpers themselves use
# deferred imports (inside function bodies) to avoid circular dependencies
# since they in turn need symbols from this file.
# ---------------------------------------------------------------------------
from olympe.parser._xml_message_helpers import (
    _create_callable,
    _init_common_tolerances,
    _init_xml_identity,
    _init_xml_type_attrs,
    _init_xml_args,
    _init_xml_ctypes,
)
from olympe.parser._proto_message_helpers import (
    _init_proto_identity,
    _init_proto_type_attrs,
    _init_proto_args,
)
from olympe.parser.doc import (
    xml_message_docstring,
    xml_resolve_doc,
    proto_resolve_doc,
)

from typing import Optional

ARSDK_CLS_DEFAULT_ID = 0

DEFAULT_TIMEOUT = 10
TIMEOUT_BY_COMMAND = {
    "animation.Cancel": 5,
    "animation.Start_candle": 5,
    "animation.Start_dolly_slide": 5,
    "animation.Start_dronie": 5,
    "animation.Start_flip": 5,
    "animation.Start_horizontal_panorama": 5,
    "animation.Start_horizontal_reveal": 5,
    "animation.Start_parabola": 5,
    "animation.Start_spiral": 5,
    "animation.Start_vertical_reveal": 5,
    "ardrone3.Animations.Flip": 5,
    "ardrone3.Antiflickering.ElectricFrequency": 5,
    "ardrone3.Antiflickering.SetMode": 5,
    "ardrone3.Camera.OrientationV2": 20,
    "ardrone3.GPSSettings.HomeType": 20,
    "ardrone3.GPSSettings.ReturnHomeDelay": 20,
    "ardrone3.GPSSettings.ReturnHomeMinAltitude": 20,
    "ardrone3.MediaRecord.PictureV2": 20,
    "ardrone3.MediaRecord.VideoV2": 15,
    "ardrone3.MediaStreaming.VideoEnable": 3,
    "ardrone3.PictureSettings.ExpositionSelection": 20,
    "ardrone3.PictureSettings.PictureFormatSelection": 20,
    "ardrone3.Piloting.CancelMoveTo": 5,
    "ardrone3.Piloting.Emergency": 10,
    "ardrone3.Piloting.FlatTrim": 5,
    "ardrone3.Piloting.MoveBy": 20,
    "ardrone3.Piloting.MoveTo": 20,
    "ardrone3.Piloting.NavigateHome": 7,
    "ardrone3.Piloting.StartPilotedPOI": 5,
    "ardrone3.Piloting.StopPilotedPOI": 5,
    "ardrone3.PilotingSettings.CirclingAltitude": 3,
    "ardrone3.PilotingSettings.CirclingDirection": 3,
    "ardrone3.PilotingSettings.MaxAltitude": 20,
    "ardrone3.PilotingSettings.MinAltitude": 20,
    "ardrone3.PilotingSettings.PitchMode": 3,
    "common.Calibration.MagnetoCalibration": 3,
    "common.Calibration.PitotCalibration": 3,
    "common.FlightPlanSettings.ReturnHomeOnDisconnect": 20,
    "common.Mavlink.Pause": 20,
    "common.Mavlink.Start": 20,
    "common.Mavlink.Stop": 20,
    "thermal_cam.Activate": 5,
    "thermal_cam.Deactivate": 5,
    "thermal_cam.Set_sensitivity": 5,
}

FLOAT_TOLERANCE_BY_FEATURE = {
    "gimbal": (1e-7, 1e-1)  # yaw/pitch/roll angles in degrees
}
INT_TOLERANCE_BY_FEATURE = {
    # fill in if necessary
}


class ArsdkMessageMeta(type):
    # _base is a sentinel for the bootstrap trick used by both metaclasses:
    # the very first call to __new__ (with no message-specific args, triggered
    # by the "class ArsdkMessage(..., metaclass=ArsdkMessageMeta):" statement)
    # creates the base class and stores it here.  Every subsequent call creates
    # one leaf class per XML message, inheriting from _base.
    _base = None

    def __new__(mcls, *args, **kwds):
        """
        ArsdkMessage constructor
        @type obj: arsdkparser.ArMsg
        @param name_path: the full xml path of the message as a list of names
            ["<feature_name>", "<class_name>" or None, "message_name"]
        @param id_path: the full xml path of the message as a list ids
            [<feature_id>, <class_id> or None, message_id]
        @type enums: olympe.messages.ArsdkEnums
        """
        if mcls._base is None:
            cls = super().__new__(mcls, *args, **kwds)
            mcls._base = cls
            return cls

        obj, name_path, id_path, enums = args

        fullPath = tuple(filter(None, name_path))
        fullName = ".".join(fullPath)
        cls = super().__new__(mcls, builtin_str(fullName), (mcls._base,), {})

        # Phase 1: populate all class attributes from the arsdkparser descriptor.
        # The call order is constrained:
        #   1. identity  — establishes fullName, feature_name, IDs (needed by all)
        #   2. type_attrs — needs message_type from identity
        #   3. args       — needs message_type and callback_type
        #   4. ctypes     — needs args_name from args; may add list_flags and
        #                   rebuild args_default/args_default_str
        #   5. tolerances — needs fullName and feature_name
        _init_xml_identity(cls, name_path, id_path)
        _init_xml_type_attrs(cls, obj)
        _init_xml_args(cls, obj, enums, ArsdkMessages._default_arguments)
        _init_xml_ctypes(cls)  # must run after _init_xml_args: rebuilds args_default
        # for EVT LIST/MAP messages after list_flags fixup
        _init_common_tolerances(
            cls,
            TIMEOUT_BY_COMMAND,
            FLOAT_TOLERANCE_BY_FEATURE,
            INT_TOLERANCE_BY_FEATURE,
            DEFAULT_TIMEOUT,
        )

        # Expectations are wired in phase 2 (_resolve_expectations), after all
        # message classes exist so that cross-references can be resolved.
        cls._expectation = None
        cls._reverse_expectation = None

        # Phase 1 docstring: built without expectation cross-references.
        # The callable is compiled here and recompiled in phase 2 (_resolve_doc)
        # once the full docstring (including expectations) is available.
        cls.doc_todos = ""
        cls.docstring = xml_message_docstring(cls)
        cls.__doc__ = cls.docstring + "\n"
        cls.__call__ = _create_callable(cls)
        return cls

    def _is_list_item(self):
        return self.callback_type is ArsdkMessageCallbackType.LIST

    def _is_map_item(self):
        return self.callback_type is ArsdkMessageCallbackType.MAP

    def _resolve_expectations(cls, messages):
        if cls.message_type == ArsdkMessageType.CMD:
            if cls.obj.expect is not None:
                expectation_objs = cls.obj.expect.immediate + cls.obj.expect.delayed
            else:
                expectation_objs = []
            cls._expectation = ArsdkCommandExpectation(cls)
            cls._reverse_expectation = ArsdkEventExpectation(
                cls, OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))
            )
            for expectation_obj in expectation_objs:
                if not isinstance(expectation_obj, list):
                    cls._expectation.append(
                        ArsdkEventExpectation.from_arsdk(messages, expectation_obj)
                    )
                else:
                    cls._expectation.append(
                        ArsdkWhenAnyExpectation.from_arsdk(messages, expectation_obj)
                    )
        else:
            cls._expectation = ArsdkEventExpectation(
                cls, OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))
            )
            cls._reverse_expectation = ArsdkCommandExpectation(cls)

    def _resolve_doc(cls, messages):
        # Phase 2: append the expectation cross-references to the docstring
        # and recompile the __call__ callable with the complete docstring.
        xml_resolve_doc(cls, messages)


class ArsdkMessageBase:
    # module_loader.ModuleLoader._get_source() checks isinstance(item, type) and
    # issubclass(item, ArsdkMessageBase), then calls item.get_source() to retrieve
    # the source text of the message's __call__ method.  That source is assembled
    # into the synthetic olympe.messages.<feature> module that Sphinx autodoc reads
    # when building the API reference (see ModuleLoader.get_source in module_loader.py).
    #
    # _call_source_ is written by _create_callable() in _xml_message_helpers.py
    # each time a message class is compiled — for both XML and proto pipelines.
    #
    # This classmethod was removed during the refactoring that moved _create_callable
    # to a shared helper module; it is restored here because module_loader.py still
    # expects it on every ArsdkMessageBase subclass.
    @classmethod
    def get_source(cls):
        return cls._call_source_


class ArsdkMessage(ArsdkMessageBase, metaclass=ArsdkMessageMeta):
    def __init__(self):
        self._reset_state()

    @classmethod
    def new(cls):
        self = cls.__new__(cls, cls.__name__, ArsdkMessage, {})
        self.__init__()
        return self

    def copy(self):
        other = self.new()
        other._last_event = self._last_event
        other._state = self._state.copy()
        return other

    @classmethod
    def _argsmap_from_args(cls, *args, **kwds):
        # Message({"a": 1, "b": 2}) is ambiguous: either "a" and "b" are the
        # names of this message's own arguments (the call means a=1, b=2),
        # or only one positional value was passed to this call, and that
        # value happens to be the dict {"a": 1, "b": 2} itself.
        # We treat it as a=1, b=2 only when "a" and "b" are both argument
        # names of this message. Otherwise, the whole dict is treated as the
        # value of that single positional argument.
        if (
            not kwds
            and len(args) == 1
            and isinstance(args[0], MutableMapping)
            and all(k in cls.args_name for k in args[0].keys())
        ):
            args = args[0]
        else:
            args = OrderedDict(zip(map(lambda a: a, cls.args_name), args))
            args_set = set(args.keys())
            kwds_set = set(kwds.keys())
            if not args_set.isdisjoint(kwds_set):
                raise RuntimeError(
                    "Message `{}` got multiple values for argument(s) {}".format(
                        cls.fullName, list(args_set & kwds_set)
                    )
                )
            args.update(kwds)

        # filter out None value
        args = OrderedDict([(k, v) for k, v in args.items() if v is not None])

        # enum conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (
                    (name, cls.args_enum[name][value])
                    if (name in cls.args_enum and isinstance(value, (bytes, str)))
                    else (name, value)
                ),
                args.items(),
            )
        )

        # bitfield conversion
        for name, value in args.copy().items():
            if name in cls.args_bitfield:
                try:
                    args[name] = cls.args_bitfield[name](value)
                except ValueError:
                    # Bits values outside the bitfield mask are unspecified
                    pass

        args = OrderedDict(starmap(lambda k, v: (k, v), args.items()))
        return args

    @classmethod
    def _expectation_from_args(cls, *args, **kwds):
        expected_args = cls._argsmap_from_args(*args, **kwds)
        return ArsdkEventExpectation(cls, expected_args)

    def _event_from_args(self, *args, **kwds):
        args = self._argsmap_from_args(*args, **kwds)
        return ArsdkMessageEvent(self, args)

    @classmethod
    def _event_type(cls):
        return ArsdkMessageEvent

    def last_event(self, key=None):
        if self._last_event is None:
            return None
        if key is None:
            return self._last_event
        else:
            return self._last_event[key]

    def last_events(self, key=None):
        if self._last_event is None:
            return
        elif self.callback_type == ArsdkMessageCallbackType.STANDARD:
            yield self._last_event
        elif self.callback_type == ArsdkMessageCallbackType.MAP:
            if key is None:
                for event in self._last_event.values():
                    if event is not None:
                        yield event
            else:
                if self._last_event[key] is not None:
                    yield self._last_event[key]

    def _set_last_event(self, event):
        if event.message.id != self.id:
            raise RuntimeError(
                "Cannot set message {} last event to {}".format(
                    self.fullName, event.message.fullName
                )
            )

        event_list_flags = event.args.get("list_flags") or []

        if self.callback_type == ArsdkMessageCallbackType.STANDARD:
            self._last_event = event
            self._state = event.args
        elif self.callback_type == ArsdkMessageCallbackType.MAP:
            if self._last_event is None:
                self._last_event = OrderedDict()
            key = event.args[self.key_name]
            if not event_list_flags or event_list_flags == [list_flags.Last]:
                self._state[key] = event.args
            if list_flags.First in event_list_flags:
                self._state = OrderedDict()
                self._state[key] = event.args
            if list_flags.Empty in event_list_flags:
                self._state = OrderedDict()
            if list_flags.Remove in event_list_flags:
                # remove the received element from the current map
                if key in self._state:
                    self._state.pop(key)
            else:
                self._last_event[key] = event
        elif self.callback_type == ArsdkMessageCallbackType.LIST:
            if not event_list_flags or event.args["list_flags"] == [list_flags.Last]:
                # append to the current list
                insert_pos = next(reversed(self._state), -1) + 1
                self._state[insert_pos] = event.args
            if list_flags.First in event_list_flags:
                self._state = OrderedDict()
                self._state[0] = event.args
            if list_flags.Empty in event_list_flags:
                self._state = OrderedDict()
            if list_flags.Remove in event_list_flags:
                # remove the received element from the current list
                for k, v in self._state:
                    for argname, argval in v.items():
                        if argname == "list_flags":
                            continue
                        if argval != event.args[argname]:
                            break
                    else:
                        # if all arguments have matched except "list_flags"
                        self._state.pop(k, None)
            else:
                self._last_event = event

    def state(self):
        if self._last_event is None:
            raise ValueError(f"{self.fullName} state is uninitialized")
        return self._state

    def _reset_state(self):
        self._last_event = None
        self._state = OrderedDict()

    def _expect_args(cls, message_type, *args, **kwds):
        default_timeout = cls.timeout if message_type is ArsdkMessageType.CMD else None
        default_float_tol = cls.float_tol
        default_int_tol = cls.int_tol
        timeout = kwds.pop("_timeout", default_timeout)
        float_tol = kwds.pop("_float_tol", default_float_tol)
        int_tol = kwds.pop("_int_tol", default_int_tol)
        matching_mode = kwds.pop("_matching_mode", MatchingMode.pattern)
        no_expect = kwds.pop("_no_expect", False)
        send_command = kwds.pop("_send_command", True)
        policy = kwds.pop("_policy", "check_wait")
        if isinstance(policy, (bytes, str)):
            policy = ExpectPolicy[policy]
        else:
            raise ValueError("policy argument must be a string")

        if not send_command and no_expect:
            raise ValueError(
                "Invalid argument combination "
                + "Message._expect(send_command=False, no_expect=True)"
            )

        if "_policy" in kwds and message_type is ArsdkMessageType.CMD:
            # A policy cannot be sent with an arsdk command. This is only
            # valuable for events.
            raise ValueError(
                f"Command cannot accept any policy (policy: '{kwds.get('_policy')}' rejected)"
            )

        args = cls._argsmap_from_args(*args, **kwds)
        # enum conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (
                    (name, cls.args_enum[name][value])
                    if (name in cls.args_enum and isinstance(value, (bytes, str)))
                    else (name, value)
                ),
                args.items(),
            )
        )

        # bitfield conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (
                    (name, cls.args_bitfield[name](value))
                    if name in cls.args_bitfield
                    else (name, value)
                ),
                args.items(),
            )
        )

        # int -> float conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (
                    (name, float(value))
                    if isinstance(value, int) and cls.args_type[name] is float
                    else (name, value)
                ),
                args.items(),
            )
        )
        return (
            args,
            send_command,
            policy,
            float_tol,
            int_tol,
            matching_mode,
            no_expect,
            timeout,
        )

    def _expect(cls, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with the provided
        command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        """
        (
            args,
            send_command,
            policy,
            float_tol,
            int_tol,
            matching_mode,
            no_expect,
            timeout,
        ) = cls._expect_args(cls.message_type, *args, **kwds)
        for arg_name in args:
            if arg_name not in cls.args_name:
                raise ValueError(
                    f"'{cls.fullName}' message has no such '{arg_name}' parameter"
                )
        if policy != ExpectPolicy.check:
            if not send_command and cls.message_type == ArsdkMessageType.CMD:
                expectations = ArsdkWhenAllExpectations(
                    cls._expectation.copy().expectations
                )
            else:
                expectations = cls._expectation.copy()
                if cls.message_type == ArsdkMessageType.CMD:
                    expectations.no_expect(no_expect)

            expectations._fill_default_arguments(cls, args)

            if (
                policy == ExpectPolicy.check_wait
                and cls.message_type is ArsdkMessageType.EVT
            ):
                check_expectation = ArsdkCheckStateExpectation(cls, args)
                expectations = ArsdkCheckWaitStateExpectation(
                    check_expectation, expectations
                )
            expectations.set_timeout(timeout)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(cls, args)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            expectations._fill_default_arguments(cls, args)
            return expectations

    def _reverse_expect(cls, *args, **kwds):
        if cls.message_type is ArsdkMessageType.CMD:
            message_type = ArsdkMessageType.EVT
        else:
            message_type = ArsdkMessageType.CMD
        (
            args,
            send_command,
            policy,
            float_tol,
            int_tol,
            matching_mode,
            no_expect,
            timeout,
        ) = cls._expect_args(message_type, *args, **kwds)
        assert send_command, "Reverse expectation send_command must always be True"
        no_expect = True  # implied when using reverse expectation
        if policy != ExpectPolicy.check:
            expectations = cls._reverse_expectation.copy()
            if cls.message_type == ArsdkMessageType.EVT:
                expectations.no_expect(no_expect)

            expectations._fill_default_arguments(cls, args)

            if (
                policy == ExpectPolicy.check_wait
                and cls.message_type is ArsdkMessageType.CMD
            ):
                check_expectation = ArsdkCheckStateExpectation(cls, args)
                expectations = ArsdkCheckWaitStateExpectation(
                    check_expectation, expectations
                )
            expectations.set_timeout(timeout)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(cls, args)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations._fill_default_arguments(cls, args)
            expectations.set_matching_mode(matching_mode)
            return expectations

    def as_event(cls, *args, **kwds):
        if cls.message_type is ArsdkMessageType.EVT:
            return cls._expect(*args, **kwds)
        else:
            return cls._reverse_expect(*args, **kwds)

    def as_command(cls, *args, **kwds):
        if cls.message_type is ArsdkMessageType.CMD:
            return cls._expect(*args, **kwds)
        else:
            return cls._reverse_expect(*args, **kwds)

    @classmethod
    def default_args(cls):
        args = {}
        for name in cls.args_name:
            if name in cls.args_enum:
                args[name] = next(iter(cls.args_enum[name]))
            elif name == "list_flags":
                args[name] = 0
            else:
                type_ = cls.args_type[name]
                if type_ is not None:
                    args[name] = type_()
                else:
                    args[name] = None
        if cls.callback_type in (
            ArsdkMessageCallbackType.MAP,
            ArsdkMessageCallbackType.LIST,
        ):
            if "list_flags" not in args:
                args[name] = list_flags._bitfield_type_()
        return args

    @classmethod
    def _encode_args(cls, args):
        """
        Encode python message arguments to ctypes. This also perform the necessary enum, bitfield
        and unicode conversions.
        """
        if len(args) != len(cls.args_type):
            raise TypeError(
                "{}() takes exactly {} arguments ({} given)".format(
                    cls.fullName, len(cls.obj.args), len(args)
                )
            )

        encoded_args = tuple(args.values())
        # enum conversion (string --> enum type)
        encoded_args = list(
            starmap(
                lambda name, value: (
                    cls.args_enum[name][value]
                    if (name in cls.args_enum and isinstance(value, (bytes, str)))
                    else value
                ),
                zip(cls.args_name, encoded_args),
            )
        )

        # enum conversion (enum type --> integer)
        encoded_args = list(
            starmap(
                lambda name, value: (
                    value._value_
                    if (name in cls.args_enum) and isinstance(value, ArsdkEnum)
                    else value
                ),
                zip(cls.args_name, encoded_args),
            )
        )

        # bitfield conversion ([string, enum list, bitfield] --> integer)
        encoded_args = list(
            starmap(
                lambda name, value: (
                    cls.args_bitfield[name](value).to_int()
                    if name in cls.args_bitfield
                    else value
                ),
                zip(cls.args_name, encoded_args),
            )
        )

        # unicode -> str utf-8 encoding
        encoded_args = list(
            map(lambda a: a.encode("utf-8") if isinstance(a, str) else a, encoded_args)
        )

        # python -> ctypes -> struct_arsdk_value argv conversion
        encode_args_len = len(cls.arsdk_type_args)
        argv = (od.struct_arsdk_value * encode_args_len)()
        for i, arg, sdktype, value_attr, ctype in zip(
            range(encode_args_len),
            encoded_args,
            cls.arsdk_type_args,
            cls.arsdk_value_attr,
            cls.encode_ctypes_args,
        ):
            argv[i].type = sdktype
            setattr(argv[i].data, value_attr, ctype(arg))
        return argv

    @classmethod
    def _decode_args(cls, message_buffer):
        """
        Decode a ctypes message buffer into a list of python typed arguments. This also perform the
        necessary enum, bitfield and unicode conversions.
        """
        od.arsdk_cmd_dec.argtypes = (
            od.arsdk_cmd_dec.argtypes[:2] + cls.decoded_args_type
        )

        res = od.arsdk_cmd_dec(message_buffer, cls.arsdk_desc, *cls.decoded_args)

        decoded_args = cls.decoded_args[:]
        for i, (name, arg) in enumerate(zip(cls.args_name, decoded_args)):
            # ctypes -> python type conversion (exception: arsdk_binary -> c_char array)
            if not isinstance(arg.contents, od.struct_arsdk_binary):
                decoded_args[i] = arg = arg.contents.value
            else:
                decoded_args[i] = arg = (ctypes.c_char * arg.contents.len).from_address(
                    arg.contents.cdata
                )
            # bytes utf-8 -> str conversion
            if isinstance(arg, bytes):
                decoded_args[i] = arg = str(arg, "utf-8")
            # ctypes c_char array -> bytes
            elif isinstance(arg, ctypes.Array):
                decoded_args[i] = arg = bytes(arg)

            if name in cls.args_enum:
                # enum conversion
                decoded_args[i] = arg = cls.args_enum[name](arg)
            elif name in cls.args_bitfield:
                # bitfield conversion
                try:
                    decoded_args[i] = arg = cls.args_bitfield[name](arg)
                except ValueError:
                    # Bits values outside the bitfield mask are unspecified
                    pass
        return (res, decoded_args)


class ArsdkMessageType(Enum):
    CMD, EVT = range(2)

    @classmethod
    def from_arsdk(cls, value):
        return {arsdkparser.ArCmd: cls.CMD, arsdkparser.ArEvt: cls.EVT}[value]


class ArsdkMessageCallbackType(Enum):
    STANDARD, LIST, MAP = range(3)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmdListType.NONE: cls.STANDARD,
            arsdkparser.ArCmdListType.LIST: cls.LIST,
            arsdkparser.ArCmdListType.MAP: cls.MAP,
        }[value]


class ArsdkMessageBufferType(Enum):
    NON_ACK, ACK, HIGH_PRIO = range(3)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmdBufferType.NON_ACK: cls.NON_ACK,
            arsdkparser.ArCmdBufferType.ACK: cls.ACK,
            arsdkparser.ArCmdBufferType.HIGH_PRIO: cls.HIGH_PRIO,
        }[value]


class ArsdkMessages:
    """
    A python class to represent arsdk messages commands and events alike.
    """

    _store = {}

    @classmethod
    def get(cls, root):
        ret = cls._store.get(root)
        if ret is None:
            ret = ArsdkMessages(root)
        return ret

    _default_arguments = {
        "ardrone3.GPSSettings.SendControllerGPS": dict(
            horizontalAccuracy=1.0, verticalAccuracy=1.0
        ),
        "ardrone3.NetworkSettings.WifiSelection": dict(channel=0),
        "ardrone3.PictureSettings.VideoAutorecordSelection": dict(mass_storage_id=0),
        "common.Mavlink.Start": dict(type="'flightPlan'"),
        "gimbal.Reset_orientation": dict(gimbal_id=0),
        "gimbal.Start_offsets_update": dict(gimbal_id=0),
        "gimbal.Stop_offsets_update": dict(gimbal_id=0),
    }

    def __init__(self, root):
        """
        ArsdkMessages constructor
        @type arsdk_enums: olympe.messaging.arsdk_enums
        """

        self.__class__._store[root] = self
        self._root = root
        self._proto = ArsdkProto.get(root)
        self.enums = ArsdkEnums.get(root)
        self._ctx = ArsdkXml.get(root).ctx
        self.by_id = OrderedDict()
        self.by_id_name = OrderedDict()
        self.by_prefix = OrderedDict()
        self.by_feature = OrderedDict()
        self.service_messages = OrderedDict()
        self._feature_name_by_id = OrderedDict()
        self.nested_proto_resolved = OrderedDict()

        # Three-phase initialisation:
        #   1. _populate_messages — create all message classes (phase 1 init)
        #   2. _resolve_expectations — wire cross-references between messages
        #   3. _resolve_doc — compile final docstrings and __call__ methods
        # Phases 2 and 3 are deferred because they depend on all message
        # classes being present first (e.g. CMD expectation refers to an EVT).
        # Proto phases 2 and 3 are triggered later, per feature, when the
        # feature module is loaded (see module_loader.py).
        self._populate_messages()
        self._resolve_expectations()
        self._resolve_doc()

    def _populate_messages(self):
        for featureId in sorted(self._ctx.featuresById.keys()):
            featureObj = self._ctx.featuresById[featureId]
            if featureObj.classes and len(featureObj.classes) != 0:
                for classId in sorted(featureObj.classesById.keys()):
                    classObj = featureObj.classesById[classId]
                    for msgId in sorted(classObj.cmdsById.keys()):
                        msgObj = classObj.cmdsById[msgId]
                        self._add_arsdk_message(
                            msgObj,
                            [featureObj.name, classObj.name, msgObj.name],
                            [featureId, classId, msgId],
                        )

            elif len(featureObj.getMsgs()) != 0:
                for msgId in sorted(featureObj.getMsgsById().keys()):
                    msgObj = featureObj.getMsgsById()[msgId]
                    self._add_arsdk_message(
                        msgObj,
                        [featureObj.name, None, msgObj.name],
                        [featureId, None, msgId],
                    )

        for feature_name, feature in self._proto.features.items():
            for message in feature.messages:
                self._add_arsdk_proto_message(feature, message)

    def _add_arsdk_message(self, msgObj, name_path, id_path):
        message = ArsdkMessageMeta.__new__(
            ArsdkMessageMeta, msgObj, name_path, id_path, self.enums
        )
        self.by_id[message.id] = message
        self.by_id_name[message.id_name] = message
        feature_id = (message.id & 0xFF000000) >> 24
        class_id = (message.id & 0x00FF0000) >> 16
        self._feature_name_by_id[(feature_id, class_id)] = (
            message.feature_name,
            message.class_name,
        )
        if message.prefix not in self.by_prefix:
            self.by_prefix[message.prefix] = OrderedDict()
        self.by_prefix[message.prefix][message.name] = message
        if message.feature_name not in self.by_feature:
            self.by_feature[message.feature_name] = OrderedDict()
        if message.class_name is not None:
            if message.class_name not in self.by_feature[message.feature_name]:
                self.by_feature[message.feature_name][
                    message.class_name
                ] = OrderedDict()
            self.by_feature[message.feature_name][message.class_name][
                message.name
            ] = message
        else:
            self.by_feature[message.feature_name][message.name] = message

    def _do_add_arsdk_proto_message(self, name_path, message, message_desc):
        context = self.by_feature
        for part in name_path[:-1]:
            if part not in context:
                context[part] = OrderedDict()
            context = context[part]
        context[name_path[-1]] = message

    def _add_arsdk_proto_message(self, feature, message_desc):
        path = message_desc.path.split(".")
        feature_path = message_desc.feature_name.split(".")
        context = self.by_feature
        for part in feature_path:
            if part not in context:
                context[part] = OrderedDict()
            context = context[part]
        if path[-1] in ("Command", "Event"):
            if not has_mapping(self.by_feature, path):
                set_mapping(self.by_feature, path, OrderedDict())
            return None
        message = None
        for service in feature.services:
            for svc_message_desc in service.messages:
                name_path = feature_path + [
                    svc_message_desc.service_type,
                    svc_message_desc.name,
                ]
                same_scope = True
                target_name_path = name_path
                while len(target_name_path) > 1:
                    if target_name_path == path:
                        if not same_scope:
                            message = ArsdkProtoMessageMeta.__new__(
                                ArsdkProtoMessageMeta,
                                self._root,
                                path,
                                service,
                                svc_message_desc,
                                message_desc,
                            )
                            self._do_add_arsdk_proto_message(
                                target_name_path, message, message_desc
                            )
                            # fixup name_path with service field_name in Pascal Case
                            name_path[-1] = (
                                svc_message_desc.field_name.replace("_", " ")
                                .title()
                                .replace(" ", "")
                            )
                        message = ArsdkProtoMessageMeta.__new__(
                            ArsdkProtoMessageMeta,
                            self._root,
                            name_path,
                            service,
                            svc_message_desc,
                            message_desc,
                        )
                        self._do_add_arsdk_proto_message(
                            name_path, message, message_desc
                        )
                        self.service_messages[(service.id, message.number)] = message
                        break
                    target_name_path = target_name_path[:-2] + [target_name_path[-1]]
                    same_scope = False

        if message is None:
            message = ArsdkProtoMessageMeta.__new__(
                ArsdkProtoMessageMeta,
                self._root,
                path,
                None,
                None,
                message_desc,
            )
            self._do_add_arsdk_proto_message(path, message, message_desc)

    def walk(self):
        for prefix, messages in self.by_prefix.items():
            for message_name, message in messages.items():
                yield prefix, message_name, message

    def walk_enums(self):
        for prefix, messages in self.by_prefix.items():
            for message_name, message in messages.items():
                for argname, enum in message.args_enum.items():
                    for enum_label, enum_value in enum.__members__.items():
                        yield prefix, message_name, argname, enum_label, enum_value

    def walk_args(self):
        for prefix, messages in self.by_prefix.items():
            for message_name, message in messages.items():
                for argname in message.args_pos.keys():
                    yield prefix, message_name, message, argname

    def unknown_message_info(self, message_id):
        feature_id = (message_id & 0xFF000000) >> 24
        class_id = (message_id & 0x00FF0000) >> 16
        msg_id = message_id & 0x0000FFFF
        feature_name, class_name = self._feature_name_by_id.get(
            (feature_id, class_id), (None, None)
        )
        if feature_name is None:
            return (None, None, message_id)
        else:
            return (feature_name, class_name, msg_id)

    def _resolve_expectations(self):
        # Phase 2 for XML messages: called after all message classes exist.
        for message in self.by_id.values():
            message._resolve_expectations(self)

    def _resolve_proto_expectations(self, module, feature_name):
        self._do_resolve_proto_expectations(
            module, feature_name, self.by_feature[feature_name]
        )

    def _do_resolve_proto_expectations(self, module, path, context):
        for name, message in context.items():
            if isinstance(message, ArsdkProtoMessageMeta):
                message._resolve_expectations(self, module)
            self._do_resolve_proto_expectations(module, f"{path}.{name}", message)

    def _resolve_proto_nested_messages(self, module, feature_name):
        self._do_resolve_nested_messages(
            module, feature_name, self.by_feature[feature_name]
        )
        self.nested_proto_resolved[feature_name] = True

    def _do_resolve_nested_messages(self, module, path, context):
        if isinstance(context, ArsdkProtoMessageMeta):
            cls_proto = context.message_proto.DESCRIPTOR
            for field in cls_proto.fields:
                message_type = field.message_type
                if message_type is None:
                    continue
                for arg in context.args_name:
                    if field.name != arg:
                        continue
                    package = message_type.file.package
                    if package not in self._proto.features_package_map:
                        continue
                    feature = self._proto.features_package_map[package]
                    message_full_name = (
                        feature + "." + message_type.full_name[len(package) + 1 :]
                    )
                    message_path = message_full_name.split(".")
                    message = get_mapping(self.by_feature, message_path)
                    if not message_type.GetOptions().map_entry:
                        context.args_message[field.name] = message
                    else:
                        # Not actually a message but a map<key, value> protobuf type
                        context.args_map[field.name] = message
                    break
        for name, message in context.items():
            self._do_resolve_nested_messages(module, f"{path}.{name}", message)

    def _resolve_doc(self):
        # Phase 2 for XML messages: append expectation cross-references and
        # recompile the __call__ callable with the complete docstring.
        for message in self.by_id.values():
            message._resolve_doc(self)

    def _resolve_proto_doc(self, module, feature_name):
        self._do_resolve_proto_doc(module, feature_name, self.by_feature[feature_name])

    def _do_resolve_proto_doc(self, module, path, context):
        for name, message in context.items():
            if isinstance(message, ArsdkProtoMessageMeta):
                message._resolve_doc(self, module)
            self._do_resolve_proto_doc(module, f"{path}.{name}", message)


class ProtoNestedMixin:
    def __getattr__(cls, key):
        if key == "nested_messages":
            raise AttributeError(f"'{cls}' object has no attribute '{key}'")
        elif key in cls.nested_messages:
            return cls[key]
        elif hasattr(super(), "__getattr__"):
            return super().__getattr__(key)
        else:
            raise AttributeError(f"'{cls}' object has no attribute '{key}'")

    def __getitem__(cls, key):
        return cls.nested_messages[key]

    def __setitem__(cls, key, item):
        cls.nested_messages[key] = item

    def __delitem__(cls, key):
        del cls.nested_messages[key]

    def __iter__(cls):
        return iter(cls.nested_messages)

    def __len__(cls):
        return len(cls.nested_messages)


class ArsdkProtoMessageMeta(type, ProtoNestedMixin):
    # Same bootstrap sentinel as ArsdkMessageMeta._base: the first call to
    # __new__ (from "class ArsdkProtoMessage(...):") stores the base class here;
    # all subsequent calls create one leaf class per proto message.
    _base = None

    def __new__(mcls, *args, **kwds):
        """
        ArsdkMessage constructor
        @param name_path: the full path of the message as a list of names
        @type name_path: Iterable[str]
        @type service: olympe.parser.proto.ArsdkProtoService
        @type message_desc: olympe.parser.proto.ArsdkProtoMessage
        @type doc_desc: olympe.parser.proto.ArsdkProtoMessageDoc
        """
        if mcls._base is None:
            cls = type.__new__(mcls, *args, **kwds)
            mcls._base = cls
            return cls

        root, name_path, service, svc_message_desc, message_desc = args
        # Both the feature-specific proto and the shared olympe proto are needed
        # because enum types may be defined in the olympe package and referenced
        # from a feature-specific .proto file.  _init_proto_args resolves enums
        # by walking the package hierarchy in feature_proto first, then falling
        # back to olympe_proto.
        olympe_proto = ArsdkProto.get("olympe")
        olympe_enums = ArsdkEnums.get("olympe")
        feature_proto = ArsdkProto.get(root)
        feature_enums = ArsdkEnums.get(root)

        fullName = ".".join(name_path)
        cls = type.__new__(mcls, builtin_str(fullName), (mcls._base,), {})

        # Phase 1: identity → type attrs → args.  Same ordering constraint as
        # the XML pipeline; each step depends on the previous one.
        service_type = _init_proto_identity(
            cls, root, name_path, service, svc_message_desc, message_desc
        )
        _init_proto_type_attrs(cls, service_type)
        _init_proto_args(cls, feature_proto, olympe_proto, feature_enums, olympe_enums)

        # Phase 1 docstring is empty; it will be populated in phase 2
        # (_resolve_doc / proto_resolve_doc) once all messages exist.
        cls.docstring = ""
        cls.__doc__ = ""
        return cls

    @property
    def id(cls):
        if cls.service is None:
            return None
        elif cls.recipient_id is None:
            return (cls.service.id, cls.number)
        else:
            return (cls.service.id, cls.number, cls.recipient_id)

    @property
    def recipient_id(cls):
        return cls._recipient_id

    @recipient_id.setter
    def recipient_id(cls, id_):
        cls._recipient_id = id_

    def _resolve_expectations(cls, messages, module):
        if cls.svc_message_desc is None:
            return
        expectation = cls._parse_expectation(
            cls.svc_message_desc.on_success or "None", module
        )
        if cls.message_type == ArsdkMessageType.CMD:
            cls._expectation = ArsdkProtoCommandExpectation(
                cls, expectation=expectation
            )

    def _parse_expectation(cls, expectation_str, module):
        return eval(expectation_str, module.__dict__, dict(this=ArsdkProtoThis()))

    def items(cls):
        return cls.nested_messages.items()

    def _create_call(cls):
        """
        Compile the __call__ method for this proto message.

        This is a thin wrapper around _create_callable so that proto_resolve_doc
        (in doc.py) can call cls._create_call() without importing
        _create_callable directly.  It must be called AFTER cls.docstring is set
        (i.e. at the end of phase 2 / proto_resolve_doc) so that the compiled
        function embeds the correct docstring.
        """
        cls.__call__ = _create_callable(cls)


class ArsdkProtoMessage(
    ArsdkMessageBase, ProtoNestedMixin, metaclass=ArsdkProtoMessageMeta
):
    def __init__(self):
        self._reset_state()
        self._proto = ArsdkProto.get(self.root)
        self._messages = ArsdkMessages.get(self.root)
        self.nested_messages = OrderedDict()
        self.args_message = OrderedDict()
        self.args_map = OrderedDict()
        self._nested_resolved = False
        self._resolve_nested_messages()

    @classmethod
    def new(cls):
        self = cls.__new__(cls, cls.__name__, ArsdkProtoMessage, {})
        self.__init__()
        return self

    def copy(self):
        other = self.new()
        other._last_event = self._last_event
        other._state = self._state.copy()
        other._resolve_nested_messages()
        return other

    @property
    def id(self):
        return self.__class__.id

    @property
    def recipient_id(self):
        return self.__class__.recipient_id

    def state(self):
        if self._last_event is None:
            raise ValueError(f"{self.fullName} state is uninitialized")
        return self._state

    def _reset_state(self):
        self._last_event = None
        self._state = OrderedDict()

    def __call__(self, *args, **kwds):
        return self._expect(*args, **kwds)

    def _validate_args(self, args):
        assert isinstance(args, Mapping)
        self._resolve_nested_messages()
        args = self._map_enum_to_int(args)
        args = self._map_float_specials(args)
        args = remove_from_collection(args, callable)
        for name, field in self.message_proto.DESCRIPTOR.fields_by_name.items():
            if name not in args:
                continue
            elif name in self.args_message:
                if isinstance(args[name], Mapping):
                    args[name] = self.args_message[name]._validate_args(args[name])
                elif field.label == ProtoFieldLabel.Repeated._value_:
                    assert isinstance(args[name], Iterable)
                    args[name] = list(
                        map(self.args_message[name]._validate_args, args[name])
                    )
            elif field.message_type is not None and (
                isinstance(args[name], Mapping)
                and field.message_type.file.package == "google.protobuf"
                and field.message_type.fields
                and list(field.message_type.fields)[0].full_name.endswith("Value.value")
            ):
                args[name] = args[name].get("value")
        protobuf_json_format.ParseDict(args, self.message_proto())
        return args

    def _map_google_protobuf(self, args, unwrap=False):
        self._resolve_nested_messages()
        for name, field in self.message_proto.DESCRIPTOR.fields_by_name.items():
            if name not in args:
                continue
            elif name in self.args_message:
                if isinstance(args[name], Mapping):
                    args[name] = self.args_message[name]._map_google_protobuf(
                        args[name], unwrap=unwrap
                    )
                elif field.label == ProtoFieldLabel.Repeated._value_:
                    assert isinstance(args[name], Iterable)
                    args[name] = list(
                        map(
                            lambda a: self.args_message[name]._map_google_protobuf(
                                a, unwrap=unwrap
                            ),
                            args[name],
                        )
                    )
            elif field.message_type is not None and (
                field.message_type.file.package == "google.protobuf"
                and field.message_type.fields
                and list(field.message_type.fields)[0].full_name.endswith("Value.value")
            ):
                if not unwrap:
                    if not isinstance(args[name], Mapping):
                        args[name] = dict(value=args[name])
                elif isinstance(args[name], Mapping):
                    args[name] = args[name].get("value")
        return args

    def _expect_args(self, message_type, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with
        the provided command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual
            command expectation (defaults to False)
        """
        default_timeout = (
            self.timeout if self.message_type is ArsdkMessageType.CMD else None
        )
        default_float_tol = self.float_tol
        default_int_tol = self.int_tol
        timeout = kwds.pop("_timeout", default_timeout)
        float_tol = kwds.pop("_float_tol", default_float_tol)
        int_tol = kwds.pop("_int_tol", default_int_tol)
        matching_mode = kwds.pop("_matching_mode", MatchingMode.pattern)
        if isinstance(matching_mode, str):
            matching_mode = MatchingMode[matching_mode]
        elif not isinstance(matching_mode, MatchingMode):
            raise RuntimeError(
                "_matching_mode argument must be of type `str` or `MatchingMode`"
            )
        no_expect = kwds.pop("_no_expect", False)
        if "_policy" in kwds and message_type is ArsdkMessageType.CMD:
            # A policy cannot be sent with an arsdk command. This is only
            # valuable for events.
            raise ValueError(
                f"Command cannot accept any policy (policy: '{kwds.get('_policy')}' "
                "rejected)"
            )
        policy = kwds.pop("_policy", "check_wait")
        if isinstance(policy, str):
            policy = ExpectPolicy[policy]
        else:
            raise RuntimeError("policy argument must be a string")
        args = self._argsmap_from_args(*args, **kwds)
        unknown_args = tuple(filter(lambda a: a not in self.args_name, args.keys()))
        if unknown_args:
            raise ValueError(
                f"Unknown {unknown_args} parameter(s) passed to {self.fullName}"
            )
        # filter out None value
        args = remove_from_collection(args, lambda a: a is None)
        self._validate_args(args)
        args = self._map_enum_type(args)
        args = self._map_message_type(args)
        return args, policy, float_tol, int_tol, matching_mode, no_expect, timeout

    def _expect_message_args(self, *args, **kwds):
        args = self._argsmap_from_args(*args, **kwds)
        unknown_args = tuple(filter(lambda a: a not in self.args_name, args.keys()))
        if unknown_args:
            raise ValueError(
                f"Unknown {unknown_args} parameter(s) passed to {self.fullName}"
            )
        self._validate_args(args)
        args = self._map_enum_type(args)
        args = self._map_message_type(args)
        return args

    def _expect(self, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with
        the provided command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual
        command expectation (defaults to False)
        """

        if self.service is None:
            # Non-service messages are just equivalent to mapping object
            # with protobuf format validation
            args = self._expect_message_args(*args, **kwds)
            return self.dict_type(**args)

        args, policy, float_tol, int_tol, matching_mode, no_expect, timeout = (
            self._expect_args(self.message_type, *args, **kwds)
        )

        if policy != ExpectPolicy.check:
            expectations = self._expectation.copy()
            if self.message_type == ArsdkMessageType.CMD:
                expectations.no_expect(no_expect)

            expectations._fill_default_arguments(self, args)
            args_to_validate = []
            if self.message_type == ArsdkMessageType.CMD:
                for expectation in expectations:
                    if (
                        hasattr(expectation, "expected_args")
                        and (hasattr(expectation, "expected_message"))
                        and (not no_expect)
                    ):
                        args_to_validate.append(
                            (
                                expectation.expected_args,
                                expectation.expected_message,
                            )
                        )
            else:
                unknown_args = tuple(
                    filter(lambda a: a not in self.args_name, args.keys())
                )
                if unknown_args:
                    raise ValueError(
                        f"Unknown {unknown_args} parameter(s) passed to {self.fullName}"
                    )
                args = expectations.expected_args
                args_to_validate.append((args, self))

            # Use protobuf_json_format to validate protobuf message format
            # filter out lambda ArdkProtoThis lambdas before validation
            for expected_args, message in args_to_validate:
                expected_args = message._validate_args(expected_args)

            if (
                policy == ExpectPolicy.check_wait
                and self.message_type is ArsdkMessageType.EVT
            ):
                check_expectation = ArsdkCheckStateExpectation(self, args)
                expectations = ArsdkCheckWaitStateExpectation(
                    check_expectation, expectations
                )
            expectations.set_timeout(timeout)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(self, args)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            expectations._fill_default_arguments(self, args)
            return expectations

    def _reverse_expect(self, *args, **kwds):

        if self.service is None:
            # Non-service messages are just equivalent to mapping object
            # with protobuf format validation
            args = self._expect_message_args(*args, **kwds)
            return self.dict_type(**args)

        if self.message_type is ArsdkMessageType.CMD:
            message_type = ArsdkMessageType.EVT
        else:
            message_type = ArsdkMessageType.CMD
        args, policy, float_tol, int_tol, matching_mode, no_expect, timeout = (
            self._expect_args(message_type, *args, **kwds)
        )

        if policy != ExpectPolicy.check:
            expectations = self._reverse_expectation.copy()
            if self.message_type == ArsdkMessageType.EVT:
                expectations.no_expect(no_expect)

            expectations._fill_default_arguments(self, args)
            args_to_validate = []
            if self.message_type == ArsdkMessageType.EVT:
                for expectation in expectations:
                    if (
                        hasattr(expectation, "expected_args")
                        and (hasattr(expectation, "expected_message"))
                        and (not no_expect)
                    ):
                        args_to_validate.append(
                            (expectation.expected_args, expectation.expected_message)
                        )
            else:
                args = expectations.expected_args
                args_to_validate.append((args, self))

            # Use protobuf_json_format to validate protobuf message format
            # filter out lambda ArdkProtoThis lambdas before validation
            for expected_args, message in args_to_validate:
                expected_args = self._validate_args(expected_args)

            if (
                policy == ExpectPolicy.check_wait
                and self.message_type is ArsdkMessageType.CMD
            ):
                check_expectation = ArsdkCheckStateExpectation(self, args)
                expectations = ArsdkCheckWaitStateExpectation(
                    check_expectation, expectations
                )
            expectations.set_timeout(timeout)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(self, args)
            expectations.set_float_tol(float_tol)
            expectations.set_int_tol(int_tol)
            expectations.set_matching_mode(matching_mode)
            expectations._fill_default_arguments(self, args)
            return expectations

    def as_event(cls, *args, **kwds):
        if cls.message_type is ArsdkMessageType.EVT:
            return cls._expect(*args, **kwds)
        else:
            return cls._reverse_expect(*args, **kwds)

    def as_command(cls, *args, **kwds):
        if cls.message_type is ArsdkMessageType.CMD:
            return cls._expect(*args, **kwds)
        else:
            return cls._reverse_expect(*args, **kwds)

    @classmethod
    def default_args(cls):
        args = {}
        for name, field in cls.message_proto.DESCRIPTOR.fields_by_name.items():
            field_desc = next(f for f in cls.fields if f.name == name)
            for exclusive_with in field_desc.exclusive_with:
                # If a oneof exclusive field is already present in args drop
                # the current field
                if exclusive_with in args:
                    break
            else:
                if name == "selected_fields":
                    continue
                elif field.label == ProtoFieldLabel.Repeated._value_:
                    args[name] = []
                elif name in cls.args_message:
                    args[name] = cls.args_message[name].default_args()
                elif name in cls.args_enum:
                    args[name] = next(iter(cls.args_enum[name]))
                else:
                    args[name] = proto_type_to_python(field.type, field.message_type)()
        return args

    def _encode_args(self, args):
        args = self._map_set_selected_fields(self.message_proto, args)
        args = self._map_enum_to_int(args)
        args = self._map_google_protobuf(args)
        if (
            self.service_proto.DESCRIPTOR.fields_by_name[self.field_name].message_type
            is None
        ):
            proto = self.service_proto(**{self.field_name: args["value"]})
        else:
            proto = self.service_proto(**{self.field_name: args})
        return bytearray(proto.SerializeToString(deterministic=True))

    def _decode_payload(self, payload):
        proto = self.service_proto()
        proto.ParseFromString(payload)
        args = protobuf_json_format.MessageToDict(
            proto,
            preserving_proto_field_name=True,
            including_default_value_fields=True,
            preserve_int64_as_int=True,
        )[self.field_name]
        if isinstance(args, Mapping):
            args = OrderedDict(
                sorted(args.items(), key=lambda a: self.real_args_name.index(a[0]))
            )
            args = self._map_filter_selected_fields(self.message_proto, args)
            args = self._map_enum_type(args)
            args = self._map_message_type(args)
            args = self._map_google_protobuf(args, unwrap=True)
        else:
            args = OrderedDict(value=args)
        return args

    def _map_float_specials(self, args):
        if args is None or callable(args):
            return args
        args = args.copy()

        self._resolve_nested_messages()

        def _conv(value):
            if math.isnan(value):
                return "NaN"
            elif math.isinf(value):
                if value > 0.0:
                    return "Infinity"
                else:
                    return "-Infinity"
            else:
                return value

        for argname, argvalue in args.copy().items():
            if isinstance(argvalue, ArsdkProtoEnum):
                args[argname] = int(argvalue._value_)
            elif isinstance(argvalue, MutableMapping):
                if argname == "selected_fields":
                    continue
                if argname in self.args_message:
                    args[argname] = self.args_message[argname]._map_float_specials(
                        argvalue
                    )
                elif argname in self.args_map:
                    args[argname] = self.args_map[argname]._map_float_specials(argvalue)
        for arg in args.keys():
            if isinstance(args[arg], float):
                args[arg] = _conv(args[arg])
            elif isinstance(args[arg], (list, tuple)) and (
                all(map(lambda a: isinstance(a, float), args[arg]))
            ):
                args[arg] = tuple(_conv(a) for a in args[arg])
        for nested_message_name, nested_message in self.args_message.items():
            if nested_message_name not in args:
                continue
            if isinstance(args[nested_message_name], (tuple, list)):
                args[nested_message_name] = type(args[nested_message_name])(
                    nested_message._map_float_specials(a)
                    for a in args[nested_message_name]
                )
            else:
                args[nested_message_name] = nested_message._map_float_specials(
                    args[nested_message_name]
                )
        return args

    def _map_enum_type(self, args):
        if args is None or callable(args):
            return args
        args = args.copy()
        self._resolve_nested_messages()
        for argname, argvalue in args.copy().items():
            if isinstance(argvalue, MutableMapping):
                if argname == "selected_fields":
                    continue
                if argname in self.args_message:
                    args[argname] = self.args_message[argname]._map_enum_type(argvalue)
                elif argname in self.args_map:
                    args[argname] = self.args_map[argname]._map_enum_type(argvalue)
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], (bytes, str)):
                args[arg] = enum[args[arg]]
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, (bytes, str)), args[arg]))
            ):
                args[arg] = tuple(enum[a] for a in args[arg])
        for nested_map_name, nested_map in self.args_map.items():
            if nested_map_name not in args:
                continue
            if "value" in nested_map.args_enum:
                for k, v in args[nested_map_name].items():
                    if isinstance(v, ArsdkProtoEnum):
                        continue
                    args[nested_map_name][k] = nested_map.args_enum["value"][
                        args[nested_map_name][k]
                    ]
        for nested_message_name, nested_message in self.args_message.items():
            if nested_message_name not in args:
                continue
            if isinstance(args[nested_message_name], (tuple, list)):
                args[nested_message_name] = type(args[nested_message_name])(
                    nested_message._map_enum_type(a) for a in args[nested_message_name]
                )
            else:
                args[nested_message_name] = nested_message._map_enum_type(
                    args[nested_message_name]
                )
        return args

    def _map_enum_to_int(self, args):
        if args is None or callable(args):
            return args
        self._resolve_nested_messages()
        args = args.copy()
        for argname, argvalue in args.copy().items():
            if isinstance(argvalue, ArsdkProtoEnum):
                args[argname] = int(argvalue._value_)
            elif isinstance(argvalue, MutableMapping):
                if argname == "selected_fields":
                    continue
                if argname in self.args_message:
                    args[argname] = self.args_message[argname]._map_enum_to_int(
                        argvalue
                    )
                elif argname in self.args_map:
                    args[argname] = self.args_map[argname]._map_enum_to_int(argvalue)
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], ArsdkProtoEnum):
                args[arg] = int(args[arg]._value_)
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, ArsdkProtoEnum), args[arg]))
            ):
                args[arg] = tuple(int(a._value_) for a in args[arg])
        for nested_map_name, nested_map in self.args_map.items():
            if nested_map_name not in args:
                continue
            if "value" in nested_map.args_enum:
                for k, v in args[nested_map_name].items():
                    if isinstance(v, int):
                        continue
                    args[nested_map_name][k] = int(
                        nested_map.args_enum["value"][v]._value_
                    )
        for nested_message_name, nested_message in self.args_message.items():
            if nested_message_name not in args:
                continue
            if isinstance(args[nested_message_name], (tuple, list)):
                args[nested_message_name] = type(args[nested_message_name])(
                    nested_message._map_enum_to_int(a)
                    for a in args[nested_message_name]
                )
            else:
                args[nested_message_name] = nested_message._map_enum_to_int(
                    args[nested_message_name]
                )
        return args

    def _map_enum_to_str(self, args):
        if args is None or callable(args):
            return args
        self._resolve_nested_messages()
        args = args.copy()
        for argname, argvalue in args.copy().items():
            if isinstance(argvalue, ArsdkProtoEnum):
                args[argname] = argvalue.to_upper_str()
            elif isinstance(argvalue, MutableMapping):
                if argname == "selected_fields":
                    continue
                if argname in self.args_message:
                    args[argname] = self.args_message[argname]._map_enum_to_str(
                        argvalue
                    )
                elif argname in self.args_map:
                    args[argname] = self.args_map[argname]._map_enum_to_str(argvalue)
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], ArsdkProtoEnum):
                args[arg] = args[arg].to_upper_str()
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, ArsdkProtoEnum), args[arg]))
            ):
                args[arg] = tuple(a.to_upper_str() for a in args[arg])
        for nested_map_name, nested_map in self.args_map.items():
            if nested_map_name not in args:
                continue
            if "value" in nested_map.args_enum:
                for k, v in args[nested_map_name].items():
                    if isinstance(v, str):
                        continue
                    args[nested_map_name][k] = (
                        nested_map.args_enum["value"][args[nested_map_name][k]]._name_
                    ).to_upper_str()
        for nested_message_name, nested_message in self.args_message.items():
            if nested_message_name not in args:
                continue
            if isinstance(args[nested_message_name], (tuple, list)):
                args[nested_message_name] = type(args[nested_message_name])(
                    nested_message._map_enum_to_str(a)
                    for a in args[nested_message_name]
                )
            else:
                args[nested_message_name] = nested_message._map_enum_to_str(
                    args[nested_message_name]
                )
        return args

    def _map_message_type(self, args):
        if args is None or callable(args):
            return args
        self._resolve_nested_messages()
        args = args.copy()
        for arg, enum in self.args_message.items():
            if arg not in args:
                continue
            if not isinstance(args[arg], Mapping):
                continue
            if isinstance(args[arg], ArsdkMessageArgs):
                continue
            args[arg] = self.args_message[arg].dict_type(**args[arg])
        return args

    ### SELECTED_FIELDS HANDLING
    # Protobuf is a messaging protocole that just aims to carry out messages as they
    # are. Internally, the way used to let the drone know which fields have to be
    # changed are the selected fields. These are a map of messages ids (whose to be
    # updated) and google empty. This is a way to send a command to the drone like
    # Configure and tells it which fields of the new Config are supposed to change.
    # On the other hand, the drone handles the command by interpreting the selected
    # fields to take care of and change in its config, he then sends a State event in
    # response with the so-called changed fields, using the selected fields to point
    # out which fields have changed. All the fields not mentionned in the selected
    # fields are set to 0 (NULL).

    # _map_set_selected_fields is used when encoding a payload, to let the receiver
    # knows which fields are to be changed in values (the others are set to 0)
    def _map_set_selected_fields(self, proto, args):
        ret = args.copy()
        if hasattr(proto, "selected_fields"):
            selected_fields = OrderedDict(
                map(
                    lambda k: (
                        proto.DESCRIPTOR.fields_by_name[k].number,
                        google.protobuf.empty_pb2.Empty(),
                    ),
                    args.keys(),
                )
            )
            ret["selected_fields"] = selected_fields
        for k, v in args.items():
            if isinstance(k, str):
                ret[k] = self._set_selected_fields(getattr(proto, k, None), v)
        return ret

    # _map_filter_selected_fields is used when decoding a payload, to retrieve the
    # the fields to update and their new values(the others are thus set to 0)
    def _map_filter_selected_fields(self, proto, args):
        if "selected_fields" not in args:
            return args
        ret = type(args)()
        selected_fields = list(
            map(
                lambda i: proto.DESCRIPTOR.fields_by_number[i].name,
                args["selected_fields"].keys(),
            )
        )
        for k, v in args.items():
            if k not in selected_fields:
                continue
            if isinstance(k, str):
                ret[k] = self._filter_selected_fields(getattr(proto, k, None), v)
        return ret

    def _filter_selected_fields(self, proto, v):
        if proto is None:
            return v
        if isinstance(v, (bytes, str)):
            return v
        elif isinstance(v, Mapping):
            descriptor = proto.DESCRIPTOR
            descriptor = getattr(descriptor, "message_type", descriptor)
            if descriptor is None:
                return v
            vproto = self._proto.message_prototype_from_descriptor(descriptor)
            return self._map_filter_selected_fields(vproto, v)
        elif isinstance(v, Iterable):
            descriptor = proto.DESCRIPTOR
            descriptor = getattr(descriptor, "message_type", descriptor)
            if descriptor is None:
                return v
            vproto = self._proto.message_prototype_from_descriptor(descriptor)
            return type(v)(self._filter_selected_fields(vproto, i) for i in v)
        else:
            return v

    def _set_selected_fields(self, proto, v):
        if proto is None:
            return v
        if isinstance(v, (bytes, str)):
            return v
        elif isinstance(v, Mapping):
            descriptor = proto.DESCRIPTOR
            descriptor = getattr(descriptor, "message_type", descriptor)
            if descriptor is None:
                return v
            vproto = self._proto.message_prototype_from_descriptor(descriptor)
            return self._map_set_selected_fields(vproto, v)
        elif isinstance(v, Iterable):
            descriptor = proto.DESCRIPTOR
            descriptor = getattr(descriptor, "message_type", descriptor)
            if descriptor is None:
                return v
            vproto = self._proto.message_prototype_from_descriptor(descriptor)
            return type(v)(self._set_selected_fields(vproto, i) for i in v)
        else:
            return v

    def _event_from_args(self, args):
        return ArsdkProtoMessageEvent(self, args)

    @classmethod
    def _event_type(cls):
        return ArsdkProtoMessageEvent

    def last_event(self, key=None):
        if key is None:
            return self._last_event
        else:
            return self._last_event[key]

    def last_events(self, key=None):
        if self._last_event is None:
            return
        if key is None:
            yield self._last_event
        else:
            if self._last_event[key] is not None:
                yield self._last_event[key]

    def _update_oneofs_state(self, state, args):
        self._resolve_nested_messages()
        for field in self.fields:
            if field.name not in args:
                continue
            # If a oneof field is present in the received event:
            # drop its associated oneofs fields state
            for exclusive_with in field.exclusive_with:
                state.pop(exclusive_with, None)
        # Recursively update oneofs
        for nested_message_name, nested_message in self.args_message.items():
            if nested_message_name not in args:
                continue
            if nested_message_name not in state:
                continue
            if isinstance(state[nested_message_name], (tuple, list)):
                for s, a in zip(state[nested_message_name], args[nested_message_name]):
                    nested_message._update_oneofs_state(s, a)
            elif args[nested_message_name] is not None:
                nested_message._update_oneofs_state(
                    state[nested_message_name], args[nested_message_name]
                )

    def _set_last_event(self, event):
        if event.id != self.id:
            raise RuntimeError(
                "Cannot set message {} last event to {}".format(
                    self.fullName, event.message.fullName
                )
            )
        if self.callback_type == ArsdkMessageCallbackType.STANDARD:
            self._last_event = event
            # Honor oneofs fields when before updating our cached states
            self._update_oneofs_state(self._state, event.args)
            # Recursively update our cached state
            update_mapping(self._state, event.args)
        elif self.callback_type == ArsdkMessageCallbackType.MAP:
            if self._last_event is None:
                # Initialize self._last_event to an empty dictionary, in case it is the
                # first time it is used
                self._last_event = OrderedDict()
            key = event.args[self.key_name]
            # Fill in self._last_event with the value of the key wanted
            self._last_event[key] = event
            # Honor oneofs fields when before updating our cached states
            if key not in self._state:
                self._state[key] = OrderedDict()
            self._update_oneofs_state(self._state[key], event.args)
            # Recursively update our cached state
            update_mapping(self._state[key], event.args)

    @classmethod
    def _argsmap_from_args(cls, *args, **kwds):
        # Message({"a": 1, "b": 2}) is ambiguous: either "a" and "b" are the
        # names of this message's own arguments (the call means a=1, b=2),
        # or only one positional value was passed to this call, and that
        # value happens to be the dict {"a": 1, "b": 2} itself.
        # We treat it as a=1, b=2 only when "a" and "b" are both argument
        # names of this message. Otherwise, the whole dict is treated as the
        # value of that single positional argument.
        if (
            not kwds
            and len(args) == 1
            and isinstance(args[0], MutableMapping)
            and all(k in cls.args_name for k in args[0].keys())
        ):
            args = args[0]
        else:
            args = OrderedDict(zip(map(lambda a: a, cls.args_name), args))
            args_set = set(args.keys())
            kwds_set = set(kwds.keys())
            if not args_set.isdisjoint(kwds_set):
                raise RuntimeError(
                    "Message `{}` got multiple values for argument(s) {}".format(
                        cls.fullName, list(args_set & kwds_set)
                    )
                )
            args.update(kwds)

        # filter out None value
        args = OrderedDict((k, v) for k, v in args.items() if v is not None)

        # enum conversion
        for name, value in args.items():
            if name in cls.args_enum and isinstance(value, (bytes, str)):
                args[name] = cls.args_enum[name][value]

        # submessages conversion
        for nested_message_name, nested_message in cls.args_message.items():
            if nested_message_name not in args:
                continue
            if isinstance(args[nested_message_name], ArsdkProtoThis):
                continue
            elif isinstance(args[nested_message_name], (tuple, list)):
                args[nested_message_name] = type(args[nested_message_name])(
                    map(
                        lambda a: nested_message._argsmap_from_args(a),
                        args[nested_message_name],
                    )
                )
            else:
                args[nested_message_name] = nested_message._argsmap_from_args(
                    **args[nested_message_name]
                )

        return args

    @classmethod
    def _expectation_from_args(cls, *args, **kwds):
        expected_args = cls._argsmap_from_args(*args, **kwds)
        return ArsdkEventExpectation(cls, expected_args)

    @classmethod
    def _resolve_doc(cls, messages, module):
        # Phase 2 for proto messages: build the full docstring and compile
        # __call__.  The module parameter is forwarded to _parse_expectation
        # so that on_success expressions are evaluated in the feature module's
        # namespace.
        proto_resolve_doc(cls, messages, module)

    def _resolve_nested_messages(self):
        # Lazy resolution: nested message classes are only instantiated once
        # the feature module has been fully loaded (_resolve_proto_nested_messages
        # marks the feature as ready in nested_proto_resolved).  This avoids
        # circular instantiation issues during startup.
        if self._nested_resolved:
            return
        if self.feature_name not in self._messages.nested_proto_resolved:
            return
        for nested_name, nested in self.__class__.nested_messages.items():
            self.nested_messages[nested_name] = nested()
        for nested_name, nested in self.__class__.args_message.items():
            self.args_message[nested_name] = nested()
        for nested_name, nested in self.__class__.args_map.items():
            self.args_map[nested_name] = nested()
        self._nested_resolved = True
