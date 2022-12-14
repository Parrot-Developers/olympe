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
import olympe_deps as od
import re

from . import enums
from . import messages
from .expectations import ArsdkExpectationBase
from .events import ArsdkMessageEvent, ArsdkProtoMessageEvent
from abc import abstractmethod
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures import CancelledError
from olympe.event import Event, EventContext
from olympe.enums import drone_manager as drone_manager_enums
from olympe.expectations import Expectation, FailedExpectation
from olympe.messages import common
from olympe.messages import drone_manager
from olympe.messages import mission
from olympe.scheduler import AbstractScheduler, Scheduler
from collections import OrderedDict
from olympe.utils import py_object_cast, callback_decorator, DEFAULT_FLOAT_TOL
from olympe.concurrent import Future
from olympe.log import LogMixin


class DisconnectedEvent(Event):
    pass


class ConnectedEvent(Event):
    pass


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


class Disconnect(Expectation):

    def __init__(self, _timeout=5.):
        self._disconnected = Disconnected()
        super().__init__()
        self.set_timeout(_timeout)

    def copy(self):
        return self.base_copy(self._timeout)

    def _schedule(self, scheduler):
        controller = scheduler.context("olympe.controller")
        if not controller.connection_state():
            self.set_success()
            return
        super()._schedule(scheduler)
        controller.async_disconnect(timeout=self._timeout)
        scheduler._schedule(self._disconnected)

    def check(self, event, *args, **kwds):
        if self.success():
            return self
        if self._disconnected.check(event):
            self.set_success()
        return self

    def expected_events(self):
        self._disconnected.expected_events()

    def received_events(self):
        self._disconnected.received_events()

    def matched_events(self):
        self._disconnected.matched_events()

    def unmatched_events(self):
        self._disconnected.unmatched_events()

    def cancel(self):
        disconnected_cancel = self._disconnected.cancel()
        return super().cancel() or disconnected_cancel

    def cancelled(self):
        disconnected_cancelled = self._disconnected.cancelled()
        return super().cancelled() and disconnected_cancelled


class Connect(Expectation):

    def __init__(self, _timeout=None, retry=1):
        self._connected = Connected()
        self._retry = retry
        super().__init__()
        self.set_timeout(_timeout)

    def copy(self):
        return self.base_copy(self._timeout, self._retry)

    def _schedule(self, scheduler):
        controller = scheduler.context("olympe.controller")
        if controller.connection_state():
            self.set_success()
            return
        super()._schedule(scheduler)
        controller.async_connect(timeout=self._timeout, retry=self._retry, later=True)
        scheduler._schedule(self._connected)

    def check(self, event, *args, **kwds):
        if self.success():
            return self
        if self._connected.check(event):
            self.set_success()
        return self

    def expected_events(self):
        self._connected.expected_events()

    def received_events(self):
        self._connected.received_events()

    def matched_events(self):
        self._connected.matched_events()

    def unmatched_events(self):
        self._connected.unmatched_events()

    def cancel(self):
        connected_cancel = self._connected.cancel()
        return super().cancel() or connected_cancel

    def cancelled(self):
        connected_cancelled = self._connected.cancelled()
        return super().cancelled() and connected_cancelled


class CommandInterfaceBase(LogMixin, AbstractScheduler):
    def __init__(self, *, name=None, drone_type=0, proto_v_min=1, proto_v_max=3, **kwds):
        super().__init__(name, None, "drone")
        self._create_backend(name, proto_v_min, proto_v_max, **kwds)
        self._thread_loop = self._backend._thread_loop
        self._scheduler = Scheduler(self._thread_loop, name=self._name)
        self._scheduler.add_context("olympe.controller", self)

        # Extract arsdk-xml infos
        self.enums = enums.ArsdkEnums.get("olympe")
        self.messages = OrderedDict()

        # Instantiate arsdk messages
        for id_, message_type in messages.ArsdkMessages.get("olympe").by_id.items():
            message = message_type.new()
            self.messages[message.id] = message
        # Instantiate protobufs messages
        self.protobuf_messages = OrderedDict()
        for (service_id, message_id), message_type in messages.ArsdkMessages.get(
            "olympe"
        ).service_messages.items():
            message = message_type.new()
            self.protobuf_messages[(service_id, message_id)] = message

        self._external_messages = OrderedDict()

        self._decoding_errors = []

        self._cmd_itf = None
        self._connected = False
        self._connecting = False
        self._reset_instance()
        self._connect_future = None
        self._disconnect_future = None
        self._declare_callbacks()
        self._thread_loop.register_cleanup(self.destroy)
        self._drone_manager_subscriber = self.subscribe(
            self._on_connection_state_changed, drone_manager.connection_state()
        )

    @abstractmethod
    def _recv_message_type(self):
        pass

    @abstractmethod
    def _create_backend(self, name, proto_v_min, proto_v_max):
        pass

    @property
    def connected(self):
        return self._connected and not self._connecting

    @connected.setter
    def connected(self, value):
        self._connected = value

    @property
    def connecting(self):
        return self._connecting

    @connecting.setter
    def connecting(self, value):
        self._connecting = value

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

    @callback_decorator()
    def _recv_cmd_cb(self, _interface, command, _user_data):
        """
        Function called when an arsdk event message has been received.
        """
        message_id = command.contents.id
        if message_id not in self.messages.keys():
            feature_name, class_name, msg_id = messages.ArsdkMessages.get(
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
            return
        message = self.messages[message_id]
        try:
            res, message_args = message._decode_args(command)
        except Exception as e:
            self.logger.exception(f"Failed to decode message {message}")
            self._decoding_errors.append(e)
            return

        if res != 0:
            msg = (
                f"Unable to decode event, error: {res} , "
                f"id: {command.contents.id} , name: {message.fullName}"
            )
            self.logger.error(msg)
            self._decoding_errors.append(RuntimeError(msg))

        try:
            message_event = message._event_from_args(*message_args)
        except Exception as e:
            self.logger.exception(f"Failed to decode message {message}{message_args}")
            self._decoding_errors.append(e)
            return

        if message.message_type is self._recv_message_type():
            msg = (
                f"an {message.message_type} message has been received from the drone:"
                f" {message_event}"
            )
            self.logger.error(msg)
            self._decoding_errors.append(RuntimeError(msg))
            return

        if message.feature_name == "generic" and message.name.startswith("custom_"):
            try:
                service_id = message_event.args["service_id"]
                msg_num = message_event.args["msg_num"]

                self.logger.debug(
                    f"Generic service message: service_id={service_id}, msg_num={msg_num}")

                # Update the drone state
                message._set_last_event(message_event)

                # Update the currently monitored expectations
                self._scheduler.process_event(message_event)

                # override the message with the protobuf message
                message = self.protobuf_messages[(service_id, msg_num)]
            except KeyError:
                msg = (
                    f"Unknown generic message service.id={service_id} msg_num={msg_num}"
                )
                self.logger.error(msg)
                self._decoding_errors.append(RuntimeError(msg))
                return
            message_args = message._decode_payload(message_event.args["payload"])
            message_event = message._event_from_args(message_args)

        elif message.feature_name == "mission" and message.name == "custom_evt":
            try:
                recipient_id = message_event.args["recipient_id"]
                service_id = message_event.args["service_id"]
                msg_num = message_event.args["msg_num"]

                self.logger.debug(
                    f"Mission message: service_id={service_id}, "
                    f"recipient_id={recipient_id}, msg_num={msg_num}"
                )

                # Update the drone state
                message._set_last_event(message_event)

                # Update the currently monitored expectations
                self._scheduler.process_event(message_event)

                # override the message with the protobuf message
                message = self._external_messages[(service_id, msg_num, recipient_id)]
            except KeyError:
                msg = (
                    f"Unknown mission message service.id={service_id}"
                    f" msg_num={msg_num} recipient_id={recipient_id}"
                )
                self.logger.error(msg)
                self._decoding_errors.append(RuntimeError(msg))
                return
            assert message.id == (service_id, msg_num, recipient_id)
            message_args = message._decode_payload(message_event.args["payload"])
            message_event = message._event_from_args(message_args)
            self.logger.info(str(message_event))

        # Update the drone state
        message._set_last_event(message_event)

        # Format received events as string
        self.logger.log(message.loglevel, str(message_event))

        # Update the currently monitored expectations
        self._scheduler.process_event(message_event)

    @callback_decorator()
    def _on_connection_state_changed(self, message_event, _):
        # Handle drone connection_state events
        if self._is_skyctrl:
            if (
                message_event._args["state"]
                == drone_manager_enums.connection_state.connected
            ):
                self.logger.info("Skycontroller connected to drone")
                # The SkyCtrl is connected to a drone: retrieve the drone states
                # and settings
                all_states_settings_commands = [
                    common.Common.AllStates,
                    common.Settings.AllSettings,
                ]
                for all_states_settings_command in all_states_settings_commands:
                    self._send_command_raw(all_states_settings_command, dict())

                # Enable airsdk mission support from the drone
                self._send_command_raw(mission.custom_msg_enable, dict())

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
        if not self._connected or not self._cmd_itf:
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

        argv = message._encode_args(*args.values())

        # Check if we are already sending a command.
        # if it the case, wait for the lock to be released
        # Define an Arsdk structure
        cmd = od.struct_arsdk_cmd()

        # Find the description of the command in libarsdk.so
        command_desc = od.struct_arsdk_cmd_desc.in_dll(
            od._libraries["libarsdk.so"], message.g_arsdk_cmd_desc
        )  # pylint: disable=E1101

        # argv is an array of struct_arsdk_value
        argc = argv._length_
        # Encode the command
        res = od.arsdk_cmd_enc_argv(
            ctypes.pointer(cmd), ctypes.pointer(command_desc), argc, argv
        )

        if res != 0:
            self.logger.error(f"Error while encoding command {message.fullName}: {res}")
        else:
            self.logger.debug(f"Command {message.fullName} has been encoded")

        # cmd_itf must exist to send command
        if self._cmd_itf is None:
            raise RuntimeError("[sendcmd] Error cmd interface seems to be destroyed")

        # Send the command
        send_command_future = Future(self._thread_loop)
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
        self.connected = False
        self.connecting = False
        for message in self.messages.values():
            message._reset_state()
        event = DisconnectedEvent()
        self.logger.info(str(event))
        self._scheduler.process_event(event)
        self._scheduler.stop()

        self._reset_instance()

    def _reset_instance(self):
        """
        Reset drone variables
        """
        self._cmd_itf = None
        self.connected = False
        self.connecting = False
        self._decoding_errors = []

    def destroy(self):
        """
        explicit destructor
        """
        if self._thread_loop is not None:
            self._thread_loop.unregister_cleanup(self.destroy, ignore_error=True)
            self._on_device_removed()
        self._drone_manager_subscriber.unsubscribe()
        self._scheduler.destroy()
        if self._thread_loop is not None:
            self._thread_loop.stop()

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
        send_future = self._send_command_raw(message, params, quiet=True)
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
        return self._thread_loop.run_async(
            self._send_command_impl, message, args, quiet=quiet
        )

    def async_disconnect(self, *, timeout=5):
        """
        Disconnects current device (if any)
        Blocks until it is done or abandoned

        :rtype: bool
        """
        if not self.connected:
            f = Future(self._thread_loop)
            f.set_result(True)
            self.logger.debug("Already disconnected")
            return f

        if self._connected_future is not None:
            f = Future(self._thread_loop)
            f.set_result(False)
            self.logger.warning("Cannot disconnect while a connection is in progress")
            return f

        self.logger.debug("We are not disconnected yet")
        disconnected = self._thread_loop.run_async(self._disconnection_impl)
        return disconnected

    def disconnect(self, *, timeout=5):
        """
        Disconnects current device (if any)
        Blocks until it is done or abandoned

        :rtype: bool
        """
        disconnected = self.async_disconnect(timeout=timeout)

        # wait max 5 sec until disconnection gets done
        try:
            if not disconnected.result_or_cancel(timeout=timeout):
                self.logger.error(
                    f"Cannot disconnect properly: {self.connected} {self._discovery}"
                )
                return False
        except (FutureTimeoutError, CancelledError):
            self.logger.error(
                f"Cannot disconnect properly: {self.connected} {self._discovery}"
            )
            return False

        self.logger.info(f"Disconnection with the device OK. IP: {self._ip_addr}")
        return True

    def get_last_event(self, message, key=None):
        """
        Returns the drone last event for the event message given in parameter
        """
        event = self._get_message(message.id).last_event(key=key)
        if event is None:
            raise KeyError(f"Message `{message.fullName}` last event is unavailable")
        return event

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
        expectation = message._expectation_from_args(*args, **kwds)
        expectation.set_float_tol(_float_tol)
        if message.callback_type is not messages.ArsdkMessageCallbackType.MAP:
            key = None
        else:
            key = expectation.expected_args[message.key_name]
        try:
            last_event = self.get_last_event(message, key=key)
        except KeyError:
            return False
        if last_event is None:
            return False
        return expectation.check(last_event).success()

    def query_state(self, query):
        """
        Query the drone current state return a dictionary of every drone state
        whose message name contains the query string
        :return: dictionary of drone state
        :param: query, the string to search for in the message received from the drone.
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

    def connection_state(self):
        """
        Returns the state of the connection to the drone

        :rtype: bool
        """
        # Check if disconnection callback was called
        if not self.connected:
            self.logger.debug("not connected")
            return False
        elif not self._is_skyctrl:
            self.logger.debug("connected to the drone")
            return True
        else:
            try:
                if self.check_state(drone_manager.connection_state, state="connected"):
                    self.logger.debug("The SkyController is connected to the drone")
                    return True
                else:
                    self.logger.debug("The SkyController is not connected to the drone")
                    return False
            except KeyError:
                self.logger.debug("The SkyController is not connected to the drone")
                return False

    def schedule_hook(self, expectations, **kwds):
        if not isinstance(expectations, ArsdkExpectationBase):
            return None
        if not self._connecting and not self._connected and (
                not isinstance(expectations, (Connect, Disconnect))):
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

        Please refer to the Olympe User Guide for more information.

        :param expectations: An SDK message expectation expression
        :rtype: ArsdkExpectationBase

        Please refer to the Olympe :ref:`user guide<user-guide>` for more information.
        """
        return self.schedule(expectations)

    @property
    def scheduler(self):
        return self._scheduler

    def subscribe(self, *args, **kwds):
        """
        See: :py:func:`~olympe.scheduler.Scheduler.subscribe`
        """
        return self._scheduler.subscribe(*args, **kwds)

    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by :py:func:`~olympe.Drone.subscribe`
        :type subscriber: Subscriber
        """
        return self._scheduler.unsubscribe(subscriber)

    def _subscriber_overrun(self, subscriber, event):
        self._scheduler._subscriber_overrun(subscriber, event)
