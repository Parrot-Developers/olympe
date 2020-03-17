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

import imp
import os.path
import sys
import traceback
from collections import Mapping

try:
    from textwrap3 import indent
except ImportError:
    from textwrap import indent

try:
    from importlib.machinery import ModuleSpec
except ImportError:
    class ModuleSpec(object):

        def __init__(self, name, loader, origin, is_package=False):
            self.name = name
            self.loader = loader
            self.origin = origin
            self.is_package = is_package


from olympe.arsdkng.enums import ArsdkEnums, ArsdkEnum, ArsdkBitfield
from olympe.arsdkng.messages import ArsdkMessages, ArsdkMessage


class ModuleLoader(object):
    """
    A module 'finder' and 'loader' as described by PEP-0302.

    This module loader is used to create a python module hierarchy for arsdk-xml
    features, classes, messages, enums and bitfields.
    """

    def __init__(self):
        try:
            self.enums = ArsdkEnums.get()
            self.messages = ArsdkMessages.get()
            self.message_root = "olympe.messages"
            self.enum_root = "olympe.enums"
            self.olympe_root = os.path.abspath(os.path.join(__file__, '../' * 3))
        except Exception:
            traceback.print_exc()

    def find_spec(self, fullname, path, target=None):
        try:
            if not fullname.startswith(self.message_root) and not fullname.startswith(self.enum_root):
                return None
            name_path = fullname.split('.')
            if len(name_path) == 2:
                # olympe.messages
                origin = os.path.join(self.olympe_root, fullname.replace('.', '/'))
                spec = ModuleSpec(fullname, self, origin=origin, is_package=False)
                # olympe.enums
                return spec
            elif len(name_path) == 3:
                # olympe.messages.<feature_name>
                # olympe.enums.<feature_name>
                type_, feature_name, class_name = name_path[1], name_path[2], None
            elif len(name_path) == 4:
                # olympe.messages.<feature_name>.<class_or_message>
                # olympe.enums.<feature_name>.<class_or_enum>
                type_, feature_name, class_name = name_path[1:]
            else:
                return None

            if feature_name not in self.messages.by_feature:
                # feature name does not exists
                return None

            if (class_name is not None and
               class_name not in self.messages.by_feature[feature_name]):
                # class name, message or enum does not exist
                return None

            is_package = class_name is None

            origin = os.path.join(self.olympe_root, fullname.replace('.', '/'))
            spec = ModuleSpec(fullname, self, origin=origin, is_package=is_package)

            return spec
        except Exception:
            traceback.print_exc()
            return None

    def find_module(self, fullname, path=None):
        try:
            spec = self.find_spec(fullname, path)
            if spec is not None:
                return self
            else:
                return None
        except Exception:
            traceback.print_exc()
            return None

    def load_module(self, fullname):
        try:
            if fullname in sys.modules:
                return sys.modules[fullname]

            name_path = fullname.split('.')
            if len(name_path) == 2:
                type_, feature_name, class_name = name_path[1], None, None
            elif len(name_path) == 3:
                type_, feature_name, class_name = name_path[1], name_path[2], None
            elif len(name_path) == 4:
                type_, feature_name, class_name = name_path[1:]
            else:
                raise ImportError("Unknown module {}".format(name_path))

            module = imp.new_module(fullname)
            module.__fakefile__ = os.path.join(self.olympe_root, fullname.replace('.', '/'))
            module.__name__ = fullname
            module.__cached__ = None
            module.__loader__ = self
            module.__all__ = []
            module.__arsdkng_feature_name__ = feature_name
            module.__arsdkng_class_name__ = class_name
            module.__arsdkng_type_name__ = type_
            if feature_name is None:
                module.__path__ = [module.__fakefile__]
                module.__package__ = "olympe.{}".format(type_)
                for feature_name in self.messages.by_feature.keys():
                    setattr(
                        module, feature_name,
                        self.load_module("{}.{}".format(fullname, feature_name))
                    )
                    module.__all__.append(feature_name)
            elif class_name is not None:
                module.__package__ = "olympe.{}.{}".format(type_, feature_name)
                if type_ == "messages":
                    for msg_name, message in self.messages.by_feature[feature_name][class_name].items():
                        obj = message()
                        obj.__module__ = module
                        setattr(module, msg_name, obj)
                        module.__all__.append(msg_name)
                else:
                    for enum_name, enum in self.enums._by_feature[feature_name][class_name].items():
                        setattr(module, enum_name, enum)
                        module.__all__.append(enum_name)
            else:
                module.__path__ = []
                module.__package__ = "olympe.{}".format(type_)
                if type_ == "messages":
                    for msg_name, message in self.messages.by_feature[feature_name].items():
                        if isinstance(message, ArsdkMessage.__class__):
                            obj = message()
                            obj.__module__ = module
                            setattr(module, msg_name, obj)
                            module.__all__.append(msg_name)
                else:
                    for enum_name, enum in self.enums._by_feature[feature_name].items():
                        if isinstance(enum, (ArsdkEnum.__class__, ArsdkBitfield.__class__)):
                            setattr(module, enum_name, enum)
                            enum.__module__ = module
                            module.__all__.append(enum_name)
                for class_name, class_def in self.messages.by_feature[feature_name].items():
                    if not isinstance(class_def, ArsdkMessage.__class__):
                        setattr(
                            module, class_name,
                            self.load_module("{}.{}".format(fullname, class_name))
                        )
                        module.__all__.append(class_name)
            sys.modules[fullname] = module
            return module
        except Exception:
            traceback.print_exc()
            return None

    def get_source(self, modname):
        if modname in sys.modules:
            module = sys.modules[modname]
        else:
            module = self.load_module(modname)

        feature_name = module.__arsdkng_feature_name__
        type_ = module.__arsdkng_type_name__
        source = ""
        if type_ == "messages" and feature_name is not None:
            for name, obj in self.messages.by_feature[feature_name].items():
                if isinstance(obj, ArsdkMessage.__class__):
                    source += "\n\n" + obj.get_source()
                elif isinstance(obj, Mapping):
                    source += "\n\nclass {}:".format(name)
                    for message in obj.values():
                        source += indent("\n",  "   ")
                        source += indent("@staticmethod", "   ")
                        source += indent(message.get_source(), "   ")
                    source += "\n\n"
        elif type_ == "enums" and feature_name is not None:
            for name, obj in self.enums._by_feature[feature_name].items():
                if isinstance(obj, ArsdkEnum.__class__):
                    source += "\n\n" + obj._source_
                elif isinstance(obj, Mapping):
                    for enum in obj.values():
                        source += "\n\n" + enum._source_
                    source += "\n\n"
        else:
            source = ""
        return source

    def create_module(self, spec):
        return self.load_module(spec.name)

    def exec_module(self, module):
        pass
