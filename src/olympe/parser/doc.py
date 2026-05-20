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
@file doc.py

Sphinx RST docstring generation for ARSDK XML messages and protobuf messages.

This module centralises all doc-building helpers that were previously scattered
across ArsdkMessageMeta (_py_ar_* methods) and ArsdkProtoMessage
(_supported_doc, _resolve_doc, ...) in messages.py.  It provides two
independent pipelines:

- **XML pipeline**: functions prefixed ``xml_`` build RST from arsdkparser
  objects (ArCmd, ArArg, ArComment, ...).
- **Proto pipeline**: functions prefixed ``proto_`` build RST from
  ArsdkProtoMessageDoc descriptors (olympe.parser.proto).

Both pipelines share _DEVICE_NAME_MAP and _parse_device_support.
"""

import re
import textwrap

from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

import arsdkparser
import olympe_deps as od

from olympe.messaging.arsdk_enums import ArsdkEnum, ArsdkBitfield
from olympe.utils import string_from_arsdkxml
from .proto import ProtoFieldLabel


# ---------------------------------------------------------------------------
# Shared constant
# ---------------------------------------------------------------------------

# Human-readable device name overrides for the raw strings returned by
# arsdk_device_type_str().  The C library uses internal enum names (e.g.
# "ANAFI4K") that differ from the marketing names shown in the documentation.
# Previously this dict was duplicated in two places in messages.py;
# centralising it here ensures both pipelines stay in sync.
_DEVICE_NAME_MAP: Dict[str, str] = {
    "ANAFI4K": "ANAFI",
    "ANAFI_THERMAL": "ANAFI Thermal",
    "ANAFI_USA": "ANAFI USA",
    "SKYCTRL_3": "SkyController3",
    "ANAFI_2": "ANAFI Ai",
    "SKYCTRL_4": "SkyController4",
}


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _parse_device_support(support_strings: List[str]) -> List[Tuple[str, List[str]]]:
    """
    Parse a list of semicolon-separated device-support strings into structured
    (device_name, versions) pairs.

    Each element of *support_strings* is a raw value from the arsdk-xml or
    proto ``support`` attribute, e.g. ``"0x0901:1.2.3,1.2.4;0x0919:"``.

    @param support_strings: list of raw support attribute strings.
    @type support_strings: list[str]
    @return: list of (device_name, versions) tuples where device_name is the
        human-readable string after applying _DEVICE_NAME_MAP and versions is a
        (possibly empty) list of version strings.
    @rtype: list[tuple[str, list[str]]]
    """
    result: List[Tuple[str, List[str]]] = []
    for support_str in support_strings:
        # Each support_str may contain multiple semicolon-separated entries,
        # one per device: "0x0901:1.2.3,1.2.4;0x0919:".
        for entry in support_str.split(";"):
            parts = entry.split(":")
            try:
                device_hex = int(parts[0], base=16)
            except (ValueError, IndexError):
                continue
            versions: List[str] = []
            if len(parts) > 1 and parts[1]:
                versions = parts[1].split(",")
            device_str = od.string_cast(od.arsdk_device_type_str(device_hex))
            device_str = _DEVICE_NAME_MAP.get(device_str, device_str)
            result.append((device_str, versions))
    return result


# ---------------------------------------------------------------------------
# XML pipeline
# ---------------------------------------------------------------------------


def xml_triggered(triggered: str) -> str:
    """
    Format the ``triggered`` attribute from arsdk-xml as RST.

    @param triggered: raw ``triggered`` string from the arsdkparser doc object.
    @return: formatted RST string prefixed with ``"Triggered: "``.
    @rtype: str
    """
    ret = string_from_arsdkxml(triggered)
    if not ret.startswith("Triggered: "):
        ret = "Triggered: " + ret
    return textwrap.fill(ret, break_long_words=False)


def xml_supported(cls, supported_devices: str, deprecated: bool) -> str:
    """
    Build the ``**Supported by**`` RST section for an XML-based ARSDK message.

    Parses the raw ``support`` attribute from arsdk-xml, maps device hex IDs
    to human-readable names via _DEVICE_NAME_MAP, and returns RST.

    @param cls: the ARSDK message class being documented; must expose
        ``cls.feature_name`` and ``cls.fullName``.
    @param supported_devices: raw ``support`` attribute string from arsdk-xml.
    @param deprecated: True if the message is marked as deprecated.
    @return: RST string describing device support, or an unsupported/deprecated
        notice.
    @rtype: str
    """
    unsupported_notice = "**Unsupported message**"
    if cls.feature_name != "debug":
        unsupported_notice += (
            "\n\n.. todo::\n    Remove unsupported message {}\n".format(cls.fullName)
        )
    deprecation_notice = (
        "**Deprecated message**\n\n.. warning::\n    "
        "This message is deprecated and should no longer be used"
    )
    if deprecated:
        unsupported_notice += "\n\n" + deprecation_notice

    if not supported_devices:
        return unsupported_notice

    raw = string_from_arsdkxml(supported_devices)
    if raw == "drones":
        return "**Supported by every drone product**"
    if raw == "none":
        return unsupported_notice

    try:
        # Pass as a single-element list; _parse_device_support handles the
        # internal semicolon splitting.
        parsed = _parse_device_support([raw])
    except Exception:
        return unsupported_notice

    lines: List[str] = []
    for device_str, versions in parsed:
        if "anafi" in device_str.lower() or "skycontroller" in device_str.lower():
            if versions:
                lines.append(
                    "    :{}: for firmware release versions {}".format(
                        device_str, ", ".join(versions)
                    )
                )
            else:
                lines.append(f"    :{device_str}: with an up to date firmware")

    if not lines:
        return unsupported_notice

    ret = "\n\n" + "\n".join(lines) + "\n\n"
    ret = "**Supported by**: " + ret
    if deprecated:
        ret += "\n\n" + deprecation_notice
    return ret


def xml_arg_docstring(cls, ar_arg) -> str:
    """
    Build an RST definition list entry for a single arsdk-xml argument.

    @param cls: the ARSDK message class being documented.
    @param ar_arg: arsdkparser argument descriptor (ArArg).
    @return: RST definition list entry string.
    @rtype: str
    """
    # Each branch assigns type_str, a plain string describing the argument type.
    # Previously the variable was named type_ and was passed through the now-deleted
    # xml_arg_directive("type", ...) helper, which formatted it as a
    # ":type name: ..." RST field-list line.  xml_arg_directive was removed because
    # we no longer emit field-list directives (see the output comment below).
    if isinstance(ar_arg.argType, int):
        type_str = arsdkparser.ArArgType.TO_STRING[ar_arg.argType]
    elif isinstance(ar_arg.argType, arsdkparser.ArBitfield):
        enum = ":py:class:`olympe.enums.{}.{}`".format(
            ".".join(cls.prefix),
            cls.args_bitfield[ar_arg.name]._enum_type_.__name__,
        )
        type_str = "BitfieldOf({}, {})".format(
            enum,
            arsdkparser.ArArgType.TO_STRING[ar_arg.argType.btfType],
        )
    elif isinstance(ar_arg.argType, arsdkparser.ArEnum):
        type_str = ":py:class:`olympe.enums.{}.{}`".format(
            ".".join(cls.prefix), cls.args_enum[ar_arg.name].__name__
        )
    else:
        raise RuntimeError(f"Unknown argument type {type(ar_arg.argType)}")

    # Output: RST definition list entry "name : type\n    description".
    # We do NOT use :type name: / :param name: RST field-list directives because
    # Sphinx renders them as <dl class="field-list"> HTML and the furo theme applies
    # text-transform: uppercase to dl.field-list > dt — turning "Parameters" into
    # "PARAMETERS" in the rendered page.
    # A plain RST definition list produces a regular <dl> (no class="field-list")
    # that furo does not uppercase.
    # ".. rubric:: Parameters" was also tried but furo applies text-transform to
    # p.rubric as well; **Parameters** (bold paragraph) is the only option that
    # avoids both furo CSS rules.
    # The 4-space indent on desc here is doubled to 8 by textwrap.indent in
    # xml_args_docstring, forming the definition body inside the RST blockquote.
    desc = textwrap.indent(xml_comment_docstring(ar_arg.doc).strip(), "    ")
    return f"\n{ar_arg.name} : {type_str}\n{desc}"


def xml_args_docstring(cls) -> str:
    """
    Build the full RST parameter section for all arguments of an XML message,
    including the standard ``_timeout``, ``_policy``, ``_float_tol`` and
    ``_int_tol`` extra parameters.

    Returns a ``**Parameters**`` bold heading followed by RST definition
    list entries, which renders without the uppercase transform applied by
    the furo theme to ``dl.field-list`` and ``p.rubric`` nodes.

    @param cls: the ARSDK message class being documented.
    @return: RST string for all parameters.
    @rtype: str
    """
    # Deferred import to avoid circular dependency at module level.
    from olympe.messaging.arsdk_messages import ArsdkMessageType

    if cls.message_type == ArsdkMessageType.CMD:
        extra_items = [
            (
                "_timeout",
                "int",
                "command message timeout (defaults to {})".format(cls.timeout),
            ),
            (
                "_no_expect",
                "bool",
                "if True, do not expect the usual command expectation"
                " (defaults to False)",
            ),
        ]
    else:
        extra_items = [
            (
                "_policy",
                ":py:class:`olympe.messaging.arsdk_expectations.ExpectPolicy`",
                "specify how to check the expectation. Possible values are"
                " 'check', 'wait' and 'check_wait' (defaults to 'check_wait')",
            ),
        ]
    extra_items += [
        (
            "_float_tol",
            "tuple",
            "specify the float comparison tolerance, a 2-tuple containing a"
            " relative tolerance float value and an absolute tolerate float value"
            f" (default to {cls.float_tol})."
            " See python 3 stdlib `math.isclose` documentation for more"
            " information",
        ),
        (
            "_matching_mode",
            ":py:class:`olympe.MatchingMode`",
            "specify the expectation matching mode"
            " (default to :py:data:`olympe.MatchingMode.pattern`)",
        ),
        (
            "_int_tol",
            "int",
            "specify the int comparison tolerance, an int containing an"
            f" absolute tolerate int value (default to {cls.int_tol})",
        ),
    ]
    extra = "\n".join(
        f"\n{name} : {type_str}\n    {desc}" for name, type_str, desc in extra_items
    )
    params = "\n".join(xml_arg_docstring(cls, arg) for arg in cls.obj.args)
    all_params = (params + extra).strip()
    # all_params.strip() guards against messages that declare no arguments at all:
    # the heading is only emitted when there is actually something to list.
    if not all_params:
        return ""
    # extra_items (list of (name, type_str, desc) tuples) replaces the previous
    # approach of building the extra-params block via string concatenation into a
    # single `extra` variable; a list of tuples is easier to read and extend.
    #
    # textwrap.indent(..., "    ") wraps the entire definition list in a 4-space RST
    # blockquote, matching the visual indentation of the device list in xml_supported
    # (the "Supported by" section).  Each term is indented 4 spaces; each description
    # body — already 4 spaces deep from xml_arg_docstring / extra_items — becomes
    # 8 spaces total, which is correct RST for a definition body inside a blockquote.
    #
    # **Parameters** choice: see xml_arg_docstring for why not :param: or .. rubric::.
    return "\n\n**Parameters**:\n\n" + textwrap.indent(all_params, "    ")


def xml_comment_docstring(
    ar_comment,
    ar_args_doc: Optional[str] = None,
    ar_is_deprecated: bool = False,
    cls=None,
) -> str:
    """
    Build a complete RST docstring body from an arsdkparser ArComment object
    (or a plain string/bytes description).

    @param ar_comment: arsdkparser ArComment object, or a str/bytes description.
    @param ar_args_doc: optional pre-built RST parameter block to inject after
        the description.
    @param ar_is_deprecated: True if the message is deprecated.
    @param cls: the ARSDK message class; required when ar_comment carries a
        ``support`` attribute (to build the supported-by section).
    @return: RST string combining title/description, parameters, support info,
        trigger and result sections.
    @rtype: str
    """
    if isinstance(ar_comment, (str, bytes)):
        return string_from_arsdkxml(str(ar_comment))
    ret = ""
    if ar_comment.title and not ar_comment.desc:
        ret += "\n\n{}".format(
            textwrap.fill(
                string_from_arsdkxml(ar_comment.title), break_long_words=False
            )
        )
    elif ar_comment.desc:
        ret += "\n\n{}".format(
            textwrap.fill(string_from_arsdkxml(ar_comment.desc), break_long_words=False)
        )
    if ar_args_doc is not None:
        ret += ar_args_doc
    if cls is not None:
        # FIXME: arsdk-xml "support" attribute is currently unreliable
        ret += "\n\n{}".format(xml_supported(cls, ar_comment.support, ar_is_deprecated))
    if ar_comment.triggered:
        ret += "\n\n{}".format(xml_triggered(ar_comment.triggered))
    if ar_comment.result:
        ret += "\n\n**Result**: {}".format(
            textwrap.fill(
                string_from_arsdkxml(ar_comment.result), break_long_words=False
            )
        )
    return ret


def xml_message_docstring(cls) -> str:
    """
    Build the top-level RST docstring for an XML-based ARSDK message.

    Combines the message's full name, comment, argument docs, support info,
    trigger and result sections into a single RST string.  This is the
    replacement for ArsdkMessageMeta._py_ar_cmd_docstring.

    @param cls: the ARSDK message class being documented.
    @return: complete RST docstring for the message.
    @rtype: str
    """
    body = xml_comment_docstring(
        cls.obj.doc,
        ar_args_doc=xml_args_docstring(cls),
        ar_is_deprecated=cls.obj.isDeprecated,
        cls=cls,
    )
    return "\n\n".join([cls.fullName, body])


def xml_cmd_expectation_argval_docstring(cls, argname: str, argval) -> str:
    """
    Format a single expectation argument value as a readable RST string.

    @param cls: the ARSDK message class being documented.
    @param argname: the argument name in the expectation.
    @param argval: the argument value; may be an ArsdkEnum, ArsdkBitfield,
        callable, or plain value.
    @return: string representation of argval suitable for RST.
    @rtype: str
    """
    if isinstance(argval, ArsdkEnum):
        return "'" + argval._name_ + "'"
    if isinstance(argval, ArsdkBitfield):
        return argval.pretty()
    if callable(argval):
        # Callable argvals are "this.*" lambdas from the arsdk-xml expect
        # attribute; they capture command arguments and compute the expected
        # event value at documentation time.
        command_args = OrderedDict((arg, f"this.{arg}") for arg in cls.args_name)
        try:
            return argval(cls, command_args)
        except KeyError:
            cls.doc_todos += "\n\n.. todo::\n    {}".format(
                "Fix wrong expectation definition for {}:\n    {}".format(
                    cls.fullName,
                    "Invalid parameter value for the '{}' expectation"
                    " parameter\n".format(argname),
                )
            )
            return "InternalError"
    return str(argval)


def xml_cmd_expectation_args_docstring(cls, args: dict) -> str:
    """
    Format the full argument list of a command expectation as RST.

    @param cls: the ARSDK message class being documented.
    @param args: dict mapping argument names to their expected values.
    @return: RST string of the form ``(arg1=val1, arg2=val2, ...)``.
    @rtype: str
    """
    args = args.copy()
    # Inject _policy='wait' so the reader can see the recommended usage when
    # the command is used inside an expectation block.
    args.update(_policy="'wait'")
    ret = "("
    ret += ", ".join(
        argname + "=" + xml_cmd_expectation_argval_docstring(cls, argname, argval)
        for argname, argval in args.items()
    )
    ret += ")"
    # Replace "this." prefixes with "self." for Python consistency in the RST.
    return ret.replace("this.", "self.")


def xml_cmd_any_expectation_docstring(cls, any_expectations) -> str:
    """
    Format a WhenAny (OR) expectation block as RST.

    @param cls: the ARSDK message class being documented.
    @param any_expectations: iterable of individual expectation objects.
    @return: RST string of the form ``(#id1(...) | #id2(...))``.
    @rtype: str
    """
    ret = "("
    for i, expectation in enumerate(any_expectations):
        ret += (
            "#"
            + expectation.expected_message.id_name
            + xml_cmd_expectation_args_docstring(cls, expectation.expected_args)
        )
        if i < len(any_expectations) - 1:
            ret += " | "
    ret += ")"
    return ret


def xml_cmd_expectation_docstring(cls) -> str:
    """
    Build the ``**Expectations**`` RST section for a command message.

    @param cls: the ARSDK command message class being documented.
    @return: RST string describing all expectations, or empty string for event
        messages.
    @rtype: str
    """
    # Deferred imports to avoid circular dependency at module level.
    from olympe.messaging.arsdk_messages import ArsdkMessageType
    from olympe.messaging.arsdk_expectations import ArsdkWhenAnyExpectation

    ret = ""
    if cls.message_type == ArsdkMessageType.CMD:
        for i, expectation in enumerate(cls._expectation):
            if isinstance(expectation, ArsdkWhenAnyExpectation):
                ret += xml_cmd_any_expectation_docstring(cls, expectation)
            else:
                ret += (
                    "#"
                    + expectation.expected_message.id_name
                    + xml_cmd_expectation_args_docstring(cls, expectation.expected_args)
                )
            if i < len(cls._expectation) - 1:
                ret += " & "
    if ret:
        ret = f"**Expectations**: {ret}"
    return ret


def xml_resolve_doc(cls, messages) -> None:
    """
    Finalise the docstring of an XML-based ARSDK message after all expectations
    have been resolved (phase 2 of the two-phase init).

    Appends the expectation section and replaces ``#<id>`` message references
    with proper ``:py:func:`` cross-references.  This is the replacement for
    ArsdkMessageMeta._resolve_doc.

    @param cls: the ARSDK message class to finalise.
    @param messages: the ArsdkMessages registry used to look up
        cross-referenced messages by numeric ID string.
    """
    cls.docstring += "\n\n" + xml_cmd_expectation_docstring(cls)

    def _message_ref(m: re.Match) -> str:
        """Replace a bare #<id> reference with a Sphinx cross-reference."""
        try:
            message = messages.by_id_name[m.group(1)]
        except KeyError:
            return m.group(0)
        args = "(" + m.group(2) + ")" if m.lastindex > 1 else "()"
        return ":py:func:`{}{}<olympe.messages.{}>`".format(
            message.name, args, message.fullName
        )

    # Three regex patterns handle the three reference formats present in
    # arsdk-xml doc strings:
    #   1. Markdown link:   [SomeName](#12-3-4)
    #   2. Inline call ref: #12-3-4(arg=val)
    #   3. Bare ref:        #12-3-4
    cls.docstring = re.sub(r"\[[\w_\\]+\]\(#([\d-]+)\)", _message_ref, cls.docstring)
    cls.docstring = re.sub(r"#([\d-]+)\(([^)]*)\)", _message_ref, cls.docstring)
    cls.docstring = re.sub(r"#([\d-]+)", _message_ref, cls.docstring)
    if cls.doc_todos:
        cls.docstring += cls.doc_todos
    cls.__doc__ = cls.docstring


# ---------------------------------------------------------------------------
# Proto pipeline
# ---------------------------------------------------------------------------


def proto_supported_doc(support: List[str]) -> str:
    """
    Build the ``Supported by:`` RST section for a protobuf message or field.

    Unlike xml_supported, this function accepts a list of raw support strings
    (from ArsdkProtoMessageDoc.support) rather than a single concatenated
    string.

    The triple-nesting bug present in the original
    ``ArsdkProtoMessage._supported_doc`` — where ``s[1:]`` returned a
    list-of-list requiring ``[x for sub in versions for v in sub for x in
    v.split(",")]`` — is fixed here by delegating to _parse_device_support.

    @param support: list of raw support attribute strings; None/"" entries
        are silently filtered out.
    @type support: list[str]
    @return: RST string for the support section, or empty string if support is
        falsy or contains only empty values.
    @rtype: str
    """
    if not support:
        return ""
    support_clean = [s for s in support if s not in (None, "")]
    if not support_clean:
        return ""

    parsed = _parse_device_support(support_clean)
    lines: List[str] = []
    for device_str, versions in parsed:
        if "anafi" in device_str.lower() or "skycontroller" in device_str.lower():
            if versions:
                lines.append(
                    "    :{}: for firmware release versions {}".format(
                        device_str, ", ".join(versions)
                    )
                )
            else:
                lines.append(f"    :{device_str}: with an up to date firmware")

    if not lines:
        return "\n**Unsupported message**\n"

    return "\nSupported by:\n" + "\n".join(lines) + "\n"


def proto_message_ref_sub(cls, messages, m: re.Match) -> str:
    """
    Regex substitution callback replacing a ``[Feature.Message]`` reference
    in a proto docstring with a Sphinx ``:py:func:`` cross-reference.

    @param cls: the proto message class whose docstring is being processed;
        must expose ``cls.prefix`` and ``cls.feature_name``.
    @param messages: the ArsdkMessages registry.
    @param m: the regex match object with named groups ``message`` and
        ``args``.
    @return: replacement string — either a ``:py:func:`` ref or the original
        match text if the message cannot be resolved.
    @rtype: str
    """
    from olympe.utils import get_mapping

    path = m.group("message").split(".")
    # Try three candidate paths in order:
    #   1. Absolute path as written in the doc attribute.
    #   2. Relative to the current message's prefix (sibling message).
    #   3. Relative to the feature root (peer message in the same feature).
    for candidate_path in (path, cls.prefix + path, [cls.feature_name] + path):
        try:
            message = get_mapping(messages.by_feature, candidate_path)
            break
        except KeyError:
            pass
    else:
        # No candidate matched — leave the reference as-is (Sphinx will warn).
        return m.group(0)

    args = "(" + m.group("args") + ")" if m.group("args") is not None else "()"
    return f":py:func:`{message.name}{args}<olympe.messages.{message.fullName}>`"


def proto_field_docstring(
    field,
    support_fn: Callable[[List[str]], str] = proto_supported_doc,
) -> str:
    """
    Build an RST definition list entry for a single protobuf field.

    Skips the ``selected_fields`` sentinel field automatically.

    @param field: an ArsdkProtoField descriptor; must expose ``name``, ``doc``,
        ``exclusive_with``, ``label``, ``type``, ``support`` and
        ``known_issues``.
    @param support_fn: callable used to render the field's support list;
        defaults to proto_supported_doc.
    @return: RST definition list entry string, or empty string for selected_fields.
    @rtype: str
    """
    # selected_fields is an internal protobuf mechanism (see ArsdkProtoMessage
    # selected_fields handling); it must never appear in user-facing docs.
    if field.name == "selected_fields":
        return ""

    exclusive_with = ""
    if field.exclusive_with:
        exclusive_with = ", ".join(field.exclusive_with)
        exclusive_with = f" (mutually exclusive with: {exclusive_with})"

    if field.label is ProtoFieldLabel.Repeated:
        type_str = f"list({field.type})"
    else:
        support_str = textwrap.indent(
            (
                f"\n{support_fn([field.support])}"
                if field.support not in (None, "")
                else ""
            ),
            "    ",
        )
        type_str = f"{field.type}, {support_str}".rstrip(", ")

    # Same format as xml_arg_docstring: RST definition list entry instead of
    # :param:/:type: field-list directives.  See xml_arg_docstring for the full
    # rationale (furo text-transform: uppercase on dl.field-list > dt).
    #
    # .rstrip(", ") on type_str: when support_str is empty (no support info for
    # this field), f"{field.type}, " would leave a trailing ", " — rstrip removes it.
    #
    # textwrap.indent(desc, "    "): 4-space indent on the definition body, consistent
    # with the XML pipeline.  proto_resolve_doc then wraps the whole block in another
    # 4-space indent to create the RST blockquote.
    desc = f"{field.doc} {exclusive_with}".strip()
    if field.known_issues not in (None, ""):
        desc += f"\n\nKnown issues: {field.known_issues}"
    desc = textwrap.indent(desc, "    ")
    return f"\n{field.name} : {type_str}\n{desc}\n"


def proto_resolve_doc(cls, messages, module) -> None:
    """
    Finalise the docstring of a proto message after all cross-references are
    available (phase 2 of the two-phase init).

    Builds the full RST docstring from the message's ArsdkProtoMessageDoc,
    appends on_success/on_failure/triggered_by extensions, substitutes
    ``[Feature.Message]`` cross-references, then calls cls._create_call() to
    compile the callable.  This is the replacement for
    ArsdkProtoMessage._resolve_doc.

    @param cls: the proto message class to finalise; must expose ``cls.doc``,
        ``cls.message_desc`` and ``cls._create_call()``.
    @param messages: the ArsdkMessages registry used for cross-reference
        resolution.
    @param module: the Python module object for the feature (used by
        ``_parse_expectation`` in the expectations layer).
    """
    if cls.doc is None:
        cls.docstring = ""
        cls.__doc__ = ""
        # _create_call() must still be called even for undocumented messages
        # so that the __call__ method exists (it delegates to _expect()).
        cls._create_call()
        return

    cls.docstring = cls.doc.doc + "\n"
    # Collect all field entries first so we can check whether there is anything
    # to document before adding the **Parameters** heading.  If every field is
    # either selected_fields (filtered out in proto_field_docstring) or produces
    # no output, fields_doc will be empty and we skip the heading entirely.
    fields_doc = "".join(proto_field_docstring(f) for f in cls.message_desc.fields)
    # Same heading + blockquote pattern as xml_args_docstring:
    # **Parameters** bold text + 4-space-indented RST definition list.
    if fields_doc.strip():
        cls.docstring += "\n\n**Parameters**:\n\n" + textwrap.indent(
            fields_doc.strip(), "    "
        )
    cls.docstring += "\n"
    cls.docstring += proto_supported_doc(cls.doc.support)

    # deprecated_version is a feature-level protobuf extension parsed in
    # ArsdkProto.parse_proto(): it holds the version string from which the
    # entire feature (and therefore all its messages) became deprecated.
    # Deferred import: ArsdkProto is available (proto.py is already imported at
    # the top of this module) but we import inside the function to avoid any
    # potential init-order issue since proto_resolve_doc is only called after
    # all proto files have been parsed.
    from olympe.parser.proto import ArsdkProto

    _feature = ArsdkProto.get(cls.root).features.get(cls.feature_name)
    if _feature is not None and _feature.deprecated_version:
        cls.docstring += (
            "\n\n**Deprecated**\n\n.. warning::\n"
            f"    This message is deprecated since version {_feature.deprecated_version}"
            " and should no longer be used."
        )

    if cls.doc.on_success not in (None, ""):
        cls.docstring += f"\nOn success: {cls.doc.on_success}\n"
    if cls.doc.on_failure not in (None, ""):
        cls.docstring += f"\nOn failure: {cls.doc.on_failure}\n"
    if cls.doc.triggered_by not in (None, ""):
        cls.docstring += f"\nTriggered by: {cls.doc.triggered_by}\n"

    # Replace [Feature.Message(args)] cross-references with Sphinx :py:func:
    # links.  The regex matches the proto doc convention where square brackets
    # denote a reference to another message, optionally with argument text.
    cls.docstring = re.sub(
        r"\[(?P<message>[\w\d._]+)(\((?P<args>[^)]*)\))?\]",
        lambda m: proto_message_ref_sub(cls, messages, m),
        cls.docstring,
    )

    cls.__doc__ = cls.docstring
    # _create_call() compiles the per-message __call__ method using the
    # docstring we just built.  It must be called AFTER cls.docstring is set
    # so that the compiled function embeds the correct help text.
    cls._create_call()
