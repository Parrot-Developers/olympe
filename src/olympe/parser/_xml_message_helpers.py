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

"""
@file _xml_message_helpers.py

Helpers for initialising XML-based ARSDK message classes.

Each helper handles one coherent concern:

- _init_xml_identity     — names, IDs, feature/class/message path
- _init_xml_type_attrs   — message type, buffer type, callback type, loglevel
- _init_xml_args         — argument dicts (enum, bitfield, type, position)
- _init_xml_ctypes       — ctypes encode/decode arrays, args_default, decoded_args
- _init_common_tolerances — timeout, float_tol, int_tol (shared with proto)
- _create_callable        — exec-based __call__ compilation (shared with proto)
"""

import ctypes
import logging
import textwrap

from collections import OrderedDict
from typing import List, Optional, Tuple

import arsdkparser
import olympe_deps as od

from olympe.messaging.arsdk_enums import list_flags
from olympe.utils import DEFAULT_FLOAT_TOL, DEFAULT_INT_TOL


# ---------------------------------------------------------------------------
# Shared helpers (used by both XML and proto pipelines)
# ---------------------------------------------------------------------------


def _create_callable(cls) -> callable:
    """
    Compile and return the __call__ method for an ARSDK message class.

    Generates a Python function via exec() whose name matches cls.name, whose
    signature matches cls.args_default_str, and whose docstring is cls.docstring.
    The generated function delegates to cls._expect().

    This helper is identical for XML and proto messages; it replaces the
    duplicated ArsdkMessageMeta._create_call / ArsdkProtoMessageMeta._create_call.

    @param cls: the ARSDK message class being compiled.
    @return: a Python function suitable for use as cls.__call__.
    @rtype: callable
    """
    args = ", ".join(cls.args_name + ["**kwds"])
    docstring = cls.docstring
    # Re-indent the docstring so it aligns correctly inside the generated
    # function body (16 spaces = 4 levels of 4-space indent).  Without this,
    # help() and Sphinx would render the raw unindented RST as a wall of text.
    docstring = "\n" + "\n".join(
        [" " * 16 + doc.strip() for doc in docstring.splitlines()]
    )
    source = textwrap.dedent(
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
    # exec() is intentional: we need the generated __call__ to carry the real
    # message name (e.g. "TakeOff") as its __name__ AND to embed the actual
    # per-message default values in the signature string.  A plain lambda or
    # functools.wraps cannot achieve either from a runtime-built string.
    # The function is extracted from locals() immediately after exec().
    exec(source, locals())  # noqa: S102
    fn = locals()[cls.name]
    # Store the generated source string on the class so that
    # ArsdkMessageBase.get_source() can return it.
    # module_loader.ModuleLoader._get_source() calls item.get_source() on every
    # ArsdkMessageBase subclass when building the synthetic olympe.messages.*
    # module sources that Sphinx autodoc traverses for the API reference
    # (see ModuleLoader._get_source in module_loader.py).
    # Before the refactoring that extracted _create_callable into this shared
    # helper, the source string was local to ArsdkMessageMeta.__new__ and
    # ArsdkProtoMessageMeta.__new__, where get_source() could reach it directly.
    # Now that both XML and proto pipelines share this function, _call_source_
    # is the bridge between _create_callable and get_source().
    cls._call_source_ = source
    return fn


def _init_common_tolerances(
    cls, timeout_map: dict, float_tol_map: dict, int_tol_map: dict, default_timeout: int
) -> None:
    """
    Initialise timeout, float tolerance and int tolerance on a message class.

    Shared between XML and proto message init paths.

    @param cls: the ARSDK message class to initialise; must already have
        ``cls.fullName`` and ``cls.feature_name`` set.
    @param timeout_map: dict mapping fullName -> timeout (TIMEOUT_BY_COMMAND).
    @param float_tol_map: dict mapping feature_name -> float tolerance tuple
        (FLOAT_TOLERANCE_BY_FEATURE).
    @param int_tol_map: dict mapping feature_name -> int tolerance
        (INT_TOLERANCE_BY_FEATURE).
    @param default_timeout: fallback timeout value (DEFAULT_TIMEOUT).
    """
    cls.timeout = timeout_map.get(cls.fullName, default_timeout)
    cls.float_tol = float_tol_map.get(cls.feature_name, DEFAULT_FLOAT_TOL)
    cls.int_tol = int_tol_map.get(cls.feature_name, DEFAULT_INT_TOL)


# ---------------------------------------------------------------------------
# XML-specific helpers
# ---------------------------------------------------------------------------


def _init_xml_identity(
    cls, name_path: List[Optional[str]], id_path: List[Optional[int]]
) -> None:
    """
    Initialise the identity attributes of an XML-based ARSDK message class.

    Sets cls.fullName, cls.prefix, cls.name_path, cls.id_path,
    cls.feature_name, cls.class_name, cls.name, cls.feature_id,
    cls.class_id, cls.msg_id, cls.id and cls.id_name.

    @param cls: the ARSDK message class to initialise.
    @param name_path: full XML path as a list of names:
        ["<feature_name>", "<class_name>" or None, "message_name"].
    @type name_path: list[str | None]
    @param id_path: full XML path as a list of integer IDs:
        [feature_id, class_id or None, msg_id].
    @type id_path: list[int | None]
    """
    # Deferred import to avoid a circular dependency: messages.py imports this
    # module at the top level, so importing from messages here would create a
    # cycle.  Importing inside the function body is safe because Python caches
    # the module after the first import; the overhead is negligible.
    from olympe.messaging.arsdk_messages import ARSDK_CLS_DEFAULT_ID

    full_path: Tuple[str, ...] = tuple(filter(None, name_path))
    cls.fullName = ".".join(full_path)
    cls.prefix = full_path[:-1]
    cls.name_path = name_path
    cls.id_path = id_path
    cls.feature_name = name_path[0]
    cls.class_name = name_path[1]
    cls.name = name_path[2]
    cls.feature_id = id_path[0]
    # class_id defaults to 0 (ARSDK_CLS_DEFAULT_ID) for messages that have no
    # intermediate class grouping in the XML spec.
    cls.class_id = id_path[1] or ARSDK_CLS_DEFAULT_ID
    cls.msg_id = id_path[2]
    # The 32-bit message ID layout (from arsdk spec):
    #   bits 31-24: feature_id  (8 bits)
    #   bits 23-16: class_id    (8 bits)
    #   bits 15-0 : msg_id      (16 bits)
    cls.id = cls.feature_id << 24 | cls.class_id << 16 | cls.msg_id
    cls.id_name = "-".join(map(str, filter(lambda x: x is not None, id_path)))


def _init_xml_type_attrs(cls, obj) -> None:
    """
    Initialise message type, buffer type, callback type and loglevel for an
    XML-based ARSDK message class.

    Applies the class_name heuristic ("state"/"event" in the name implies EVT)
    and the loglevel override list for high-frequency messages.

    @param cls: the ARSDK message class to initialise; must already have
        _init_xml_identity applied.
    @param obj: the arsdkparser message descriptor (ArCmd or ArEvt).
    """
    from olympe.messaging.arsdk_messages import (
        ArsdkMessageType,
        ArsdkMessageBufferType,
        ArsdkMessageCallbackType,
    )

    cls.obj = obj

    cls.callback_type = ArsdkMessageCallbackType.from_arsdk(obj.listType)
    cls.message_type = ArsdkMessageType.from_arsdk(type(obj))
    cls.buffer_type = ArsdkMessageBufferType.from_arsdk(obj.bufferType)

    cls.loglevel = logging.INFO
    # Non-ACK events are typically high-frequency telemetry (position, attitude,
    # etc.).  Logging them at DEBUG avoids flooding the log in normal operation.
    if (
        cls.message_type is ArsdkMessageType.EVT
        and cls.buffer_type is not ArsdkMessageBufferType.ACK
    ):
        cls.loglevel = logging.DEBUG

    # Some ACK events are also high-frequency (e.g. mapper items during a full
    # mapping dump).  They are listed explicitly because the buffer type alone
    # is not enough to detect them.
    # Kept inline (not a module-level constant) because this function is called
    # exactly once per message class at startup; the frozenset is cheap to
    # create and the O(1) lookup cost is negligible.
    _HIGH_FREQUENCY_MESSAGES = frozenset(
        (
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
        )
    )
    if cls.fullName in _HIGH_FREQUENCY_MESSAGES:
        cls.loglevel = logging.DEBUG

    # class_name heuristic: "state" or "event" in the class name implies EVT.
    # This is applied LAST so it can override the arsdkparser-derived type for
    # features like ardrone3 where command class names contain "State" but the
    # XML spec declares the messages as commands rather than events.
    if cls.class_name is not None and (
        "state" in cls.class_name.lower() or "event" in cls.class_name.lower()
    ):
        cls.message_type = ArsdkMessageType.EVT


def _init_xml_args(cls, obj, enums, default_arguments: Optional[dict] = None) -> None:
    """
    Populate all argument-related attributes of an XML-based ARSDK message
    class from the arsdkparser descriptor.

    Sets cls.args_pos, cls.args_enum, cls.args_bitfield, cls.args_name,
    cls.key_name, cls.callback_type (MAP/LIST overrides), cls.args_type,
    cls.args_default and cls.args_default_str.

    @param cls: the ARSDK message class to initialise; must already have
        _init_xml_identity and _init_xml_type_attrs applied.
    @param obj: the arsdkparser message descriptor (ArCmd or ArEvt).
    @param enums: the ArsdkEnums mapping for the feature root.
    @type enums: olympe.messaging.arsdk_enums.ArsdkEnums or compatible mapping.
    @param default_arguments: optional dict mapping fullName -> OrderedDict of
        default argument values (ArsdkMessages._default_arguments).
    """
    from olympe.messaging.arsdk_messages import (
        ArsdkMessageType,
        ArsdkMessageCallbackType,
    )

    cls.args_pos = OrderedDict()
    cls.args_enum = OrderedDict()
    cls.args_bitfield = OrderedDict()

    for i, arg in enumerate(obj.args):
        cls.args_pos[arg.name] = i
        if isinstance(arg.argType, arsdkparser.ArEnum):
            cls.args_enum[arg.name] = enums[cls.feature_name][arg.argType.name]
        elif isinstance(arg.argType, arsdkparser.ArBitfield):
            cls.args_bitfield[arg.name] = enums[cls.feature_name][
                arg.argType.enum.name
            ]._bitfield_type_

    cls.args_name = [arg.name for arg in obj.args]

    cls.key_name = None
    if obj.listType == arsdkparser.ArCmdListType.MAP:
        cls.key_name = obj.mapKey and obj.mapKey.name or obj.args[0].name
    # Workaround for arsdk-xml features that use a MAP-like pattern but do
    # not declare the listType="MAP_ITEM" attribute.  We infer it from the
    # presence of a well-known key argument name combined with the feature name
    # (to avoid false positives in other features that happen to have the same
    # argument name by coincidence).
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

    cls.args_type = OrderedDict()
    for argname, ar_arg in zip(cls.args_name, obj.args):
        cls.args_type[argname] = _ar_argtype_to_python(cls, argname, ar_arg.argType)

    # CMD messages have explicit default values (from _default_arguments);
    # EVT messages default all args to None so any value matches when used in
    # an expectation (None == "don't care").
    if cls.message_type is ArsdkMessageType.CMD:
        cls.args_default = (default_arguments or {}).get(cls.fullName, OrderedDict())
    else:
        cls.args_default = OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))
    # NOTE: for EVT LIST/MAP messages this is a *tentative* value.
    # _init_xml_ctypes may add a synthetic "list_flags" argument and will
    # rebuild both args_default and args_default_str after the fixup.
    cls.args_default_str = ", ".join(
        (
            f"{argname}={cls.args_default[argname]}"
            if argname in cls.args_default
            else argname
        )
        for argname in cls.args_name + ["**kwds"]
    )


def _init_xml_ctypes(cls) -> None:
    """
    Populate the ctypes encode/decode arrays for an XML-based ARSDK message.

    Requires that _init_xml_identity, _init_xml_type_attrs, and _init_xml_args
    have already been applied.

    Sets cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args,
    cls.arsdk_desc, cls.decode_ctypes_args, cls.decoded_args and
    cls.decoded_args_type.

    Also applies the ``list_flags`` fixup for LIST_ITEM/MAP_ITEM event messages
    that are missing the field in the descriptor.

    @param cls: the ARSDK message class to initialise.
    """
    from olympe.messaging.arsdk_messages import (
        ArsdkMessageType,
        ArsdkMessageCallbackType,
    )

    if cls.obj.args:
        cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args = map(
            list,
            zip(
                *(
                    _ar_argtype_encode_type(cls, ar_arg.argType)
                    for ar_arg in cls.obj.args
                )
            ),
        )
    else:
        cls.arsdk_type_args = []
        cls.arsdk_value_attr = []
        cls.encode_ctypes_args = []

    # arsdk_desc is the *C runtime* descriptor for the message (from libarsdk).
    # We use it as the authoritative source for decoding (decode_ctypes_args)
    # rather than re-deriving from arsdkparser because the C wire format may
    # differ from the XML spec in edge cases: e.g. all enum values are int32 on
    # the wire regardless of their declared range in the XML.
    # encode_ctypes_args (built above from arsdkparser) drives the Python→C
    # encoding path and can tolerate those differences since we control the
    # input.
    cls.arsdk_desc = od.arsdk_cmd_find_desc(
        od.struct_arsdk_cmd.bind(
            {
                "prj_id": cls.feature_id,
                "cls_id": cls.class_id,
                "cmd_id": cls.msg_id,
            }
        )
    )

    _DECODE_MAP = {
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
    cls.decode_ctypes_args = []
    for i in range(cls.arsdk_desc.contents.arg_desc_count):
        arg_type = cls.arsdk_desc.contents.arg_desc_table[i].type
        cls.decode_ctypes_args.append(_DECODE_MAP[arg_type])

    # Some LIST_ITEM/MAP_ITEM event messages carry a "list_flags" bitfield in
    # the binary protocol but do NOT declare it in the XML spec.  We synthesise
    # it here so the decoder and call signature stay consistent.
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
        # Rebuild args_default and args_default_str so that list_flags=None
        # appears in the call signature.  In the original __new__, these were
        # computed AFTER the fixup; we must do the same here since
        # _init_xml_args runs before _init_xml_ctypes.
        cls.args_default = OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))
        cls.args_default_str = ", ".join(
            (
                f"{argname}={cls.args_default[argname]}"
                if argname in cls.args_default
                else argname
            )
            for argname in cls.args_name + ["**kwds"]
        )

    cls.decoded_args = list(
        map(lambda ctype: ctypes.pointer(ctype()), cls.decode_ctypes_args)
    )
    cls.decoded_args_type = list(
        map(lambda ctype: ctypes.POINTER(ctype), cls.decode_ctypes_args)
    )


# ---------------------------------------------------------------------------
# Internal type-mapping helpers (previously methods on ArsdkMessageMeta)
# ---------------------------------------------------------------------------


def _ar_arsdk_encode_type_info(cls, ar_argtype) -> Tuple:
    """
    Return the (arsdk_type_const, value_attr_str, ctypes_type) triple for an
    arsdkparser argtype.

    @param cls: the ARSDK message class (used only for the BINARY lambda
        closure referencing _ar_arsdk_encode_binary).
    @param ar_argtype: arsdkparser argtype constant.
    @return: triple of (ARSDK type constant, value attribute string, ctypes
        type or encoder callable).
    @rtype: tuple
    """
    # _MAP is defined inside the function (not at module level) because the
    # BINARY entry must close over the *current* cls to call
    # _ar_arsdk_encode_binary(cls, ...).  A module-level dict would capture a
    # stale or wrong cls, and changing the signature of the encoder would break
    # the uniform calling convention used by _encode_args.
    _MAP = {
        arsdkparser.ArArgType.I8: (od.ARSDK_ARG_TYPE_I8, "i8", ctypes.c_int8),
        arsdkparser.ArArgType.U8: (od.ARSDK_ARG_TYPE_U8, "u8", ctypes.c_uint8),
        arsdkparser.ArArgType.I16: (od.ARSDK_ARG_TYPE_I16, "i16", ctypes.c_int16),
        arsdkparser.ArArgType.U16: (od.ARSDK_ARG_TYPE_U16, "u16", ctypes.c_uint16),
        arsdkparser.ArArgType.I32: (od.ARSDK_ARG_TYPE_I32, "i32", ctypes.c_int32),
        arsdkparser.ArArgType.U32: (od.ARSDK_ARG_TYPE_U32, "u32", ctypes.c_uint32),
        arsdkparser.ArArgType.I64: (od.ARSDK_ARG_TYPE_I64, "i64", ctypes.c_int64),
        arsdkparser.ArArgType.U64: (od.ARSDK_ARG_TYPE_U64, "u64", ctypes.c_uint64),
        arsdkparser.ArArgType.FLOAT: (od.ARSDK_ARG_TYPE_FLOAT, "f32", ctypes.c_float),
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
            lambda t: _ar_arsdk_encode_binary(cls, t),
        ),
    }
    return _MAP[ar_argtype]


def _ar_arsdk_encode_binary(cls, buf) -> od.struct_arsdk_binary:
    """
    Wrap a bytes buffer into an arsdk_binary struct for encoding.

    @param cls: unused; present for symmetry with the original method API.
    @param buf: the raw bytes buffer to wrap.
    @return: arsdk_binary struct pointing to the buffer.
    @rtype: od.struct_arsdk_binary
    """
    buf_type = ctypes.c_char * len(buf)
    return od.struct_arsdk_binary(
        ctypes.cast(buf_type.from_buffer(buf), od.POINTER_T(None)), len(buf)
    )


def _ar_argtype_encode_type(cls, ar_argtype) -> Tuple:
    """
    Return the ctypes type (and associated constants) for an arsdkparser
    argtype, handling ArEnum and ArBitfield transparently.

    @param cls: the ARSDK message class (forwarded to _ar_arsdk_encode_type_info).
    @param ar_argtype: arsdkparser argtype (may be ArEnum, ArBitfield or a
        plain ArArgType constant).
    @return: triple of (ARSDK type constant, value attribute string, ctypes
        type or encoder callable).
    @rtype: tuple
    """
    # ArEnum is handled here directly (not delegated to _ar_arsdk_encode_type_info)
    # because ArEnum instances are not plain ArArgType constants and therefore
    # not present in _ar_arsdk_encode_type_info's _MAP.  All enum args are
    # encoded as int32 on the wire regardless of the underlying C enum size.
    if isinstance(ar_argtype, arsdkparser.ArEnum):
        return od.ARSDK_ARG_TYPE_ENUM, "i32", ctypes.c_int32
    # ArBitfield wraps a numeric base type; we encode it the same way as its
    # underlying integer type (.btfType).
    if isinstance(ar_argtype, arsdkparser.ArBitfield) or (
        ar_argtype == arsdkparser.ArArgType.BITFIELD
    ):
        return _ar_arsdk_encode_type_info(cls, ar_argtype.btfType)
    return _ar_arsdk_encode_type_info(cls, ar_argtype)


def _ar_argtype_to_python(cls, argname: str, ar_argtype):
    """
    Return the Python type associated to an arsdkparser argtype.

    @param cls: the ARSDK message class (used to look up args_enum and
        args_bitfield).
    @param argname: the argument name.
    @param ar_argtype: arsdkparser argtype constant.
    @return: the corresponding Python type, or None if not mappable.
    """
    if argname in cls.args_enum:
        return cls.args_enum[argname]
    if argname in cls.args_bitfield:
        return cls.args_bitfield[argname]
    _INT_TYPES = (
        arsdkparser.ArArgType.I8,
        arsdkparser.ArArgType.U8,
        arsdkparser.ArArgType.I16,
        arsdkparser.ArArgType.U16,
        arsdkparser.ArArgType.I32,
        arsdkparser.ArArgType.U32,
        arsdkparser.ArArgType.I64,
        arsdkparser.ArArgType.U64,
    )
    if ar_argtype in _INT_TYPES:
        return int
    if ar_argtype in (arsdkparser.ArArgType.DOUBLE, arsdkparser.ArArgType.FLOAT):
        return float
    if ar_argtype == arsdkparser.ArArgType.STRING:
        return str
    return None
