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
from __future__ import print_function
from __future__ import absolute_import
from future.builtins import str, bytes


import ctypes
import datetime
import functools
import inspect
import json
import olympe_deps as od
import pprint
import re
import time
from tzlocal import get_localzone
from collections import OrderedDict
from logging import getLogger
from warnings import warn


import olympe.arsdkng.enums as enums
import olympe.arsdkng.messages as messages
from olympe.arsdkng.expectations import (
    AbstractScheduler, Scheduler, FailedExpectation)

from olympe.arsdkng.backend import Backend
from olympe.arsdkng.discovery import DiscoveryNet, DiscoveryNetRaw, SKYCTRL_DEVICE_TYPE_LIST
from olympe.arsdkng.pdraw import Pdraw, PDRAW_LOCAL_STREAM_PORT
from olympe.arsdkng.pdraw import PDRAW_LOCAL_CONTROL_PORT
from olympe.media import Media

from olympe.tools.error import ErrorCodeDrone
from olympe._private import makeReturnTuple, DEFAULT_FLOAT_TOL, py_object_cast, callback_decorator
from olympe._private.controller_state import ControllerState
from olympe._private.pomp_loop_thread import Future
from olympe._private.format import columns as format_columns
from olympe.messages import ardrone3
from olympe.messages import common
from olympe.messages import skyctrl
from olympe.messages import drone_manager
from olympe.enums import drone_manager as drone_manager_enums
from concurrent.futures import TimeoutError as FutureTimeoutError


# pylint: disable=W0613


##############################################################################


def ensure_connected(function):
    """
    Ensure that the decorated function is called when Olympe is connected to a drone
    """

    @functools.wraps(function)
    def wrapper(self, *args, **kwargs):
        if not self._device_conn_status.connected:
            self.logger.info(
                "Disconnection has been detected, reconnection will be done")
            if not self.connect():
                self.logger.error("Cannot make connection")
                return makeReturnTuple(
                    ErrorCodeDrone.ERROR_CONNECTION, "Cannot make connection"
                )

        result = function(self, *args, **kwargs)

        return result

    return wrapper


##############################################################################

class ControllerBase(AbstractScheduler):

    def __init__(self,
                 ip_addr,
                 name=None,
                 dcport=44444,
                 drone_type=0,
                 is_skyctrl=None,
                 video_buffer_queue_size=8,
                 media_autoconnect=True):
        """
        :param ip_addr: the drone IP address
        :type ip_addr: str
        :param name: (optional) the controller name (used by Olympe logs)
        :type name: str
        :param dcport: drone control port (default to 44444)
        :type dcport: int
        :param drone_type: (optional) the drone device type ID
        :type drone_type: int
        :param is_sky_controller: True if Olympe needs to connect to a drone through a SkyController
        :type is_sky_controller: bool
        :param video_buffer_queue_size: the video buffer pool size (defaults to 8)
        :param media_autoconnect: autoconnect to the drone media API when the SDK
            connection is established (defaults to True)
        """

        self._name = name
        self._device_name = None
        if self._name is not None:
            self.logger = getLogger("olympe.{}.drone".format(name))
        else:
            self.logger = getLogger("olympe.drone")
        self._ip_addr_str = str(ip_addr)
        self._ip_addr = ip_addr.encode('utf-8')

        self._backend = Backend(name=name)
        self._thread_loop = self._backend._thread_loop
        self.video_buffer_queue_size = video_buffer_queue_size
        self._scheduler = Scheduler(self._thread_loop, name=self._name)
        self._scheduler.add_context("olympe.controller", self)

        self._media = None
        self._media_autoconnect = media_autoconnect

        # Extract arsdk-xml infos
        self.enums = enums.ArsdkEnums.get()
        self.messages = OrderedDict()

        # Instantiate arsdk messages
        for id_, message_type in messages.ArsdkMessages.get().by_id.items():
            message = message_type.new()
            self.messages[message.id] = message
            # Add arsdk messages interface to this object
            # FIXME: remove this legacy/deprecated/undocumented API
            message._bind_send_command(self._send_command)
            for cmd_aliases in message.aliases:
                self.__dict__[cmd_aliases] = message.send

        self._decoding_errors = []
        self.error_code_drones = ErrorCodeDrone()

        self._controller_state = ControllerState()
        self._connect_future = None
        self._disconnect_future = None
        self._device_conn_status = self._controller_state.device_conn_status
        self._device_states = self._controller_state.device_states
        self._piloting_command = self._controller_state.piloting_command

        # Set skyctrl parameter
        self._is_skyctrl = is_skyctrl

        self._pdraw = None

        self._reset_instance()

        self._thread_loop.register_cleanup(self.destroy)

        self._declare_callbacks()

        # Setup piloting commands timer
        self._piloting_timer = self._thread_loop.create_timer(
            self._piloting_timer_cb)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.destroy()

    def _declare_callbacks(self):
        """
        Define all callbacks
        """
        self._device_cbs_cfg = od.struct_arsdk_device_conn_cbs.bind({
            "connecting": self._connecting_cb,
            "connected": self._connected_cb,
            "disconnected": self._disconnected_cb,
            "canceled": self._canceled_cb,
            "link_status": self._link_status_cb,
        })

        self._send_status = od.arsdk_cmd_itf_send_status_cb_t(self._cmd_itf_send_status_cb)
        self._send_status_userdata = {}
        self._userdata = ctypes.c_void_p()

        self._cmd_itf_cbs = od.struct_arsdk_cmd_itf_cbs.bind({
            "dispose": self._dispose_cmd_cb,
            "recv_cmd": self._recv_cmd_cb,
            "send_status": self._send_status,
        })

    @callback_decorator()
    def _connecting_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection initiation.
        """
        self.logger.info("Connecting to device: {}".format(
            od.string_cast(arsdk_device_info.contents.name)))

    @callback_decorator()
    def _connected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection completion.
        """
        device_name = od.string_cast(arsdk_device_info.contents.name)
        if self._device_name is None:
            self._device_name = device_name
            if self._name is None:
                self.logger = getLogger(
                    "olympe.drone.{}".format(self._device_name))
        self.logger.info("Connected to device: {}".format(device_name))
        json_info = od.string_cast(arsdk_device_info.contents.json)
        try:
            self._controller_state.device_conn_status.device_infos["json"] = \
                json.loads(json_info)
            self.logger.info(
                '%s' % pprint.pformat(self._controller_state.device_conn_status.device_infos))
        except ValueError:
            self.logger.error(
                'json contents cannot be parsed: {}'.format(json_info))

        self._controller_state.device_conn_status.connected = True

        if not self._is_skyctrl and self._media_autoconnect:
            media_hostname = self._ip_addr_str
            if self._media is not None:
                self._media.shutdown()
                self._media = None
            self._media = Media(
                media_hostname,
                name=self._name,
                device_name=self._device_name,
                scheduler=self._scheduler
            )
            self._media.async_connect()
        if self._connect_future is not None:
            self._connect_future.set_result(True)

    @callback_decorator()
    def _disconnected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
         Notify disconnection.
        """
        self.logger.info("Disconnected from device: {}".format(
            od.string_cast(arsdk_device_info.contents.name)))
        self._controller_state.device_conn_status.connected = False
        if self._disconnect_future is not None:
            self._disconnect_future.set_result(True)
        self._thread_loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _canceled_cb(self, _arsdk_device, arsdk_device_info, reason, _user_data):
        """
        Notify connection cancellation. Either because 'disconnect' was
        called before 'connected' callback or remote aborted/rejected the
        request.
        """
        reason_txt = od.string_cast(
            od.arsdk_conn_cancel_reason_str(reason))
        self.logger.info(
            "Connection to device: {} has been canceled for reason: {}".format(
                od.string_cast(arsdk_device_info.contents.name), reason_txt))
        if self._connect_future is not None:
            self._connect_future.set_result(False)
        self._thread_loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_device, _arsdk_device_info, status, _user_data):
        """
         Notify link status. At connection completion, it is assumed to be
         initially OK. If called with KO, user is responsible to take action.
         It can either wait for link to become OK again or disconnect
         immediately. In this case, call arsdk_device_disconnect and the
         'disconnected' callback will be called.
        """
        self.logger.info("Link status: {}".format(status))
        # If link has been lost, we must start disconnection procedure
        if status == od.ARSDK_LINK_STATUS_KO:
            # the device has been disconnected
            self._controller_state.device_conn_status.connected = False
            self._thread_loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _recv_cmd_cb(self, _interface, command, _user_data):
        """
        Function called when an arsdk event message has been received.
        """
        message_id = command.contents.id
        if message_id not in self.messages.keys():
            feature_name, class_name, msg_id = (
                messages.ArsdkMessages.get().unknown_message_info(message_id))
            if feature_name is not None:
                if class_name is not None:
                    scope = "{}.{}".format(feature_name, class_name)
                else:
                    scope = feature_name
                self.logger.warning(
                    "Unknown message id: {} in {}".format(msg_id, scope)
                )
            else:
                self.logger.warning(
                    "Unknown message id 0x{:08x}".format(message_id))
            return
        message = self.messages[message_id]
        try:
            res, message_args = message._decode_args(command)
        except Exception as e:
            self.logger.exception("Failed to decode message {}".format(message))
            self._decoding_errors.append(e)
            return

        if res != 0:
            msg = ("Unable to decode event, error: {} , id: {} , name: {}".
                   format(res, command.contents.id, message.FullName))
            self.logger.error(msg)
            self._decoding_errors.append(RuntimeError(msg))

        try:
            message_event = message._event_from_args(*message_args)
        except Exception as e:
            self.logger.exception("Failed to decode message {}{}".format(
                message, message_args))
            self._decoding_errors.append(e)
            return

        if message.message_type is messages.ArsdkMessageType.CMD:
            msg = (f"a commande message has been received "
                   f"from the drone: {message_event}")
            self.logger.error(msg)
            self._decoding_errors.append(RuntimeError(msg))
            return

        # Save the states and settings in a dictionary
        self._update_states(message, message_args, message_event)

        # Format received events as string
        self.logger.log(message.loglevel, str(message_event))

        # Handle drone connection_state events
        if self._is_skyctrl and message_id == drone_manager.connection_state.id:
            if message_event._args["state"] == drone_manager_enums.connection_state.connected:
                self.logger.info("Skycontroller connected to drone")
                all_states_settings_commands = [
                    common.Common.AllStates, common.Settings.AllSettings]
                for all_states_settings_command in all_states_settings_commands:
                    self._send_command(
                        all_states_settings_command,
                        _no_expect=True, _async=True, _ensure_connected=False
                    )

                # The SkyController forwards port tcp/180 to the drone tcp/80
                # for the web API endpoints
                if self._media_autoconnect:
                    if self._media is not None:
                        self._media.shutdown()
                        self._media = None
                    media_hostname = self._ip_addr_str + ":180"
                    self._media = Media(
                        media_hostname,
                        name=self._name,
                        device_name=self._device_name,
                        scheduler=self._scheduler
                    )
                    self._media.async_connect()
            if message_event._args["state"] == drone_manager_enums.connection_state.disconnecting:
                self.logger.info("Skycontroller disconnected from drone")
                if self._media is not None:
                    self._media.shutdown()
                    self._media = None

        # Update the currently monitored expectations
        self._scheduler.process_event(message_event)

    def _synchronize_clock(self):
        date_time = datetime.datetime.now(
            get_localzone()).strftime("%Y%m%dT%H%M%S%z")
        if not self._is_skyctrl:
            current_date_time = common.Common.CurrentDateTime
        else:
            current_date_time = skyctrl.Common.CurrentDateTime
        res = self._send_command(
            current_date_time,
            date_time,
            _ensure_connected=False,
            _async=True,
            _timeout=0.5
        )

        def _on_sync_done(res):
            if not res.success():
                msg = "Time synchronization failed for {}".format(
                    self._ip_addr)
                self.logger.warning(msg)
            else:
                self.logger.info("Synchronization of {} at {}".format(
                    self._ip_addr, date_time))

        res.add_done_callback(_on_sync_done)

    def _update_states(self, message, message_args, message_event):
        # Set the last event for this message, this will also update the message state
        message._set_last_event(message_event)

        # The rest of this function is only useful to the legacy state API
        message_name = message.FULL_NAME
        callback_type = message.callback_type
        args_pos = message.args_pos
        message_args = OrderedDict((
            (name.upper(), message_args[pos])
            for name, pos in args_pos.items()
        ))

        if callback_type == messages.ArsdkMessageCallbackType.STANDARD:
            self._controller_state.device_states.states[message_name] = message_args
        elif callback_type == messages.ArsdkMessageCallbackType.MAP:
            key = message_args[message.key_name.upper()]
            self._controller_state.device_states.states[message_name][key] = message_args
        elif callback_type == messages.ArsdkMessageCallbackType.LIST:
            insert_pos = next(reversed(self._controller_state.device_states.states[message_name]), -1) + 1
            self._controller_state.device_states.states[message_name][insert_pos] = message_args

    @callback_decorator()
    def _piloting_timer_cb(self, timer, _user_data):
        self.logger.debug("piloting timer callback: {}".format(timer))
        if self._controller_state.device_conn_status.connected:
            self._send_piloting_command()

    @callback_decorator()
    def _dispose_cmd_cb(self, _interface, _user_data):
        self.logger.debug("Dispose callback")

    @callback_decorator()
    def _cmd_itf_send_status_cb(self, _interface, _command, status, done, userdata):
        """
        Function called when a new command has been received.
         0 -> ARSDK_CMD_ITF_SEND_STATUS_SENT,
         1 -> ARSDK_CMD_ITF_SEND_STATUS_ACK_RECEIVED,
         2 -> ARSDK_CMD_ITF_SEND_STATUS_TIMEOUT,
         3 -> ARSDK_CMD_ITF_SEND_STATUS_CANCELED,
        """
        status_repr = od.arsdk_cmd_itf_send_status__enumvalues.get(
            status, status)
        done = bool(done)
        send_status_userdata = py_object_cast(userdata)
        send_command_future, message, args = send_status_userdata
        self.logger.debug(
            f"Command send status: {message.fullName} "
            f"{status_repr}, done: {done}"
        )
        if not done:
            return
        if status in (
                od.ARSDK_CMD_ITF_SEND_STATUS_ACK_RECEIVED,
                od.ARSDK_CMD_ITF_SEND_STATUS_SENT):
            send_command_future.set_result(True)
        else:
            send_command_future.set_result(False)
            self.logger.error(
                "Command send status cancel/timeout: "
                f"{message.fullName} {status_repr}, done: {done}"
            )
        del self._send_status_userdata[id(send_command_future)]

    def _destroy_pdraw(self):
        if self._pdraw is not None:
            self._pdraw.dispose()
        self._pdraw = None

    def _create_pdraw_interface(self):
        legacy_streaming = not (
            self._is_skyctrl or
            od.arsdk_device_type__enumvalues[
                self._device_type].startswith("ARSDK_DEVICE_TYPE_ANAFI")
        )
        return Pdraw(
            name=self._name,
            device_name=self._device_name,
            legacy=legacy_streaming,
            buffer_queue_size=self.video_buffer_queue_size,
        )

    @callback_decorator()
    def _enable_legacy_video_streaming_impl(self):
        """
        Enable the streaming on legacy drones (pre-anafi)
        """

        try:
            ret = self._send_command_impl(ardrone3.mediaStreaming.VideoEnable, 1)
        except Exception as e:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "MediaStreaming_VideoEnable 1 Failed {}".format(str(e))
            )
        if ret != ErrorCodeDrone.OK:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "MediaStreaming_VideoEnable 1 Failed"
            )
        else:
            return makeReturnTuple(
                ErrorCodeDrone.OK,
                "MediaStreaming_VideoEnable 1 Success"
            )

    @callback_decorator()
    def _disable_legacy_video_streaming_impl(self):
        """
        Disable the streaming on legacy drones (pre-anafi)
        """
        try:
            ret = self._send_command_impl(ardrone3.mediaStreaming.VideoEnable, 0)
        except Exception as e:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "MediaStreaming_VideoEnable 0 Failed {}".format(str(e))
            )

        if ret != ErrorCodeDrone.OK:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE, "MediaStreaming_VideoEnable 0 Failed")
        else:
            return makeReturnTuple(
                ErrorCodeDrone.OK, "MediaStreaming_VideoEnable 0 Success")

        return ret

    @callback_decorator()
    def _create_command_interface(self):
        """
        Create a command interface to send command to the device
        """

        cmd_itf = od.POINTER_T(od.struct_arsdk_cmd_itf)()

        res = od.arsdk_device_create_cmd_itf(
            self._device.arsdk_device,
            self._cmd_itf_cbs,
            ctypes.pointer(cmd_itf))

        if res != 0:
            self.logger.error(
                "Error while creating command interface: {}".format(res))
            cmd_itf = None
        else:
            self.logger.info("Command interface has been created: itf=%s"
                              % self._cmd_itf)

        return cmd_itf

    @callback_decorator()
    def _send_command_impl(self, message, *args, quiet=False):
        """
        Must be run from the pomp loop
        """

        argv = message._encode_args(*args)
        command_name = "g_arsdk_cmd_desc_{}".format(message.Full_Name)

        # Check if we are already sending a command.
        # if it the case, wait for the lock to be released
        # Define an Arsdk structure
        cmd = od.struct_arsdk_cmd()

        # Find the description of the command in libarsdk.so
        command_desc = od.struct_arsdk_cmd_desc.in_dll(
            od._libraries['libarsdk.so'],
            command_name
        )  # pylint: disable=E1101

        # argv is an array of struct_arsdk_value
        argc = argv._length_
        # Encode the command
        res = od.arsdk_cmd_enc_argv(ctypes.pointer(cmd), ctypes.pointer(command_desc), argc, argv)

        if res != 0:
            self.logger.error("Error while encoding command {}: {}".format(
                message.fullName, res))
        else:
            self.logger.debug("Command {} has been encoded".format(message.fullName))

        # cmd_itf must exist to send command
        if self._cmd_itf is None:
            raise RuntimeError("[sendcmd] Error cmd interface seems to be destroyed")

        # Send the command
        send_command_future = Future(self._thread_loop)
        send_status_userdata = ctypes.pointer(ctypes.py_object((
            send_command_future, message, args
        )))
        self._send_status_userdata[id(send_command_future)] = send_status_userdata
        res = od.arsdk_cmd_itf_send(
            self._cmd_itf, ctypes.pointer(cmd), self._send_status, send_status_userdata)

        if res != 0:
            self.logger.error("Error while sending command: {}".format(res))
            return ErrorCodeDrone.ERROR_BAD_STATE

        mess = "{}{} has been sent to the device".format(message.fullName, tuple(args))
        if quiet:
            self.logger.debug(mess)
        else:
            self.logger.info(mess)

        return send_command_future

    def _send_piloting_command(self):

        # When piloting time is 0 => send default piloting commands
        if self._controller_state.piloting_command.piloting_time:
            # Check if piloting time since last piloting_pcmd order has been reached
            diff_time = (
                datetime.datetime.now() -
                self._controller_state.piloting_command.initial_time
            )
            if diff_time.total_seconds() >= self._controller_state.piloting_command.piloting_time:
                self._controller_state.piloting_command.set_default_piloting_command()

        # Flag to activate movement on roll and pitch. 1 activate, 0 deactivate
        if self._controller_state.piloting_command.roll or (
            self._controller_state.piloting_command.pitch
        ):
            activate_movement = 1
        else:
            activate_movement = 0

        self._send_command_impl(
            ardrone3.Piloting.PCMD,
            activate_movement,
            self._controller_state.piloting_command.roll,
            self._controller_state.piloting_command.pitch,
            self._controller_state.piloting_command.yaw,
            self._controller_state.piloting_command.gaz,
            0,
            quiet=True,
        )

    @callback_decorator()
    def _start_piloting_impl(self):

        delay = 100
        period = 25

        ok = self._thread_loop.set_timer(self._piloting_timer, delay, period)

        if ok:
            self._piloting = True
            self.logger.info(
                "Piloting interface has been correctly launched")
        else:
            self.logger.error("Unable to launch piloting interface")
        return self._piloting

    @callback_decorator()
    def _stop_piloting_impl(self):

        # Stop the drone movements
        self._controller_state.piloting_command.set_default_piloting_command()
        time.sleep(0.1)

        ok = self._thread_loop.clear_timer(self._piloting_timer)
        if ok:
            # Reset piloting state value to False
            self._piloting = False
            self.logger.info("Piloting interface stopped")
        else:
            self.logger.error("Unable to stop piloting interface")

    @callback_decorator()
    def _connection_impl(self):

        # Use default values for connection json. If we want to changes values
        # (or add new info), we just need to add them in req (using json format)
        # For instance:
        req = bytes('{ "%s": "%s", "%s": "%s", "%s": "%s"}' % (
            "arstream2_client_stream_port", PDRAW_LOCAL_STREAM_PORT,
            "arstream2_client_control_port", PDRAW_LOCAL_CONTROL_PORT,
            "arstream2_supported_metadata_version", "1"), 'utf-8')
        device_id = b""

        device_conn_cfg = od.struct_arsdk_device_conn_cfg(
            ctypes.create_string_buffer(b"arsdk-ng"), ctypes.create_string_buffer(b"desktop"),
            ctypes.create_string_buffer(bytes(device_id)), ctypes.create_string_buffer(req))

        # Send connection command
        self._connect_future = Future(self._thread_loop)
        res = od.arsdk_device_connect(
            self._device.arsdk_device,
            device_conn_cfg,
            self._device_cbs_cfg,
            self._thread_loop.pomp_loop
        )
        if res != 0:
            self.logger.error("Error while connecting: {}".format(res))
            self._connect_future.set_result(False)
        else:
            self.logger.info("Connection in progress...")
        return self._connect_future

    @callback_decorator()
    def _on_device_removed(self):

        if self._discovery:
            self._discovery.stop()

        if self._piloting:
            self._stop_piloting_impl()

        if self._pdraw:
            self._destroy_pdraw()

        if self._media:
            self._media.shutdown()
            self._media = None

        self._disconnection_impl()

        self._controller_state.device_conn_status.reset_status()
        self._controller_state.device_states.reset_all_states()
        for message in self.messages.values():
            message._reset_state()
        self._scheduler.stop()

        self._reset_instance()

    @callback_decorator()
    def _disconnection_impl(self):

        f = Future(self._thread_loop)
        if not self._controller_state.device_conn_status.connected:
            return f.set_result(True)

        self._disconnect_future = f
        res = od.arsdk_device_disconnect(self._device.arsdk_device)
        if res != 0:
            self.logger.error(
                "Error while disconnecting from device: {} ({})".format(self._ip_addr, res))
            self._disconnect_future.set_result(False)
        else:
            self.logger.info(
                "disconnected from device: {}".format(self._ip_addr))
        return self._disconnect_future

    def _reset_instance(self):
        """
        Reset drone variables
        """
        self._piloting = False
        self._device = None
        self._device_name = None
        self._discovery = None
        self._cmd_itf = None
        self._pdraw = None
        self._media = None
        self._decoding_errors = []

    def destroy(self):
        """
        explicit destructor
        """
        if self._thread_loop is not None:
            self._thread_loop.unregister_cleanup(self.destroy, ignore_error=True)
            self._thread_loop.stop()
            self._on_device_removed()
        self._destroy_pdraw()
        if self._media is not None:
            self._media.shutdown()
            self._media = None
        self._scheduler.destroy()
        self._backend.destroy()

    def connection(self):
        warn(
            "Drone.connection is deprecated, "
            "please use Drone.connect instead",
            DeprecationWarning
        )
        return self.connect()

    def connect(self):
        """
        Make all step to make the connection between the device and the pc

        :rtype: ReturnTuple
        """

        # If not already connected to a device
        if not self._device_conn_status.connected:

            # Try to idenfity the device type we are attempting to connect to...
            discovery = DiscoveryNet(self._backend, ip_addr=self._ip_addr)
            device = discovery.get_device()
            if device is None:
                self.logger.info("Net discovery failed for {}".format(self._ip_addr))
                self.logger.info("Trying 'NetRaw' discovery for {} ...".format(self._ip_addr))
                discovery.stop()
                discovery = DiscoveryNetRaw(self._backend, ip_addr=self._ip_addr)
                device = discovery.get_device()

            if device is None:
                msg = "Unable to discover the device: {}".format(self._ip_addr)
                self.logger.error(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, msg)

            # Save device related info
            self._device = device
            self._discovery = discovery
            self._device_type = self._device.type
            if self._is_skyctrl is None:
                if self._device_type in SKYCTRL_DEVICE_TYPE_LIST:
                    self._is_skyctrl = True
                else:
                    self._is_skyctrl = False

            # Connect to the device
            connected = self._thread_loop.run_async(self._connection_impl)

            # Wait while not connected or timeout is reached
            try:
                if not connected.result_or_cancel(timeout=5):
                    msg = "Unable to connect to the device: {}".format(
                        self._ip_addr)
                    self.logger.error(msg)
                    self._thread_loop.run_later(self._on_device_removed)
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_CONNECTION, msg)
            except FutureTimeoutError:
                msg = "connection time out for device: {}".format(
                    self._ip_addr)
                self.logger.error(msg)
                self._thread_loop.run_later(self._on_device_removed)
                return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, msg)

            # Create the arsdk command interface
            if self._cmd_itf is None:
                self._cmd_itf = self._thread_loop.run_async(
                    self._create_command_interface).result_or_cancel(timeout=1)
                if self._cmd_itf is None:
                    msg = "Unable to create command interface: {}".format(
                        self._ip_addr)
                    self.logger.error(msg)
                    self.disconnect()
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_CONNECTION, msg)

            # Create pdraw video streaming interface
            if self._pdraw is None:
                self._pdraw = self._create_pdraw_interface()
                if self._pdraw is None:
                    msg = "Unable to create video streaming interface: {}".format(
                        self._ip_addr)
                    self.logger.error(msg)
                    self.disconnect()
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_CONNECTION, msg)

            if not self._ip_addr_str.startswith("10.202"):
                self._synchronize_clock()

            # We're connected to the device, get all device states and settings if necessary
            if not self._is_skyctrl:
                all_states_settings_commands = [
                    common.Common.AllStates, common.Settings.AllSettings]
            else:
                all_states_settings_commands = [
                    skyctrl.Common.AllStates, skyctrl.Settings.AllSettings]

            def _send_states_settings_cmd(self, command):
                res = self._send_command(command, _ensure_connected=False)
                if not res.OK:
                    msg = "Unable get device state/settings: {} for {}".format(
                        command.fullName, self._ip_addr)
                    self.logger.error(msg)
                    self.disconnect()
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_CONNECTION, msg)
                return res

            # Get device specific states and settings
            for states_settings_command in all_states_settings_commands:
                res = _send_states_settings_cmd(self, states_settings_command)
                if not res.OK:
                    return res

            if self._is_skyctrl:
                # If the skyctrl is connected to a drone get the drone states and settings too
                if self(drone_manager.connection_state(
                        state="connected", _policy="check")):
                    all_states = (self)(
                        common.CommonState.AllStatesChanged() &
                        common.SettingsState.AllSettingsChanged()
                    ).wait(_timeout=5)
                    if not all_states.success():
                        msg = "Unable get connected drone states and/or settings"
                        self.logger.error(msg)

        return makeReturnTuple(
            ErrorCodeDrone.OK,
            "Connection with device OK. IP {}".format(self._ip_addr))

    def _get_message(self, id_):
        """
        Returns a drone internal message object given its ID
        """
        return self.messages[id_]

    def _send_command_raw(self, message, *args):
        """
        Just send a command message asynchronously without waiting for the
        command expectations.
        """
        return self._thread_loop.run_async(self._send_command_impl, message, *args)

    def _send_command(self, message, *args, **kwds):
        """
        Send a command message to the drone and wait for the associated expectation

        :param _timeout: timeout value for the expectations
            ignored if `no_expect` is False or if `async` is True.
        :param _no_expect: boolean value. If True, monitor the command message expected
            callbacks. Otherwise, just send the command message. (defaults to True)
        :param _async: boolean value. If True, this function returns immediately an Expectation
            object that will be signaled when the command message is sent and
            the command message expected callbacks have been received (if `_no_expect`
            is also True). If `_async` is False, this function will wait for the message to be
            sent and for the command message expected callbacks to be received
            (if `_no_expect` is also True). This parameter defaults to False.
        :param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        :param _ensure_connected: boolean value. If true this function will try to ensure that
            olympe is connected to the drone before sending the command. (defaults to True)

        :return: an expectation object if `_async` is True or a ReturnTuple otherwise.
        """
        # "arsdkng.messages" module contains timeouts customized by commands.
        # Be careful for some cmd this timeout is not appropriate
        # for example : for a move by to detect the end,
        # we don't take different time for a little move by or a big move by
        default_timeout = message.timeout
        timeout = kwds.pop('_timeout', default_timeout)
        no_expect = kwds.pop('_no_expect', False)
        _async = kwds.pop('_async', False)
        ensure_connected = kwds.pop('_ensure_connected', True)
        deprecated_statedict = kwds.pop('_deprecated_statedict', False)

        if kwds:
            error_message = "'{}' does not take any keywords parameter, got {}".format(
                message.FullName, kwds),
            if _async:
                return FailedExpectation(error_message)
            return makeReturnTuple(ErrorCodeDrone.ERROR_PARAMETER, error_message)

        if ensure_connected and not self._device_conn_status.connected and not self.connect():
            error_message = "Cannot make connection"
            if _async:
                return FailedExpectation(error_message)
            return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, error_message)
        elif not self._device_conn_status.connected:
            error_message = "Not connected to any device: cannot send message"
            if _async:
                return FailedExpectation(error_message)
            return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, error_message)

        expectations = message._expect(
            *args,
            _no_expect=no_expect,
            _timeout=(None if not _async else timeout)
        )
        if deprecated_statedict:
            expectations._set_deprecated_statedict()

        self.schedule(expectations)

        if _async:
            return expectations
        elif expectations.wait(timeout).success():
            return makeReturnTuple(
                self.error_code_drones.OK,
                "message {} successfully sent".format(message.fullName),
                expectations.received_events()
            )
        else:
            message = "message {} not sent".format(message.fullName)
            explanation = expectations.explain()
            if explanation is not None:
                message += ": {}".format(explanation)
            self.logger.error(message)
            return makeReturnTuple(self.error_code_drones.ERROR_PARAMETER, message, None)

    def schedule(self, expectations, **kwds):
        """
        See: Drone.__call__()
        """
        ensure_connected = kwds.pop('_ensure_connected', False)
        if kwds:
            return FailedExpectation(
                "Drone.schedule got unexpected keyword parameter(s): {}".format(kwds.keys())
            )

        if ensure_connected and not self._device_conn_status.connected and not self.connect():
            return FailedExpectation("Cannot make connection")
        elif not self._device_conn_status.connected:
            return FailedExpectation("Not connected to any device")

        self._scheduler.schedule(expectations)
        return expectations

    def __call__(self, expectations, **kwds):
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
        return self.schedule(expectations, **kwds)

    def disconnection(self):
        warn(
            "Drone.disconnection is deprecated, "
            "please use Drone.disconnect instead",
            DeprecationWarning
        )
        return self.disconnect()

    def disconnect(self):
        """
        Disconnects current device (if any)
        Blocks until it is done or abandoned

        :rtype: ReturnTuple
        """
        if not self._device_conn_status.connected:
            return makeReturnTuple(ErrorCodeDrone.OK, 'Already disconnected')

        self.logger.info("we are not disconnected yet")
        disconnected = self._thread_loop.run_async(self._disconnection_impl)

        # wait max 5 sec until disconnection gets done
        if disconnected.result_or_cancel(timeout=5):
            mess = "Disconnection with the device OK. IP: {}".format(
                self._ip_addr)
            self.logger.info(mess)
            return makeReturnTuple(ErrorCodeDrone.OK, mess)

        mess = 'Cannot disconnect properly: {} {}'.format(
               self._device_conn_status.connected, self._discover)
        self.logger.error(mess)

        return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, mess)

    def help(self, pattern=""):
        """
        Returns the list of all commands available in olympe or the docstring of a specific command

            :type pattern: string
            :param pattern: a string that should be part of a command's name
            :rtype: list or string
            :return:
                - if no pattern is given, a list of all commands available
                - if the pattern is not exactly a command name, a list of commands containing the pattern
                - if pattern is the name of a command, the docstring of the command as a string (use print to display it)
        """
        func_doc = OrderedDict()
        matching_func = []
        # Use inspect to get olympe specific methods
        func_list = inspect.getmembers(
            Drone,
            lambda m: inspect.ismethod(m) and m.__func__ in m.im_class.__dict__.values())
        func_names = [
            name for name, _value in func_list if not name.startswith('_')
        ]
        # Extend the function list with arsdk commands
        func_names.extend([
            message_alias
            for message in self.messages.values()
            for message_alias in message.aliases
        ])
        # Get the docstring for each command
        for func_name in func_names:
            func_doc[func_name] = inspect.getdoc(
                getattr(self, func_name)
            )

        # If no pattern is given, return the name of all commands
        if pattern == "":
            return format_columns(sorted(func_doc.keys()))
        for func_name in func_doc.keys():
            # If the pattern is a perfect match for a command name,
            # return the docstring of that command
            if pattern == func_name:
                return func_doc[func_name]
            # Else, accumulate all commands that match the pattern
            elif re.search(pattern, func_name, re.IGNORECASE):
                matching_func.append(func_name)
        if not matching_func:
            # no matching function, just return the list of all commands name
            return format_columns(sorted(func_doc.keys()))
        elif len(matching_func) == 1:
            # just one matching function, return the help of that command
            return func_doc[matching_func[0]]
        else:
            # multiple matching functions, return the list of these commands name
            return format_columns(sorted(matching_func))

    def connection_state(self):
        """
        Returns the state of the connection to the drone

        :rtype: ReturnTuple
        """
        # Check if disconnection callback was called
        if not self._device_conn_status.connected:
            self.logger.info("Disconnection has been detected")
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "The device has been disconnected"
            )
        elif not self._is_skyctrl:
            self.logger.info("connected to the drone")
            return makeReturnTuple(ErrorCodeDrone.OK, "The device is connected")
        else:
            try:
                if self.check_state(drone_manager.connection_state, state="connected"):
                    return makeReturnTuple(
                        ErrorCodeDrone.OK,
                        "The SkyController is connected to the drone"
                    )
                else:
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_BAD_STATE,
                        "The SkyController is not connected to the drone"
                    )
            except KeyError:
                    return makeReturnTuple(
                        ErrorCodeDrone.ERROR_BAD_STATE,
                        "The SkyController is not connected to the drone"
                    )

    def get_last_event(self, message, key=None):
        """
        Returns the drone last event for the event message given in parameter
        """
        event = self._get_message(message.id).last_event(key=key)
        if event is None:
            raise KeyError("Message `{}` last event is unavailable".format(message.fullName))
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
        _float_tol = kwds.pop('_float_tol', DEFAULT_FLOAT_TOL)
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

    @property
    def scheduler(self):
        return self._scheduler

    def subscribe(self, *args, **kwds):
        """
        See: :py:func:`~olympe.expectations.Scheduler.subscribe`
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

    def start_piloting(self):
        """
        Start interface to send piloting commands

        :rtype: ReturnTuple
        """
        if self._piloting:
            self.logger.info("Piloting interface already launched")
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting interface already launched")

        f = self._thread_loop.run_async(self._start_piloting_impl)

        ok = f.result_or_cancel(timeout=2)
        if not ok:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "Unable to launch piloting interface")

        return makeReturnTuple(
            ErrorCodeDrone.OK,
            "Piloting interface has been correctly launched")

    def stop_piloting(self):
        """
        Stop interface to send piloting commands

        :rtype: ReturnTuple
        """
        # Check piloting interface is running
        if not self._piloting:
            self.logger.info("Piloting interface already stopped")
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting interface already stopped")

        f = self._thread_loop.run_async(self._stop_piloting_impl)

        ok = f.result_or_cancel(timeout=2)
        if not ok:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "Unable to stop piloting interface")

        return makeReturnTuple(ErrorCodeDrone.OK, "Piloting interface stopped")

    @ensure_connected
    def piloting_pcmd(self, roll, pitch, yaw, gaz, piloting_time):
        """
        Send command to the drone to move it.
        This function is a non-blocking function.

        :type roll: int
        :param roll: roll consign for the drone (must be in [-100:100])
        :type pitch: int
        :param pitch: pitch consign for the drone  (must be in [-100:100])
        :type yaw: int
        :param yaw: yaw consign for the drone  (must be in [-100:100])
        :type gaz: int
        :param gaz: gaz consign for the drone  (must be in [-100:100])
        :type piloting_time: float
        :param piloting_time: The time of the piloting command
        :rtype: ReturnTuple

        """
        # Check if piloting has been started and update piloting command
        if self._piloting:
            self._piloting_command.update_piloting_command(
                roll, pitch, yaw, gaz, piloting_time)
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting PCMD mode OK")

        else:
            self.logger.error("You must launch start_piloting")
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_PILOTING_STATE,
                "You must launch start_piloting")

    def start_video_streaming(self,
                              resource_name="live",
                              media_name="DefaultVideo"):
        """
        Starts a video streaming session

        :type resource_name: str
        :param resource_name: video streaming resource. This parameter defaults
            to "live" for the live video stream from the drone front camera.
            Alternatively, `resource_name` can also point to a video file on the
            drone that is available for replay. In the later case, it takes the
            form "replay/RESOURCE_ID" where `RESOURCE_ID` can be obtained
            through the drone media REST API at
            http://10.202.0.1/api/v1/media/medias.

            Examples:

                - "live"
                - "replay/100000010001.MP4"
                - "replay/200000020002.MP4"
                - ...

        :type media_name: str
        :param media_name: video stream media name. A video stream resource
            (e.g. "live" or "replay/..") may provide multiple media tracks.
            Use the `media_name` parameter to select the source from the
            available media. This parameter defaults to "DefaultVideo".
            If the requested media is unavailable, the default media will be
            selected instead without reporting any error.

            Possible values:

                - "DefaultVideo"
                - "ParrotThermalVideo" (available with an ANAFI Thermal when
                  replaying a thermal video).

        See:
            - :py:func:`~olympe.Drone.set_streaming_output_files`
            - :py:func:`~olympe.Drone.set_streaming_callbacks`


        :rtype: ReturnTuple
        """
        if self._pdraw is None:
            msg = "Cannot start streaming while the drone is not connected"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if self._pdraw.is_legacy():
            f = self._thread_loop.run_async(
                self._enable_legacy_video_streaming_impl)
            try:
                if not f.result_or_cancel(timeout=5):
                    msg = "Unable to enable legacy video streaming"
                    self.logger.error(msg)
                    return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            except FutureTimeoutError:
                msg = "Unable to enable legacy video streaming (timeout)"
                self.logger.error(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        try:
            play_res = self._pdraw.play(
                server_addr=self._ip_addr,
                resource_name=resource_name,
                media_name=media_name).result_or_cancel(timeout=5)
        except FutureTimeoutError:
            msg = "video stream play timedout"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        if not play_res:
            msg = "Failed to play video stream"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        return makeReturnTuple(
            self.error_code_drones.OK, "Playing video stream")

    def stop_video_streaming(self):
        """
        Stops the live video stream from the drone front camera

        :rtype: ReturnTuple
        """
        if self._pdraw is None:
            msg = "Cannot start streaming while the drone is not connected"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if self._pdraw.is_legacy():
            f = self._thread_loop.run_async(
                self._disable_legacy_video_streaming_impl)
            try:
                if not f.result_or_cancel(timeout=5):
                    msg = "Unable to disable legacy video streaming"
                    self.logger.error(msg)
                    return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            except FutureTimeoutError:
                msg = "Unable to disable legacy video streaming (timeout)"
                self.logger.error(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        try:
            if not self._pdraw.pause().result_or_cancel(timeout=5):
                msg = "Failed to pause video stream"
                self.logger.error(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            if not self._pdraw.close().result_or_cancel(timeout=5):
                msg = "Failed to close video stream"
                self.logger.error(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        except FutureTimeoutError:
            msg = "Failed to stop video stream (timeout)"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        return makeReturnTuple(self.error_code_drones.OK, "Video stream paused")

    def wait_video_streaming(self, state, timeout=None):
        """
        Wait for the provided Pdraw state

        This function returns True when the requested state is reached or False
        if the timeout duration is reached.

        If the requested state is already reached, this function returns True
        immediately.

        This function may block indefinitely when called without a timeout
        value.

        :type state: PdrawState
        :param timeout: the timeout duration in seconds or None (the default)
        :type timeout: float
        :rtype: bool
        """

        if self._pdraw is None:
            msg = "Cannot wait streaming state while the drone is not connected"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if not self._pdraw.wait(state, timeout=timeout):
            msg = "Wait for streaming state {} timedout".format(state)
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        return makeReturnTuple(
            ErrorCodeDrone.OK, "Wait for {} streaming state OK".format(state))

    def set_streaming_output_files(self,
                                   h264_data_file=None,
                                   h264_meta_file=None,
                                   h264_info_file=None,
                                   raw_data_file=None,
                                   raw_meta_file=None,
                                   raw_info_file=None):
        """
        Records the video streams from the drone

        - xxx_meta_file: video stream metadata output files
        - xxx_data_file: video stream frames output files
        - xxx_info_file: video stream frames info files
        - h264_***_file: files associated to the H264 encoded video stream
        - raw_***_file: files associated to the decoded video stream

        This function MUST NOT be called when the video streaming is active.
        Otherwise raises a RuntimeError exception.
        Setting a file parameter to `None` disables the recording for the related stream part.
        """

        if self._pdraw is None:
            msg = "Cannot set streaming output file while the drone is not connected"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        self._pdraw.set_output_files(h264_data_file,
                                     h264_meta_file,
                                     h264_info_file,
                                     raw_data_file,
                                     raw_meta_file,
                                     raw_info_file)
        return makeReturnTuple(self.error_code_drones.OK, "Video stream paused")

    def set_streaming_callbacks(self,
                                h264_cb=None,
                                raw_cb=None,
                                start_cb=None,
                                end_cb=None,
                                flush_h264_cb=None,
                                flush_raw_cb=None):
        """
        Set the callback functions that will be called when a new video stream frame is available,
        when the video stream starts/ends or when the video buffer needs to get flushed.

        **Video frame callbacks**

        - `h264_cb` is associated to the H264 encoded video stream
        - `raw_cb` is associated to the decoded video stream

        Each video frame callback function takes an :py:func:`~olympe.VideoFrame` parameter whose
        lifetime ends after the callback execution. If this video frame is passed to another thread,
        its internal reference count need to be incremented first by calling
        :py:func:`~olympe.VideoFrame.ref`. In this case, once the frame is no longer needed, its
        reference count needs to be decremented so that this video frame can be returned to
        memory pool.

        **Video flush callbacks**

        - `flush_h264_cb` is associated to the H264 encoded video stream
        - `flush_raw_cb` is associated to the decoded video stream

        Video flush callback functions are called when a video stream reclaim all its associated
        video buffer. Every frame that has been referenced

        **Start/End callbacks**

        The `start_cb`/`end_cb` callback functions are called when the video stream start/ends.
        They don't accept any parameter.

        The return value of all these callback functions are ignored.
        If a callback is not desired, leave the parameter to its default value or set it to `None`
        explicitly.
        """

        if self._pdraw is None:
            msg = "Cannot set streaming callbacks while the drone is not connected"
            self.logger.error(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        self._pdraw.set_callbacks(
            h264_cb=h264_cb,
            raw_cb=raw_cb,
            start_cb=start_cb,
            end_cb=end_cb,
            flush_h264_cb=flush_h264_cb,
            flush_raw_cb=flush_raw_cb,
        )
        return makeReturnTuple(self.error_code_drones.OK, "Video stream set_callbacks")

    def get_streaming_session_metadata(self):
        if self._pdraw is None:
            return None
        return self._pdraw.get_session_metadata()

    @ensure_connected
    def get_last_available_states(self):
        """
        Return states values store since the beginning of the connection with the drone.
        DEPRECATED: Use :py:func:`~olympe.Drone.get_state` instead.
            :rtype: [integer, string, dict]
            :return: [Error code, Error string, dictionary of all states of the drone]
        """
        # All states are requested during the drone connection and are kept
        # up to date since then
        return makeReturnTuple(
            self.error_code_drones.OK, "Get all states results OK",
            self._device_states.states)

    @ensure_connected
    def get_last_available_settings(self):
        """
        Return settings values store since the beginning of the connection with the drone.
        DEPRECATED: Use :py:func:`~olympe.Drone.get_state` instead.
            :rtype: [integer, string, dict]
            :return: [Error code, Error string, dictionary of all settings of the drone]
        """
        # All settings are requested during the drone connection and are kept
        # up to date since then
        return makeReturnTuple(
            self.error_code_drones.OK, "Get all settings OK",
            self._device_states.states)

    def _get_last_state_and_settings(self):
        return self._device_states.states

    @property
    def media(self):
        return self._media

    @property
    def media_autoconnect(self):
        return self._media_autoconnect


class Drone(ControllerBase):
    """
    Drone class

    Use this class to send and/or receive SDK messages to a simulated or physical drone.
    This class can also be used to connect to a SkyController that is already
    paired with a drone. SkyController class is appropriate if you need
    to connect to an unknown drone.

    Please refer to the Olympe :ref:`user guide<user-guide>` for more information.

    Example:

    .. code-block:: python

        import olympe
        from olympe.messages.ardrone3.Piloting import TakeOff

        drone = olympe.Drone("10.202.0.1")
        drone.connect()
        drone(TakeOff()).wait()
        drone.disconnect()
    """


class SkyController(ControllerBase):
    """
    SkyController class

    Use this class to send and/or receive SDK messages to a SkyController.
    See drone_manager feature to connect/forget a drone.

    Please refer to the Olympe :ref:`user guide<user-guide>` for more information.

    Example:

    .. code-block:: python

        import olympe
        from olympe.messages.drone_manager import connection_state

        skyctrl = olympe.Skyctrl("192.168.53.1")
        skyctrl.connect()
        skyctrl(connection_state(state="connected")).wait(_timeout=10)
        skyctrl.disconnect()
    """

    def __init__(self, *args, **kwds):
        super().__init__(*args, is_skyctrl=True, **kwds)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_device, _arsdk_device_info, status, _user_data):
        """
         Notify link status. At connection completion, it is assumed to be
         initially OK. If called with KO, user is responsible to take action.
         It can either wait for link to become OK again or disconnect
         immediately. In this case, call arsdk_device_disconnect and the
         'disconnected' callback will be called.
        """
        self.logger.info("Link status: {}".format(status))
        if status == od.ARSDK_LINK_STATUS_KO:
            # FIXME: Link status KO seems to be an unrecoverable
            # random error with a SkyController when `drone_manager.forget`
            # is sent to the SkyController
            self.logger.error("Link status KO")
