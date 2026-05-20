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

from __future__ import annotations

import binascii
import functools
import io
import json
import shutil
import tarfile
import tempfile
import threading

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from google.protobuf import json_format as protobuf_json_format
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from olympe.controllers.controller import ControllerBase

from olympe.concurrent import Loop, Future
from olympe.connection.http import Session, HTTPError
from olympe.controllers.scheduler import Scheduler
from olympe.log import LogMixin
from olympe.messaging.arsdk_enums import ArsdkEnums
from olympe.messaging.arsdk_events import ArsdkMessageEvent
from olympe.messaging.arsdk_messages import (
    ArsdkMessages,
    ArsdkMessageBase,
    ArsdkProtoMessage,
)
from olympe.module_loader import module_loader
from olympe.parser.proto import ArsdkProto
from olympe.utils import dataclass_from_dict
from olympe.utils.crypto import tarball_hash, PublicKey
from olympe.utils.path import is_directory_writable, olympe_data_dir, Resource

import olympe.messages.mission

MAX_NB_MISSION_REVISIONS = 10


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
    def from_dict(cls, data: Dict) -> MissionMetadata:
        def _from_int_str(x: str) -> int:
            try:
                return int(x)
            except ValueError:
                return int(f"0x{x}", 16)

        return dataclass_from_dict(cls, data, type_hooks={int: _from_int_str})


@dataclass
class MissionMetadataRemote(MissionMetadata):
    digest: str
    """SHA512 digest hex dump"""


@dataclass
class MissionSignature:
    filenames: List[str]
    """List of signed file names"""

    signature: str
    """fips-186-3/SHA512 signature of the mission files as an hex dump"""

    pub_key_der: str
    """ASN.1/DER public key hex dump"""

    pub_key_rpb: Optional[str] = None
    """(optional) public key in a pre-computed form (RPB)"""

    @property
    def public_key(self) -> PublicKey:
        return PublicKey.from_der(self.pub_key_der)

    @classmethod
    def from_file(cls, signature_filepath: Path) -> MissionSignature:
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
        return dataclass_from_dict(cls, signature)

    def to_string_io(self) -> io.StringIO:
        signature = io.StringIO()
        signature.write(f"filenames={';'.join(self.filenames)}\n")
        signature.write(f"pub_key_der={self.pub_key_der}\n")
        if self.pub_key_rpb is not None:
            signature.write(f"pub_key_rpb={self.pub_key_rpb}\n")
        else:
            signature.write("pub_key_rpb=\n")
        signature.write(f"signature={self.signature}\n")
        return signature

    def to_string(self) -> str:
        signature = self.to_string_io()
        content = signature.getvalue()
        signature.close()
        return content

    def to_file(self, signature_filepath: Path):
        with open(signature_filepath, "w") as signature_file:
            signature_file.write(self.to_string())


class MissionHttpApiError(Enum):
    def __new__(cls, value: Any, string: str):
        self = object.__new__(cls)
        self._value_ = value
        return self

    def __init__(self, value: Any, string: str):
        self._string = string

    @property
    def string(self) -> str:
        return self._string

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
    def __init__(
        self,
        controller: ControllerBase,
        hostname: str,
        version: int = 1,
        name: Optional[str] = None,
        session: Optional[Session] = None,
        mission_dir: Optional[Path] = None,
    ):
        super().__init__(name, None, "missions")
        self._root = "olympe.airsdk"
        if name:
            self._root += f".{name}"
            module_loader.add_package_root(self._root)

        self._controller = controller
        self._scheduler = self._controller._scheduler
        self._version = version
        self.set_hostname(hostname)
        # In case no session is transmitted through the mixins, MissionController builts
        # its own with its own loop.
        if session is None:
            self._session = Session(self._loop)
        else:
            self._session = session
        # Use mission_dir setter, to prevent self._mission_dir from being None
        self.mission_dir = mission_dir

        self._missions = defaultdict(list)
        self._arsdk_mission = ArsdkMessages.get("olympe").by_feature["mission"]

        self._loop = Loop(self.logger, max_workers=1)
        self._loop.start()

    def stop(self):
        self._loop.stop()

    def set_hostname(self, hostname: str):
        self._hostname = hostname
        self._mission_api_url = (
            f"http://{self._hostname}/api/v{self._version}/mission/missions"
        )

    def set_device_name(self, device_name: str):
        super().set_device_name(device_name)
        for missions in self._missions.values():
            for mission in missions:
                mission.logger = self.logger

    @property
    def mission_dir(self) -> Path:
        return self._mission_dir

    @mission_dir.setter
    def mission_dir(self, dir_path: Optional[Path]):
        if dir_path is None:
            self._mission_dir = olympe_data_dir() / "missions"
            self._mission_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
        else:
            self._mission_dir = dir_path
        if not is_directory_writable(self._mission_dir):
            raise RuntimeError(
                f"{self._mission_dir} is not writable directory. Mission payload"
                "cannot be relocated into this directory."
            )

    async def alist_remote(self) -> Optional[List[MissionMetadata]]:
        try:
            # Get the response to the request 'GET' to self._mission_api_url, that
            # should return the list of the missions installed on the drone
            response = await self._session.get(self._mission_api_url)
            # Check that the status_code is 'ok'
            response.raise_for_status()
        except HTTPError as e:
            # If not, retrieve the error message to log
            msg = str(e)
            try:
                # In case the status code is registered in MissionHttpApiError, rather
                # use its pre-filled error message
                assert e.response is not None
                msg = MissionHttpApiError(e.response.status_code).string
            except ValueError:
                pass
            self.logger.error(msg)
            return None
        except Exception as e:
            # For any other unhandled error, just log the error message as is
            self.logger.error(str(e))
            return None
        # If the request to self._mission_api_url succeeded, get the json listing the
        # missions installed on the drone, parse then format
        missions = await response.json()
        missions = [MissionMetadataRemote.from_dict(m) for m in missions]
        return missions

    def list_remote(self):
        return self._loop.run_async(self.alist_remote).result_or_cancel(timeout=3)

    def from_path(
        self,
        url_or_path: Union[Path, str],
        feature_name_from_file: bool = False,
        timeout: Optional[float] = None,
    ) -> Mission:
        """
        Creates and returns an olympe.Mission object from a local path or an URL to an
        AirSDK mission archive.
        """
        return Mission(
            self,
            Resource(url_or_path, self._session, timeout=timeout),
            feature_name_from_file=feature_name_from_file,
        )


def _data_checked(func):
    """Decorator to prevent access to a member variable if self._data is None or self._closed is True

    :param func: decorated getter of the member variable
    :type func: Any
    :raises ValueError: if self._data is None
    :raises ValueError: if self._closed is True
    :return: wrapper function that checks self._data and self._closed values
    :rtype: callable
    """

    @functools.wraps(func)
    # @functools.wraps enables to keep name and docs of the real method
    def wrapper(self):
        if self._data is None:
            raise ValueError(
                f"Can't call {func.__name__}: mission {self} has no mission data available"
            )
        if self._closed:
            raise ValueError(
                f"Can't call {func.__name__}: mission {self} has been closed"
            )
        # Call real method
        return func(self)

    # Call decorated method. @property aims to transform func into a property
    return property(wrapper)


class Mission:
    _mission_filenames = ["mission.json", "payload.tar.gz"]

    def __init__(
        self,
        mission_controller: MissionController,
        resource: Resource,
        feature_name_from_file: bool = False,
    ):
        self.logger: Logger = mission_controller.logger
        self._controller: MissionController = mission_controller
        self._resource: Resource = resource
        self._feature_name_from_file: bool = feature_name_from_file
        self._ready_condition = threading.Condition()
        self._state = None
        self._recipient_id: Optional[int] = None
        self._data: Optional[MissionMetadata] = None
        self._messages = None
        self._enums = None
        self._modules = None
        self._hash = None
        self._signature: Optional[MissionSignature] = None
        self._closed: bool = False
        self._subscribers: list = []

    def __enter__(self):
        return self.open()

    def __exit__(self, exception_type, exception_value, traceback):
        self.close()

    def __bool__(self):
        return self._data is not None and not self._closed

    def _load_protos(
        self,
        mission_uid: str,
        payload_proto_path: Path,
        feature_name_from_file: bool = False,
        raw: bool = False,
    ):
        arsdk_proto = ArsdkProto.get(self._controller._root)
        modules = arsdk_proto.parse_mission_proto(
            payload_proto_path,
            mission_uid,
            feature_name_from_file=feature_name_from_file,
            raw=raw,
        )
        messages = ArsdkMessages.get(self._controller._root)
        enums = ArsdkEnums.get(self._controller._root)
        mission_messages = dict()
        mission_enums = dict()

        if not arsdk_proto.features or raw:
            return (
                arsdk_proto.features,
                mission_messages,
                mission_enums,
                modules,
            )

        module_loader.reload(self._controller._root)
        for feature_name, feature in arsdk_proto.features.items():
            for service in feature.services:
                for enum_desc in service.enums:
                    enums._add_proto_enum(enum_desc)
            for enum_desc in feature.enums:
                enums._add_proto_enum(enum_desc)
            for message_desc in feature.messages:
                messages._add_arsdk_proto_message(feature, message_desc)
        module_loader.reload(self._controller._root)
        for feature_name, feature in arsdk_proto.features.items():
            mission_messages[feature_name] = module_loader.get_messages(
                self._controller._root, feature_name
            )
            try:
                mission_enums[feature_name] = module_loader.get_enums(
                    self._controller._root, feature_name
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

    async def _arelocate_in_mission_dir(self):
        # Get the mission directory from MissionController
        mission_dir = self._controller.mission_dir
        # Before relocating a new resource, empty the mission folder if too big
        mission_counter = len(list(mission_dir.iterdir()))
        # Threshold is set by MAX_NB_MISSION_REVISIONS
        if mission_counter >= MAX_NB_MISSION_REVISIONS:
            for path in mission_dir.iterdir():
                if not path.is_dir():
                    # In case a file finds itself in that directory, we are not
                    # responsible for this and just ignore it
                    continue
                if not path.samefile(mission_dir / self.uid):
                    # Check that the current payload resource is not being deleted,
                    # because it still needed. Empty and delete the others
                    shutil.rmtree(mission_dir / path)

        # Rename the payload with the hexadecimal digest associated to this mission
        # version
        mission_path = mission_dir / self.uid / self.hash().hexdigest()
        # Copy the resource payload from the cache folder to that data missions folder
        self._resource: Resource = await self._controller._loop.run_async(
            self._resource.copy, mission_path
        )

    def _relocate_in_mission_dir(self, timeout: Optional[float] = None):
        return self._controller._loop.run_async(
            self._arelocate_in_mission_dir,
        ).result_or_cancel(timeout=timeout)

    def _safe_extract(
        self,
        tar: tarfile.TarFile,
        path: str = ".",
    ):
        if hasattr(tarfile, "data_filter"):
            # Python 3.11 and above will correctly deal with safe extract when the filter
            # 'data' is documented
            tar.extractall(path, filter="data")
        else:
            # Remove this part when Python would be updated to 3.11
            self.logger.warning(
                f"Extracting to '{path}' may be unsafe; consider updating Python"
            )
            tar.extractall(path)

    def open(self, raw: bool = False, verify: bool = True, ca_pub_key_der=None):
        verify = verify or bool(ca_pub_key_der)
        with tempfile.TemporaryDirectory() as root_tmp_dir:
            with tarfile.open(self.filepath) as f:
                # A safe extract has to be guaranteed. That has been applied in Python 3.11
                # and above. This prevent from 'Path Traversal' and malicious files to
                # execute from the folder they would be extracted to.
                # See CVE-2007-4559 vulnerability fix for further details
                self._safe_extract(tar=f, path=root_tmp_dir)

            signature = None
            for signature_path in Path(root_tmp_dir).glob("signature.ecdsa*"):
                signature = MissionSignature.from_file(signature_path)
                break
            if signature is None:
                raise ValueError(
                    f"Mission signature 'signature.ecdsa' not found in {root_tmp_dir}"
                )
            if self._signature is not None and self._signature != signature:
                raise ValueError(
                    f"Mission signature mismatch: {self._signature} != {signature}"
                )

            with open(Path(root_tmp_dir) / "mission.json") as f:
                mission_json = MissionMetadata.from_dict(json.load(f))

            if self._data is not None and self._data != mission_json:
                raise ValueError(
                    f"Mission data mismatch: {self._data} != {mission_json}"
                )

            with tempfile.TemporaryDirectory() as payload_tmp_dir:
                payload_proto_path = Path(payload_tmp_dir) / "share" / "protobuf"
                with tarfile.open(Path(root_tmp_dir) / "payload.tar.gz") as f:
                    self._safe_extract(f, payload_tmp_dir)
                    features, messages, enums, modules = self._load_protos(
                        mission_json.uid,
                        payload_proto_path,
                        raw=raw,
                        feature_name_from_file=self._feature_name_from_file,
                    )

        if mission_json.uid in self._controller._missions:
            for existing_mission in self._controller._missions[mission_json.uid]:
                if existing_mission._data == mission_json:
                    self.logger.info(
                        f"Opening mission '{mission_json.uid}' multiple times"
                    )

        (
            self._data,
            self._messages,
            self._enums,
            self._modules,
            self._signature,
        ) = (
            mission_json,
            messages,
            enums,
            modules,
            signature,
        )

        if verify:
            self.verify()
        self._subscribe()
        self._controller._missions[self.uid].append(self)
        _ = self._relocate_in_mission_dir()
        return self

    def close(self):
        if self._data is not None:
            del self._controller._missions[self.uid]
            # TODO: unload mission messages/enums module
        for subscriber in self._subscribers:
            subscriber.unsubscribe()
        self._subscribers = []
        self._closed = True
        self._state = None
        self._recipient_id = None

    async def _ainstall(
        self,
        verify: bool = True,
        ca_pub_key_der=None,
        allow_downgrade=None,
        is_default=None,
        timeout: Optional[float] = 30,
        **kwds,
    ):
        if self.uid not in self._controller._missions:
            self.open(verify=verify, ca_pub_key_der=ca_pub_key_der)

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

        with open(self.filepath, "rb") as mission_payload:
            try:
                # Send mission metadata with the request 'PUT' to self._mission_api_url,
                # that should display the data formatted in json on the drone's webserver
                # NOTE: the extra / is currently mandatory for this HTTP PUT request
                response = await self._controller._session.put(
                    self._controller._mission_api_url + "/",
                    data=mission_payload.read(),
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
                # Check that the status_code is 'ok'
                response.raise_for_status()
            except HTTPError as e:
                # If not, retrieve the error message to log
                msg = str(e)
                try:
                    # In case the status code is registered in MissionHttpApiError, rather
                    # use its pre-filled error message
                    assert e.response is not None
                    msg = MissionHttpApiError(e.response.status_code).string
                except ValueError:
                    pass
                self.logger.error(msg)
                return False
            except Exception as e:
                # For any other unhandled error, just log the error message as is
                self.logger.error(str(e))
                return False
        self.logger.info(
            f"Mission '{self.uid}' has successfully been installed to {self._controller._hostname}"
        )
        return True

    def install(
        self,
        verify: bool = True,
        ca_pub_key_der=None,
        allow_downgrade=None,
        is_default=None,
        timeout: Optional[float] = 30,
        **kwds,
    ):
        """
        Install this mission onto the remote drone. The drone must be rebooted
        before this mission becomes available.
        """
        return self._controller._loop.run_async(
            self._ainstall,
            verify=verify,
            ca_pub_key_der=ca_pub_key_der,
            allow_downgrade=allow_downgrade,
            is_default=is_default,
            timeout=timeout,
            **kwds,
        ).result_or_cancel(timeout=timeout)

    def hash(self):
        if self._hash is None:
            self._hash = tarball_hash(self.filepath, self._mission_filenames)
        return self._hash.copy()

    def verify(self) -> bool:
        if self.signature is None:
            raise ValueError(f"Mission '{self}' is not signed")
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
        return True

    @property
    def resource(self) -> Resource:
        return self._resource

    @property
    def filepath(self) -> Path:
        filepath_future = self._controller._loop.run_async(self._resource.get)
        filepath: Path = filepath_future.result_or_cancel(self._resource.timeout)
        return filepath

    @property
    def state(self):
        return self._state

    @property
    def recipient_id(self) -> Optional[int]:
        return self._recipient_id

    @property
    def ready(self) -> bool:
        return self._recipient_id is not None

    @property
    def messages(self) -> Dict:
        """
        Returns a dictionary of mission (non-protobuf) messages usable with the Olympe DSL API.
        """
        if self._messages is None:
            raise ValueError(
                "There are no proto mission messages that have successfully been loaded"
            )
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
    def signature(self) -> Optional[MissionSignature]:
        return self._signature

    @_data_checked
    def uid(self) -> str:
        return self._data.uid

    @_data_checked
    def name(self) -> str:
        return self._data.name

    @_data_checked
    def desc(self) -> str:
        return self._data.desc

    @_data_checked
    def version(self) -> str:
        return self._data.version

    @_data_checked
    def target_model_id(self) -> int:
        return self._data.target_model_id

    @_data_checked
    def target_min_version(self) -> str:
        return self._data.target_min_version

    @_data_checked
    def target_max_version(self) -> str:
        return self._data.target_max_version

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """Wait for the mission to become ready to communicate with the drone. Which
        means wait for the drone to send the mission the recipient ID.

        :param timeout: timeout to wait the mission to be ready for, defaults to None.
        :type timeout: Optional[float], default to None. If timeout is set to 'None',
            this means the timeout runs undefinitely
        :return: True if recipient_id is retrieved, False otherwise
        :rtype: bool
        """
        with self._ready_condition:
            if self._recipient_id is not None:
                return True
            else:
                return self._ready_condition.wait_for(
                    lambda: self._recipient_id is not None, timeout=timeout
                )

    def send(
        self,
        proto_message: ArsdkProtoMessage,
        service_name: str,
        msg_num,
        proto_args: Optional[dict] = None,
        recipient_id: Optional[int] = None,
        quiet: bool = False,
    ) -> Future:
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
        try:
            ctrl = self._controller._scheduler.context("olympe.controller")
        except KeyError:
            raise ValueError(
                "Cannot send a mission protobuf message without a controller command interface"
            )

        recipient_id = recipient_id or self.recipient_id
        if recipient_id is None:
            raise ValueError(
                "Cannot send a mission protobuf message without a recipient_id"
            )

        if not proto_args:
            proto_args = {}

        service_id = ArsdkProto.service_id(service_name)
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

    def subscribe(
        self,
        callback,
        service_name: Optional[str] = None,
        msg_num: Optional[int] = None,
        recipient_id: Optional[int] = None,
    ):
        """
        Subscribe a callback function to every event messages associated to this mission.

        See: :py:func:`~olympe.messaging.expectations.Scheduler.subscribe`
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

    def _subscribe(self):
        self._subscribers.append(
            self._controller._scheduler.subscribe(
                self._on_state_update,
                expectation=olympe.messages.mission.state(uid=self.uid, _policy="wait"),
            )
        )
        self._subscribers.append(
            self._controller._scheduler.subscribe(
                self._on_capabilities_update,
                expectation=olympe.messages.mission.capabilities(
                    uid=self.uid, _policy="wait"
                ),
            )
        )
        # When opening a mission, the recipient_id has already been transmitted. It is
        # indeed necessary to send a 'capabilities' command before opening the mission to
        # that purpose. Without recipient_id, the mission can't be opened. The thing is,
        # the recipient_id won't be taken into account because sent before the mission opening.
        # This is why when suscribing, the last event (capabilities) has to be retrieved, so that
        # the mission type can be updated, as done in _on_capabilities_update
        try:
            last_capabilities_event = self._controller._controller.get_last_event(
                olympe.messages.mission.capabilities, key=self.uid
            )
            self._on_capabilities_update(
                last_capabilities_event, self._controller._scheduler
            )
        except KeyError:
            pass

    def _on_state_update(
        self, mission_state_event: ArsdkMessageEvent, scheduler: Scheduler
    ):
        self._state = mission_state_event.args["state"]

    def _on_capabilities_update(
        self, mission_capabilities_event: ArsdkMessageEvent, scheduler: Scheduler
    ):
        # The mission recipient_id is given by mission_capabilities_event,
        # which has to be sent with this mission protobuf message, encapsulated
        # into the arsdk mission.custom_cmd and mission.custom_evt messages.
        self._recipient_id = mission_capabilities_event.args["recipient_id"]
        controller = scheduler.context("olympe.controller")
        for messages_type in self.messages.values():
            if hasattr(messages_type, "Command"):
                for message_name in messages_type.Command.__all__:
                    message = getattr(messages_type.Command, message_name)
                    if not isinstance(message, ArsdkMessageBase):
                        continue
                    message.__class__.arsdk_message = self._controller._arsdk_mission[
                        "custom_cmd"
                    ]
                    # Set the recipient ID for all messages of this type
                    # This is OK since every mission should get its own
                    # message types.
                    message.__class__.recipient_id = self._recipient_id
                    controller.register_message(message)
            if hasattr(messages_type, "Event"):
                for message_name in messages_type.Event.__all__:
                    message = getattr(messages_type.Event, message_name)
                    if not isinstance(message, ArsdkMessageBase):
                        continue
                    message.__class__.arsdk_message = self._controller._arsdk_mission[
                        "custom_evt"
                    ]
                    # Set the recipient ID for all messages of this type
                    # This is OK since every mission should get its own
                    # message types.
                    message.__class__.recipient_id = self._recipient_id
                    # and register this event message so that our arsdk
                    # controller knows about it.
                    controller.register_message(message)

        with self._ready_condition:
            self._ready_condition.notify_all()
