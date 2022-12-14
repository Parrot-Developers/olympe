#  Copyright (C) 2020-2021 Parrot Drones SAS
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


import aenum
import google.protobuf.message_factory
import google.protobuf.symbol_database
import importlib.util
import logging
import shlex
import subprocess
import sys
import tempfile
import olympe_deps as od


from olympe.protobuf.proto_builder import MakeSimpleProtoClass
from olympe.utils import mapping_as_attributes
from collections import namedtuple, OrderedDict
from google.protobuf.descriptor import FieldDescriptor
from pathlib import Path


logger = logging.getLogger(__name__)


def proto_type_to_python(proto_type):
    return {
        FieldDescriptor.TYPE_DOUBLE: float,
        FieldDescriptor.TYPE_FLOAT: float,
        FieldDescriptor.TYPE_INT64: int,
        FieldDescriptor.TYPE_UINT64: int,
        FieldDescriptor.TYPE_INT32: int,
        FieldDescriptor.TYPE_FIXED64: int,
        FieldDescriptor.TYPE_FIXED32: int,
        FieldDescriptor.TYPE_BOOL: bool,
        FieldDescriptor.TYPE_STRING: str,
        FieldDescriptor.TYPE_GROUP: list,
        FieldDescriptor.TYPE_MESSAGE: dict,
        FieldDescriptor.TYPE_BYTES: bytes,
        FieldDescriptor.TYPE_UINT32: int,
        FieldDescriptor.TYPE_ENUM: str,
        FieldDescriptor.TYPE_SFIXED32: int,
        FieldDescriptor.TYPE_SFIXED64: int,
        FieldDescriptor.TYPE_SINT32: int,
        FieldDescriptor.TYPE_SINT64: int,
    }[proto_type]


class ProtoFieldLabel(aenum.Enum):
    Optional = FieldDescriptor.LABEL_OPTIONAL
    Required = FieldDescriptor.LABEL_REQUIRED
    Repeated = FieldDescriptor.LABEL_REPEATED


class ArsdkProtoEnumValueDoc(
    namedtuple(
        "ArsdkProtoEnumValueDoc",
        ["name", "doc"],
    )
):
    pass


class ArsdkProtoEnumDoc(
    namedtuple(
        "ArsdkProtoEnumDoc",
        ["doc", "values_doc"],
    )
):
    pass


class ArsdkProtoEnum(
    namedtuple(
        "ArsdkProtoEnum",
        ["name", "path", "enum", "feature_name", "doc"],
    )
):
    pass


class ArsdkProtoFieldDoc(
    namedtuple(
        "ArsdkProtoFieldDoc",
        ["name", "type", "label", "doc"],
    )
):
    pass


class ArsdkProtoMessageDoc(
    namedtuple(
        "ArsdkProtoMessageDoc",
        ["doc", "fields_doc", "support"],
    )
):
    pass


class ArsdkProtoMessage(
    namedtuple(
        "ArsdkProtoMessage",
        ["name", "path", "message", "feature_name", "doc"],
    )
):
    pass


class ArsdkProtoServiceMessage(
    namedtuple(
        "ArsdkProtoServiceMessage",
        [
            "number",
            "name",
            "camelcase_name",
            "field_name",
            "field_type",
            "service_type",
            "service",
            "message",
            "on_success",
            "on_failure",
        ],
    )
):
    pass


class ArsdkProtoService(
    namedtuple(
        "ArsdkProtoService",
        ["id", "feature_name", "type", "full_name", "messages", "enums"],
    )
):
    pass


class ArsdkProtoFeature(
    namedtuple(
        "ArsdkProtoFeature",
        ["feature_name", "module", "module_spec", "services", "messages", "enums"],
    )
):
    pass


class ArsdkProto:

    _store = {}

    @classmethod
    def get(cls, root):
        parent = None
        parent_path = ".".join(root.split(".")[:-1])
        if parent_path:
            parent = cls._store.get(parent_path)
            if parent is None:
                raise ValueError(
                    f"Cannot create ArsdkProto with non-existing parent {parent_path}"
                )
            ret = cls._store.get(root)
            if ret is not None:
                if ret.parent is not parent:
                    raise ValueError(
                        "Cannot re-attach existing ArsdkProto object to a new parent"
                    )
            else:
                ret = ArsdkProto(root, parent=parent)
                cls._store[root] = ret
            return ret
        else:
            ret = cls._store.get(root)
            if ret is None:
                ret = ArsdkProto(root)
                cls._store[root] = ret
            return ret

    @classmethod
    def service_id(cls, service_name):
        # Jenkins 16 bits hash function
        # Note: We have to mask every operation result on 32 bits to emulate
        # regular 32 bits integer overflow in Python. Integers in Python have an
        # infinite precision.
        ret = 0
        for c in bytes(service_name, encoding="ascii"):
            ret += c
            ret &= 0xFFFFFFFF
            ret += (ret << 10) & 0xFFFFFFFF
            ret &= 0xFFFFFFFF
            ret ^= (ret >> 6) & 0xFFFFFFFF
            ret &= 0xFFFFFFFF

        ret += (ret << 3) & 0xFFFFFFFF
        ret &= 0xFFFFFFFF
        ret ^= (ret >> 11) & 0xFFFFFFFF
        ret &= 0xFFFFFFFF
        ret += (ret << 15) & 0xFFFFFFFF
        # final mask on 16 bits
        ret &= 0xFFFF
        return ret

    def __init__(self, root, parent=None):
        self.__class__._store[root] = self
        self.parent = parent
        self.extensions = None
        od_path = Path(od.__file__)
        if od_path.stem == "__init__":
            od_path = od_path.parent
        site_path = od_path.parent
        root_path = site_path.parent.parent.parent
        self.shared_proto_path = site_path / "olympe_protobuf"
        self.arsdk_proto_path = self.shared_proto_path / "arsdk"
        self.parrot_proto_path = self.shared_proto_path / "parrot"
        self.google_proto_path = self.shared_proto_path / "google"
        self.proto_def_path = root_path / "include"
        self.message_factory = google.protobuf.message_factory.MessageFactory()
        self.symbol_db = google.protobuf.symbol_database.Default()
        self.pool = google.protobuf.descriptor_pool.Default()
        self.features = OrderedDict()
        self.features_package_map = OrderedDict()
        if parent is None:
            self.on_success_ext = None
            self.on_failure_ext = None
            self.package_ext = None
            self.message_doc_ext = None
            self.enum_doc_ext = None
            self.enum_value_doc_ext = None
            self.field_doc_ext = None
            self.support_ext = None
            self.parse_protos()
        else:
            self.on_success_ext = self.parent.on_success_ext
            self.on_failure_ext = self.parent.on_failure_ext
            self.package_ext = self.parent.package_ext
            self.message_doc_ext = self.parent.message_doc_ext
            self.enum_doc_ext = self.parent.enum_doc_ext
            self.enum_value_doc_ext = self.parent.enum_value_doc_ext
            self.field_doc_ext = self.parent.field_doc_ext
            self.support_ext = self.parent.support_ext

    def create_service(self, feature_name, service_descriptor):
        service_type = service_descriptor.name
        service_name = service_descriptor.full_name
        service_id = self.service_id(service_name)
        service = self.message_prototype_from_descriptor(service_descriptor)
        messages = []
        enums = []
        for (
            number,
            name,
            camelcase_name,
            field_name,
            field_type,
            field_enums,
            message,
            success,
            failure,
        ) in self.list_oneof_messages(
            feature_name, service_descriptor.oneofs_by_name["id"]
        ):
            messages.append(
                ArsdkProtoServiceMessage(
                    number,
                    name,
                    camelcase_name,
                    field_name,
                    field_type,
                    service_type,
                    service,
                    message,
                    success,
                    failure,
                )
            )
            enums.extend(field_enums)
        return ArsdkProtoService(
            service_id, feature_name, service_type, service_name, messages, enums
        )

    def message_prototype_from_descriptor(self, message_descriptor):
        return self.message_factory.GetPrototype(message_descriptor)

    def message_type_from_field(self, feature_name, field_descriptor):
        package = field_descriptor.file.package
        name = "_Generated" + field_descriptor.full_name.split(".")[-1].title()
        full_name = f"{field_descriptor.file.package}.{name}"
        message_type = MakeSimpleProtoClass(
            {"value": field_descriptor}, package=package, full_name=full_name
        ).DESCRIPTOR
        enum_types = []
        for enum_type in message_type.enum_types:
            package = message_type.file.package
            path = enum_type.full_name[len(package) + 1 :]
            feature_name = enum_type.full_name[
                : len(enum_type.full_name) - len(path) - 1
            ]
            enum_types.append(self._make_enum(feature_name, path, enum_type))
            self.features_package_map[message_type.file.package] = feature_name
        return message_type, enum_types

    def list_oneof_messages(self, feature_name, oneof_descriptor):
        for field in oneof_descriptor.fields:
            if field.message_type is None:
                message_type, enum_types = self.message_type_from_field(
                    feature_name, field
                )
                message_name = field.name
            else:
                enum_types = []
                message_type = field.message_type
                if message_type.full_name != "google.protobuf.Empty":
                    message_name = field.message_type.name
                else:
                    message_name = field.camelcase_name
                    message_name = message_name[0].upper() + message_name[1:]
            prototype = self.message_prototype_from_descriptor(message_type)
            success_exp = None
            failure_exp = None
            if self.on_success_ext is not None:
                success_exp = message_type.GetOptions().Extensions[self.on_success_ext]
                failure_exp = message_type.GetOptions().Extensions[self.on_failure_ext]
            yield (
                field.number,
                message_name,
                field.camelcase_name,
                field.name,
                message_type,
                enum_types,
                prototype,
                success_exp,
                failure_exp,
            )

    def _get_field_type(self, module_descriptor, feature_name, field_descriptor):
        if field_descriptor.enum_type is not None:
            field_type_path = field_descriptor.enum_type.full_name[
                len(module_descriptor.package) + 1 :
            ]
            return f":py:class:`olympe.enums.{feature_name}.{field_type_path}`"
        elif field_descriptor.message_type is not None:
            field_type_path = field_descriptor.message_type.full_name[
                len(module_descriptor.package) + 1 :
            ]
            return f":py:func:`olympe.messages.{feature_name}.{field_type_path}`"
        else:
            return {
                FieldDescriptor.TYPE_DOUBLE: "double",
                FieldDescriptor.TYPE_FLOAT: "float",
                FieldDescriptor.TYPE_INT64: "i64",
                FieldDescriptor.TYPE_UINT64: "u64",
                FieldDescriptor.TYPE_INT32: "i32",
                FieldDescriptor.TYPE_FIXED64: "u64",
                FieldDescriptor.TYPE_FIXED32: "u32",
                FieldDescriptor.TYPE_BOOL: "bool",
                FieldDescriptor.TYPE_STRING: "string",
                FieldDescriptor.TYPE_GROUP: "group",
                FieldDescriptor.TYPE_MESSAGE: "message",
                FieldDescriptor.TYPE_BYTES: "bytes",
                FieldDescriptor.TYPE_UINT32: "u32",
                FieldDescriptor.TYPE_ENUM: "enum",
                FieldDescriptor.TYPE_SFIXED32: "i32",
                FieldDescriptor.TYPE_SFIXED64: "i64",
                FieldDescriptor.TYPE_SINT32: "i32",
                FieldDescriptor.TYPE_SINT64: "i64",
            }[field_descriptor.type]

    def feature_messages(
        self, root, filename, feature_name, module_descriptor, services
    ):
        ret = []
        proto_path = f"{root}/{filename}.proto"
        try:
            messages = self.symbol_db.GetMessages([proto_path])
        except KeyError:
            proto_path = proto_path.replace("_", "-")
            messages = self.symbol_db.GetMessages([proto_path])
        for name, message in messages.items():
            path = f"{feature_name}."
            path += message.DESCRIPTOR.full_name[len(module_descriptor.package) + 1 :]
            support = None
            if self.support_ext is not None:
                support = message.DESCRIPTOR.GetOptions().Extensions[self.support_ext]
            doc = None
            if self.message_doc_ext is not None:
                message_doc = message.DESCRIPTOR.GetOptions().Extensions[
                    self.message_doc_ext
                ]
                field_docs = []
                for field in message.DESCRIPTOR.fields:
                    type_ = self._get_field_type(module_descriptor, feature_name, field)
                    label = None
                    if field.label:
                        label = ProtoFieldLabel(field.label)
                    field_docs.append(
                        ArsdkProtoFieldDoc(
                            field.name,
                            type_,
                            label,
                            field.GetOptions().Extensions[self.field_doc_ext],
                        )
                    )
                doc = ArsdkProtoMessageDoc(message_doc, field_docs, support)
            ret.append(
                ArsdkProtoMessage(
                    message.DESCRIPTOR.name, path, message, feature_name, doc
                )
            )
        for service in services:
            for svc_message_desc in service.messages:
                for msg in ret:
                    if msg.name == svc_message_desc.name:
                        break
                else:
                    path = f"{feature_name}."
                    path += service.full_name[len(module_descriptor.package) + 1 :]
                    path += f".{svc_message_desc.name}"
                    ret.append(
                        ArsdkProtoMessage(
                            svc_message_desc.name,
                            path,
                            svc_message_desc.field_type,
                            feature_name,
                            None,
                        )
                    )
        return ret

    def _make_enum(self, feature_name, path, enum_desc):
        doc = None
        if self.enum_doc_ext is not None:
            enum = enum_desc.GetOptions().Extensions[self.enum_doc_ext]
            value_docs = []
            for value in enum_desc.values:
                value_docs.append(
                    ArsdkProtoEnumValueDoc(
                        value.name,
                        value.GetOptions().Extensions[self.enum_value_doc_ext],
                    )
                )
            doc = ArsdkProtoEnumDoc(enum, value_docs)
        return ArsdkProtoEnum(enum_desc.name, path, enum_desc, feature_name, doc)

    def _walk_message_enums(self, path, message_desc):
        for (
            submessage_name,
            submessage_desc,
        ) in message_desc.nested_types_by_name.items():
            p = f"{path}.{submessage_name}"
            yield from self._walk_message_enums(p, submessage_desc)

        for enum_desc in message_desc.enum_types:
            yield f"{path}.{enum_desc.name}", enum_desc

    def feature_enums(self, filename, feature_name, module_descriptor):
        ret = []
        path = ""
        for enum_full_name, enum_desc in module_descriptor.enum_types_by_name.items():
            if enum_desc.file is not module_descriptor:
                continue
            ret.append(self._make_enum(feature_name, path, enum_desc))
        for (
            message_name,
            message_desc,
        ) in module_descriptor.message_types_by_name.items():
            path = f"{message_name}"
            for p, enum_desc in self._walk_message_enums(path, message_desc):
                ret.append(self._make_enum(feature_name, p, enum_desc))
        return ret

    def parse_protos(self):
        protoc = str(Path(od.__file__).parent / "protoc")
        try:
            ld_musl = list(Path(od.__file__).parent.glob("ld-musl*"))[0]
            protoc = f"{ld_musl} {protoc}"
        except IndexError:
            pass
        with tempfile.TemporaryDirectory() as tmp_dir:
            proto_paths = [self.parrot_proto_path / "protobuf" / "extensions.proto"]
            for proto_path in self.arsdk_proto_path.glob("**/*.proto"):
                proto_paths.append(proto_path)
            for proto_path in proto_paths:
                cmd = (
                    f"{protoc} --python_out={tmp_dir}"
                    f" --proto_path={self.shared_proto_path}"
                    f" {proto_path}"
                )
                try:
                    subprocess.check_call(shlex.split(cmd))
                except subprocess.CalledProcessError as e:
                    logger.error(e)
                    raise
            # first load generic.proto
            sys.path.append(tmp_dir)
            try:
                self.extensions, _ = self.parse_proto(
                    tmp_dir,
                    Path(tmp_dir) / "parrot" / "protobuf",
                    "extensions_pb2.py",
                    extension=True,
                )
                self.on_success_ext = getattr(self.extensions, "on_success", None)
                self.on_failure_ext = getattr(self.extensions, "on_failure", None)
                self.package_ext = getattr(self.extensions, "olympe_package", None)
                self.message_doc_ext = getattr(self.extensions, "message_doc", None)
                self.field_doc_ext = getattr(self.extensions, "field_doc", None)
                self.enum_doc_ext = getattr(self.extensions, "enum_doc", None)
                self.enum_value_doc_ext = getattr(
                    self.extensions, "enum_value_doc", None
                )
                self.support_ext = getattr(self.extensions, "support", None)
                for pb_path in Path(tmp_dir).glob("**/*_pb2.py"):
                    if pb_path.name == "extensions_pb2.py":
                        continue
                    try:
                        _, feature = self.parse_proto(
                            tmp_dir, pb_path.parent, pb_path.name
                        )
                    except TypeError as e:
                        logger.warning(f"{pb_path.name}: {e}")
                        continue
                    if feature is None:
                        raise RuntimeError(f"Cannot load {pb_path}, invalid feature")
                self.features = OrderedDict(
                    sorted(self.features.items(), key=lambda t: t[0])
                )
            finally:
                sys.path.remove(tmp_dir)

    def parse_mission_proto(
        self, mission_path, mission_uid, raw=False, feature_name_from_file=False
    ):
        proto_modules = OrderedDict()
        with tempfile.TemporaryDirectory() as tmp_dir:
            for proto_path in Path(mission_path).glob("**/*.proto"):
                if proto_path.name == "extensions.proto":
                    continue

                protoc = Path(od.__file__).parent / "protoc"
                cmd = (
                    f"{protoc} --python_out={tmp_dir}"
                    f" --proto_path={self.shared_proto_path}"
                    f" --proto_path={mission_path}"
                    f" {proto_path}"
                )
                try:
                    subprocess.run(shlex.split(cmd), check=True, capture_output=True)
                except subprocess.CalledProcessError as e:
                    logger.error(
                        "Failed to parse proto file"
                        f" {proto_path}:\n{e.stdout}\n{e.stderr}"
                    )
                    raise
            # first load generic.proto
            sys.path.append(tmp_dir)
            try:
                for pb_path in Path(tmp_dir).glob("**/*_pb2.py"):
                    feature_name = None
                    if feature_name_from_file:
                        feature_name = (
                            mission_uid
                            + "."
                            + str(pb_path.relative_to(tmp_dir)).replace("/", ".")[
                                : -len("_pb2.py")
                            ]
                        )
                    proto_module, feature = self.parse_proto(
                        tmp_dir,
                        pb_path.parent,
                        pb_path.name,
                        feature_name=feature_name,
                        raw=raw,
                    )
                    proto_modules[
                        proto_module.DESCRIPTOR.name.replace("/", ".")
                    ] = proto_module
                    if feature is None:
                        # Not a feature ?
                        continue
                proto_modules_name = mission_uid.translate(str.maketrans("./-", "___"))
                proto_modules = mapping_as_attributes(
                    f"{proto_modules_name}_modules", proto_modules
                )
                self.features = OrderedDict(
                    sorted(self.features.items(), key=lambda t: t[0])
                )
            finally:
                sys.path.remove(tmp_dir)
            return proto_modules

    def parse_proto(
        self, root_dir, path, filename, *, feature_name=None, extension=False, raw=False
    ):
        # default feature name is based on .proto filename
        path = Path(path)
        proto_root = str(path.relative_to(root_dir))
        path = path / filename
        filename = path.stem
        module_name = f"{proto_root.replace('/', '.')}.{filename}"
        filename = filename[: -(len("_pb2"))]
        try:
            module = importlib.import_module(module_name)
            spec = module.__spec__
        except ImportError:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        if raw:
            return module, None
        if feature_name is None:
            if self.package_ext:
                # get the feature_name from the "olympe_package" extension if present
                package = module.DESCRIPTOR.GetOptions().Extensions[self.package_ext]
                if not package:
                    #  missing olympe package information
                    return module, None
                feature_name = package
            else:
                assert extension, (
                    f"Cannot import {filename} because olympe protobuf extensions are"
                    " not loaded yet"
                )
                feature_name = filename
        if module.DESCRIPTOR.package:
            self.features_package_map[module.DESCRIPTOR.package] = feature_name
        services = []
        if hasattr(module, "Command"):
            services.append(
                self.create_service(feature_name, module.Command.DESCRIPTOR)
            )
        if hasattr(module, "Event"):
            services.append(self.create_service(feature_name, module.Event.DESCRIPTOR))
        feature = ArsdkProtoFeature(
            feature_name,
            module,
            spec,
            services,
            self.feature_messages(
                proto_root, filename, feature_name, module.DESCRIPTOR, services
            ),
            self.feature_enums(filename, feature_name, module.DESCRIPTOR),
        )
        self.features[feature_name] = feature
        return module, feature
