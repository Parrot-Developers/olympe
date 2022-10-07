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


from .arsdkng.enums import ArsdkEnums
from .arsdkng.messages import ArsdkMessages, ArsdkMessageBase
from .arsdkng.proto import ArsdkProto
from .log import LogMixin
from .module_loader import module_loader
from .utils.path import directory_is_writable, olympe_data_dir, Resource
from .utils.crypto import tarball_hash, PublicKey
from aenum import Enum
from collections import defaultdict
from dataclasses import dataclass
from google.protobuf import json_format as protobuf_json_format
from pathlib import Path
from typing import List, Optional, Union
import binascii
import functools
import dacite
import io
import json
import olympe.messages.mission
import operator
import requests
import tarfile
import threading
import tempfile


@dataclass
class MissionMetadata:
    uid: str
    """Unique ID of the mission"""

    name: str
    """Name of the mission"""

    desc: str
    """Description of the mission"""

    version: str
    """Version of the mission"""

    target_model_id: int
    """Model ID of the drone product"""

    target_min_version: str
    """Minimum version of drone firmware supported"""

    target_max_version: str
    """Maximum version of drone firmware supported"""

    build_sdk_version: str
    """SDK version used to build the mission"""

    build_sdk_target_arch: str
    """Target architecture used to build the mission"""

    @classmethod
    def from_dict(cls, data):
        def _from_int_str(x):
            try:
                return int(x)
            except ValueError:
                return int(f"0x{x}", 16)

        return dacite.from_dict(
            cls, data, dacite.Config(type_hooks={int: _from_int_str})
        )


@dataclass
class MissionMetadataRemote(MissionMetadata):
    digest: str
    """SHA512 digest hex dump"""


@dataclass
class MissionSignature:
    filenames: List[str]
    """List of signed file names"""

    pub_key_der: str
    """ASN.1/DER public key hex dump"""

    pub_key_rpb: Optional[str]
    """(optional) public key in a pre-computed form (RPB)"""

    signature: str
    """fips-186-3/SHA512 signature of the mission files as an hex dump"""

    @property
    def public_key(self):
        return PublicKey.from_der(self.pub_key_der)

    @classmethod
    def from_file(cls, signature_filepath):
        with open(signature_filepath) as signature_file:
            signature_data = signature_file.read()
        signature = dict()
        for line in signature_data.split("\n"):
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if ";" in value:
                value = value.split(";")
                value = [v.strip() for v in value if v.strip()]
            if value:
                signature[key] = value
        return dacite.from_dict(cls, signature)

    def to_file(self, signature_filepath):
        with open(signature_filepath, "w") as signature_file:
            signature_file.write(self.to_string())

    def to_string_io(self):
        signature = io.StringIO()
        signature.write(f"filenames={';'.join(self.filenames)}\n")
        signature.write(f"pub_key_der={self.pub_key_der}\n")
        if self.pub_key_rpb is not None:
            signature.write(f"pub_key_rpb={self.pub_key_rpb}\n")
        else:
            signature.write("pub_key_rpb=\n")
        signature.write(f"signature={self.signature}\n")
        return signature.getvalue()

    def to_string(self):
        signature = self.to_string_io()
        content = signature.getvalue()
        signature.close()
        return content


class MissionHttpApiError(Enum):

    _init_ = "value string"

    UidExistsError = (
        403,
        "A mission with the same uid already exists (allow_downgrade = no?)",
    )
    RequestError = 405, "Request error"
    TarballReadError = (
        415,
        "Error while reading the mission tarball (bad file format or signature ?)",
    )
    ServerInternalError = 500, "The server encountered an internal error"
    NoSpaceLeftError = 507, "No more space left in the internal storage"


class MissionController(LogMixin):
    def __init__(self, scheduler, hostname, name=None, version=1, mission_dir=None):
        super().__init__(name, None, "missions")
        self._root = "olympe.airsdk"
        if name:
            self._root += f".{name}"
            module_loader.add_package_root(self._root)
        if mission_dir is None:
            mission_dir = olympe_data_dir() / "missions"
            mission_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
        if not mission_dir.is_dir():
            self.logger.error(f"{mission_dir} is not a directory")
            mission_dir = None
        if not directory_is_writable(mission_dir):
            self.logger.error(f"{mission_dir} is not a writable directory")
            mission_dir = None
        self._mission_dir = mission_dir
        self._scheduler = scheduler
        self._hostname = hostname
        self._version = version
        self._missions = defaultdict(list)
        self._arsdk_mission = ArsdkMessages.get("olympe").by_feature["mission"]
        self._session = requests.Session()
        self._mission_api_url = (
            f"http://{self._hostname}/api/v{self._version}/mission/missions"
        )

    @property
    def mission_dir(self):
        return self._mission_dir

    def list_remote(self):
        try:
            response = self._session.get(self._mission_api_url)
            response.raise_for_status()
        except requests.HTTPError as e:
            msg = str(e)
            try:
                msg = MissionHttpApiError(e.response.status_code).string
            except ValueError:
                pass
            self.logger.error(msg)
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(str(e))
            return None
        except Exception as e:
            self.logger.error(str(e))
            return None
        missions = response.json()
        missions = [MissionMetadataRemote.from_dict(m) for m in missions]
        return missions

    def from_uid(self, uid, version=None):
        missions = self._missions.get(uid)
        if not missions:
            raise KeyError(f"Unknown mission uid: {uid}")
        if version is None:
            return max(missions, key=operator.attrgetter("version"))
        for mission in missions:
            if mission.version == version:
                return mission
        raise KeyError(f"Unknown mission uid: '{uid}' version '{version}'")

    def from_path(self, url_or_path: Union[Path, str], feature_name_from_file=False):
        """
        Creates and returns an olympe.Mission object from a local path or an URL to an
        AirSDK mission archive.
        """
        return Mission(
            self, Resource(url_or_path), feature_name_from_file=feature_name_from_file
        )

    def set_device_name(self, device_name):
        super().set_device_name(device_name)
        for missions in self._missions.values():
            for mission in missions:
                mission.logger = self.logger

    def _open(self, mission, verify=True, raw=False, ca_pub_key_der=None):
        verify = verify or bool(ca_pub_key_der)
        with tempfile.TemporaryDirectory() as root_tmp_dir:
            with tarfile.open(mission.filepath) as f:
                f.extractall(root_tmp_dir)
            signature = None
            for signature_path in Path(root_tmp_dir).glob("signature.ecdsa*"):
                signature = MissionSignature.from_file(signature_path)
                break
            with open(Path(root_tmp_dir) / "mission.json") as f:
                mission_json = MissionMetadata.from_dict(json.load(f))
            with tempfile.TemporaryDirectory() as payload_tmp_dir:
                payload_proto_path = Path(payload_tmp_dir) / "share" / "protobuf"
                with tarfile.open(Path(root_tmp_dir) / "payload.tar.gz") as f:
                    f.extractall(payload_tmp_dir)
                    features, messages, enums, modules = self._load_protos(
                        mission.filepath,
                        mission_json.uid,
                        payload_proto_path,
                        raw=raw,
                        feature_name_from_file=mission._feature_name_from_file,
                    )
        if mission._signature is not None and mission._signature != signature:
            raise ValueError(
                f"Mission signature mismatch {mission._signature} != {signature}"
            )
        if mission._data is not None and mission._data != mission_json:
            raise ValueError(f"Mission data mismatch {mission._data} != {mission_json}")
        if mission_json.uid in self._missions:
            for existing_mission in self._missions[mission_json.uid]:
                if existing_mission._data == mission_json:
                    self.logger.info(
                        f"Opening mission '{mission_json.uid}' multiple times"
                    )
        (
            mission._data,
            mission._messages,
            mission._enums,
            mission._modules,
            mission._signature,
        ) = (
            mission_json,
            messages,
            enums,
            modules,
            signature,
        )
        if verify:
            mission.verify()
        mission._subscribe(self._scheduler)
        self._missions[mission.uid].append(mission)
        return mission

    def _install(
        self,
        mission,
        verify=True,
        ca_pub_key_der=None,
        allow_downgrade=None,
        is_default=None,
        timeout=30,
        **kwds,
    ):
        if mission.uid not in self._missions:
            self._open(mission, verify=verify, ca_pub_key_der=ca_pub_key_der)
            mission._relocate_in_mission_dir()
        params = kwds
        if allow_downgrade is not None:
            params.update(allow_downgrade=allow_downgrade)
        if is_default is not None:
            params.update(is_default=is_default)

        def _bool_to_yes_no(value):
            return "yes" if value else "no"

        params = {
            k: (_bool_to_yes_no(v) if isinstance(v, bool) else v)
            for k, v in params.items()
        }
        headers = {"Content-type": "application/gzip"}
        with open(mission.filepath, "rb") as mission_payload:
            try:
                # NOTE: the extra / is currently mandatory for this HTTP PUT request
                response = self._session.put(
                    self._mission_api_url + "/",
                    data=mission_payload,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
            except requests.HTTPError as e:
                msg = str(e)
                try:
                    msg = MissionHttpApiError(e.response.status_code).string
                except ValueError:
                    pass
                self.logger.error(msg)
                return False
            except requests.exceptions.RequestException as e:
                self.logger.error(str(e))
                return False
            except Exception as e:
                self.logger.error(str(e))
                return False
            self.logger.info(f"Mission {mission.uid} installed to {self._hostname}")
            return True

    def _close(self, mission_uid):
        del self._missions[mission_uid]
        # TODO: unload mission messages/enums module

    def _load_protos(
        self,
        mission_path,
        mission_uid,
        payload_proto_path,
        feature_name_from_file=False,
        raw=False,
    ):
        arsdk_proto = ArsdkProto.get(self._root)
        messages = ArsdkMessages.get(self._root)
        enums = ArsdkEnums.get(self._root)
        modules = arsdk_proto.parse_mission_proto(
            payload_proto_path,
            mission_uid,
            feature_name_from_file=feature_name_from_file,
            raw=raw,
        )
        mission_messages = dict()
        mission_enums = dict()
        if not arsdk_proto.features or raw:
            return (
                arsdk_proto.features,
                mission_messages,
                mission_enums,
                modules,
            )
        for feature_name, feature in arsdk_proto.features.items():
            for service in feature.services:
                for enum_desc in service.enums:
                    enums._add_proto_enum(enum_desc)
            for enum_desc in feature.enums:
                enums._add_proto_enum(enum_desc)
        for feature_name, feature in arsdk_proto.features.items():
            for message_desc in feature.messages:
                messages._add_arsdk_proto_message(feature, message_desc)
        module_loader.reload(self._root)
        for feature_name, feature in arsdk_proto.features.items():
            mission_messages[feature_name] = module_loader.get_messages(
                self._root, feature_name
            )
            try:
                mission_enums[feature_name] = module_loader.get_enums(
                    self._root, feature_name
                )
            except ImportError:
                # enums modules are optional
                pass
        return (
            arsdk_proto.features,
            mission_messages,
            mission_enums,
            modules,
        )


class Mission:

    _mission_filenames = ["mission.json", "payload.tar.gz"]

    def __init__(
        self,
        mission_controller: MissionController,
        resource: Resource,
        feature_name_from_file=False,
    ):
        self.logger = mission_controller.logger
        self._controller = mission_controller
        self._resource = resource
        self._feature_name_from_file = feature_name_from_file
        self._ready_condition = threading.Condition()
        self._state = None
        self._recipient_id = None
        self._data = None
        self._messages = None
        self._enums = None
        self._modules = None
        self._hash = None
        self._signature = None
        self._closed = False
        self._subscribers = []

    def open(self, raw=False, verify=True, ca_pub_key_der=None):
        self._controller._open(
            self, raw=raw, verify=verify, ca_pub_key_der=ca_pub_key_der
        )
        self._relocate_in_mission_dir()
        return self

    def close(self):
        if self._data is not None:
            self._controller._close(self.uid)
        for subscriber in self._subscribers:
            subscriber.unsubscribe()
        self._subscribers = []
        self._closed = True

    def install(self, allow_downgrade=None, is_default=None, timeout=30, **kwds):
        """
        Install this mission onto the remote drone. The drone must be rebooted
        before this mission becomes available.
        """
        return self._controller._install(
            self,
            allow_downgrade=allow_downgrade,
            is_default=is_default,
            timeout=timeout,
            **kwds,
        )

    def __enter__(self):
        return self.open()

    def __exit__(self, exception_type, exception_value, traceback):
        self.close()

    def __bool__(self):
        return self._data is not None and not self._closed

    def hash(self):
        if self._hash is None:
            self._hash = tarball_hash(self.filepath, self._mission_filenames)
        return self._hash.copy()

    def verify(self):
        if self.signature is None:
            raise ValueError(f"Mission object {self} is not signed")
        pub_key_der = binascii.unhexlify(self.signature.pub_key_der)
        final_hash = self.hash()
        final_hash.update(pub_key_der)
        pub_key_rpb = self.signature.pub_key_rpb
        if pub_key_rpb is not None:
            pub_key_rpb = binascii.unhexlify(pub_key_rpb)
        else:
            pub_key_rpb = b""
        final_hash.update(pub_key_rpb)
        if not self.signature.public_key.verify(final_hash, self.signature.signature):
            self.logger.warning(f"Mission {self.uid} integrity verification failed")
            return False
        # if pub_key_der != ca_pub_key_der:
        #    self.logger.warning(f"Mission {self.uid} signature verification failed")
        #    return False
        # else:
        #    self.logger.info(f"Mission {self.uid} verification OK")
        #    return True
        return True

    def _relocate_in_mission_dir(self):
        mission_dir = self._controller.mission_dir
        mission_path = mission_dir / self.uid / self.hash().hexdigest()
        self._resource = self._resource.copy(mission_path)

    def _data_check(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwds):
            if self._data is None:
                raise ValueError(
                    f"Can't call {func}: mission {self} has no mission data available"
                )
            if self._closed:
                raise ValueError(f"Can't call {func}: mission {self} has been closed")
            return func(self, *args, **kwds)

        return wrapper

    @property
    def resource(self):
        return self._resource

    @property
    def filepath(self):
        return self._resource.get()

    @property
    @_data_check
    def uid(self):
        return self._data.uid

    @property
    @_data_check
    def name(self):
        return self._data.name

    @property
    @_data_check
    def desc(self):
        return self._data.desc

    @property
    @_data_check
    def version(self):
        return self._data.version

    @property
    @_data_check
    def target_model_id(self):
        return self._data.target_model_id

    @property
    @_data_check
    def target_min_version(self):
        return self._data.target_min_version

    @property
    @_data_check
    def target_max_version(self):
        return self._data.target_max_version

    @property
    def state(self):
        return self._state

    @property
    def recipient_id(self):
        return self._recipient_id

    @property
    def ready(self):
        return self._recipient_id is not None

    @property
    def messages(self):
        """
        Returns a dictionary of mission (non-protobuf) messages usable with the Olympe DSL API.
        """
        return self._messages

    @property
    def enums(self):
        """
        Returns a dictionary of mission enums usable with the Olympe DSL API.
        """
        return self._enums

    @property
    def modules(self):
        return self._modules

    @property
    def signature(self):
        return self._signature

    def wait_ready(self, timeout=None):
        """
        Wait for this mission to become ready to communicate with the drone. This method
        waits for the associated drone to send this mission instance recipient ID.
        """
        with self._ready_condition:
            if self._recipient_id is not None:
                return True
            else:
                return self._ready_condition.wait_for(
                    lambda: self._recipient_id is not None,
                    timeout=timeout
                )

    def send(
        self,
        proto_message,
        service_name,
        msg_num,
        proto_args=None,
        recipient_id=None,
        quiet=False,
    ):
        """
        Send an AirSDK mission custom protobuf message to the drone.

        :param proto_message: An AirSDK protocol buffer message
        :param service_name: the associated custom message service
        :param msg_num: the associated custom message number
        :param proto_args: an optional mapping of arguments to merge into
         the protocol buffer message
        :param recipient_id: specify or override the associated recipient ID
        :param quiet: optional boolean flag to decrease log verbosity (defaults to False)
        """
        service_id = ArsdkProto.service_id(service_name)
        recipient_id = recipient_id or self.recipient_id
        try:
            ctrl = self._controller._scheduler.context("olympe.controller")
        except KeyError:
            raise ValueError(
                "Cannot send a mission protobuf message without a controller command interface"
            )
        if recipient_id is None:
            raise ValueError(
                "Cannot send a mission protobuf message without a recipient_id"
            )

        if not proto_args:
            proto_args = {}

        message = protobuf_json_format.ParseDict(proto_args, proto_message)
        payload = bytearray(message.SerializeToString(deterministic=True))
        params = dict(
            service_id=service_id,
            recipient_id=recipient_id,
            msg_num=msg_num,
            payload=payload,
        )
        send_future = ctrl._send_command_raw(
            olympe.messages.mission.custom_cmd, params, quiet=quiet
        )
        if send_future.done() and not send_future.result():
            self.logger.error(f"Error while sending mission message: {message}")
        return send_future

    def subscribe(self, callback, service_name=None, msg_num=None, recipient_id=None):
        """
        Subscribe a callback function to every event messages associated to this mission.

        See: :py:func:`~olympe.expectations.Scheduler.subscribe`
        """
        recipient_id = recipient_id or self.recipient_id
        service_id = ArsdkProto.service_id(service_name) if service_name else None
        params = dict(service_id=service_id, recipient_id=recipient_id, msg_num=msg_num)

        self._subscribers.append(
            self._controller._scheduler.subscribe(
                callback,
                expectation=self._controller._arsdk_mission["custom_evt"]()(**params),
            )
        )

    def _subscribe(self, scheduler):
        self._subscribers.append(
            scheduler.subscribe(
                self._on_state_update,
                expectation=self._controller._arsdk_mission["state"]()(
                    uid=self.uid,
                ),
            )
        )
        self._subscribers.append(
            scheduler.subscribe(
                self._on_capabilities_update,
                expectation=self._controller._arsdk_mission["capabilities"]()(
                    uid=self.uid,
                ),
            )
        )

    def _on_state_update(self, mission_state_event, scheduler):
        self._state = mission_state_event.args["state"]

    def _on_capabilities_update(self, mission_capabilities_event, scheduler):
        # The mission capabilities event gives us our mission "recipient_id"
        # which has to be sent with this mission protobuf messages encapsulated
        # into the arsdk mission.custom_cmd and mission.custom_evt messages.
        self._recipient_id = mission_capabilities_event.args["recipient_id"]
        controller = scheduler.context("olympe.controller")
        for feature in self.messages.values():
            if hasattr(feature, "Command"):
                for message_name in feature.Command.__all__:
                    message = getattr(feature.Command, message_name)
                    if not isinstance(message, ArsdkMessageBase):
                        continue
                    message.__class__.arsdk_message = self._controller._arsdk_mission[
                        "custom_cmd"
                    ]
                    # Set the recipient ID for all message of this type
                    # This is OK since, every mission should get its own
                    # message types.
                    message.__class__.recipient_id = self._recipient_id
            if hasattr(feature, "Event"):
                for message_name in feature.Event.__all__:
                    message = getattr(feature.Event, message_name)
                    if not isinstance(message, ArsdkMessageBase):
                        continue
                    message.__class__.arsdk_message = self._controller._arsdk_mission[
                        "custom_evt"
                    ]
                    # Set the recipient ID for all message of this type
                    # This is OK since, every mission should get its own
                    # message types.
                    message.__class__.recipient_id = self._recipient_id
                    # and register this event message so that our arsdk
                    # controller knows about it.
                    controller.register_message(message)
