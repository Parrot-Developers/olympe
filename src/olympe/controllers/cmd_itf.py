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

import ctypes
import inspect
import re

from abc import abstractmethod
from collections import OrderedDict
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

import olympe_deps as od

from olympe.concurrent import Future
from olympe.log import LogMixin
from olympe.messaging.arsdk_enums import ArsdkEnums
from olympe.messaging.arsdk_events import (
    ArsdkMessageEvent,
    ArsdkProtoMessageEvent,
    Event,
    UnknownArsdkMessageEvent,
    UnknownGenericMessageEvent,
    UnknownMissionMessageEvent,
)
from olympe.messaging.arsdk_expectations import (
    ArsdkExpectationBase,
    Expectation,
)
from olympe.messaging.arsdk_messages import (
    ArsdkMessage,
    ArsdkMessages,
    ArsdkMessageCallbackType,
    ArsdkMessageMeta,
    ArsdkProtoMessage,
)
from olympe.messaging.expectations import FailedExpectation
from olympe.messaging.events import EventContext
from olympe.utils import (
    py_object_cast,
    callback_decorator,
    DEFAULT_FLOAT_TOL,
    DEFAULT_INT_TOL,
)
from olympe.utils.controller_state import ConnectionStateObserver
from .scheduler import AbstractScheduler, Scheduler

from olympe.messages import devicemanager
from olympe.messages import drone_manager

if TYPE_CHECKING:
    from olympe.concurrent import Loop

# Authorize block of code for Unitary Tests only
TU_ONLY = False


class DisconnectedEvent(Event):
    pass


class ConnectedEvent(Event):
    pass


class ConnectionState(Enum):
    Created = auto()
    Connecting = auto()
    Connected = auto()
    Disconnecting = auto()
    Disconnected = auto()
    Timeout = auto()
    Error = auto()


class Disconnected(Expectation):
    def __init__(self):
        self._received_event = None
        super().__init__()

    def copy(self):
        return self.base_copy()

    def check(self, event, *args, **kwds):
        if not isinstance(event, DisconnectedEvent):
            return self
        self._received_event = event
        self.set_success()
        return self

    def expected_events(self):
        if self:
            return EventContext()
        else:
            return EventContext([DisconnectedEvent()])

    def received_events(self):
        if not self:
            return EventContext()
        else:
            return EventContext([self._received_event])

    def matched_events(self):
        return self.received_events()

    def unmatched_events(self):
        return self.expected_events()


class Connected(Expectation):
    def __init__(self):
        self._received_event = None
        super().__init__()

    def copy(self):
        return self.base_copy()

    def check(self, event, *args, **kwds):
        if not isinstance(event, ConnectedEvent):
            return self
        self._received_event = event
        self.set_success()
        return self

    def expected_events(self):
        if self:
            return EventContext()
        else:
            return EventContext([ConnectedEvent()])

    def received_events(self):
        if not self:
            return EventContext()
        else:
            return EventContext([self._received_event])

    def matched_events(self):
        return self.received_events()

    def unmatched_events(self):
        return self.expected_events()


class CommandInterfaceBase(LogMixin, AbstractScheduler):
    def __init__(
        self,
        *,
        name=None,
        drone_type=0,
        proto_v_min=1,
        proto_v_max=3,
        connection_listener=None,
        **kwds,
    ):
        super().__init__(name, None, "drone")
        self._create_backend(
            name,
            proto_v_min,
            proto_v_max,
            connection_listener=connection_listener,
            **kwds,
        )
        self._loop: Loop = self._backend._loop
        self._scheduler = Scheduler(self._loop, name=self._name)
        self._scheduler.add_context("olympe.controller", self)

        # Extract arsdk-xml infos
        self.enums = ArsdkEnums.get("olympe")
        self.messages = OrderedDict()

        # Instantiate arsdk messages
        for id_, message_type in ArsdkMessages.get("olympe").by_id.items():
            message = message_type.new()
            self.messages[message.id] = message
        # Instantiate protobufs messages
        self.protobuf_messages = OrderedDict()
        for (service_id, message_id), message_type in ArsdkMessages.get(
            "olympe"
        ).service_messages.items():
            message = message_type.new()
            self.protobuf_messages[(service_id, message_id)] = message

        self._external_messages = OrderedDict()

        self._decoding_errors = []

        self._cmd_itf = None

        self._connection_state = ConnectionState.Created
        # For TU test_connecion only
        if TU_ONLY:
            self.connection_state_observer = ConnectionStateObserver()

        self._declare_callbacks()

    @abstractmethod
    def _recv_message_type(self):
        pass

    @abstractmethod
    def _create_backend(self, name, proto_v_min, proto_v_max, connection_listener):
        pass

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @connection_state.setter
    def connection_state(self, value: ConnectionState):
        self.logger.info(f"ConnectionState: {self._connection_state} --> {value}")
        self._connection_state = value
        # For TU test_connecion only
        if TU_ONLY:
            if self.connection_state_observer:
                self.connection_state_observer.notify(value)

        # depth indicates the numbers of levels down which
        # the function call will be traced. If depth == N,
        # this will trace the function that directly called the setter
        # and its parent function, and the parent of the parent and so on
        # until N levels are reached. It would be very helpful for debugging
        # Default depth is 1. And normally, you wouldn't need to go deeper than 3.
        depth = 1
        for d in range(1, depth + 1):
            function = inspect.getouterframes(inspect.currentframe())[d][3]
            line_number = inspect.getouterframes(inspect.currentframe())[d][2]
            self.logger.info(f"Called by {function}:{line_number}")

    @property
    def connected(self) -> bool:
        return self.connection_state == ConnectionState.Connected

    @property
    def connecting(self) -> bool:
        return self.connection_state == ConnectionState.Connecting

    @property
    def disconnected(self) -> bool:
        return self.connection_state == ConnectionState.Disconnected

    @property
    def disconnecting(self) -> bool:
        return self.connection_state == ConnectionState.Disconnecting

    @property
    def created(self) -> bool:
        return self.connection_state == ConnectionState.Created

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.destroy()

    def _declare_callbacks(self):
        """
        Define all callbacks
        """
        self._send_status = od.arsdk_cmd_itf_cmd_send_status_cb_t(
            self._cmd_itf_cmd_send_status_cb
        )
        self._send_status_userdata = {}
        self._userdata = ctypes.c_void_p()

        self._cmd_itf_cbs = od.struct_arsdk_cmd_itf_cbs.bind(
            {
                "dispose": self._dispose_cmd_cb,
                "recv_cmd": self._recv_cmd_cb,
                "cmd_send_status": self._send_status,
                "link_quality": self._link_quality_cb,
            }
        )

    @callback_decorator()
    def _link_quality_cb(
        self, _interface, tx_quality, rx_quality, rx_useful, _user_data
    ):
        self.logger.info(
            f"Link quality: tx={tx_quality}, rx={rx_quality}, rx_useful={rx_useful}"
        )

    def _notify_unknown_message_id_reception(self, message_id: int):
        feature_name, class_name, msg_id = ArsdkMessages.get(
            "olympe"
        ).unknown_message_info(message_id)
        if feature_name is not None:
            if class_name is not None:
                scope = f"{feature_name}.{class_name}"
            else:
                scope = feature_name
            self.logger.warning(f"Unknown message id: {msg_id} in {scope}")
        else:
            self.logger.warning(f"Unknown message id 0x{message_id:08x}")

    def _send_unknown_message_event_on_unknown_command_reception(
        self, command, message_id: int
    ):
        # When an unknown message is received, an event is sent so that the command
        # sending can be expected even if the message is unknown
        payload_address = ctypes.c_void_p()
        data_length = ctypes.c_size_t()
        capacity = ctypes.c_size_t()
        # The payload has to be decoded to be retrieved
        od.pomp_buffer_get_cdata(
            command.contents.buf,
            ctypes.pointer(payload_address),
            ctypes.pointer(data_length),
            ctypes.pointer(capacity),
        )
        payload = ctypes.string_at(payload_address, data_length.value)
        # Generate an event for an unknown message
        event = UnknownArsdkMessageEvent(message_id, payload)
        self.logger.info(str(event))
        self._scheduler.process_event(event)

    def _decode_arsdk_event_message(self, command, message_id: int) -> tuple[
        ArsdkMessageMeta,
        Optional[ArsdkMessageEvent],
    ]:
        message = self.messages[message_id]

        # Get the arguments (ie data) of the message
        try:
            res, message_args = message._decode_args(command)
        except Exception as e:
            self.logger.error(
                f"Failed to decode arguments of the received message {message.fullName} "
                f"({message}) with id {message_id}"
            )
            self._decoding_errors.append(e)
            return message, None
        if res != 0:
            msg = (
                f"Unable to decode received command {message}, error: {res} , "
                f"id: {command.contents.id} , name: {message.fullName}"
            )
            self.logger.error(msg)
            self._decoding_errors.append(RuntimeError(msg))

        # Rebuild an ArsdkMessageEvent from an ArsdkMessage
        try:
            message_event = message._event_from_args(*message_args)
        except Exception as e:
            self.logger.error(
                f"Failed to decode event of the received message {message.fullName} "
                f"({message}) with id {message_id} and arguments {message_args}"
            )
            self._decoding_errors.append(e)
            return message, None

        return message, message_event

    def _send_unknown_mission_message_event(
        self,
        service_id: int,
        msg_num: int,
        message_event: ArsdkMessageEvent,
        recipient_id: Optional[int] = None,
    ):
        # When an unknown mission message is received, an event is sent so that the message
        # sending can be expected
        if recipient_id:
            event = UnknownMissionMessageEvent(
                recipient_id, service_id, msg_num, message_event.args["payload"]
            )
        else:
            event = UnknownGenericMessageEvent(
                service_id, msg_num, message_event.args["payload"]
            )
        self.logger.info(str(event))
        self._scheduler.process_event(event)

    @callback_decorator()
    def _recv_cmd_cb(self, _interface, command, _user_data):
        """
        Function called when an arsdk event message has been received.
        """
        message_id = command.contents.id
        if message_id not in self.messages.keys():
            self._notify_unknown_message_id_reception(message_id)
            return self._send_unknown_message_event_on_unknown_command_reception(
                command, message_id
            )

        # The message id is a well-known one and can be retrieve among the arsdk messages catalog
        message, message_event = self._decode_arsdk_event_message(command, message_id)
        if message_event is None:
            return

        # Case1: Generic Custom Event/Command. This concerns custom arsdk message, whether
        # they are commands or events.
        # Antiflicker, pointnfly, camera2 are for example concerned. Proto messages on the whole
        # Common, Ardrone3 are for example not concerned. XML messages on the whole
        if message.feature_name == "generic" and message.name.startswith("custom_"):
            service_id = message_event.args["service_id"]
            msg_num = message_event.args["msg_num"]
            self.logger.debug(
                f"Generic custom proto message has been received: service_id={service_id}, msg_num={msg_num}"
            )
            try:
                # Override the message with the detailed protobuf message information, for
                # better lisibility
                message = self.protobuf_messages[(service_id, msg_num)]
            except KeyError:
                msg = f"Unknown generic custom proto message: service.id={service_id} msg_num={msg_num}"
                self.logger.error(msg)
                self._decoding_errors.append(RuntimeError(msg))
                return self._send_unknown_mission_message_event(
                    service_id=service_id, msg_num=msg_num, message_event=message_event
                )
            decoded_args = message._decode_payload(message_event.args["payload"])
            # Rebuild message event with its concrete args
            message_event = message._event_from_args(decoded_args)
        # Case2: Event/Command related to a mission
        elif message.feature_name == "mission" and (
            message.name == "custom_evt" or message.name == "custom_cmd"
        ):
            service_id = message_event.args["service_id"]
            msg_num = message_event.args["msg_num"]
            recipient_id = message_event.args["recipient_id"]
            self.logger.debug(
                f"Mission message has been received: service_id={service_id}, "
                f"msg_num={msg_num}, recipient_id={recipient_id},"
            )
            try:
                # Override the message with the detailed message information, for better
                # lisibility
                message = self._external_messages[(service_id, msg_num, recipient_id)]
            except KeyError:
                msg = (
                    f"Unknown mission message service.id={service_id}"
                    f" msg_num={msg_num} recipient_id={recipient_id}"
                )
                self.logger.error(msg)
                self._decoding_errors.append(RuntimeError(msg))
                return self._send_unknown_mission_message_event(
                    service_id=service_id,
                    msg_num=msg_num,
                    message_event=message_event,
                    recipient_id=recipient_id,
                )
            assert message.id == (service_id, msg_num, recipient_id)
            decoded_args = message._decode_payload(message_event.args["payload"])
            # Rebuild message event with its concrete args
            message_event = message._event_from_args(decoded_args)
        else:
            if message.message_type is self._recv_message_type():
                msg = (
                    f"A {message.message_type} message has been received from the drone:"
                    f" {message_event}"
                )
                self.logger.error(msg)
                self._decoding_errors.append(RuntimeError(msg))
                return

        # Update the drone state
        message._set_last_event(message_event)
        # Update the currently monitored expectations
        self._scheduler.process_event(message_event)
        # Format received events as string
        self.logger.log(message.loglevel, str(message_event))

    @callback_decorator()
    def _dispose_cmd_cb(self, _interface, _user_data):
        self.logger.debug("Dispose callback")

    @callback_decorator()
    def _cmd_itf_cmd_send_status_cb(
        self, _interface, _command, _type, status, _seq, done, userdata
    ):
        """
        Function called when a command is processed.
         0 -> ARSDK_CMD_ITF_CMD_SEND_STATUS_PARTIALLY_PACKED,
         1 -> ARSDK_CMD_ITF_CMD_SEND_STATUS_PACKED,
         2 -> ARSDK_CMD_ITF_CMD_SEND_STATUS_ACK_RECEIVED,
         3 -> ARSDK_CMD_ITF_CMD_SEND_STATUS_TIMEOUT,
         4 -> ARSDK_CMD_ITF_CMD_SEND_STATUS_CANCELED,
        """
        if not self.connected and not self.connecting or not self._cmd_itf:
            return
        status_repr = od.arsdk_cmd_itf_cmd_send_status__enumvalues.get(status, status)
        done = bool(done)
        send_status_userdata = py_object_cast(userdata)
        send_command_future, message, args = send_status_userdata
        self.logger.debug(
            f"Command send status: {message.fullName} {status_repr}, done: {done}"
        )
        if not done or send_command_future.done():
            return
        if status in (
            od.ARSDK_CMD_ITF_CMD_SEND_STATUS_ACK_RECEIVED,
            od.ARSDK_CMD_ITF_CMD_SEND_STATUS_PACKED,
        ):
            send_command_future.set_result(True)
        else:
            send_command_future.set_result(False)
            self.logger.error(
                "Command send status cancel/timeout: "
                f"{message.fullName} {status_repr}, done: {done}"
            )
        del self._send_status_userdata[id(send_command_future)]

    @callback_decorator()
    def _send_command_impl(self, message, args, quiet=False):
        """
        Must be run from the pomp loop
        """

        argv = message._encode_args(args)

        # Check if we are already sending a command.
        # if it the case, wait for the lock to be released
        # Define an Arsdk structure
        cmd = od.struct_arsdk_cmd()

        # argv is an array of struct_arsdk_value
        argc = argv._length_
        # Encode the command
        res = od.arsdk_cmd_enc_argv(ctypes.pointer(cmd), message.arsdk_desc, argc, argv)

        if res != 0:
            self.logger.error(f"Error while encoding command {message.fullName}: {res}")
        else:
            self.logger.debug(f"Command {message.fullName} has been encoded")

        # cmd_itf must exist to send command
        if self._cmd_itf is None:
            raise RuntimeError("[sendcmd] Error cmd interface seems to be destroyed")

        # Send the command
        send_command_future = Future(self._loop)
        send_status_userdata = ctypes.pointer(
            ctypes.py_object((send_command_future, message, args))
        )
        self._send_status_userdata[id(send_command_future)] = send_status_userdata
        res = od.arsdk_cmd_itf_send(
            self._cmd_itf, ctypes.pointer(cmd), self._send_status, send_status_userdata
        )

        if res != 0:
            self.logger.error(f"Error while sending command: {res}")
            send_command_future.set_result(False)
            return send_command_future

        event = ArsdkMessageEvent(message, args)
        # Update the currently monitored expectations
        self._scheduler.process_event(event)
        log_msg = f"{event} has been sent to the device"
        if quiet:
            self.logger.debug(log_msg)
        else:
            self.logger.info(log_msg)

        return send_command_future

    @callback_decorator()
    def _on_device_removed(self):
        for message in self.messages.values():
            message._reset_state()

        self._reset_instance()

    def _reset_instance(self):
        """
        Reset drone variables
        """
        self.connection_state = ConnectionState.Created
        self._cmd_itf = None
        self._decoding_errors = []

    def _cleanup(self):
        self._scheduler.stop()
        self._scheduler.destroy()

    def destroy(self):
        """
        explicit destructor
        """
        self._loop.stop()

    def register_message(self, message):
        self._external_messages[message.id] = message

    def _get_message(self, id_):
        """
        Returns a drone internal message object given its ID
        """
        if isinstance(id_, tuple):
            # protobuf messages
            try:
                return self.protobuf_messages[id_]
            except KeyError:
                return self._external_messages[id_]
        else:
            # standard arsdk messages
            return self.messages[id_]

    def _send_protobuf_command(self, proto_message, proto_args, quiet=False):
        payload = proto_message._encode_args(proto_args)
        message = proto_message.arsdk_message
        params = dict(
            service_id=proto_message.service.id,
            msg_num=proto_message.number,
            payload=payload,
        )
        if proto_message.recipient_id is not None:
            params.update(recipient_id=proto_message.recipient_id)
        params = {name: params[name] for name in proto_message.arsdk_message.args_name}
        send_future = self._loop.run_async(
            self._send_command_impl, message, params, quiet=True
        )
        event = ArsdkProtoMessageEvent(proto_message, proto_args)
        if send_future.done() and not send_future.result():
            self.logger.error(f"Error while sending command: {event}")
            return send_future

        log_msg = f"{event} has been sent to the device"
        # Update the currently monitored expectations
        self._scheduler.process_event(event)
        if quiet:
            self.logger.debug(log_msg)
        else:
            self.logger.info(log_msg)
        return send_future

    def _send_command_raw(self, message, args, quiet=False):
        """
        Just send a command message asynchronously without waiting for the
        command expectations.
        """
        if isinstance(message, ArsdkMessage):
            return self._loop.run_async(
                self._send_command_impl, message, args, quiet=quiet
            )
        elif isinstance(message, ArsdkProtoMessage):
            return self._send_protobuf_command(message, args, quiet=quiet)
        else:
            raise RuntimeError(f"Unknown message type {message}")

    def get_last_event(self, message, key=None):
        """
        Returns the drone last event for the event message given in parameter
        """
        event = self._get_message(message.id).last_event(key=key)
        if event is None:
            raise KeyError(f"Message `{message.fullName}` last event is unavailable")
        return event

    def get_last_events(self, message, key=None):
        """
        Returns the drone last event for the event message given in parameter
        """
        event = None
        for event in self._get_message(message.id).last_events(key=key):
            yield event
        if event is None:
            raise KeyError(f"Message `{message.fullName}` last event is unavailable")

    def get_state(self, message):
        """
        Returns the drone current state for the event message given in parameter

        :param message: an event message type
        :type message: ArsdkMessage
        :return: an ordered dictionary containing the arguments of the last received event message
                 that matches the message ID provided in parameter
        """
        return self._get_message(message.id).state()

    def check_state(self, message, *args, **kwds):
        """
        Returns True if the drone state associated to the given message is already reached.
        Otherwise, returns False
        """
        _float_tol = kwds.pop("_float_tol", DEFAULT_FLOAT_TOL)
        _int_tol = kwds.pop("_int_tol", DEFAULT_INT_TOL)
        expectation = message._expectation_from_args(*args, **kwds)
        expectation.set_float_tol(_float_tol)
        expectation.set_int_tol(_int_tol)
        expectation._await(self._scheduler)
        if message.callback_type is not ArsdkMessageCallbackType.MAP:
            key = None
        else:
            if message.key_name not in expectation.expected_args:
                key = None
            else:
                key = expectation.expected_args[message.key_name]
        try:
            for last_event in self.get_last_events(message, key=key):
                if expectation.check(last_event).success():
                    return True
        except KeyError:
            return False
        else:
            return False

    def query_state(self, query):
        """Query the drone current state

        :param query: the string to search for in the message received from the drone
        :return: dictionary of every drone state whose message name contains the query string
        :rtype: dict
        """
        result = OrderedDict()
        for message in self.messages.values():
            if re.search(query, message.fullName, re.IGNORECASE):
                try:
                    result[message.fullName] = message.state()
                except RuntimeError:
                    continue
            for arg_name in message.args_name:
                name = message.fullName + "." + arg_name
                if re.search(query, name, re.IGNORECASE):
                    try:
                        result[message.fullName] = message.state()
                    except RuntimeError:
                        break
        return result

    def decoding_errors(self):
        return self._decoding_errors

    def drone_connection_state(self):
        """
        Returns the state of the connection to the drone

        :rtype: bool
        """

        # Check if disconnection callback was called
        if not self.connected:
            self.logger.debug("Controller is not connected to the drone")
            return False
        elif not self._is_skyctrl:
            self.logger.debug("Controller is connected to the drone")
            return True
        else:
            if self.check_state(drone_manager.connection_state, state="connected"):
                self.logger.debug("The SkyController is connected to the drone")
                return True
            elif self.check_state(
                devicemanager.Event.State,
                connected=devicemanager.ConnectionState.Connected(),
            ):
                self.logger.debug("The SkyController is connected to the drone")
                return True
            else:
                self.logger.debug("The SkyController is not connected to the drone")
                return False

    def schedule_hook(self, expectations, **kwds):
        if not isinstance(expectations, ArsdkExpectationBase):
            return None
        if not self.connecting and not self.connected:
            return FailedExpectation("Not connected to any device")
        return None

    def schedule(self, expectations):
        """
        See: Drone.__call__()
        """
        return self._scheduler.schedule(expectations)

    def __call__(self, expectations):
        """
        This method can be used to:

            - send command messages and waiting for their associated expectations
            - monitor spontaneous drone event messages
            - check the state of the drone

        It asynchronously process arsdk command and event message and expectations.

        :param expectations: An SDK message expectation expression
        :rtype: ArsdkExpectationBase
        """
        return self.schedule(expectations)

    @property
    def scheduler(self):
        return self._scheduler

    def subscribe(self, callback, expectation, *args, **kwds):
        """
        See: :py:func:`~olympe.scheduler.Scheduler.subscribe`
        """
        return self._scheduler.subscribe(
            callback=callback, expectation=expectation, *args, **kwds
        )

    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by :py:func:`~olympe.Drone.subscribe`
        :type subscriber: Subscriber
        """
        return self._scheduler.unsubscribe(subscriber)

    def _subscriber_overrun(self, subscriber, event):
        self._scheduler._subscriber_overrun(subscriber, event)
