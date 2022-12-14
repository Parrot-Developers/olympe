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


import faulthandler
import logging
import os.path
import sys
import olympe_deps

from collections import OrderedDict
from collections.abc import Mapping
from importlib import import_module
from importlib.machinery import ModuleSpec
from textwrap import indent
from types import ModuleType

# Preload olympe_deps bundled libprotobuf so that we don't rely on system installed libprotobuf.
olympe_deps._load_library("libprotobuf.so.30")  # noqa

from .arsdkng.enums import ArsdkEnums, ArsdkEnum, ArsdkBitfield, ArsdkProtoEnum  # noqa
from .arsdkng.messages import ArsdkMessages, ArsdkMessageBase, ArsdkProtoMessage  # noqa
from .utils import get_mapping  # noqa


logger = logging.getLogger(__name__)


class ModuleLoader:
    """
    A module 'finder' and 'loader' as described by PEP-0302.

    This module loader is used to create a python module hierarchy for arsdk-xml
    features, classes, messages, enums and bitfields.
    """

    def __init__(self):
        self.messages = OrderedDict()
        self.messages_root = OrderedDict()
        self.enums = OrderedDict()
        self.enums_root = OrderedDict()

    def add_package_root(self, root):
        if root in self.messages:
            logger.info(f"ModuleLoader '{root}' root package is already registered")
            return
        try:
            self.enums[root] = ArsdkEnums.get(root)
            self.messages[root] = ArsdkMessages.get(root)
            self.messages_root[root] = f"{root}.messages"
            self.enums_root[root] = f"{root}.enums"
            logger.info(f"ModuleLoader '{root}' root package has been registered")
        except Exception:
            logger.exception("ModuleLoader unhandled exception")

    def reload(self, root=None):
        if root is None:
            for root in self.messages.keys():
                self._reload_root(root)
        else:
            self._reload_root(root)

    def _reload_root(self, root):
        if self.messages_root[root] in sys.modules:
            for module in list(sys.modules):
                if module.startswith(self.messages_root[root]):
                    del sys.modules[module]
        import_module(self.messages_root[root])
        if self.enums_root[root] in sys.modules:
            for module in list(sys.modules):
                if module.startswith(self.enums_root[root]):
                    del sys.modules[module]
        import_module(self.enums_root[root])

    def get_messages(self, root, feature_name):
        return self._get_module(root, self.messages_root, feature_name)

    def get_enums(self, root, feature_name):
        return self._get_module(root, self.enums_root, feature_name)

    def _get_module(self, root, names, feature_name):
        module_name = names.get(root)
        if module_name is None:
            raise ImportError(f"Unknown module or package {root}")
        module_name = f"{module_name}.{feature_name}"
        module = sys.modules.get(module_name)
        if module is None:
            raise ImportError(f"Unknown module {module_name}")
        return module

    def find_spec(self, fullname, path, target=None):
        try:
            for root in self.messages:
                if (
                    fullname == root
                    or fullname.startswith(self.messages_root[root])
                    or fullname.startswith(self.enums_root[root])
                ):
                    break
            else:
                return None
            root_path = root.replace(".", "/")
            fullname_path = fullname.replace(".", "/")
            messages = self.messages[root]
            enums = self.enums[root]
            name = fullname[len(root) + 1 :]
            name_path = name.split(".")
            if len(name_path) == 0 or len(name_path) == 1 and not name_path[0]:
                # {root}
                origin = os.path.join(root_path, fullname_path)
                spec = ModuleSpec(fullname, self, origin=origin, is_package=True)
                spec.item = None
                spec.root = root
                return spec
            type_ = name_path[0]
            if type_ == "messages":
                features = messages.by_feature
            else:
                features = enums._by_feature
            name_path = name_path[1:]
            if len(name_path) == 0:
                # {root}.messages/enums
                origin = os.path.join(root_path, fullname_path)
                spec = ModuleSpec(fullname, self, origin=origin, is_package=True)
                spec.item = features
                spec.root = root
                return spec

            item = get_mapping(features, name_path)
            if isinstance(
                item,
                (
                    ArsdkEnum.__class__,
                    ArsdkBitfield.__class__,
                    ArsdkProtoEnum.__class__,
                ),
            ):
                return None

            is_package = not (
                isinstance(item, type) and issubclass(item, ArsdkMessageBase)
            )

            origin = os.path.join(root_path, fullname_path)
            spec = ModuleSpec(fullname, self, origin=origin, is_package=is_package)
            spec.item = item
            spec.root = root

            return spec
        except Exception:
            logger.exception("ModuleLoader.find_spec unhandled exception")
            return None

    def find_module(self, fullname, path=None):
        try:
            spec = self.find_spec(fullname, path)
            if spec is not None:
                return self
            else:
                return None
        except Exception:
            logger.exception("ModuleLoader.find_module unhandled exception")
            return None

    def iter_modules(self, prefix):
        try:
            package = self.load_module(prefix.rstrip("."))
        except Exception:
            return
        else:
            for name in package.__all__:
                item = getattr(package, name, None)
                if isinstance(item, ModuleType):
                    yield prefix + name, item.__spec__.submodule_search_locations is not None

    def load_module(self, fullname):
        try:
            module = sys.modules.get(fullname)
            if module and module.__loader__ is self:
                return module
            spec = self.find_spec(fullname, None)
            if spec is None:
                raise ImportError(f"Unknown module {fullname}")
            root = spec.root
            root_path = root.replace(".", "/")
            fullname_path = fullname.replace(".", "/")
            messages = self.messages[root]
            enums = self.enums[root]

            name = fullname[len(root) + 1 :]
            name_path = name.split(".")
            is_feature = len(name_path) == 2
            type_, feature_name, class_name, *_ = name_path + 2 * [None]  # noqa

            module = ModuleType(fullname)
            module.__spec__ = spec
            module.__fakefile__ = f"{root_path}://{fullname_path}"
            if spec.submodule_search_locations is not None:
                module.__path__ = [module.__fakefile__]
            module.__name__ = fullname
            module.__cached__ = None
            module.__loader__ = self
            module.__all__ = []
            module.__arsdkng_feature_name__ = feature_name
            module.__arsdkng_class_name__ = class_name
            module.__arsdkng_type_name__ = type_
            module.__arsdkng_is_proto__ = False
            module.__arsdkng_root_package__ = root
            if feature_name is None:
                module.__package__ = f"{root}.{type_}"
                if not type_:
                    features = []
                elif type_ == "messages":
                    features = list(messages.by_feature.keys())
                else:
                    features = list(enums._by_feature.keys())
                for feature_name in features:
                    setattr(
                        module,
                        feature_name,
                        self.load_module(f"{fullname}.{feature_name}"),
                    )
                    module.__all__.append(feature_name)
            else:
                is_proto = False
                objs = []
                for name, item in spec.item.items():
                    if isinstance(item, type) and issubclass(item, ArsdkMessageBase):
                        obj = item()
                        objs.append(obj)
                        obj.__arsdk_module__ = module
                    elif not isinstance(
                        item,
                        (
                            ArsdkEnum.__class__,
                            ArsdkBitfield.__class__,
                            ArsdkProtoEnum.__class__,
                        ),
                    ):
                        obj = self.load_module(f"{fullname}.{name}")
                        obj.__arsdk_module__ = module
                        is_proto = is_proto or obj.__arsdkng_is_proto__
                    else:
                        obj = item
                    is_proto = is_proto or isinstance(obj, ArsdkProtoMessage)
                    obj.__module__ = module.__name__
                    module.__arsdkng_is_proto__ = is_proto
                    setattr(module, name, obj)
                    module.__all__.append(name)
                if is_proto:
                    messages._resolve_proto_nested_messages(module, feature_name)
                for obj in objs:
                    if isinstance(obj, ArsdkProtoMessage):
                        obj._resolve_nested_messages()
                if is_feature and is_proto:
                    messages._resolve_proto_expectations(module, feature_name)
                    messages._resolve_proto_doc(module, feature_name)
            sys.modules[fullname] = module
            return module
        except Exception:
            logger.exception("ModuleLoader.load_module unhandled exception")
            return None

    def _get_source_mapping(self, mapping):
        source = ""
        for k, v in mapping.items():
            source += self._get_source(v, k)
        return source

    def _get_source(self, item, name=None):
        source = ""
        if isinstance(item, type) and issubclass(item, ArsdkMessageBase):
            source += "\n"
            source += "@staticmethod"
            source += item.get_source()
            source += "\n\n"
        elif isinstance(item, (list, tuple)):
            source += "".join(self._get_source(i) for i in item)
        if isinstance(item, Mapping):
            source += f"\n\nclass {name}:"
            source += indent(self._get_source_mapping(item), "   ")
            source += "\n\n"
        source += "\n\n"
        return source

    def get_source(self, modname):
        if modname in sys.modules:
            module = sys.modules[modname]
        else:
            module = self.load_module(modname)

        root = module.__arsdkng_root_package__
        feature_name = module.__arsdkng_feature_name__
        type_ = module.__arsdkng_type_name__
        messages = self.messages[root]
        enums = self.enums[root]
        source = ""
        if type_ == "messages" and feature_name is not None:
            source += self._get_source(messages.by_feature[feature_name], feature_name)
        elif type_ == "enums" and feature_name is not None:
            for name, obj in enums._by_feature[feature_name].items():
                if isinstance(obj, ArsdkEnum.__class__):
                    source += "\n\n" + obj._source_
                elif isinstance(obj, Mapping):
                    for enum in obj.values():
                        if isinstance(enum, ArsdkEnum.__class__):
                            source += "\n\n" + enum._source_
                    source += "\n\n"
        else:
            source = ""
        return source

    def create_module(self, spec):
        return self.load_module(spec.name)

    def exec_module(self, module):
        pass


def path_hook(path):
    global module_loader
    if path.startswith("olympe://"):
        return module_loader
        return module_loader
    if path.startswith("olympe.airsdk://"):
        return module_loader
    raise ImportError


faulthandler.enable()
module_loader = ModuleLoader()
module_loader.add_package_root("olympe")
module_loader.add_package_root("olympe.airsdk")
sys.meta_path.append(module_loader)
sys.path_hooks.append(path_hook)
sys.path_importer_cache[
    os.path.join(os.path.dirname(sys.modules["olympe"].__file__), "messages")
] = module_loader
sys.path_importer_cache[
    os.path.join(os.path.dirname(sys.modules["olympe"].__file__), "enums")
] = module_loader
