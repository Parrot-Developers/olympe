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
@file _proto_message_helpers.py

Helpers for initialising protobuf-based ARSDK message classes.

Each helper handles one coherent concern:

- _init_proto_identity   — names, service descriptor, message descriptor,
                           real_args_name / args_name
- _init_proto_type_attrs — message type, buffer type, callback type, loglevel,
                           expectations skeleton, arsdk_message alias
- _init_proto_args       — args_default, args_default_str, args_enum (via
                           find_enum closure)
"""

import google.protobuf.descriptor
import logging

from builtins import str as builtin_str
from collections import OrderedDict
from collections.abc import MutableMapping
from typing import List, Optional

from olympe.messaging.arsdk_events import ArsdkMessageArgs
from olympe.utils import get_mapping


# ---------------------------------------------------------------------------
# _init_proto_identity
# ---------------------------------------------------------------------------


def _init_proto_identity(
    cls,
    root: str,
    name_path: List[str],
    service,
    svc_message_desc,
    message_desc,
) -> Optional[str]:
    """
    Initialise the identity attributes of a proto ARSDK message class.

    Sets cls.root, cls.nested_messages, cls.args_message, cls.args_map,
    cls.dict_type, cls.service, cls.svc_message_desc, cls.message_desc,
    cls.feature_name, cls.name, cls.doc, cls.field_name, cls.service_proto,
    cls.message_proto, cls.number, cls.fields, cls.fullName, cls.prefix,
    cls._recipient_id, cls.name_path, cls.real_args_name and cls.args_name.

    @param cls: the proto message class being initialised; the class object
        itself (already created by type.__new__ in the metaclass).
    @param root: the arsdk root package name, e.g. ``"olympe"`` or
        ``"olympe.airsdk"``.
    @param name_path: full dotted path as a list, e.g.
        ``["battery", "Alert", "level"]``.
    @type name_path: list[str]
    @param service: ArsdkProtoService descriptor, or None for nested messages.
    @param svc_message_desc: ArsdkProtoServiceMessage descriptor, or None for
        messages that are not direct service messages.
    @param message_desc: ArsdkProtoMessage descriptor.
    @return: service_type string (``"Command"``, ``"Event"``, or None) for use
        in _init_proto_type_attrs.
    @rtype: str | None
    """
    fullName = ".".join(name_path)

    # dict_type is a lightweight ArsdkMessageArgs subclass specific to this
    # message; it is used as a typed mapping returned by _expect() for
    # non-service (nested) proto messages.
    dict_type = type.__new__(type, builtin_str(fullName), (ArsdkMessageArgs,), {})
    MutableMapping.register(cls)
    MutableMapping.register(dict_type)

    cls.root = root
    cls.nested_messages = OrderedDict()
    cls.args_message = OrderedDict()
    cls.args_map = OrderedDict()
    cls.dict_type = dict_type
    cls.service = service
    cls.svc_message_desc = svc_message_desc
    cls.message_desc = message_desc
    cls.feature_name = name_path[0]
    cls.name = name_path[-1]
    cls.doc = message_desc.doc

    if svc_message_desc is not None:
        # This message is a direct member of a protobuf service (Command or
        # Event); it carries wire metadata (field_name, number, etc.).
        cls.field_name = svc_message_desc.field_name
        cls.service_proto = svc_message_desc.service
        cls.message_proto = svc_message_desc.message
        cls.number = svc_message_desc.number
        cls.fields = svc_message_desc.fields
        service_type: Optional[str] = svc_message_desc.service_type
    else:
        # Nested or orphan message: no wire metadata, treated purely as a
        # typed mapping (validated via protobuf_json_format but never sent
        # directly over the transport).
        cls.field_name = None
        cls.service_proto = None
        cls.message_proto = message_desc.message
        cls.number = None
        cls.fields = message_desc.fields
        service_type = None

    cls.fullName = fullName
    cls.prefix = name_path[:-1]
    cls._recipient_id = None
    cls.name_path = name_path
    cls.real_args_name = list(
        map(lambda f: f.name, cls.message_proto.DESCRIPTOR.fields)
    )
    # "selected_fields" is a protobuf sentinel used internally to track which
    # fields were explicitly set by the caller (see selected_fields handling in
    # ArsdkProtoMessage).  It is excluded from the public args_name so that
    # user-facing call signatures and expectations never mention it.
    cls.args_name = [name for name in cls.real_args_name if name != "selected_fields"]

    return service_type


# ---------------------------------------------------------------------------
# _init_proto_type_attrs
# ---------------------------------------------------------------------------


def _init_proto_type_attrs(
    cls,
    service_type: Optional[str],
    generic: Optional[dict] = None,
) -> None:
    """
    Initialise message type, buffer type, callback type, loglevel, timeout,
    float/int tolerances and expectations skeleton for a proto message class.

    @param cls: the proto message class being initialised; must already have
        _init_proto_identity applied.
    @param service_type: the value returned by _init_proto_identity.  One of
        ``"Command"``, ``"Event"``, or None (for nested/orphan messages).
    @param generic: optional reference to
        ``ArsdkMessages.get("olympe").by_feature["generic"]``; resolved
        automatically if None.
    """
    # Deferred imports to avoid circular dependency at module level:
    # messages.py imports this module, so we cannot import from messages at
    # the top of this file.
    from olympe.messaging.arsdk_messages import (
        ArsdkMessages,
        ArsdkMessageType,
        ArsdkMessageBufferType,
        ArsdkMessageCallbackType,
        TIMEOUT_BY_COMMAND,
        DEFAULT_TIMEOUT,
        FLOAT_TOLERANCE_BY_FEATURE,
        INT_TOLERANCE_BY_FEATURE,
    )
    from olympe.messaging.arsdk_expectations import (
        ArsdkEventExpectation,
        ArsdkProtoCommandExpectation,
    )
    from ._xml_message_helpers import _init_common_tolerances

    # "generic" contains the low-level arsdk wrapper messages used to
    # transport proto payloads over the classic arsdk wire format.
    # custom_cmd / custom_cmd_non_ack are used for Commands;
    # custom_evt / custom_evt_non_ack are used for Events.
    if generic is None:
        generic = ArsdkMessages.get("olympe").by_feature["generic"]

    cls.loglevel = logging.INFO
    cls.buffer_type = ArsdkMessageBufferType.ACK
    cls.key_name: Optional[str] = None
    cls.callback_type = ArsdkMessageCallbackType.STANDARD

    if cls.svc_message_desc is not None and cls.svc_message_desc.map_key is not None:
        cls.callback_type = ArsdkMessageCallbackType.MAP
        cls.key_name = cls.svc_message_desc.map_key

    # Messages with a number < 16 (or no number at all) are non-acknowledged
    # and logged at DEBUG to avoid flooding the log.
    if cls.number is None or cls.number < 16:
        cls.loglevel = logging.DEBUG
        cls.buffer_type = ArsdkMessageBufferType.NON_ACK

    _init_common_tolerances(
        cls,
        TIMEOUT_BY_COMMAND,
        FLOAT_TOLERANCE_BY_FEATURE,
        INT_TOLERANCE_BY_FEATURE,
        DEFAULT_TIMEOUT,
    )

    if service_type is None:
        # Nested/orphan message: no expectations — it is used as a structured
        # argument sub-type, never sent or awaited directly.
        cls.message_type = None
        cls._expectation = None
        cls._reverse_expectation = None
    elif service_type == "Command":
        cls.message_type = ArsdkMessageType.CMD
        # _expectation is None here (phase 1); it will be populated by
        # _resolve_expectations (phase 2) once on_success is available.
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
        # _reverse_expectation lets the user treat an EVT as a CMD in
        # reverse-mode expectations (drone(SomeEvent()) blocks until sent).
        cls._reverse_expectation = ArsdkProtoCommandExpectation(cls)
        cls.arsdk_message = generic["custom_evt"]
        if not cls.number or cls.number < 16:
            cls.arsdk_message = generic["custom_evt_non_ack"]


# ---------------------------------------------------------------------------
# _init_proto_args
# ---------------------------------------------------------------------------


def _init_proto_args(
    cls,
    feature_proto,
    olympe_proto,
    feature_enums,
    olympe_enums,
) -> None:
    """
    Initialise args_default, args_default_str and args_enum for a proto message
    class.

    Resolves enum types by walking up the protobuf package hierarchy, looking
    first in *feature_proto* then in *olympe_proto*.

    @param cls: the proto message class being initialised; must already have
        _init_proto_identity and _init_proto_type_attrs applied.
    @param feature_proto: ArsdkProto instance for the current root
        (e.g. ArsdkProto.get(root)).
    @param olympe_proto: ArsdkProto instance for the olympe root
        (ArsdkProto.get("olympe")).
    @param feature_enums: ArsdkEnums mapping for the current root.
    @param olympe_enums: ArsdkEnums mapping for the olympe root.
    """
    # All proto fields — CMD and EVT alike — default to None.
    # For CMD: proto3 has no required fields; None means the field is left
    #   unset in the outgoing message so the drone receives the proto3 zero
    #   value for that type.  Callers that do not care about a field simply
    #   omit it.
    # For EVT: None == "don't care" in expectations; the expectation engine
    #   ignores None-valued fields.
    cls.args_default = OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))

    cls.args_default_str = ", ".join(
        (
            f"{argname}={cls.args_default[argname]}"
            if argname in cls.args_default
            else argname
        )
        for argname in cls.args_name + ["**kwds"]
    )

    cls.args_enum = OrderedDict()

    def _find_enum(field_desc):
        """
        Resolve the ArsdkProtoEnum for a protobuf enum field descriptor.

        Walks up the package hierarchy of field_desc.enum_type.file.package,
        searching first in feature_proto then in olympe_proto at each level.

        The two-pass lookup (feature first, then olympe) handles cross-package
        enum references: a feature proto file may reuse enum types defined in
        the shared olympe proto package.  Walking up the package path handles
        nested package names (e.g. "parrot.drone.camera.v2" → "parrot.drone.camera"
        → ...) until a match is found.

        @param field_desc: a protobuf FieldDescriptor with TYPE_ENUM.
        @return: the matching ArsdkProtoEnum class.
        @raises ValueError: if the enum cannot be resolved.
        """
        package = field_desc.enum_type.file.package
        while package:
            try:
                feature = feature_proto.features_package_map[package]
                enum_full_name = (
                    feature + "." + field_desc.enum_type.full_name[len(package) + 1 :]
                )
                return get_mapping(feature_enums, enum_full_name.split("."))
            except KeyError:
                pass
            try:
                feature = olympe_proto.features_package_map[package]
                enum_full_name = (
                    feature + "." + field_desc.enum_type.full_name[len(package) + 1 :]
                )
                return get_mapping(olympe_enums, enum_full_name.split("."))
            except KeyError:
                pass
            # Climb one level up the package path and retry.
            package = ".".join(package.split(".")[:-1])
        raise ValueError(f"Cannot find definition of {field_desc}")

    for field_desc in cls.message_proto.DESCRIPTOR.fields:
        if field_desc.type != google.protobuf.descriptor.FieldDescriptor.TYPE_ENUM:
            continue
        cls.args_enum[field_desc.name] = _find_enum(field_desc)
