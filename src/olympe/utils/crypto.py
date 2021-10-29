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

from aenum import Enum
from Cryptodome.Hash import SHA256, SHA512
from Cryptodome.Signature import DSS, pkcs1_15
from Cryptodome.PublicKey import RSA, DSA, ECC
from dataclasses import dataclass, InitVar
from typing import AnyStr, Any, Dict, NewType, Optional, Tuple, Union
import asn1tools
import binascii
import dacite
import dataclasses
import tarfile


def _unhexlify_if_necessary(data: AnyStr):
    if isinstance(data, bytes):
        return data
    else:
        return binascii.unhexlify(data)


class KeyKind(Enum):
    RSA = object()
    DSA = object()
    ECC = object()


_key_kind_pycrypto_map = {KeyKind.RSA: RSA, KeyKind.DSA: DSA, KeyKind.ECC: ECC}

_key_oid_kind_map = {
    "1.2.840.113549.1.1.1": KeyKind.RSA,
    "1.2.840.10040.4.1": KeyKind.DSA,
    "1.2.840.10045.2.1": KeyKind.ECC,  # id-ecPublicKey
    "1.3.132.1.12": KeyKind.ECC,  # id-ecDH
    "1.3.132.1.13": KeyKind.ECC,  # id-ecMQV
}

_key_signature_scheme_map = {
    KeyKind.RSA: pkcs1_15.new,
    KeyKind.DSA: lambda key: DSS.new(key, "fips-186-3", encoding="der"),
    KeyKind.ECC: lambda key: DSS.new(key, "fips-186-3", encoding="der"),
}


class PublicKeyCodecBase:
    _CodecDict = asn1tools.parse_string(
        """
    -- Simplified public key definition based on IETF/RFC5912
    -- Support RSA, DSA and ECC (**named curve only**) public keys
    SimplifiedPublicKey DEFINITIONS IMPLICIT TAGS ::= BEGIN

    PublicKey ::= SEQUENCE {
      algorithm SEQUENCE {
        algorithmIdentifier OBJECT IDENTIFIER,
        algorithmParameters CHOICE {
            rsaParams NULL,
            dsaParams SEQUENCE {
                p INTEGER,
                q INTEGER,
                g INTEGER
            },
            ecNamedCurve OBJECT IDENTIFIER
        } OPTIONAL
      },
      subjectPublicKeyInfo BIT STRING
    }
    END
    """
    )

    @classmethod
    def decode(cls, input_: AnyStr) -> AnyStr:
        return cls._Codec.decode("PublicKey", input_)

    @classmethod
    def encode(cls, input_: AnyStr) -> AnyStr:
        return cls._Codec.encode("PublicKey", input_)


class DerPublicKeyCodec(PublicKeyCodecBase):
    _Codec = asn1tools.compile_dict(PublicKeyCodecBase._CodecDict, codec="der")


class JerPublicKeyCodec(PublicKeyCodecBase):
    _Codec = asn1tools.compile_dict(PublicKeyCodecBase._CodecDict, codec="jer")


DerBitStringValueType = NewType("DerBitStringValueType", bytes)
ObjectIdentifier = NewType("ObjectIdentifier", str)


@dataclass
class AlgorithmDSAParams:
    p: int
    q: int
    g: int


AlgorithmECCParamsNamedCurve = NewType("AlgorithmECCParamsNamedCurve", ObjectIdentifier)
AlgorithmParams = NewType(
    "AlgorithmParams",
    Tuple[str, Union[None, AlgorithmDSAParams, AlgorithmECCParamsNamedCurve]],
)


@dataclass
class Algorithm:
    algorithmIdentifier: ObjectIdentifier
    algorithmParameters: AlgorithmParams


DerBitStringType = NewType("DerBitStringType", Tuple[DerBitStringValueType, int])


PyCryptoKeyType = NewType("PyCryptoKeyType", Any)


@dataclass
class PublicKey:
    """
    PublicKey (RSA,DSA,ECC) dataclass with some convenient data conversion
    methods and a verify() method to perform RSA/PKCS#1v.15 and (EC)DSA/DSS
    signature verifications.
    """

    algorithm: Algorithm
    subjectPublicKeyInfo: DerBitStringType

    pycrypto_key: InitVar[Optional[PyCryptoKeyType]] = dataclasses.field(default=None)

    def __eq__(self, other):
        return (
            self.algorithm == other.algorithm
            and self.subjectPublicKeyInfo == other.subjectPublicKeyInfo
        )

    @property
    def kind(self):
        return _key_oid_kind_map[self.algorithm.algorithmIdentifier]

    @classmethod
    def from_dict(cls, data: Dict):
        return dacite.from_dict(cls, data, config=_dacite_config)

    @classmethod
    def from_jer(cls, data: AnyStr):
        """PublicKey from ASN.1/JER RFC5912 14. SubjectPublicKeyInfo"""
        if isinstance(data, str):
            data = data.encode()
        data = JerPublicKeyCodec.decode(data)
        return cls.from_dict(data)

    @classmethod
    def from_der(cls, data: AnyStr):
        """PublicKey from ASN.1/DER RFC5912 14. SubjectPublicKeyInfo"""
        data = _unhexlify_if_necessary(data)
        data = DerPublicKeyCodec.decode(data)
        return cls.from_dict(data)

    @classmethod
    def from_pycrypto(cls, pycrypto_key: PyCryptoKeyType):
        der_data = pycrypto_key.public_key().export_key(format="DER")
        key = cls.from_der(der_data)
        key._pycrypto_key = pycrypto_key
        return key

    def as_dict(self) -> Dict:
        return dataclasses.asdict(self)

    def as_jer(self) -> str:
        return JerPublicKeyCodec.encode(self.as_dict()).decode()

    def as_der(self) -> bytes:
        return bytes(DerPublicKeyCodec.encode(self.as_dict()))

    def __post_init__(self, pycrypto_key):
        if pycrypto_key is None:
            self._pycrypto_key = self._import_key(self.as_der(), key_kind=self.kind)
        else:
            assert pycrypto_key.public_key() == self._import_key(self.as_der())
            self._pycrypto_key = pycrypto_key

    @property
    def __key__(self):
        return self._pycrypto_key

    @property
    def __public_key__(self):
        return self._pycrypto_key.public_key()

    @classmethod
    def _import_key(cls, pub_key_der: AnyStr, key_kind: Optional[KeyKind] = None):
        if key_kind is None:
            key_types = list(map(lambda k: _key_kind_pycrypto_map[k], KeyKind))
        else:
            key_types = [_key_kind_pycrypto_map[key_kind]]
        last_e = None
        for key_type in key_types:
            try:
                return key_type.import_key(pub_key_der)
            except (ValueError, IndexError, TypeError) as e:
                last_e = e
        raise last_e

    def verify(self, hash_object, signature):
        # TODO: we might want to perform hash_object / signature parameters
        # type validation before handing off the verification to pycryptodome
        # (for example for key-algorithm/key-size constrain the hashing
        # algorithm) in order to improve programming errors diagnosis.
        signature = _unhexlify_if_necessary(signature)
        scheme_type = _key_signature_scheme_map[self.kind]
        scheme = scheme_type(self.__public_key__)
        try:
            scheme.verify(hash_object, signature)
            return True
        except ValueError:
            return False


_dacite_config = dacite.Config(
    type_hooks={DerBitStringValueType: _unhexlify_if_necessary}
)


class HashType(Enum):
    SHA256 = SHA256
    SHA512 = SHA512


def tarball_hash(filepath, filenames=None, hash_type=HashType.SHA512):
    """
    Returns an hash object of the `filepath` tarball. If filenames is
    not None (the default) compute the hash for the given file names
    archive members. Otherwise, every file in the tarball is used
    to compute the hash. The final returned hash depends on the
    effectively hashed file names.
    """
    hash_type = hash_type._value_
    hash_object = hash_type.new()
    with tarfile.open(filepath) as f:
        if filenames is None:
            filenames = f.getnames()
        for filename in filenames:
            hash_object.update(hash_type.new(f.extractfile(filename).read()).digest())
    hash_object.update(";".join(filenames).encode())
    return hash_object


__all__ = ["PublicKey", "tarball_hash"]
