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


from builtins import str as builtin_str

import arsdkparser
import ctypes
import functools
import textwrap

from aenum import OrderedEnum
from collections import OrderedDict
from collections.abc import Iterable, Mapping, MutableMapping
from itertools import starmap
from olympe.arsdkng.xml import ArsdkXml
import google.protobuf.empty_pb2
import logging
import olympe_deps as od
import re

from olympe.arsdkng.enums import (
    ArsdkEnums,
    ArsdkEnum,
    list_flags,
    ArsdkBitfield,
    ArsdkProtoEnum,
)
from olympe.arsdkng.expectations import ArsdkEventExpectation
from olympe.arsdkng.expectations import ArsdkCommandExpectation
from olympe.arsdkng.expectations import ArsdkProtoCommandExpectation
from olympe.arsdkng.expectations import ArsdkWhenAnyExpectation
from olympe.arsdkng.expectations import ArsdkWhenAllExpectations
from olympe.arsdkng.expectations import ArsdkCheckStateExpectation
from olympe.arsdkng.expectations import ArsdkCheckWaitStateExpectation
from olympe.arsdkng.expectations import ExpectPolicy
from olympe.arsdkng.events import (
    ArsdkMessageEvent,
    ArsdkProtoMessageEvent,
    ArsdkMessageArgs,
)
from olympe.arsdkng.proto import ArsdkProto, ProtoFieldLabel, proto_type_to_python
from olympe.utils import (
    string_from_arsdkxml,
    DEFAULT_FLOAT_TOL,
    update_mapping,
    get_mapping,
    set_mapping,
    has_mapping,
    remove_from_collection,
)
from olympe.protobuf import json_format as protobuf_json_format


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


class ArsdkMessageMeta(type):

    _base = None

    def __new__(mcls, *args, **kwds):
        """
        ArsdkMessage constructor
        @type obj: arsdkparser.ArMsg
        @param name_path: the full xml path of the message as a list of names
            ["<feature_name>", "<class_name>" or None, "message_name"]
        @param id_path: the full xml path of the message as a list ids
            [<feature_id>, <class_id> or None, message_id]
        @type enums: olympe.arsdkng.ArsdkEnums
        """
        if mcls._base is None:
            cls = super().__new__(mcls, *args, **kwds)
            mcls._base = cls
            return cls

        obj, name_path, id_path, enums = args

        fullPath = tuple(filter(None, name_path))
        fullName = ".".join(fullPath)

        cls = super().__new__(mcls, builtin_str(fullName), (mcls._base,), {})

        cls.fullName = fullName
        cls.prefix = fullPath[:-1]
        Full_Name = "_".join(name[0].upper() + name[1:] for name in fullPath)
        cls.g_arsdk_cmd_desc = f"g_arsdk_cmd_desc_{Full_Name}"

        cls.obj = obj
        cls.name_path = name_path
        cls.id_path = id_path

        cls.args_pos = OrderedDict()
        cls.args_enum = OrderedDict()
        cls.args_bitfield = OrderedDict()

        cls.callback_type = ArsdkMessageCallbackType.from_arsdk(cls.obj.listType)
        cls.message_type = ArsdkMessageType.from_arsdk(type(cls.obj))
        cls.buffer_type = ArsdkMessageBufferType.from_arsdk(cls.obj.bufferType)

        cls.loglevel = logging.INFO
        if (
            cls.message_type is ArsdkMessageType.EVT
            and cls.buffer_type is not ArsdkMessageBufferType.ACK
        ):
            # Avoid being flooded by spontaneous event messages sent by the drone
            cls.loglevel = logging.DEBUG
        if cls.fullName in (
            "ardrone3.PilotingState.AltitudeChanged",
            "ardrone3.PilotingState.AltitudeAboveGroundChanged",
            "ardrone3.PilotingState.AttitudeChanged",
            "ardrone3.PilotingState.GpsLocationChanged",
            "ardrone3.PilotingState.PositionChanged",
            "ardrone3.PilotingState.SpeedChanged",
            "skyctrl.SkyControllerState.AttitudeChanged",
            "mapper.button_mapping_item",
            "mapper.axis_mapping_item",
            "mapper.expo_map_item",
            "mapper.inverted_map_item",
        ):
            cls.loglevel = logging.DEBUG

        cls.feature_name = name_path[0]
        cls.class_name = name_path[1]
        if cls.class_name is not None and (
            "state" in cls.class_name.lower() or "event" in cls.class_name.lower()
        ):
            cls.message_type = ArsdkMessageType.EVT
        cls.name = name_path[2]

        cls.feature_id = id_path[0]
        cls.class_id = id_path[1] or ARSDK_CLS_DEFAULT_ID
        cls.msg_id = id_path[2]

        cls.id = cls.feature_id << 24 | cls.class_id << 16 | cls.msg_id
        cls.id_name = "-".join(map(str, filter(lambda x: x is not None, cls.id_path)))

        # process arguments information
        for i, arg in enumerate(cls.obj.args):
            cls.args_pos[arg.name] = i
            if isinstance(arg.argType, arsdkparser.ArEnum):
                enum_name = arg.argType.name
                cls.args_enum[arg.name] = enums[cls.feature_name][enum_name]
            elif isinstance(arg.argType, arsdkparser.ArBitfield):
                cls.args_bitfield[arg.name] = enums[cls.feature_name][
                    arg.argType.enum.name
                ]._bitfield_type_

        cls.args_name = [arg.name for arg in cls.obj.args]

        cls.key_name = None
        if cls.obj.listType == arsdkparser.ArCmdListType.MAP:
            cls.key_name = (
                cls.obj.mapKey and cls.obj.mapKey.name or cls.obj.args[0].name
            )
        # README: workaround missing MAP_ITEMS
        elif "cam_id" in cls.args_name and cls.feature_name == "camera":
            cls.callback_type = ArsdkMessageCallbackType.MAP
            cls.key_name = "cam_id"
        elif "gimbal_id" in cls.args_name and cls.feature_name == "gimbal":
            cls.callback_type = ArsdkMessageCallbackType.MAP
            cls.key_name = "gimbal_id"
        elif "uid" in cls.args_name and cls.feature_name == "mission":
            cls.callback_type = ArsdkMessageCallbackType.MAP
            cls.key_name = "uid"
        elif (
            "list_flags" in cls.args_bitfield
            and cls.args_bitfield["list_flags"] == list_flags._bitfield_type_
        ):
            cls.callback_type = ArsdkMessageCallbackType.LIST

        if cls.obj.args:
            cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args = map(
                list,
                zip(
                    *(
                        cls._ar_argtype_encode_type(ar_arg.argType)
                        for ar_arg in cls.obj.args
                    )
                ),
            )
        else:
            cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args = (
                [],
                [],
                [],
            )

        cls.args_type = OrderedDict()
        for argname, ar_arg in zip(cls.args_name, cls.obj.args):
            cls.args_type[argname] = cls._ar_argtype_to_python(argname, ar_arg.argType)

        cls.timeout = TIMEOUT_BY_COMMAND.get(cls.fullName, DEFAULT_TIMEOUT)
        cls.float_tol = FLOAT_TOLERANCE_BY_FEATURE.get(
            cls.feature_name, DEFAULT_FLOAT_TOL
        )

        cls._expectation = None
        cls._reverse_expectation = None

        # Get information on callback ctypes arguments
        cls.arsdk_desc = od.arsdk_cmd_find_desc(
            od.struct_arsdk_cmd.bind(
                {
                    "prj_id": cls.feature_id,
                    "cls_id": cls.class_id,
                    "cmd_id": cls.msg_id,
                }
            )
        )

        cls.decode_ctypes_args = []

        decode_ctypes_args_map = {
            od.ARSDK_ARG_TYPE_I8: ctypes.c_int8,
            od.ARSDK_ARG_TYPE_U8: ctypes.c_uint8,
            od.ARSDK_ARG_TYPE_I16: ctypes.c_int16,
            od.ARSDK_ARG_TYPE_U16: ctypes.c_uint16,
            od.ARSDK_ARG_TYPE_I32: ctypes.c_int32,
            od.ARSDK_ARG_TYPE_U32: ctypes.c_uint32,
            od.ARSDK_ARG_TYPE_I64: ctypes.c_int64,
            od.ARSDK_ARG_TYPE_U64: ctypes.c_uint64,
            od.ARSDK_ARG_TYPE_FLOAT: ctypes.c_float,
            od.ARSDK_ARG_TYPE_DOUBLE: ctypes.c_double,
            od.ARSDK_ARG_TYPE_STRING: ctypes.c_char_p,
            od.ARSDK_ARG_TYPE_ENUM: ctypes.c_int,
            od.ARSDK_ARG_TYPE_BINARY: od.struct_arsdk_binary,
        }

        for i in range(cls.arsdk_desc.contents.arg_desc_count):
            arg_type = cls.arsdk_desc.contents.arg_desc_table[i].type
            cls.decode_ctypes_args.append(decode_ctypes_args_map[arg_type])

        # Fixup missing list_flags arguments for LIST_ITEM/MAP_ITEM messages
        if ("list_flags" not in cls.args_name) and (
            cls.message_type is ArsdkMessageType.EVT
            and cls.callback_type
            in (ArsdkMessageCallbackType.LIST, ArsdkMessageCallbackType.MAP)
        ):
            cls.args_pos["list_flags"] = len(cls.args_pos)
            cls.args_name.append("list_flags")
            cls.args_bitfield["list_flags"] = list_flags._bitfield_type_
            cls.args_type["list_flags"] = int
            cls.decode_ctypes_args.append(ctypes.c_uint8)
            cls.encode_ctypes_args.append(ctypes.c_uint8)

        if cls.message_type is ArsdkMessageType.CMD:
            cls.args_default = ArsdkMessages._default_arguments.get(
                cls.fullName, OrderedDict()
            )
        else:
            cls.args_default = OrderedDict(
                zip(cls.args_name, [None] * len(cls.args_name))
            )
        cls.args_default_str = ", ".join(
            f"{argname}={cls.args_default[argname]}"
            if argname in cls.args_default
            else argname
            for argname in cls.args_name + ["**kwds"]
        )
        cls.decoded_args = list(
            map(lambda ctype: ctypes.pointer(ctype()), cls.decode_ctypes_args)
        )
        cls.decoded_args_type = list(
            map(lambda ctype: ctypes.POINTER(ctype), cls.decode_ctypes_args)
        )

        # docstring
        cls.doc_todos = ""
        cls.docstring = cls._py_ar_cmd_docstring()
        cls.__doc__ = cls.docstring + "\n"

        cls.__call__ = cls._create_call()
        return cls

    def _py_ar_cmd_docstring(cls):
        """
        Returns a python docstring from an ArCmd object
        """
        docstring = "\n\n".join(
            [cls.fullName]
            + [
                cls._py_ar_comment_docstring(
                    cls.obj.doc,
                    cls._py_ar_args_docstring(cls.obj.args),
                    cls.obj.isDeprecated,
                )
            ]
        )
        return docstring

    def _py_ar_arg_directive(cls, directive, argname, doc):
        directive = f":{directive} {argname}: "
        doc = f"{directive}{doc}"
        doc = textwrap.fill(
            doc, subsequent_indent=(" " * len(directive)), break_long_words=False
        )
        return doc

    def _py_ar_args_docstring(cls, ar_args):
        if cls.message_type == ArsdkMessageType.CMD:
            extra_params_docstring = (
                "\n\n"
                + ":param _timeout: command message timeout (defaults to {})\n".format(
                    cls.timeout
                )
                + ":type _timeout: int\n"
                + ":param _no_expect: if True for,do not expect the usual command"
                " expectation " + "(defaults to False)\n" + ":type _no_expect: bool\n"
            )
        else:
            extra_params_docstring = (
                "\n\n"
                + ":param _policy: specify how to check the expectation. Possible"
                " values are "
                + "'check', 'wait' and 'check_wait' (defaults to 'check_wait')\n"
                + ":type _policy: `olympe.arsdkng.expectations.ExpectPolicy`\n"
            )
        extra_params_docstring += (
            ":param _float_tol: specify the float comparison tolerance, a 2-tuple"
            " containing a "
            + "relative tolerance float value and an absolute tolerate float value "
            + f"(default to {cls.float_tol}). "
            + "See python 3 stdlib `math.isclose` "
            + "documentation for more information\n"
            + ":type _float_tol: `tuple`\n"
        )
        return (
            "\n".join(cls._py_ar_arg_docstring(arg) for arg in ar_args)
            + extra_params_docstring
        )

    def _py_ar_arg_docstring(cls, ar_arg):
        """
        Returns a python docstring from an ArArg object
        """
        if isinstance(ar_arg.argType, (int,)):
            type_ = cls._py_ar_arg_directive(
                "type", ar_arg.name, arsdkparser.ArArgType.TO_STRING[ar_arg.argType]
            )
        elif isinstance(ar_arg.argType, (arsdkparser.ArBitfield,)):
            enum = ":py:class:`olympe.enums.{}.{}`".format(
                ".".join(cls.prefix),
                cls.args_bitfield[ar_arg.name]._enum_type_.__name__,
            )
            doc = "BitfieldOf({}, {})".format(
                enum,
                arsdkparser.ArArgType.TO_STRING[ar_arg.argType.btfType],
            )
            type_ = cls._py_ar_arg_directive("type", ar_arg.name, doc)
        elif isinstance(ar_arg.argType, (arsdkparser.ArEnum,)):
            doc = ":py:class:`olympe.enums.{}.{}`".format(
                ".".join(cls.prefix), cls.args_enum[ar_arg.name].__name__
            )
            type_ = cls._py_ar_arg_directive("type", ar_arg.name, doc)
        else:
            raise RuntimeError(f"Unknown argument type {type(ar_arg.argType)}")

        param = cls._py_ar_arg_directive(
            "param", ar_arg.name, cls._py_ar_comment_docstring(ar_arg.doc)
        )
        return f"\n\n{type_}\n\n{param}"

    def _py_ar_supported(cls, supported_devices, deprecated):
        unsupported_notice = "**Unsupported message**"
        if not cls.feature_name == "debug":
            unsupported_notice += (
                "\n\n.. todo::\n    Remove unsupported message {}\n".format(
                    cls.fullName
                )
            )
        deprecation_notice = (
            "**Deprecated message**\n\n.. warning::\n    "
            "This message is deprecated and should no longer be used"
        )
        if deprecated:
            unsupported_notice += "\n\n" + deprecation_notice
        if not supported_devices:
            return unsupported_notice
        supported_devices = string_from_arsdkxml(supported_devices)
        if supported_devices == "drones":
            return "**Supported by every drone product**"
        elif supported_devices == "none":
            return unsupported_notice
        supported_devices = supported_devices.split(";")
        supported_devices = list(
            map(lambda s: s.split(":", maxsplit=2), supported_devices)
        )
        try:
            supported_devices = list(
                map(lambda s: (int(s[0], base=16), *s[1:]), supported_devices)
            )
        except ValueError:
            return unsupported_notice
        ret = []
        for device in supported_devices:
            device_str, *versions = device
            versions = iter(versions)
            device_str = od.string_cast(od.arsdk_device_type_str(device_str))
            since = next(versions, None)
            until = next(versions, None)
            mapping = {
                "ANAFI4K": "Anafi/AnafiFPV",
                "ANAFI_THERMAL": "AnafiThermal",
                "SKYCTRL_3": "SkyController3",
                "ANAFI_2": "Anafi Ai",
            }
            device_str = mapping.get(device_str, device_str)
            if "anafi" in device_str.lower() or "skycontroller" in device_str.lower():
                if until:
                    ret.append(
                        "    :{}: since {} and until {} firmware release".format(
                            device_str, since, until
                        )
                    )
                else:
                    ret.append(
                        f"    :{device_str}: with an up to date firmware"
                    )
        if not ret:
            return unsupported_notice

        ret = "\n".join(ret)
        ret = "\n\n" + ret + "\n\n"
        ret = "**Supported by**: " + ret
        if deprecated:
            ret += "\n\n" + deprecation_notice
        return ret

    def _py_ar_triggered(cls, triggered):
        ret = string_from_arsdkxml(triggered)
        if not ret.startswith("Triggered "):
            ret = "Triggered " + ret
        return textwrap.fill(ret, break_long_words=False)

    def _py_ar_comment_docstring(
        cls, ar_comment, ar_args_doc=None, ar_is_deprecated=False
    ):
        """
        Returns a python docstring from an ArComment object
        """
        if isinstance(ar_comment, (str, bytes)):
            return string_from_arsdkxml(str(ar_comment))
        ret = ""
        if ar_comment.title and not ar_comment.desc:
            ret += "\n\n{}".format(
                textwrap.fill(
                    string_from_arsdkxml(ar_comment.title), break_long_words=False
                ),
            )
        elif ar_comment.desc:
            ret += "\n\n{}".format(
                textwrap.fill(
                    string_from_arsdkxml(ar_comment.desc), break_long_words=False
                ),
            )
        if ar_args_doc is not None:
            ret += ar_args_doc
        # FIXME: arsdk-xml "support" attribute is currently unreliable
        ret += "\n\n{}".format(
            cls._py_ar_supported(ar_comment.support, ar_is_deprecated),
        )
        if ar_comment.triggered:
            ret += "\n\n{}".format(
                cls._py_ar_triggered(ar_comment.triggered),
            )
        if ar_comment.result:
            ret += "\n\n**Result**: {}".format(
                textwrap.fill(
                    string_from_arsdkxml(ar_comment.result), break_long_words=False
                ),
            )
        return ret

    def _py_ar_cmd_expectation_docstring(cls):
        ret = ""
        if cls.message_type == ArsdkMessageType.CMD:
            for i, expectation in enumerate(cls._expectation):
                if isinstance(expectation, ArsdkWhenAnyExpectation):
                    ret += cls._py_ar_cmd_any_expectation_docstring(expectation)
                else:
                    ret += (
                        "#"
                        + expectation.expected_message.id_name
                        + cls._py_ar_cmd_expectation_args_docstring(
                            expectation.expected_args
                        )
                    )
                if i < len(cls._expectation) - 1:
                    ret += " & "
        if ret:
            ret = f"**Expectations**: {ret}"
        return ret

    def _py_ar_cmd_any_expectation_docstring(cls, any_expectations):
        ret = "("
        for i, expectation in enumerate(any_expectations):
            ret += (
                "#"
                + expectation.expected_message.id_name
                + cls._py_ar_cmd_expectation_args_docstring(expectation.expected_args)
            )
            if i < len(any_expectations) - 1:
                ret += " | "
        ret += ")"
        return ret

    def _py_ar_cmd_expectation_args_docstring(cls, args):
        args = args.copy()
        args.update(_policy="'wait'")
        ret = "("
        ret += ", ".join(
            (
                argname
                + "="
                + cls._py_ar_cmd_expectation_argval_docstring(argname, argval)
                for argname, argval in args.items()
            )
        )
        ret += ")"
        ret = ret.replace("this.", "self.")
        return ret

    def _py_ar_cmd_expectation_argval_docstring(cls, argname, argval):
        if isinstance(argval, ArsdkEnum):
            return "'" + argval._name_ + "'"
        elif isinstance(argval, ArsdkBitfield):
            return argval.pretty()
        elif callable(argval):
            command_args = OrderedDict(
                (arg, f"this.{arg}") for arg in cls.args_name
            )
            try:
                return argval(cls, command_args)
            except KeyError:
                cls.doc_todos += "\n\n.. todo::\n    {}".format(
                    "Fix wrong expectation definition for {}:\n    {}".format(
                        cls.fullName,
                        "Invalid parameter value for the '{}' expectation parameter\n".format(
                            argname
                        ),
                    )
                )
                return "InternalError"
        else:
            return str(argval)

    def get_source(cls):
        args = ", ".join(cls.args_name + ["**kwds"])
        docstring = cls.docstring
        # The docstring needs to be correctly indented in order to be
        # interpreted just below
        docstring = "\n" + "\n".join(
            [" " * 16 + doc.strip() for doc in docstring.splitlines()]
        )
        return textwrap.dedent(
            """
            def {name}(self, {defaulted_args}):
                u\"""{docstring}
                \"""
                try:
                    return self._expect({args})
                except Exception as e:
                    import logging
                    logging.exception("")
                    raise
            """.format(
                name=cls.name,
                defaulted_args=cls.args_default_str,
                args=args,
                docstring=docstring,
            )
        )

    def _create_call(cls):
        """
        Returns a python function that sends a specific ARSDK command

        The name of the returned python function is cls.name
        The parameters of the returned function repsect the naming of arsdk-xml.
        The docstring of the returned function is also extracted from the XMLs.

        @param send_command: ArCmd object provided by the arsdk-xml parser

        """
        exec(cls.get_source(), locals())
        return locals()[cls.name]

    def _ar_arsdk_encode_type_info(cls, ar_argtype):
        arsdk_encode_type_info_map = {
            arsdkparser.ArArgType.I8: (od.ARSDK_ARG_TYPE_I8, "i8", ctypes.c_int8),
            arsdkparser.ArArgType.U8: (od.ARSDK_ARG_TYPE_U8, "u8", ctypes.c_uint8),
            arsdkparser.ArArgType.I16: (od.ARSDK_ARG_TYPE_I16, "i16", ctypes.c_int16),
            arsdkparser.ArArgType.U16: (od.ARSDK_ARG_TYPE_U16, "u16", ctypes.c_uint16),
            arsdkparser.ArArgType.I32: (od.ARSDK_ARG_TYPE_I32, "i32", ctypes.c_int32),
            arsdkparser.ArArgType.U32: (od.ARSDK_ARG_TYPE_U32, "u32", ctypes.c_uint32),
            arsdkparser.ArArgType.I64: (od.ARSDK_ARG_TYPE_I64, "i64", ctypes.c_int64),
            arsdkparser.ArArgType.U64: (od.ARSDK_ARG_TYPE_U64, "u64", ctypes.c_uint64),
            arsdkparser.ArArgType.FLOAT: (
                od.ARSDK_ARG_TYPE_FLOAT,
                "f32",
                ctypes.c_float,
            ),
            arsdkparser.ArArgType.DOUBLE: (
                od.ARSDK_ARG_TYPE_DOUBLE,
                "f64",
                ctypes.c_double,
            ),
            arsdkparser.ArArgType.STRING: (
                od.ARSDK_ARG_TYPE_STRING,
                "cstr",
                od.char_pointer_cast,
            ),
            arsdkparser.ArArgType.ENUM: (od.ARSDK_ARG_TYPE_ENUM, "i32", ctypes.c_int32),
            arsdkparser.ArArgType.BINARY: (
                od.ARSDK_ARG_TYPE_BINARY,
                "binary",
                lambda t: cls._ar_arsdk_encode_binary(t),
            ),
        }
        return arsdk_encode_type_info_map[ar_argtype]

    def _ar_arsdk_encode_binary(cls, buf):
        buf_type = ctypes.c_char * len(buf)
        return od.struct_arsdk_binary(
            ctypes.cast(buf_type.from_buffer(buf), od.POINTER_T(None)), len(buf)
        )

    def _ar_argtype_encode_type(cls, ar_argtype):
        """
        Returns the ctypes type associated to the ar_argtype from arsdkparser
        """
        arbitfield = isinstance(ar_argtype, arsdkparser.ArBitfield)
        ar_bitfield_value = ar_argtype == arsdkparser.ArArgType.BITFIELD

        if isinstance(ar_argtype, arsdkparser.ArEnum):
            return od.ARSDK_ARG_TYPE_ENUM, "i32", ctypes.c_int32
        elif arbitfield or ar_bitfield_value:
            return cls._ar_arsdk_encode_type_info(ar_argtype.btfType)
        else:
            return cls._ar_arsdk_encode_type_info(ar_argtype)

    def _ar_argtype_to_python(cls, argname, ar_argtype):
        """
        Returns the python type associated to the ar_argtype from arsdkparser
        """

        if argname in cls.args_enum:
            return cls.args_enum[argname]
        elif argname in cls.args_bitfield:
            return cls.args_bitfield[argname]
        elif ar_argtype in (
            arsdkparser.ArArgType.I8,
            arsdkparser.ArArgType.U8,
            arsdkparser.ArArgType.I16,
            arsdkparser.ArArgType.U16,
            arsdkparser.ArArgType.I32,
            arsdkparser.ArArgType.U32,
            arsdkparser.ArArgType.I64,
            arsdkparser.ArArgType.U64,
        ):
            return int
        elif ar_argtype in (arsdkparser.ArArgType.DOUBLE, arsdkparser.ArArgType.FLOAT):
            return float
        elif ar_argtype == arsdkparser.ArArgType.STRING:
            return str
        else:
            return None

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
        cls.docstring += "\n\n" + cls._py_ar_cmd_expectation_docstring()

        def message_ref(m):
            try:
                message = messages.by_id_name[m.group(1)]
            except KeyError:
                return m.group(0)
            if m.lastindex > 1:
                args = "(" + m.group(2) + ")"
            else:
                args = "()"
            return ":py:func:`{}{}<olympe.messages.{}>`".format(
                message.name, args, message.fullName
            )

        cls.docstring = re.sub(r"\[[\w_\\]+\]\(#([\d-]+)\)", message_ref, cls.docstring)
        cls.docstring = re.sub(r"#([\d-]+)\(([^)]*)\)", message_ref, cls.docstring)
        cls.docstring = re.sub(r"#([\d-]+)", message_ref, cls.docstring)
        if cls.doc_todos:
            cls.docstring += cls.doc_todos
        cls.__doc__ = cls.docstring


class ArsdkMessageBase:
    pass


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
                lambda name, value: (name, cls.args_enum[name][value])
                if (name in cls.args_enum and isinstance(value, (bytes, str)))
                else (name, value),
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
            if not event_list_flags or event_list_flags == [
                list_flags.Last
            ]:
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
            if not event_list_flags or event.args["list_flags"] == [
                list_flags.Last
            ]:
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
            raise RuntimeError(f"{self.fullName} state is uninitialized")
        return self._state

    def _reset_state(self):
        self._last_event = None
        self._state = OrderedDict()

    def _expect_args(cls, *args, **kwds):
        default_timeout = (
            cls.timeout if cls.message_type is ArsdkMessageType.CMD else None
        )
        default_float_tol = cls.float_tol
        timeout = kwds.pop("_timeout", default_timeout)
        float_tol = kwds.pop("_float_tol", default_float_tol)
        no_expect = kwds.pop("_no_expect", False)
        send_command = kwds.pop("_send_command", True)
        policy = kwds.pop("_policy", "check_wait")
        if isinstance(policy, (bytes, str)):
            policy = ExpectPolicy[policy]
        else:
            raise RuntimeError("policy argument must be a string")

        if not send_command and no_expect:
            raise RuntimeError(
                "Invalid argument combination "
                + "Message._expect(send_command=False, no_expect=True)"
            )

        args = cls._argsmap_from_args(*args, **kwds)
        # enum conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (name, cls.args_enum[name][value])
                if (name in cls.args_enum and isinstance(value, (bytes, str)))
                else (name, value),
                args.items(),
            )
        )

        # bitfield conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (name, cls.args_bitfield[name](value))
                if name in cls.args_bitfield
                else (name, value),
                args.items(),
            )
        )

        # int -> float conversion
        args = OrderedDict(
            starmap(
                lambda name, value: (name, float(value))
                if isinstance(value, int) and cls.args_type[name] is float
                else (name, value),
                args.items(),
            )
        )
        return args, send_command, policy, float_tol, no_expect, timeout

    def _expect(cls, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with the provided
        command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        """
        args, send_command, policy, float_tol, no_expect, timeout = cls._expect_args(
            *args, **kwds
        )
        for arg_name in args:
            if arg_name not in cls.args_name:
                raise ValueError(f"'{cls.fullName}' message has no such '{arg_name}' parameter")
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
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(cls, args)
            expectations.set_float_tol(float_tol)
            expectations._fill_default_arguments(cls, args)
            return expectations

    def _reverse_expect(cls, *args, **kwds):
        args, send_command, policy, float_tol, no_expect, timeout = cls._expect_args(
            *args, **kwds
        )
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
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(cls, args)
            expectations.set_float_tol(float_tol)
            expectations._fill_default_arguments(cls, args)
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
        if cls.callback_type in (ArsdkMessageCallbackType.MAP, ArsdkMessageCallbackType.LIST):
            if "list_flags" not in args:
                args[name] = list_flags._bitfield_type_()
        return args

    @classmethod
    def _encode_args(cls, *args):
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

        encoded_args = args
        # enum conversion (string --> enum type)
        encoded_args = list(
            starmap(
                lambda name, value: cls.args_enum[name][value]
                if (name in cls.args_enum and isinstance(value, (bytes, str)))
                else value,
                zip(cls.args_name, encoded_args),
            )
        )

        # enum conversion (enum type --> integer)
        encoded_args = list(
            starmap(
                lambda name, value: value._value_
                if (name in cls.args_enum) and isinstance(value, ArsdkEnum)
                else value,
                zip(cls.args_name, encoded_args),
            )
        )

        # bitfield conversion ([string, enum list, bitfield] --> integer)
        encoded_args = list(
            starmap(
                lambda name, value: cls.args_bitfield[name](value).to_int()
                if name in cls.args_bitfield
                else value,
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
        for (i, arg, sdktype, value_attr, ctype) in zip(
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
        od.arsdk_cmd_dec.argtypes = od.arsdk_cmd_dec.argtypes[:2] + cls.decoded_args_type

        res = od.arsdk_cmd_dec(message_buffer, cls.arsdk_desc, *cls.decoded_args)

        decoded_args = cls.decoded_args[:]
        for i, (name, arg) in enumerate(zip(cls.args_name, decoded_args)):
            # ctypes -> python type conversion (exception: arsdk_binary -> c_char array)
            if not isinstance(arg.contents, od.struct_arsdk_binary):
                decoded_args[i] = arg = arg.contents.value
            else:
                decoded_args[i] = arg = (ctypes.c_char * arg.contents.len).from_address(
                    arg.contents.cdata)
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


class ArsdkMessageType(OrderedEnum):
    CMD, EVT = range(2)

    @classmethod
    def from_arsdk(cls, value):
        return {arsdkparser.ArCmd: cls.CMD, arsdkparser.ArEvt: cls.EVT}[value]


class ArsdkMessageCallbackType(OrderedEnum):
    STANDARD, LIST, MAP = range(3)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmdListType.NONE: cls.STANDARD,
            arsdkparser.ArCmdListType.LIST: cls.LIST,
            arsdkparser.ArCmdListType.MAP: cls.MAP,
        }[value]


class ArsdkMessageBufferType(OrderedEnum):
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
        @type arsdk_enums: olympe.arsdkng.Enums
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
        self.nested_proto_resolved = False

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
                                message_desc.doc,
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
                            message_desc.doc,
                        )
                        self._do_add_arsdk_proto_message(
                            name_path, message, message_desc
                        )
                        self.service_messages[(service.id, message.number)] = message
                        break
                    target_name_path = target_name_path[:-2] + [target_name_path[-1]]
                    same_scope = False
            if message is not None:
                break

        if message is None:
            message = ArsdkProtoMessageMeta.__new__(
                ArsdkProtoMessageMeta,
                self._root,
                path,
                None,
                message_desc,
                message_desc.doc,
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
        self.nested_proto_resolved = True

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
                    context.args_message[field.name] = message
                    break
        for name, message in context.items():
            self._do_resolve_nested_messages(module, f"{path}.{name}", message)

    def _resolve_doc(self):
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
        if key in cls:
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

    _base = None

    def __new__(mcls, *args, **kwds):
        """
        ArsdkMessage constructor
        @param name_path: the full path of the message as a list of names
        @type name_path: Iterable[str]
        @type service: olympe.arsdkng.proto.ArsdkProtoService
        @type message_desc: olympe.arsdkng.proto.ArsdkProtoMessage
        @type doc_desc: olympe.arsdkng.proto.ArsdkProtoMessageDoc
        """
        if mcls._base is None:
            cls = type.__new__(mcls, *args, **kwds)
            mcls._base = cls
            return cls

        root, name_path, service, message_desc, doc_desc = args
        olympe_proto = ArsdkProto.get("olympe")
        olympe_messages = ArsdkMessages.get("olympe")
        olympe_enums = ArsdkEnums.get("olympe")
        feature_proto = ArsdkProto.get(root)
        feature_enums = ArsdkEnums.get(root)
        generic = olympe_messages.by_feature["generic"]

        fullName = ".".join(name_path)

        cls = type.__new__(mcls, builtin_str(fullName), (mcls._base,), {})
        dict_type = type.__new__(type, builtin_str(fullName), (ArsdkMessageArgs,), {})
        MutableMapping.register(cls)
        MutableMapping.register(dict_type)
        cls.root = root
        cls.nested_messages = OrderedDict()
        cls.args_message = OrderedDict()
        cls.dict_type = dict_type
        cls.service = service
        cls.message_desc = message_desc
        cls.feature_name = name_path[0]
        cls.name = name_path[-1]
        cls.doc = doc_desc
        cls.field_name = getattr(message_desc, "field_name", None)
        cls.service_proto = getattr(message_desc, "service", None)
        cls.message_proto = message_desc.message
        cls.fullName = fullName
        cls.prefix = name_path[:-1]
        cls.number = getattr(message_desc, "number", None)
        cls._recipient_id = None
        cls.loglevel = logging.INFO
        cls.buffer_type = ArsdkMessageBufferType.ACK
        cls.callback_type = ArsdkMessageCallbackType.STANDARD
        if not cls.number or cls.number < 16:
            cls.loglevel = logging.DEBUG
            cls.buffer_type = ArsdkMessageBufferType.NON_ACK

        cls.timeout = TIMEOUT_BY_COMMAND.get(cls.fullName, DEFAULT_TIMEOUT)
        cls.float_tol = FLOAT_TOLERANCE_BY_FEATURE.get(
            cls.feature_name, DEFAULT_FLOAT_TOL
        )

        cls.name_path = name_path
        cls.real_args_name = list(
            map(lambda f: f.name, cls.message_proto.DESCRIPTOR.fields)
        )
        cls.args_name = list(
            name for name in cls.real_args_name if name != "selected_fields"
        )
        service_type = getattr(message_desc, "service_type", None)
        if service_type is None:
            cls.message_type = None
            cls._expectation = None
            cls._reverse_expectation = None
        elif service_type == "Command":
            cls.message_type = ArsdkMessageType.CMD
            # command message expectations need to be resolved later
            cls._expectation = None
            cls._reverse_expectation = ArsdkEventExpectation(
                cls, OrderedDict.fromkeys(cls.args_name)
            )
            cls.arsdk_message = generic["custom_cmd"]
            if not cls.number or cls.number < 16:
                cls.arsdk_message = generic["custom_cmd_non_ack"]
        else:
            cls.message_type = ArsdkMessageType.EVT
            cls._expectation = ArsdkEventExpectation(
                cls, OrderedDict.fromkeys(cls.args_name)
            )
            cls._reverse_expectation = ArsdkProtoCommandExpectation(cls)
            cls.arsdk_message = generic["custom_evt"]
            if not cls.number or cls.number < 16:
                cls.arsdk_message = generic["custom_evt_non_ack"]

        if cls.message_type is ArsdkMessageType.CMD:
            cls.args_default = OrderedDict()
        else:
            cls.args_default = OrderedDict(
                zip(cls.args_name, [None] * len(cls.args_name))
            )
        cls.args_default_str = ", ".join(
            f"{argname}={cls.args_default[argname]}"
            if argname in cls.args_default
            else argname
            for argname in cls.args_name + ["**kwds"]
        )

        cls.args_enum = OrderedDict()
        for field_desc in cls.message_proto.DESCRIPTOR.fields:
            if field_desc.type != google.protobuf.descriptor.FieldDescriptor.TYPE_ENUM:
                continue

            arg = field_desc.name
            package = field_desc.enum_type.file.package
            while package:
                try:
                    feature = feature_proto.features_package_map[package]
                    enum_full_name = (
                        feature
                        + "."
                        + field_desc.enum_type.full_name[len(package) + 1 :]
                    )
                    enum_path = enum_full_name.split(".")
                    enum = get_mapping(feature_enums, enum_path)
                    break
                except KeyError:
                    try:
                        feature = olympe_proto.features_package_map[package]
                        enum_full_name = (
                            feature
                            + "."
                            + field_desc.enum_type.full_name[len(package) + 1 :]
                        )
                        enum_path = enum_full_name.split(".")
                        enum = get_mapping(olympe_enums, enum_path)
                        break
                    except KeyError:
                        package = ".".join(package.split(".")[:-1])
            cls.args_enum[arg] = enum
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
        expectation = cls._parse_expectation(
            getattr(cls.message_desc, "on_success", None) or "None", module
        )
        if cls.message_type == ArsdkMessageType.CMD:
            cls._expectation = ArsdkProtoCommandExpectation(
                cls, expectation=expectation
            )

    def _parse_expectation(cls, expectation_str, module):
        return eval(expectation_str, module.__dict__, dict(this=ArsdkProtoThis()))

    def items(cls):
        return cls.nested_messages.items()

    def get_source(cls):
        args = ", ".join(cls.args_name + ["**kwds"])
        docstring = cls.docstring
        # The docstring needs to be correctly indented in order to be
        # interpreted just below
        docstring = "\n" + "\n".join(
            [" " * 16 + doc.strip() for doc in docstring.splitlines()]
        )
        return textwrap.dedent(
            """
            def {name}(self, {defaulted_args}):
                u\"""{docstring}
                \"""
                try:
                    return self._expect({args})
                except Exception as e:
                    import logging
                    logging.exception("")
                    raise
            """.format(
                name=cls.name,
                defaulted_args=cls.args_default_str,
                args=args,
                docstring=docstring,
            )
        )

    def _create_call(cls):
        """
        Returns a python function that sends a specific ARSDK command

        The name of the returned python function is cls.name
        The parameters of the returned function repsect the naming of arsdk-xml.
        The docstring of the returned function is also extracted from the XMLs.

        @param send_command: ArCmd object provided by the arsdk-xml parser

        """
        exec(cls.get_source(), locals())
        return locals()[cls.name]


class ArsdkProtoThis:
    def __getattr__(self, name):

        def resolve(arg_name, command_message, command_args):
            return command_args.get(arg_name)
        return functools.partial(resolve, name)


class ArsdkProtoMessage(
    ArsdkMessageBase, ProtoNestedMixin, metaclass=ArsdkProtoMessageMeta
):
    def __init__(self):
        self._reset_state()
        self._proto = ArsdkProto.get(self.root)
        self._messages = ArsdkMessages.get(self.root)
        self.nested_messages = OrderedDict()
        self.args_message = OrderedDict()
        if self._messages.nested_proto_resolved:
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
        return other

    @property
    def id(self):
        return self.__class__.id

    @property
    def recipient_id(self):
        return self.__class__.recipient_id

    def state(self):
        if self._last_event is None:
            raise RuntimeError(f"{self.fullName} state is uninitialized")
        return self._state

    def _reset_state(self):
        self._last_event = None
        self._state = OrderedDict()

    def __call__(self, *args, **kwds):
        return self._expect(*args, **kwds)

    def _expect_args(self, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with the provided
        command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        """
        default_timeout = (
            self.timeout if self.message_type is ArsdkMessageType.CMD else None
        )
        default_float_tol = self.float_tol
        timeout = kwds.pop("_timeout", default_timeout)
        float_tol = kwds.pop("_float_tol", default_float_tol)
        no_expect = kwds.pop("_no_expect", False)
        policy = kwds.pop("_policy", "check_wait")
        if isinstance(policy, (bytes, str)):
            policy = ExpectPolicy[policy]
        else:
            raise RuntimeError("policy argument must be a string")
        args = kwds
        # filter out None value
        args = remove_from_collection(args, lambda a: a is None)
        # convert enums parameters
        args = self._map_enum_type(args)
        args = self._map_message_type(args)
        return args, policy, float_tol, no_expect, timeout

    def _expect(self, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with the provided
        command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        """

        args, policy, float_tol, no_expect, timeout = self._expect_args(*args, **kwds)

        if self.service is None:
            # Non-service messages are just equivalent to mapping object
            # with protobuf format validation
            return self.dict_type(**args)

        message = self.message_proto()

        if policy != ExpectPolicy.check:
            expectations = self._expectation.copy()
            if self.message_type == ArsdkMessageType.CMD:
                expectations.no_expect(no_expect)

            expectations._fill_default_arguments(self, args)
            args_to_validate = []
            if self.message_type == ArsdkMessageType.CMD:
                for expectation in expectations:
                    if hasattr(expectation, "expected_args") and (
                        hasattr(expectation, "expected_message")) and (
                        not no_expect
                    ):
                        args_to_validate.append(
                            (
                                expectation.expected_args,
                                expectation.expected_message.message_proto(),
                            )
                        )
            else:
                args = expectations.expected_args
                args_to_validate.append((args, message))

            # Use protobuf_json_format to validate protobuf message format
            # filter out lambda ArdkProtoThis lambdas before validation
            for expected_args, message in args_to_validate:
                expected_args = self._map_enum_to_int(expected_args)
                expected_args = remove_from_collection(expected_args, callable)
                protobuf_json_format.ParseDict(expected_args, message)

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
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(self, args)
            expectations.set_float_tol(float_tol)
            expectations._fill_default_arguments(self, args)
            return expectations

    def _reverse_expect(self, *args, **kwds):
        args, policy, float_tol, no_expect, timeout = self._expect_args(
            *args, **kwds
        )

        if self.service is None:
            # Non-service messages are just equivalent to mapping object
            # with protobuf format validation
            return self.dict_type(**args)

        message = self.message_proto()

        if policy != ExpectPolicy.check:
            expectations = self._reverse_expectation.copy()
            if self.message_type == ArsdkMessageType.EVT:
                expectations.no_expect(no_expect)

            expectations._fill_default_arguments(self, args)
            args_to_validate = []
            if self.message_type == ArsdkMessageType.EVT:
                for expectation in expectations:
                    if hasattr(expectation, "expected_args") and (
                        hasattr(expectation, "expected_message")) and (
                        not no_expect
                    ):
                        args_to_validate.append(
                            (
                                expectation.expected_args,
                                expectation.expected_message.message_proto(),
                            )
                        )
            else:
                args = expectations.expected_args
                args_to_validate.append((args, message))

            # Use protobuf_json_format to validate protobuf message format
            # filter out lambda ArdkProtoThis lambdas before validation
            for expected_args, message in args_to_validate:
                expected_args = self._map_enum_to_int(expected_args)
                expected_args = remove_from_collection(expected_args, callable)
                protobuf_json_format.ParseDict(expected_args, message)

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
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(self, args)
            expectations.set_float_tol(float_tol)
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
            if name == "selected_fields":
                continue
            elif field.label == ProtoFieldLabel.Repeated._value_:
                args[name] = []
            elif name in cls.args_message:
                args[name] = cls.args_message[name].default_args()
            elif name in cls.args_enum:
                args[name] = next(iter(cls.args_enum[name]))
            else:
                args[name] = proto_type_to_python(field.type)()
        return args

    def _encode_args(self, args):
        args = self._map_set_selected_fields(self.message_proto, args)
        args = self._map_enum_to_str(args)
        if self.service_proto.DESCRIPTOR.fields_by_name[self.field_name].message_type is None:
            proto = self.service_proto(**{self.field_name: args['value']})
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
        else:
            args = OrderedDict(value=args)
        return args

    def _map_enum_type(self, args):
        if callable(args):
            return args
        args = args.copy()
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], (bytes, str)):
                args[arg] = enum[args[arg]]
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, (bytes, str)), args[arg]))
            ):
                args[arg] = tuple(enum[a] for a in args[arg])
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
        if callable(args):
            return args
        args = args.copy()
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], ArsdkProtoEnum):
                args[arg] = int(args[arg]._value_)
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, ArsdkProtoEnum), args[arg]))
            ):
                args[arg] = tuple(int(a._value_) for a in args[arg])
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
        if callable(args):
            return args
        args = args.copy()
        for arg, enum in self.args_enum.items():
            if arg not in args:
                continue
            if isinstance(args[arg], ArsdkProtoEnum):
                args[arg] = args[arg].to_upper_str()
            elif isinstance(args[arg], Iterable) and (
                all(map(lambda a: isinstance(a, ArsdkProtoEnum), args[arg]))
            ):
                args[arg] = tuple(a.to_upper_str() for a in args[arg])
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
        if callable(args):
            return args
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
            ret[k] = self._set_selected_fields(getattr(proto, k, None), v)
        return ret

    def _map_filter_selected_fields(self, proto, args):
        ret = type(args)()
        selected_fields = list(args)
        if "selected_fields" in args:
            selected_fields = list(
                map(
                    lambda i: proto.DESCRIPTOR.fields_by_number[i].name,
                    args["selected_fields"].keys(),
                )
            )
        for k, v in args.items():
            if k not in selected_fields:
                continue
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

    def _set_last_event(self, event):
        if event.id != self.id:
            raise RuntimeError(
                "Cannot set message {} last event to {}".format(
                    self.fullName, event.message.fullName
                )
            )
        self._last_event = event
        update_mapping(self._state, event.args)

    @classmethod
    def _argsmap_from_args(cls, *args, **kwds):
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
                lambda name, value: (name, cls.args_enum[name][value])
                if (name in cls.args_enum and isinstance(value, (bytes, str)))
                else (name, value),
                args.items(),
            )
        )

        args = OrderedDict(starmap(lambda k, v: (k, v), args.items()))
        return args

    @classmethod
    def _expectation_from_args(cls, *args, **kwds):
        expected_args = cls._argsmap_from_args(*args, **kwds)
        return ArsdkEventExpectation(cls, expected_args)

    @classmethod
    def _message_ref_sub(cls, messages, m):
        path = m.group("message").split(".")
        for p in (path, cls.prefix + path, [cls.feature_name] + path):
            try:
                message = get_mapping(messages.by_feature, p)
                break
            except KeyError:
                pass
        else:
            return m.group(0)
        if m.group("args") is not None:
            args = "(" + m.group("args") + ")"
        else:
            args = "()"
        return f":py:func:`{message.name}{args}<olympe.messages.{message.fullName}>`"

    @classmethod
    def _supported_doc(cls):
        if not cls.doc.support:
            return ""
        supported_devices = list(
            map(lambda s: s.split(":", maxsplit=2), cls.doc.support)
        )
        supported_devices = list(
            map(lambda s: (int(s[0], base=16), s[1:]), supported_devices)
        )
        ret = []
        for device in supported_devices:
            device_hex, *versions = device
            versions = iter(versions)
            device_str = od.string_cast(od.arsdk_device_type_str(device_hex))
            since = next(versions, None)
            until = next(versions, None)
            mapping = {
                "ANAFI4K": "Anafi/AnafiFPV",
                "ANAFI_THERMAL": "AnafiThermal",
                "SKYCTRL_3": "SkyController3",
                "ANAFI_2": "Anafi Ai",
            }
            device_str = mapping.get(device_str, device_str)
            if "anafi" in device_str.lower() or "skycontroller" in device_str.lower():
                if until:
                    ret.append(
                        "    :{}: since {} and until {} firmware release".format(
                            device_str, since, until
                        )
                    )
                else:
                    ret.append(
                        f"    :{device_str}: with an up to date firmware"
                    )
        if not ret:
            return "\n**Unsupported message**\n"
        docstring = "\nSupported by:\n"
        docstring += "\n".join(ret)
        docstring += "\n"
        return docstring

    @classmethod
    def _resolve_doc(cls, messages, module):
        if cls.doc is not None:
            cls.docstring = cls.doc.doc + "\n"
            for field_doc in cls.doc.fields_doc:
                if field_doc.name == "selected_fields":
                    continue
                cls.docstring += f"\n:param {field_doc.name}: {field_doc.doc}\n"
                if field_doc.label is ProtoFieldLabel.Repeated:
                    cls.docstring += (
                        f"\n:type {field_doc.name}: list({field_doc.type})\n"
                    )
                else:
                    cls.docstring += f"\n:type {field_doc.name}: {field_doc.type}\n"
            cls.docstring += "\n"
            cls.docstring += cls._supported_doc()

            cls.docstring = re.sub(
                r"\[(?P<message>[\w\d._]+)(\((?P<args>[^)]*)\))?\]",
                lambda m: cls._message_ref_sub(messages, m),
                cls.docstring,
            )
        else:
            cls.docstring = ""
        cls.__doc__ = cls.docstring
        cls._create_call()

    def _resolve_nested_messages(self):
        for nested_name, nested in self.__class__.nested_messages.items():
            self.nested_messages[nested_name] = nested()
        for nested_name, nested in self.__class__.args_message.items():
            self.args_message[nested_name] = nested()
