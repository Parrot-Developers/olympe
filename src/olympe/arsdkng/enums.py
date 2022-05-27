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

import re
import textwrap

from aenum import EnumMeta, OrderedEnum
from collections import OrderedDict
from itertools import starmap

from olympe.arsdkng.proto import ArsdkProto
from olympe.arsdkng.xml import ArsdkXml
from olympe.utils import string_from_arsdkxml, get_mapping


_EnumBase = OrderedEnum


class ArsdkBitfieldMeta(type):

    _base = None
    _classes = OrderedDict()

    def __new__(mcls, enum_type, *args, **kwds):
        """
        Creates an ArsdkBitfield type from its associated enum type
        """
        if ArsdkBitfieldMeta._base is None:
            cls = type.__new__(
                ArsdkBitfieldMeta, builtin_str("ArsdkBitfield"), *args, **kwds)
            mcls._base = cls
        else:
            cls = mcls._classes.get(enum_type)
            if cls is not None:
                return cls
            cls = type.__new__(
                mcls,
                builtin_str(enum_type.__name__ + "_Bitfield"),
                (mcls._base,),
                dict(_enum_type_=enum_type))
            mcls._classes[enum_type] = cls
        return cls

    @property
    def _feature_name_(cls):
        return ArsdkEnums.get(cls._enum_type_.root)._enums_feature[cls._enum_type_]


class ArsdkBitfield(metaclass=ArsdkBitfieldMeta):
    """
    A python base class to represent arsdk bitfield types.
    All bitfields types are derived from this class.
    """

    def __init__(self, enums=[]):
        if isinstance(enums, self.__class__):
            self._enums = enums._enums[:]
        elif isinstance(enums, self._enum_type_):
            self._enums = [enums]
        elif isinstance(enums, (int)):
            # from int
            self._enums = list(map(self._enum_type_, self._bits_order(enums)))
        elif isinstance(enums, (bytes, str)):
            # from str
            self._enums = self.from_str(enums)._enums
        else:
            # from iterable of enums
            enums = list(sorted(map(self._enum_type_, enums)))
            if not all(map(lambda v: isinstance(v, self._enum_type_), enums)):
                raise TypeError(
                    f"Not all values in {enums} are of type {self._enum_type_}")
            seen_enums = set()
            self._enums = [
                enum for enum in enums
                if not (enum in seen_enums or seen_enums.add(enum))]

    @classmethod
    def _bits_order(cls, n):
        while n:
            b = n & (~n + 1)
            order = b.bit_length() - 1
            yield (order if order >= 0 else 0)
            n ^= b

    @classmethod
    def from_str(cls, enums):
        if enums == '':
            return cls([])
        try:
            enums = list(map(cls._enum_type_.__getitem__, enums.split('|')))
        except KeyError as e:
            raise ValueError("{} is not an enum label of {}".format(
                str(e), cls._enum_type_.__name__))
        return cls(enums)

    @classmethod
    def empty(cls):
        return cls()

    @classmethod
    def full(cls):
        return ~cls()

    def to_str(self):
        return str(self)

    def to_flag_list(self):
        flags = []
        for enum in self.full():
            flags.append(enum in self)
        return flags

    def __getattr__(self, name):
        try:
            enum = self.__class__._enum_type_.__getitem__(name)
        except KeyError:
            raise AttributeError(
                f'{name} is not a {self.__class__.__name__} bitfield flag')
        return enum in self

    def __str__(self):
        return '|'.join(map(lambda v: v.name, self._enums))

    def __repr__(self):
        return f'<{self.__class__.__name__}: {self._enums}>'

    def pretty(self):
        return "'" + '|'.join(map(lambda v: v.name, self._enums)) + "'"

    def __contains__(self, enum):
        return enum in self._enums

    def __iter__(self):
        return iter(self._enums)

    def __len__(self):
        return len(self._enums)

    def to_int(self):
        r = 0
        for enum in self._enums:
            r += 2 ** enum.value
        return r

    def __invert__(self):
        return self.__class__([enum for enum in self._enum_type_ if enum not in self._enums])

    def __or__(self, other):
        other = self.__class__(other)
        return self.__class__(self._enums + other._enums)

    def __and__(self, other):
        other = self.__class__(other)
        return self.__class__([enum for enum in self._enums if enum in other._enums])

    def __xor__(self, other):
        other = self.__class__(other)
        return self & ~other | ~self & other

    __ror__ = __or__
    __rand__ = __and__
    __xor__ = __xor__

    def __eq__(self, other):
        other = self.__class__(other)
        return self.to_int() == other.to_int()

    def __neq__(self, other):
        return not self == other

    def __nonzero__(self):
        return bool(self.to_int())

    __bool__ = __nonzero__  # Python 3


class ArsdkEnumMeta(_EnumBase.__class__):

    _base = None
    _classes = OrderedDict()
    _aliases = OrderedDict()

    def __new__(mcls, name, bases, ns, **kwds):
        """
        Creates an ArsdkEnum type and its associated bitfield type.
        All equivalent enum types are derived from a common based enum class.
        Two enum types are considered equal if they define the same labels.
        """
        if ArsdkEnumMeta._base is None:
            cls = _EnumBase.__class__.__new__(mcls, builtin_str(name), (_EnumBase,), ns, **kwds)
            ArsdkEnumMeta._base = cls
        else:
            # Arsdk enums may have aliases.
            # For example the following enums found in ardrone3.xml and rth.xml
            # should be comparable
            #
            # <arg name="state" type="enum">
            #     <enum name="available"></enum>
            #     <enum name="inProgress"></enum>
            #     <enum name="unavailable"></enum>
            #     <enum name="pending"></enum>
            # </arg>
            # <enum name="state">
            #     <value name="available"></value>
            #     <value name="in_progress"></value>
            #     <value name="unavailable"></value>
            #     <value name="pending"></value>
            # </enum>
            #
            # Notice the subtle difference between the two labels
            # "state.inProgress" vs "state.in_progress"
            #
            # This modules defines one enum type for each xml definition and
            # automatically define enum aliases for equivalent definitions.
            # The following code handles these aliases so that the user should
            # not be bothered with this. Enum types that have the same
            # ArsdkEnumAlias_* base class are comparable with each others.

            class_key = (name,) + tuple(starmap(lambda k, v: k + "_" + str(v), ns.items()))
            cls = mcls._classes.get(class_key)
            if cls is not None:
                return cls

            alias_key = tuple(starmap(
                lambda k, v: (k.replace('_', '').lower(), v), ns.items()))
            alias_name = (label + "_" + str(value) for label, value in alias_key)
            alias_name = str("ArsdkEnumAlias_" + '_'.join(alias_name))

            if alias_key not in mcls._aliases:
                alias_base = _EnumBase.__class__.__new__(
                    mcls, builtin_str(alias_name), (ArsdkEnumMeta._base,), {})
                mcls._aliases[alias_key] = alias_base
            else:
                alias_base = mcls._aliases[alias_key]
            kwds.pop("root", None)
            cls = _EnumBase.__class__.__new__(mcls, builtin_str(name), (alias_base,), ns, **kwds)
            mcls._classes[class_key] = cls
        return cls

    @classmethod
    def __prepare__(mcls, cls, bases, *args, **kwds):
        if bases and not issubclass(bases[-1], _EnumBase):
            bases = (bases[-1], _EnumBase)
        elif not bases:
            bases = (_EnumBase,)
        return _EnumBase.__class__.__prepare__(cls, bases, *args, **kwds)

    @property
    def _bitfield_type_(cls):
        return ArsdkBitfieldMeta.__new__(ArsdkBitfieldMeta, cls)

    @property
    def _feature_name_(cls):
        return ArsdkEnums.get(cls._root_)._enums_feature[cls]

    @property
    def _source_(cls):
        return ArsdkEnums.get(cls._root_)._enums_source[cls]


class ArsdkEnum(metaclass=ArsdkEnumMeta):
    """
    A python class to represent arsdk enum types
    All enum types are derived from this class.
    """

    @classmethod
    def from_str(cls, value):
        if value == '':
            raise ValueError(f"Empty string cannot be converted to {cls.__name__}")
        try:
            return cls[value]
        except KeyError as e:
            raise ValueError(f"{str(e)} is not an enum label of {cls.__name__}")

    def to_str(self):
        return self._name_

    def pretty(self):
        return f"'{self.to_str()}'"

    def _to_bitfield(self):
        return self.__class__._bitfield_type_([self])

    def __invert__(self):
        return self._to_bitfield().__invert__()

    def __or__(self, other):
        return self._to_bitfield().__or__(other)

    def __and__(self, other):
        return self._to_bitfield().__and__(other)

    def __xor__(self, other):
        return self._to_bitfield().__xor__(other)

    __ror__ = __or__
    __rand__ = __and__
    __xor__ = __xor__

    @classmethod
    def aliases(cls):
        if cls.__base__.__name__.startswith("ArsdkEnumAlias"):
            return [alias for alias in cls.__base__.__subclasses__()]
        else:
            return []

    def __eq__(self, other):
        if other.__class__ in self.aliases():
            return self._value_ == other._value_
        else:
            return NotImplemented

    def __ne__(self, other):
        if other.__class__ in self.aliases():
            return self._value_ != other._value_
        else:
            return NotImplemented

    def __hash__(self):
        return self._value_


class ArsdkProtoEnumMeta(EnumMeta):
    @property
    def _feature_name_(cls):
        return ArsdkEnums.get(cls._root_)._enums_feature[cls]

    @property
    def _source_(cls):
        return ArsdkEnums.get(cls._root_)._enums_source[cls]


class ArsdkProtoEnum(OrderedEnum, metaclass=ArsdkProtoEnumMeta):
    @classmethod
    def from_str(cls, value):
        if value == '':
            raise ValueError(f"Empty string cannot be converted to {cls.__name__}")
        try:
            return cls[value]
        except KeyError as e:
            raise ValueError(f"{str(e)} is not an enum label of {cls.__name__}")

    def to_str(self):
        return self._name_

    def to_upper_str(self):
        for name, value in self.__class__.__members__.items():
            if value is self and name.isupper():
                return name
        return self.to_str()

    def __hash__(self):
        return hash(f"{self.__class__.__name__}.{self.to_upper_str()}")

    def __eq__(self, other):
        if other == self._name_:
            return True
        if other == self._value_:
            return True
        if other == self.to_upper_str():
            return True
        return False

    def __ne__(self, other):
        return not (self == other)

    # This is needed for json serialization support.
    # IntEnum and stdlib json are currently not working properly
    # See: https://bugs.python.org/issue18264
    def __str__(self):
        return (f'"olympe.enums.{self.__class__._feature_name}'
                f'.{self.__class__.__name__}.{self._name_}"')

    def pretty(self):
        return f"'{self.to_str()}'"

    def __int__(self):
        # Needed for python3 json stdlib
        return self

    @property
    def _feature_name(cls):
        return ArsdkEnums.get(cls._root_)._enums_feature[cls]

    @property
    def _source(cls):
        return ArsdkEnums.get(cls._root_)._enums_source[cls]


class list_flags(ArsdkEnum):
    """
    Arsdk built-in "list_flags" enum that is used to in "LIST_ITEM" event messages
    """
    First, Last, Empty, Remove = range(4)


list_flags._root_ = "olympe"


class ArsdkEnums:

    _store = {}

    @classmethod
    def get(cls, root):
        ret = cls._store.get(root)
        if ret is None:
            ret = ArsdkEnums(root)
        return ret

    def __init__(self, root):
        """
        ArsdkEnums constructor
        """
        self._root = root
        self.__class__._store[root] = self
        self._ctx = ArsdkXml.get(root).ctx
        self._proto = ArsdkProto.get(root)
        self._bitfields = OrderedDict()
        self._by_feature = OrderedDict()
        self._enums_feature = OrderedDict()
        self._enums_source = OrderedDict()
        list_flags_values = "\n".join(
            map(lambda v: (v._name_ + " = " + str(v._value_)), list_flags))
        self._enums_source[list_flags] = textwrap.dedent(
            f"""
            class list_flags(ArsdkEnum):
                {list_flags_values}


            list_flags._root_ = {self._root}
            """
        )
        for feature in self._ctx.features:
            if feature.name not in self._bitfields:
                self._bitfields[feature.name] = OrderedDict()
            if feature.name not in self._by_feature:
                self._by_feature[feature.name] = OrderedDict()
            for class_name in feature.classesByName:
                if class_name not in self._by_feature[feature.name]:
                    self._by_feature[feature.name][class_name] = OrderedDict()
            for enum in feature.enums:
                self._add_enum(feature, enum)
            self._bitfields[feature.name]["list_flags_Bitfield"] = list_flags._bitfield_type_
            self._by_feature[feature.name]["list_flags"] = list_flags
            self._by_feature[feature.name]["list_flags_Bitfield"] = list_flags._bitfield_type_

        for feature in self._by_feature.values():
            for enum in feature.values():
                if (
                        isinstance(enum, ArsdkEnum.__class__) and
                        len(enum.aliases()) > 1 and
                        "Enum aliases" not in enum.__doc__):
                    try:
                        doc = "\n    - ".join(map(
                            lambda a: ":py:class:`olympe.enums.{}.{}`".format(
                                self._enums_feature[a], a.__name__),
                            enum.aliases()))
                        doc = (
                            "\n\nEnum aliases:\n\n" +
                            "    - " + doc +
                            "\n\n"
                        )

                        enum.__doc__ = enum.__doc__ + doc
                    except KeyError:
                        pass
        for feature_name, feature in self._proto.features.items():
            for enum in feature.enums:
                self._add_proto_enum(enum)

        for feature_name, feature in self._proto.features.items():
            for service in feature.services:
                for enum_desc in service.enums:
                    self._add_proto_enum(enum_desc)

        for feature_name, feature in self._proto.features.items():
            for service in feature.services:
                for message_desc in service.messages:
                    self._bitfields.setdefault(feature_name, OrderedDict())
                    self._by_feature.setdefault(feature_name, OrderedDict())

    def _add_enum(self, feature, enumObj):
        values = OrderedDict()
        for enumValObj in enumObj.values:
            values[enumValObj.name] = enumValObj.value
        enum_class = None
        name = enumObj.name
        for class_name in feature.classesByName:
            prefix = class_name + "_"
            if name.startswith(prefix):
                name = enumObj.name[len(prefix):]
                enum_class = class_name
                break
        enum = ArsdkEnum(name, names=values)
        enum.__doc__ = string_from_arsdkxml(enumObj.doc)
        enum._root_ = self._root
        for enumvalue, enumvalueobj in zip(enum, enumObj.values):
            enumvalue.__doc__ = string_from_arsdkxml(enumvalueobj.doc)
        bitfield = enum._bitfield_type_
        self._enums_feature[enum] = feature.name
        values = "\n".join(
            map(lambda v: (v._name_ + " = " + str(v._value_)), enum))
        self._enums_source[enum] = textwrap.dedent(
            f"""
            class {enumObj.name}(ArsdkEnum):
                {enum.__doc__}
                {values}


            {enumObj.name}._root_ = {self._root}
            """
        )
        self._bitfields[feature.name][bitfield.__name__] = bitfield
        self._by_feature[feature.name][enum.__name__] = enum
        if enum_class is not None:
            self._by_feature[feature.name][enum_class + "_" + enum.__name__] = enum
            self._by_feature[feature.name][enum_class + "_" + bitfield.__name__] = bitfield
            self._by_feature[feature.name][enum_class][enum.__name__] = enum
            self._by_feature[feature.name][enum_class][bitfield.__name__] = bitfield

    def _add_proto_enum(self, enum_desc):
        feature_name = enum_desc.feature_name
        path = enum_desc.path.split(".")
        feature_path = feature_name.split(".")
        context = self._by_feature
        for part in feature_path:
            if part not in context:
                context[part] = OrderedDict()
            context = context[part]
        context = get_mapping(self._by_feature, feature_path)
        for part in path[:-1]:
            if part not in context:
                context[part] = OrderedDict()
            context = context[part]
        values = {k: v.index for k, v in enum_desc.enum.values_by_name.items()}
        # Add short form enum aliases for convenience
        # For example: CameraMode.photo for CameraMode.CAMERA_MODE_PHOTO
        aliases = {}
        for name, value in values.items():
            camel_name = ''.join(word.title() for word in name.split('_'))
            if camel_name.startswith(enum_desc.name):
                enum_case_snake_case = re.sub(r"([A-Z])([a-z])", "\1_\2", enum_desc.name).upper()
                alias = name[len(enum_case_snake_case):].lower()
                aliases[alias] = value
        if len(aliases) == len(values):
            # Put the short aliases first so that the long-form is actually an Enum value alias
            # This is only important for enum values representation
            aliases.update(values)
            values = aliases
        enum = ArsdkProtoEnum(
            enum_desc.name,
            names=values
        )
        enum.__doc__ = ""
        enum._root_ = self._root
        self._enums_source[enum] = textwrap.dedent(
            f"""
            class {enum_desc.name}(ArsdkProtoEnum):
                {enum.__doc__}
                {values}


            {enum_desc.name}._root_ = {self._root}
            """
        )
        self._enums_feature[enum] = feature_name
        for enumvalue in enum:
            enumvalue.__doc__ = ""
        if enum_desc.doc:
            enum.__doc__ = enum_desc.doc.doc
            for enumvalue, value_doc in zip(enum, enum_desc.doc.values_doc):
                enumvalue.__doc__ = value_doc.doc
        context[enum_desc.name] = enum
        return enum

    def __getitem__(self, feature_name):
        return self._by_feature[feature_name]

    def walk(self):
        for feature_name, feature in self._by_feature.items():
            for enum_name, enum in feature.items():
                for enum_label, enum_value in enum.__members__.items():
                    yield feature_name, enum_name, enum_label, enum_value


if __name__ == '__main__':
    # Tests
    import unittest

    class FlyingState(ArsdkEnum):
        LANDED = 0
        LANDING = 1
        TAKING_OFF = 2
        HOVERING = 3
        FLYING = 4

    class TestEnums(unittest.TestCase):

        def test_enum(self):
            for i, name in enumerate(
                    ['LANDED', 'LANDING', 'TAKING_OFF', 'HOVERING', 'FLYING']):
                # no implicit int conversion (for the greater good)
                with self.assertRaises(TypeError):
                    self.assertEqual(int(FlyingState(i)), i)
                with self.assertRaises(TypeError):
                    self.assertEqual(int(FlyingState[name]), i)
                self.assertEqual(FlyingState(i).value, i)
                self.assertEqual(FlyingState(i)._value_, i)
                self.assertEqual(FlyingState[name]._value_, i)
                self.assertEqual(FlyingState[name].value, i)
                self.assertEqual(str(FlyingState[name]), 'FlyingState.' + name)
                self.assertEqual(str(FlyingState(i)), 'FlyingState.' + name)
                self.assertEqual(FlyingState[name].name, name)
                self.assertEqual(FlyingState[name]._name_, name)
                self.assertEqual(FlyingState(i).name, name)
                self.assertEqual(FlyingState(i)._name_, name)

        def test_bitfield(self):
            # bitwise operations
            self.assertEqual(
                FlyingState.LANDED | 'LANDING' | 4,
                [
                    FlyingState.LANDED,
                    FlyingState.LANDING,
                    FlyingState.TAKING_OFF
                ]
            )
            self.assertEqual(
                ~FlyingState._bitfield_type_(),
                [
                    FlyingState.LANDED,
                    FlyingState.LANDING,
                    FlyingState.TAKING_OFF,
                    FlyingState.HOVERING,
                    FlyingState.FLYING
                ]
            )
            self.assertEqual(
                (FlyingState.LANDED | 'LANDING' | 4) & 'LANDING',
                [FlyingState.LANDING]
            )
            self.assertEqual(
                ~FlyingState.LANDED,
                [
                    FlyingState.LANDING,
                    FlyingState.TAKING_OFF,
                    FlyingState.HOVERING,
                    FlyingState.FLYING
                ]
            )
            self.assertEqual(
                (FlyingState.LANDED | 'LANDING' | 4) ^ 'LANDING',
                [FlyingState.LANDED, FlyingState.TAKING_OFF]
            )

            # string conversions
            self.assertEqual(
                str(FlyingState._bitfield_type_('LANDED')), 'LANDED')
            self.assertEqual(
                str(FlyingState._bitfield_type_('LANDED|LANDING')),
                'LANDED|LANDING'
            )
            self.assertEqual(
                str(FlyingState._bitfield_type_('LANDED|LANDING|TAKING_OFF')),
                'LANDED|LANDING|TAKING_OFF'
            )
            self.assertEqual(
                str(FlyingState._bitfield_type_(
                    'LANDED|LANDING|TAKING_OFF|HOVERING')),
                'LANDED|LANDING|TAKING_OFF|HOVERING'
            )
            self.assertEqual(
                str(FlyingState._bitfield_type_(
                    'LANDED|LANDING|TAKING_OFF|HOVERING|FLYING')),
                'LANDED|LANDING|TAKING_OFF|HOVERING|FLYING'
            )
            self.assertEqual(
                str(FlyingState._bitfield_type_(
                    'LANDING|LANDED|TAKING_OFF|HOVERING|FLYING')),
                'LANDED|LANDING|TAKING_OFF|HOVERING|FLYING'
            )

            # collection operations
            self.assertEqual(
                list(FlyingState.LANDED | 'LANDING'),
                [FlyingState.LANDED, FlyingState.LANDING]
            )
            self.assertTrue(
                FlyingState.LANDING in FlyingState.LANDED | 'LANDING'
            )

            # empty bitfield
            self.assertEqual(FlyingState._bitfield_type_(''), '')
            self.assertEqual(
                repr(FlyingState._bitfield_type_('')),
                '<FlyingState_Bitfield: []>'
            )
            self.assertEqual(FlyingState._bitfield_type_('').to_int(), 0)

            # no implicit int conversion
            with self.assertRaises(TypeError):
                self.assertEqual(int(FlyingState._bitfield_type_('')), 0)

            # bitfield values
            for i in range(2**5):
                self.assertEqual(FlyingState._bitfield_type_(i).to_int(), i)

            # bitfield attributes
            bitfield = FlyingState._bitfield_type_.from_str(
                'LANDED|LANDING|TAKING_OFF|HOVERING')
            self.assertTrue(bitfield.LANDED)
            self.assertTrue(bitfield.LANDING)
            self.assertTrue(bitfield.TAKING_OFF)
            self.assertTrue(bitfield.HOVERING)
            self.assertFalse(bitfield.FLYING)

            # to_flag_list
            self.assertEqual(
                FlyingState._bitfield_type_.full().to_flag_list(),
                [True] * len(FlyingState))
            self.assertEqual(
                FlyingState._bitfield_type_.empty().to_flag_list(),
                [False] * len(FlyingState))
            self.assertEqual(
                FlyingState._bitfield_type_('LANDED').to_flag_list(),
                [True] + [False] * (len(FlyingState) - 1))

        def test_errors(self):
            with self.assertRaises(ValueError):
                FlyingState._bitfield_type_('FOOBAR')

            with self.assertRaises(ValueError):
                FlyingState._bitfield_type_('LANDED|FOOBAR')

            with self.assertRaises(ValueError):
                FlyingState.from_str('')

            with self.assertRaises(ValueError):
                FlyingState.from_str('FOOBAR')

            with self.assertRaises(ValueError):
                FlyingState.from_str('LANDED|FOOBAR')

    unittest.main()
