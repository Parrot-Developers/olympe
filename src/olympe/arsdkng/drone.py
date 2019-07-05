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
import sys
import time
import traceback
from collections import OrderedDict


import olympe.arsdkng.enums as enums
import olympe.arsdkng.messages as messages
from olympe.arsdkng.expectations import FailedExpectation, FutureExpectation

from olympe.arsdkng.pdraw import Pdraw, PDRAW_LOCAL_STREAM_PORT
from olympe.arsdkng.pdraw import PDRAW_LOCAL_CONTROL_PORT

from olympe.tools.logger import TraceLogger, DroneLogger, ErrorCodeDrone
from olympe._private import makeReturnTuple
from olympe._private.controller_state import ControllerState
from olympe._private.format import columns as format_columns
from olympe._private.pomp_loop_thread import PompLoopThread
from olympe.messages import common as common
from olympe.messages import skyctrl as skyctrl
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
            DroneLogger.LOGGER.logI(
                "Disconnection has been detected, reconnection will be done")
            if not self.connection():
                DroneLogger.LOGGER.logE("Cannot make connection")
                return makeReturnTuple(
                    ErrorCodeDrone.ERROR_CONNECTION, "Cannot make connection"
                )

        result = function(self, *args, **kwargs)

        return result

    return wrapper


##############################################################################

class Drone(object):
    """
    Drone controller class

    Use this class to send and/or receive SDK messages to a simulated or physical drone.

    Please refer to the Olympe :ref:`user guide<user-guide>` for more information.

    Example:

    .. code-block:: python

        import olympe
        from olympe.messages.ardrone3.Piloting import TakeOff

        drone = olympe.Drone("10.202.0.1")
        drone.connection()
        drone(TakeOff()).wait()
        drone.disconnection()
    """

    def __init__(self,
                 ip_addr,
                 drone_type=None,
                 avahi=False,
                 dcport=44444,
                 mpp=False,
                 loglevel=TraceLogger.level.info,
                 logfile=sys.stdout,
                 video_buffer_queue_size=2):
        """
        :param ip_addr: the drone IP address
        :type ip_addr: str
        :param drone_type: (optional) the drone device type ID
        :type drone_type: int
        :param avahi: use avahi discovery if True (defaults to False). Avahi discovery is only
                      relevant for legacy drones.
        :type avahi: bool
        :param dcport: drone control port (default to 44444)
        :type dcport: int
        :param mpp: True if Olympe needs to connect to a drone through an MPP
        :type mpp: bool
        :param loglevel: drone logger log level (defaults to :py:attr:`olympe.tools.logger.level.info`)
        :type loglevel: int
        :param logfile: drone logger file (defaults to sys.stdout)
        :type logfile: FileObjectLike
        """

        if isinstance(ip_addr, str):
            self.addr = ip_addr.encode('utf-8')
        else:
            self.addr = ip_addr
        self.drone_type = drone_type
        self.rawdiscov_info = None
        self.backend_type = od.ARSDK_BACKEND_TYPE_NET
        self.avahi = avahi
        self.manager = None
        self.backend_net = None
        self.thread_loop = None
        self.discovery_inst = None
        self.stop_discov_steps = dict()

        if dcport is not None and drone_type is not None:
            self.rawdiscov_info = {
                'name': b'drone_under_test',
                'addr': self.addr,
                'port': dcport,
                'dronetype': drone_type,
                'serialnb': b'000000000',
            }
        self.mpp = mpp
        self.video_buffer_queue_size = video_buffer_queue_size

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

        self.error_code_drones = ErrorCodeDrone()

        self.logging = TraceLogger(loglevel, logfile)
        DroneLogger.LOGGER = self.logging

        self._controller_state = ControllerState()
        self._device_conn_status = self._controller_state.device_conn_status
        self._device_states = self._controller_state.device_states
        self._callbacks = self._controller_state.callbacks
        self._piloting_command = self._controller_state.piloting_command

        self.DRONE_DEVICE_TYPE_LIST = []
        self.SKYCTRL_DEVICE_TYPE_LIST = []
        for name, value in od.__dict__.items():
            if name.startswith('ARSDK_DEVICE_TYPE_'):
                if name.startswith('ARSDK_DEVICE_TYPE_SKYCTRL'):
                    self.SKYCTRL_DEVICE_TYPE_LIST.append(value)
                else:
                    self.DRONE_DEVICE_TYPE_LIST.append(value)

        if self.drone_type is not None:
            self.discovery_device_types = [self.drone_type] + self.SKYCTRL_DEVICE_TYPE_LIST
        else:
            self.discovery_device_types = (
                self.DRONE_DEVICE_TYPE_LIST + self.SKYCTRL_DEVICE_TYPE_LIST)

        self.pdraw = None

        self.thread_loop = PompLoopThread(self.logging)

        self._reset_instance()

        self.thread_loop.register_cleanup(self.destroy)

        # set methods according to backend choice
        if self.backend_type == od.ARSDK_BACKEND_TYPE_NET:
            self._create_backend = self._create_net_backend
            if self.avahi:
                self._start_discovery = self._start_avahi_discovery
            else:
                self._start_discovery = self._start_net_discovery
        else:
            raise RuntimeError('Unknown backend type: {}'.format(
                self.backend_type))

        self._declare_callbacks()
        self._create_manager()
        self._create_backend()

        # Setup piloting commands timer
        self.piloting_timer = self.thread_loop.create_timer(
            self._piloting_timer_cb)

        # Setup expectations monitoring timer, this is used to detect timedout
        # expectations periodically
        self.expectations_timer = self.thread_loop.create_timer(
            self._expectations_timer_cb)
        if not self.thread_loop.set_timer(self.expectations_timer, delay=200, period=50):
            error_message = "Unable to launch piloting interface"
            self.logging.logE(error_message)
            raise RuntimeError(error_message)
        # Start pomp loop thread (this is the only thread that will be running waiting
        # for commands to be sent or received)
        self.thread_loop.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.destroy()

    def _declare_callbacks(self):
        """
        Define all callbacks
        """
        self.device_cbs_cfg = od.struct_arsdk_device_conn_cbs.bind({
            "connecting": self._connecting_cb,
            "connected": self._connected_cb,
            "disconnected": self._disconnected_cb,
            "canceled": self._canceled_cb,
            "link_status": self._link_status_cb,
        })

        self.arsdk_mngr_device_cbs = od.struct_arsdk_ctrl_device_cbs.bind({
            "added": self._device_added_cb,
            "removed": self._device_removed_cb,
        })

        self.cmd_itf_cbs = od.struct_arsdk_cmd_itf_cbs.bind({
            "dispose": self._dispose_cmd_cb,
            "recv_cmd": self._recv_cmd_cb,
            "send_status": self._cmd_itf_send_status_cb,
        })

        self.backend_net_socket_callback = \
            od.arsdkctrl_backend_net_socket_cb_t(
                lambda *args: self._backend_socket_cb(*args))

        self.send_status = od.arsdk_cmd_itf_send_status_cb_t()
        self.userdata = ctypes.c_void_p()

    def _connecting_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection initiation.
        """
        self.logging.logI("Connecting to device: {}".format(
            od.string_cast(arsdk_device_info.contents.name)))

    def _connected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection completion.
        """
        device_name = od.string_cast(arsdk_device_info.contents.name)
        self.logging.logI("Connected to device: {}".format(device_name))
        json_info = od.string_cast(arsdk_device_info.contents.json)
        try:
            self._controller_state.device_conn_status.device_infos["json"] = \
                json.loads(json_info)
            self.logging.logI(
                '%s' % pprint.pformat(self._controller_state.device_conn_status.device_infos))
        except ValueError:
            self.logging.logE(
                'json contents cannot be parsed: {}'.format(json_info))

        self._create_command_interface()
        self._create_pdraw_interface()
        self._controller_state.device_conn_status.connected = True

    def _disconnected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
         Notify disconnection.
        """
        self.logging.logI("Disconnected from device: {}".format(
            od.string_cast(arsdk_device_info.contents.name)))
        self._controller_state.device_conn_status.connected = False
        self.thread_loop.run_later(self._on_device_removed)

    def _canceled_cb(self, _arsdk_device, arsdk_device_info, reason, _user_data):
        """
        Notify connection cancellation. Either because 'disconnect' was
        called before 'connected' callback or remote aborted/rejected the
        request.
        """
        reason_txt = od.string_cast(
            od.arsdk_conn_cancel_reason_str(reason))
        self.logging.logI(
            "Connection to device: {} has been canceled for reason: {}".format(
                arsdk_device_info.contents.name, reason_txt))
        time.sleep(0.1)
        self.thread_loop.run_later(self._on_device_removed)

    def _backend_socket_cb(self, backend_net, socket_fd, socket_kind, userdata):
        self.logging.logI(
            "backend_pointer {} socket_fd {} socket_kind {} userdate_pointer {}"
            .format(backend_net, socket_fd, socket_kind, userdata)
        )

    def _link_status_cb(self, _arsdk_device, _arsdk_device_info, status, _user_data):
        """
         Notify link status. At connection completion, it is assumed to be
         initially OK. If called with KO, user is responsible to take action.
         It can either wait for link to become OK again or disconnect
         immediately. In this case, call arsdk_device_disconnect and the
         'disconnected' callback will be called.
        """
        self.logging.logI("Link status: {}".format(status))
        # If link has been lost, we must start disconnection procedure
        if status == od.ARSDK_LINK_STATUS_KO:
            # the device has been destroyed
            self.device = None
            self.thread_loop.run_later(self._on_device_removed)
            self._controller_state.device_conn_status.connected = False

    def _device_added_cb(self, arsdk_device, _user_data):
        """
        Callback received when a new device is detected.
        Detected devices depends on discovery parameters
        """
        self.logging.logI("New device has been detected")

        self.device = arsdk_device
        device_conn_status = self._controller_state.device_conn_status
        device_conn_status.device_infos["state"] = 0
        self.logging.logI(
            '%s' % pprint.pformat(self._controller_state.device_conn_status.device_infos))

        # Check if the detected device has the right ip address
        # and if we are not already connected
        if not self._controller_state.device_conn_status.connected:
            # Connect the controller to the device
            # FIXME: here we are connecting to the first device we've
            # discovered. We should somehow filter on self.addr (at least for
            # AVAHI). This shouldn't be a problem for the NET discovery though.
            self._connect_to_device()

            # stop the AVAHI discovery
            # (needs to be done after connection for avahi)
            if self.backend_type == od.ARSDK_BACKEND_TYPE_NET\
                    and self.avahi:
                self._stop_discovery()
        # TODO: check if we still want a connection

    def _device_removed_cb(self, arsdk_device, _user_data):
        """
        Callback received when a device disappear from discovery search
        """
        self.logging.logI("Device has been removed")
        # TODO: Check if the device removed is the device we are connected to
        # if arsdk_device.contents.id == self._controller_state.device_conn_status.device_infos.get("id", ""):
        # the device has been destroyed
        self.device = None
        self.thread_loop.run_later(self._on_device_removed)
        self._controller_state.device_conn_status.connected = False

    def _recv_cmd_cb(self, _interface, command, _user_data):
        """
        Function called when an arsdk event message has been received.
        """
        message_id = command.contents.id
        if message_id not in self.messages.keys():
            raise RuntimeError("Unknown message id {}".format(message_id))
        message = self.messages[message_id]
        res, message_args = message._decode_args(command)
        message_event = message._event_from_args(*message_args)

        if res != 0:
            self.logging.logE(
                "Unable to decode callback, error: {} , id: {} , name: {}".
                format(res, command.contents.id, message.FullName))

        # Save the states and settings in a dictionary
        self._update_states(message, message_args, message_event)

        # Format received callback as string
        self.logging.log(str(message_event), message.loglevel)

        # Update the currently monitored expectations
        self._update_expectations(message_event)

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

    def _update_expectations(self, message_event):
        """
        Update the current expectations when an arsdk message is received
        """
        # For all current expectations
        garbage_collected_expectations = []
        for expectation in self._controller_state.callbacks.expectations:
            if expectation.cancelled() or expectation.timedout():
                # Garbage collect canceled/timedout expectations
                garbage_collected_expectations.append(expectation)
            elif expectation.check(message_event).success():
                # If an expectation successfully matched a message, signal the expectation
                # and remove it from the currently monitored expectations.
                expectation.set_result()
                garbage_collected_expectations.append(expectation)
        for expectation in garbage_collected_expectations:
            self._controller_state.callbacks.expectations.remove(expectation)

    def _expectations_timer_cb(self, timer, _user_data):
        """
        Check for canceled/timedout expectations periodically even if no message is received
        """
        # For all current expectations
        garbage_collected_expectations = []
        for expectation in self._controller_state.callbacks.expectations:
            if expectation.cancelled() or expectation.timedout():
                garbage_collected_expectations.append(expectation)
        for expectation in garbage_collected_expectations:
            self._controller_state.callbacks.expectations.remove(expectation)

    def _piloting_timer_cb(self, timer, _user_data):
        """
         Notify link status. At connection completion, it is assumed to be
         initially OK. If called with KO, user is responsible to take action.
         It can either wait for link to become OK again or disconnect
         immediately. In this case, call arsdk_device_disconnect and the
         'disconnected' callback will be called.
        """
        if self._controller_state.device_conn_status.connected:
            # Each time this callback is received; piloting command should be send
            self.logging.logD("Loop timer callback: {}".format(timer))
            if self._controller_state.device_conn_status.connected:
                self._send_piloting_command()

    def _dispose_cmd_cb(self, _interface, _user_data):
        """
        Function called when a dispose command callback has been received.
        """
        self.logging.logD("Dispose command received")

    def _cmd_itf_send_status_cb(self, _interface, _command, status, done, _userdata):
        """
        Function called when a new command has been received.
         0 -> ARSDK_CMD_ITF_SEND_STATUS_SENT,
         1 -> ARSDK_CMD_ITF_SEND_STATUS_ACK_RECEIVED,
         2 -> ARSDK_CMD_ITF_SEND_STATUS_TIMEOUT,
         3 -> ARSDK_CMD_ITF_SEND_STATUS_CANCELED,
        """
        self.logging.logD("Command send status: {0}, done: {1}".format(
            status, done))

    def _create_manager(self):
        """
        Create a manager
        """
        # Create the manager
        self.manager = od.POINTER_T(od.struct_arsdk_ctrl)()
        res = od.arsdk_ctrl_new(self.thread_loop.pomp_loop, ctypes.byref(self.manager))

        if res != 0:
            raise RuntimeError("ERROR: {}".format(res))

        self.logging.logI("New manager has been created!")

        # Send a command to add callbacks to the manager
        res = od.arsdk_ctrl_set_device_cbs(self.manager, self.arsdk_mngr_device_cbs)

        if res != 0:
            raise RuntimeError("ERROR: {}".format(res))

        self.logging.logI(
            "Manager device callbacks has been added to the manager")

    def _destroy_manager(self):
        """
        Destroy the manager
        """

        if self.manager is not None:
            res = od.arsdk_ctrl_destroy(self.manager)

            if res != 0:
                self.logging.logE(
                    "Error while destroying manager: {}".format(res))
            else:
                self.manager = None
                self.logging.logI("Manager has been destroyed")

    def _create_net_backend(self):

        self.backend_net = od.POINTER_T(od.struct_arsdkctrl_backend_net)()

        cfg = od.struct_arsdkctrl_backend_net_cfg(ctypes.create_string_buffer(b"net_config"))
        cfg.stream_supported = 1

        res = od.arsdkctrl_backend_net_new(
            self.manager, ctypes.pointer(cfg), ctypes.byref(self.backend_net))

        if res != 0:
            raise RuntimeError("ERROR: {}".format(res))

        self.logging.logI("New net backend has been created")

        if not self.avahi:
            res_set_socket = od.arsdkctrl_backend_net_set_socket_cb(
                self.backend_net, self.backend_net_socket_callback,
                self.userdata)
            if res_set_socket != 0:
                raise RuntimeError(
                    "ERROR while set callback backend set socket: {}".format(
                        res))

            self.logging.logI("Set backend socket callback OK")

    def _destroy_net_backend(self):
        if self.backend_net is not None:

            res = od.arsdkctrl_backend_net_destroy(self.backend_net)

            if res != 0:
                self.logging.logE(
                    "Error while destroying net backend: {}".format(res))
            else:
                self.backend_net = None
                self.logging.logI("Net backend has been destroyed")

    def _start_avahi_discovery(self):
        """
         Start avahi discovery in order to detect devices
        """

        if self.discovery_inst:
            self.logging.logE('Discovery already running')
            return

        devices_type = (ctypes.c_int * len(self.discovery_device_types))(
            *self.discovery_device_types
        )
        discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.addressof(devices_type), len(devices_type))

        self.discovery_inst = od.POINTER_T(od.struct_arsdk_discovery_avahi)()

        res = od.arsdk_discovery_avahi_new(
            self.manager, self.backend_net, discovery_cfg,
            ctypes.byref(self.discovery_inst))

        if res != 0:
            raise RuntimeError("ERROR: {}".format(res))

        self.logging.logI("Avahi discovery object has been created")
        self.stop_discov_steps['destroy'] = od.arsdk_discovery_avahi_destroy

        res = od.arsdk_discovery_avahi_start(self.discovery_inst)

        if res != 0:
            self._stop_discovery()
            raise RuntimeError("Avahi: {}".format(res))

        self.logging.logI("Avahi discovery has been started")
        self.stop_discov_steps['stop'] = od.arsdk_discovery_avahi_stop

    def _start_raw_discovery(self):
        """
        Raw discovery corresponds to a discovery without any active
        method to search for devices.
        That means that this discovery type only works when adding
        manually a device (using function _inject_device_to_raw_discovery).

        This method should be considered as a fallback.
        """
        if self.discovery_inst:
            self.logging.logE('Discovery already running')
            return

        self.discovery_inst = od.POINTER_T(od.struct_arsdk_discovery)()

        backendparent = od.arsdkctrl_backend_net_get_parent(self.backend_net)

        res = od.arsdk_discovery_new(
            b'raw',
            backendparent,
            self.manager,
            ctypes.byref(self.discovery_inst))
        if res != 0:
            raise RuntimeError("Unable to create raw discovery:{}".format(res))

        self.stop_discov_steps['destroy'] = od.arsdk_discovery_destroy
        self.logging.logI("discovery object has been created")

        res = od.arsdk_discovery_start(self.discovery_inst)
        if res != 0:
            self._stop_discovery()
            raise RuntimeError("Unable to start raw discovery:{}".format(res))

        self.logging.logI("discovery started")
        self.stop_discov_steps['stop'] = od.arsdk_discovery_stop

    def _inject_device_to_raw_discovery(self, info):
        """
        Calling this function supposes that a *raw* discovery is started.
        :param info dict built as follows:
                       {
                            'name':<name of the device>,
                            'dronetype':<hex number representing the drone model>
                            'addr':<ip address of the drone>
                            'port':<tcp port to connect to the drone>
                            'serialnb':<UUID specific to the drone>
                       }
        """
        info_struct = od.struct_arsdk_discovery_device_info(
            od.char_pointer_cast(info['name']), ctypes.c_int(info['dronetype']),
            od.char_pointer_cast(info['addr']), ctypes.c_uint16(info['port']),
            od.char_pointer_cast(info['serialnb']))

        res = od.arsdk_discovery_add_device(self.discovery_inst, info_struct)

        if res != 0:
            self.logging.logE("{}".format(res))
        else:
            self.stop_discov_steps['remove'] = info_struct
            self.logging.logI('Device manually added to raw discovery')

    def _start_net_discovery(self):
        """
        Start net discovery in order to detect devices
        """
        if self.discovery_inst:
            self.logging.logE('Discovery already running')
            return

        devices_type = (ctypes.c_int * len(self.discovery_device_types))(
            *self.discovery_device_types
        )
        discovery_cfg = od.struct_arsdk_discovery_cfg(
            ctypes.cast(devices_type, od.POINTER_T(od.arsdk_device_type)), len(devices_type))

        self.discovery_inst = od.POINTER_T(od.struct_arsdk_discovery_net)()

        res = od.arsdk_discovery_net_new(
            self.manager, self.backend_net, discovery_cfg,
            ctypes.c_char_p(self.addr), ctypes.byref(self.discovery_inst))
        if res != 0:
            raise RuntimeError("ERROR: {}".format(res))

        self.logging.logI("Net discovery object has been created")
        self.stop_discov_steps['destroy'] = od.arsdk_discovery_net_destroy

        res = od.arsdk_discovery_net_start(self.discovery_inst)

        if res != 0:
            self._stop_discovery()
            raise RuntimeError("Net: {}".format(res))

        self.logging.logI("Net discovery has been started")
        self.stop_discov_steps['stop'] = od.arsdk_discovery_net_stop

    def _stop_discovery(self):
        if self.discovery_inst is None:
            self.logging.logI('No discovery instance to be stopped')
            self.stop_discov_steps.clear()
            return

        if len(self.stop_discov_steps) == 0:
            self.logging.logE('Nothing to execute to stop discovery!')

        # manually remove device from discovery
        if 'remove' in self.stop_discov_steps:
            res = od.arsdk_discovery_remove_device(
                self.discovery_inst, self.stop_discov_steps['remove'])
            if res != 0:
                self.logging.logE(
                    "Error while removing device from discovery: {}".format(res))
            else:
                self.logging.logI("Device removed from discovery")

        # stop currently running discovery
        if 'stop' in self.stop_discov_steps:
            res = self.stop_discov_steps['stop'](self.discovery_inst)
            if res != 0:
                self.logging.logE(
                    "Error while stopping discovery: {}".format(res))
            else:
                self.logging.logI("Discovery has been stopped")

        # then, destroy it
        if 'destroy' in self.stop_discov_steps:
            res = self.stop_discov_steps['destroy'](self.discovery_inst)
            if res != 0:
                self.logging.logE(
                    "Error while destroying discovery object: {}".format(res))
            else:
                self.logging.logI("Discovery object has been destroyed")

        self.discovery_inst = None
        self.stop_discov_steps.clear()

    def _destroy_pdraw(self):
        if self.pdraw is not None:
            self.pdraw.dispose()
        self.pdraw = None

    def _create_pdraw_interface(self):
        legacy_streaming = self.drone_type not in (None, od.ARSDK_DEVICE_TYPE_ANAFI4K)
        self.pdraw = Pdraw(
            logging=self.logging,
            legacy=legacy_streaming,
            buffer_queue_size=self.video_buffer_queue_size,
        )

    def _enable_legacy_video_streaming_impl(self):
        """
        Enable the streaming on legacy drones (pre-anafi)
        """

        try:
            ret = self._send_command_impl(
                "g_arsdk_cmd_desc_Ardrone3_MediaStreaming_VideoEnable", 1)
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

    def _disable_legacy_video_streaming_impl(self):
        """
        Disable the streaming on legacy drones (pre-anafi)
        """
        try:
            ret = self._send_command_impl(
                "g_arsdk_cmd_desc_Ardrone3_MediaStreaming_VideoEnable", 0)
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

    def _create_command_interface(self):
        """
        Create a command interface to send command to the device
        """

        cmd_itf_candidate = od.POINTER_T(od.struct_arsdk_cmd_itf)()

        res = od.arsdk_device_create_cmd_itf(
            self.device,
            self.cmd_itf_cbs,
            ctypes.pointer(cmd_itf_candidate))

        if res != 0:
            self.logging.logE(
                "Error while creating command interface: {}".format(res))
            ret = False
        else:
            self.cmd_itf = cmd_itf_candidate
            self.logging.logI("Command interface has been created: itf=%s"
                              % self.cmd_itf)
            ret = True

        return ret

    def _send_command_impl(self, command, *args):
        """
        Must be run from the pomp loop
        """
        # Check if we are already sending a command.
        # if it the case, wait for the lock to be released
        # Define an Arsdk structure
        cmd = od.struct_arsdk_cmd()

        # Find the description of the command in libarsdk.so
        command_desc = od.struct_arsdk_cmd_desc.in_dll(
            od._libraries['libarsdk.so'],
            command
        )  # pylint: disable=E1101

        od.arsdk_cmd_enc.argtypes = od.arsdk_cmd_enc.argtypes[:2]

        # Manage commands with args by adding there types in argtypes
        for arg in args:
            od.arsdk_cmd_enc.argtypes.append(type(arg))

        # Encode the command
        res = od.arsdk_cmd_enc(ctypes.pointer(cmd), ctypes.pointer(command_desc), *args)

        if res != 0:
            self.logging.logE("Error while encoding command {}: {}".format(
                command, res))
        else:
            self.logging.logD("Command {} has been encoded".format(command))

        # cmd_itf must exist to send command
        if self.cmd_itf is None:
            raise RuntimeError("[sendcmd] Error cmd interface seems to be destroyed")

        # Send the command
        try:
            res = None
            res = od.arsdk_cmd_itf_send(
                self.cmd_itf, ctypes.pointer(cmd), self.send_status, self.userdata)
            # FIXME: We should wait for _cmd_itf_send_status_cb to be called for this command
            # before 'cmd' is garbage collected. This could also be used to get an drone
            # acknowledgment or a timeout status for this command.
        except Exception:
            self.logging.logE(
                "Error while send command. Is connected or not ?")
            self.logging.logE(traceback.format_exc())

        if res != 0:
            self.logging.logE("Error while sending command: {}".format(res))
            return ErrorCodeDrone.ERROR_BAD_STATE

        mess = "Command {} has been sent to the drone with arg {}".format(command, args)
        self.logging.logD(mess)
        return ErrorCodeDrone.OK

    def _send_piloting_command(self):

        try:
            # When piloting time is 0 => send default piloting commands
            if self._controller_state.piloting_command.piloting_time:
                # Check if piloting time since last piloting_pcmd order has been reached
                diff_time = datetime.datetime.now() - self._controller_state.piloting_command.initial_time
                if diff_time.total_seconds() >= self._controller_state.piloting_command.piloting_time:
                    self._controller_state.piloting_command.set_default_piloting_command()

            # Generate a piloting command with data from PilotingCommand class
            cmd = od.struct_arsdk_cmd()

            command_desc = od.struct_arsdk_cmd_desc.in_dll(
                od._libraries['libarsdk.so'], "g_arsdk_cmd_desc_Ardrone3_Piloting_PCMD")

            # Flag to activate movement on roll and pitch. 1 activate, 0 deactivate
            if self._controller_state.piloting_command.roll or self._controller_state.piloting_command.pitch:
                activate_movement = 1
            else:
                activate_movement = 0

            cfunc = od.arsdk_cmd_enc
            cfunc.argtypes = cfunc.argtypes[:2] + [
                ctypes.c_uint8,
                ctypes.c_int8,
                ctypes.c_int8,
                ctypes.c_int8,
                ctypes.c_int8,
                ctypes.c_uint32,
            ]
            res = cfunc(
                ctypes.pointer(cmd),
                ctypes.pointer(command_desc),
                ctypes.c_uint8(activate_movement),
                ctypes.c_int8(self._controller_state.piloting_command.roll),
                ctypes.c_int8(self._controller_state.piloting_command.pitch),
                ctypes.c_int8(self._controller_state.piloting_command.yaw),
                ctypes.c_int8(self._controller_state.piloting_command.gaz),
                ctypes.c_uint32(0),
            )

            if res != 0:
                self.logging.logE("Error while encoding command: {}".format(res))
            else:
                self.logging.logD("Piloting command has been encoded")

            # cmd_itf must exist to send command
            if self.cmd_itf is None:
                raise RuntimeError("[pcmd]Error cmd interface seems to be destroyed")

            ret = ErrorCodeDrone.OK
            res = od.arsdk_cmd_itf_send(self.cmd_itf, cmd, self.send_status, self.userdata)
            if res != 0:
                raise RuntimeError("Error while sending  piloting command: {}".format(res))

            self.logging.logD("Piloting command has been sent to the drone")

        except RuntimeError as e:
            self.logging.logE(str(e))
            ret = ErrorCodeDrone.ERROR_BAD_STATE
        return ret

    def _start_piloting_impl(self):

        delay = 100
        period = 25

        # Set piloting command depending on the drone
        self._controller_state.piloting_command.set_piloting_command(
            "g_arsdk_cmd_desc_Ardrone3_Piloting_PCMD")

        ok = self.thread_loop.set_timer(self.piloting_timer, delay, period)

        if ok:
            self.piloting = True
            self.logging.logI(
                "Piloting interface has been correctly launched")
        else:
            self.logging.logE("Unable to launch piloting interface")
        return self.piloting

    def _stop_piloting_impl(self):

        # Stop the drone movements
        self._controller_state.piloting_command.set_default_piloting_command()
        time.sleep(0.1)

        ok = self.thread_loop.clear_timer(self.piloting_timer)
        if ok:
            # Reset piloting state value to False
            self.piloting = False
            self.logging.logI("Piloting interface stopped")
        else:
            self.logging.logE("Unable to stop piloting interface")

    def _connect_to_device(self):

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
        res = od.arsdk_device_connect(
            self.device, device_conn_cfg, self.device_cbs_cfg, self.thread_loop.pomp_loop)
        if res != 0:
            self.logging.logE("Error while connecting: {}".format(res))
        else:
            self.logging.logI("Connection in progress...")

    def _on_device_removed(self):

        self._stop_discovery()

        if self.piloting:
            self._stop_piloting_impl()

        self._disconnection_impl()

        self._controller_state.device_conn_status.reset_status()
        self._controller_state.device_states.reset_all_states()
        for message in self.messages.values():
            message._reset_state()
        for expectation in self._controller_state.callbacks.expectations:
            expectation.cancel()
        self._controller_state.callbacks.reset()

        self._reset_instance()

    def _disconnection_impl(self):

        if self.device is None:
            return

        res = od.arsdk_device_disconnect(self.device)
        if res != 0:
            self.logging.logE(
                "Error while disconnecting from device: {} ({})".format(self.addr, res))
        else:
            self.logging.logI(
                "disconnected from device: {}".format(self.addr))

    def _reset_instance(self):
        """
        Reset drone variables
        """
        self.piloting = False
        self.device = None
        self.cmd_itf = None

    def destroy(self):
        """
        explicit destructor
        """
        self.thread_loop.unregister_cleanup(self.destroy)
        self._destroy_pdraw()
        self.thread_loop.stop()
        self._on_device_removed()
        if self.backend_type == od.ARSDK_BACKEND_TYPE_NET:
            self._destroy_net_backend()
        self._destroy_manager()

    def _wait_until(self, condfunc, timeout_sec, pollingstep=0.05):
        """
        Helper function to poll a condition until a given timeout.
        Parameter 'condfunc' is a function that return True if the condition
        is met, False otherwise.
        """
        timer = timeout_sec
        while timer > 0:
            if condfunc():
                return True
            time.sleep(pollingstep)
            timer -= pollingstep

        return False

    def connection(self):
        """
        Make all step to make the connection between the device and the pc

        :rtype: ReturnTuple
        """

        # If not already connected to a drone
        if not self._device_conn_status.connected:

            self.thread_loop.run_async(self._start_discovery)

            # Wait while connected callback hasn't been received or timeout is reached
            connected = self._wait_until(lambda: self._device_conn_status.connected, 5)

            if not connected and self.rawdiscov_info:
                self.thread_loop.run_async(self._stop_discovery)
                self.thread_loop.run_async(self._disconnection_impl)

                self.logging.logI(
                    'trying to bypass discovery using device %s'
                    % self.rawdiscov_info)
                self.thread_loop.run_async(self._start_raw_discovery)
                self.thread_loop.run_async(
                    self._inject_device_to_raw_discovery, self.rawdiscov_info)
                connected = self._wait_until(lambda: self._device_conn_status.connected, 5)
            if not connected:
                self.thread_loop.run_async(self._stop_discovery)
                self.thread_loop.run_async(self._disconnection_impl)
                msg = "Unable to connect to the device. IP : {} ".format(self.addr)
                self.logging.logE(msg)

                return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, msg)

        # We're connected to the drone, get all drone states and settings if necessary
        if not self._device_states.get_all_states_done:
            all_drone_states = self._send_command(
                common.Common.AllStates, _ensure_connected=False)
            if all_drone_states.OK and not self.mpp:
                self._device_states.get_all_states_done = True
            elif all_drone_states.OK:
                all_ctrl_states = self._send_command(
                    skyctrl.Common.AllStates, _ensure_connected=False)
                self._device_states.get_all_states_done = all_ctrl_states.OK

        if not self._device_states.get_all_settings_done:
            all_drone_settings = self._send_command(
                common.Settings.AllSettings, _ensure_connected=False)
            if all_drone_settings.OK and not self.mpp:
                self._device_states.get_all_settings_done = True
            elif all_drone_settings.OK:
                all_ctrl_settings = self._send_command(
                    skyctrl.Settings.AllSettings, _ensure_connected=False)
                self._device_states.get_all_settings_done = all_ctrl_settings.OK
        if not self._device_states.get_all_states_done:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_CONNECTION,
                "Cannot get states info {}".format(self.addr)
            )
        if not self._device_states.get_all_settings_done:
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_CONNECTION,
                "Cannot get settings info {}".format(self.addr)
            )
        return makeReturnTuple(
            ErrorCodeDrone.OK,
            "Connection with device OK. IP {}".format(self.addr))

    def _get_message(self, id_):
        """
        Returns a drone internal message object given its ID
        """
        return self.messages[id_]

    def _send_command_raw(self, message, *args):
        """
        Convenience wrapper around _send_command. Just send a command message
        asynchronously without waiting for the command expectations. DOES NOT
        perform a connection automatically for you if necessary.
        """
        return self._send_command(
            message, *args,
            _no_expect=True, _async=True, _ensure_connected=False
        )

    def _send_command(self, message, *args, **kwds):
        """
        Send a command message to the drone and wait for the associated expectation

        :param _timeout: timeout value for the expectations
            ignored if `no_expect` is False or if `async` is True.
        :param _no_expect: boolean value. If True, monitor the command message expected
            callbacks. Otherwise, just send the command message. (defaults to True)
        :param _async: boolean value. If True, this function returns immedialty an Expectation
            object that will be signaled when the command message is sent and
            the command message expected callbacks have been received (if `_no_expect`
            is also True). If `_async` is False, this function will wait for the message to be
            sent and for the command message expected callbacks to be received
            (if `_no_expect` is also True). This parameter defaults to False.
        :param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        :param _ensure_connected: boolean value. If true this function will try to ensure that
            olympe is connected to the drone before sending the command. (defaults to True)

        :return: an expectation object if `_async` is True or a ReturnTuple otherwize.
        """
        # "arsdkng.messages" module contains timeouts customized by commands.
        # Be careful for some cmd this timeout is not appropriate
        # for example : for a move by to detect the end,
        # we don't take same time for a little move by or a big move by
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

        if ensure_connected and not self._device_conn_status.connected and not self.connection():
            error_message = "Cannot make connection"
            if _async:
                return FailedExpectation(error_message)
            return makeReturnTuple(ErrorCodeDrone.ERROR_CONNECTION, error_message)

        if not no_expect:
            expectations = message._expect(
                *args, _send_command=False)
            if deprecated_statedict:
                expectations._set_deprecated_statedict()

            self._expect(expectations)

        args = message._encode_args(*args)
        g_arsdk_command_name = "g_arsdk_cmd_desc_{}".format(message.Full_Name)
        # commands need to be sent from the pomp loop since libpomp is NOT thread-safe.
        f = self.thread_loop.run_async(self._send_command_impl, g_arsdk_command_name, *args)

        if no_expect:
            if _async:
                e = FutureExpectation(
                    f, status_checker=lambda status: status == ErrorCodeDrone.OK)
                e._schedule(self)
                self.logging.logI("{}{}: has been sent asynchronously".format(
                    message.FullName, tuple(args)))
                return e
            try:
                f.result(timeout)
                self.logging.logI("{}{}: has been sent".format(
                    message.FullName, tuple(args)))
                return makeReturnTuple(
                    self.error_code_drones.OK,
                    "message {} sent without expectation".format(message.fullName),
                    None
                )
            except FutureTimeoutError:
                message = "message {} not sent: unexpected error".format(message.fullName)
                self.logging.logE(message)
                return makeReturnTuple(self.error_code_drones.ERROR_PARAMETER, message, None)

        if _async:
            self.logging.logI("{}{}: sent asynchronously".format(
                message.FullName, tuple(args)))
            return expectations

        if expectations.wait(timeout):
            self.logging.logI("{}{}: sent and acknowledged".format(
                message.FullName, tuple(args)))
            return makeReturnTuple(
                self.error_code_drones.OK,
                "callback found",
                expectations.received_events()
            )
        else:
            self.logging.logE("{}: Warning some callbacks weren't called: {}".format(
                message.FullName, expectations.unmatched_events()))
            return makeReturnTuple(
                self.error_code_drones.ERROR_PARAMETER,
                "Callback not found or expectation canceled: {}".format(
                    expectations.unmatched_events()),
                expectations.received_events()
            )

    def _expect(self, expectations, **kwds):
        """
        See: Drone.__call__()
        """
        ensure_connected = kwds.pop('_ensure_connected', False)
        if kwds:
            return FailedExpectation(
                "Drone._expect got unexpected keyword parameter(s): {}".format(kwds.keys())
            )

        if ensure_connected and not self._device_conn_status.connected and not self.connection():
            return FailedExpectation("Cannot make connection")

        self.thread_loop.run_async(self._expect_impl, expectations).result()
        return expectations

    def _expect_impl(self, expectations):
        expectations._schedule(self)
        if not expectations.success():
            self._callbacks.expectations.append(expectations)

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
        return self._expect(expectations, **kwds)

    def disconnection(self):
        """
        Disconnects current device (if any)
        Blocks until it is done or abandoned

        :rtype: ReturnTuple
        """
        if not self._device_conn_status.connected:
            return makeReturnTuple(ErrorCodeDrone.OK, 'Already disconnected')

        self.logging.logI("we are not disconnected yet")
        self.thread_loop.run_async(self._disconnection_impl)

        # wait max 5 sec until disconnection gets done
        ok = self._wait_until(lambda:
                              not self._device_conn_status.connected
                              and not self.discovery_inst, 5)
        if ok:
            mess = "Disconnection with the device OK. IP: {}".format(self.addr)
            self.logging.logI(mess)
            return makeReturnTuple(ErrorCodeDrone.OK, mess)

        mess = 'Cannot disconnect properly: {} {}'.format(
               self._device_conn_status.connected, self.discovery_inst)
        self.logging.logE(mess)

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
            self.logging.logI("Disconnection has been detected")
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_BAD_STATE,
                "The device has been disconnected")
        else:
            self.logging.logI("The device is connected")
            return makeReturnTuple(ErrorCodeDrone.OK, "The device is connected")

    def get_last_event(self, message):
        """
        Returns the drone last event for the event message given in parameter
        """
        event = self._get_message(message.id).last_event()
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
        last_event = self.get_last_event(message)
        expectation = message._expectation_from_args(*args, **kwds)
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

    def start_piloting(self):
        """
        Start interface to send piloting commands

        :rtype: ReturnTuple
        """
        if self.piloting:
            self.logging.logI("Piloting interface already launched")
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting interface already launched")

        f = self.thread_loop.run_async(self._start_piloting_impl)

        ok = f.result(timeout=2)
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
        if not self.piloting:
            self.logging.logI("Piloting interface already stopped")
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting interface already stopped")

        f = self.thread_loop.run_async(self._stop_piloting_impl)

        ok = f.result(timeout=2)
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
        if self.piloting:
            self._piloting_command.update_piloting_command(
                roll, pitch, yaw, gaz, piloting_time)
            return makeReturnTuple(ErrorCodeDrone.OK, "Piloting PCMD mode OK")

        else:
            self.logging.logE("You must launch start_piloting")
            return makeReturnTuple(
                ErrorCodeDrone.ERROR_PILOTING_STATE,
                "You must launch start_piloting")

    def start_video_streaming(self,
                              resource_name="live",
                              media_name="DefaultVideo"):
        """
        Starts a video streaming session

        :type resource_name: str
        :param resource_name: video streaming ressource. This parameter defaults
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
            Use the `media_name` parameter to select the media from the
            available medias. This parameter defaults to "DefaultVideo".
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
        if self.pdraw is None:
            msg = "Cannot start streaming while the drone is not connected"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if self.pdraw.is_legacy():
            f = self.thread_loop.run_async(
                self._enable_legacy_video_streaming_impl)
            try:
                if not f.result(timeout=5):
                    msg = "Unable to enable legacy video streaming"
                    self.logging.logE(msg)
                    return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            except FutureTimeoutError:
                msg = "Unable to enable legacy video streaming (timeout)"
                self.logging.logE(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if (not self.pdraw.play(
                server_addr=self.addr,
                resource_name=resource_name,
                media_name=media_name).result(timeout=5)):
            msg = "Failed to play video stream"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        return makeReturnTuple(
            self.error_code_drones.OK, "Playing video stream")

    def stop_video_streaming(self):
        """
        Stops the live video stream from the drone front camera

        :rtype: ReturnTuple
        """
        if self.pdraw is None:
            msg = "Cannot start streaming while the drone is not connected"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        if self.pdraw.is_legacy():
            f = self.thread_loop.run_async(
                self._disable_legacy_video_streaming_impl)
            try:
                if not f.result(timeout=5):
                    msg = "Unable to disable legacy video streaming"
                    self.logging.logE(msg)
                    return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            except FutureTimeoutError:
                msg = "Unable to disable legacy video streaming (timeout)"
                self.logging.logE(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        try:
            if not self.pdraw.pause().result(timeout=5):
                msg = "Failed to pause video stream"
                self.logging.logE(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
            if not self.pdraw.close().result(timeout=5):
                msg = "Failed to close video stream"
                self.logging.logE(msg)
                return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        except FutureTimeoutError:
            msg = "Failed to stop video stream (timeout)"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)

        return makeReturnTuple(self.error_code_drones.OK, "Video stream paused")

    def set_streaming_output_files(self,
                                   h264_data_file=None,
                                   h264_meta_file=None,
                                   raw_data_file=None,
                                   raw_meta_file=None):
        """
        Records the video streams from the drone

        - xxx_meta_file: video stream metadata output files
        - xxx_data_file: video stream frames output files
        - h264_***_file: files associated to the H264 encoded video stream
        - raw_***_file: files associated to the decoded video stream

        This function MUST NOT be called when the video streaming is active.
        Otherwise raises a RuntimeError exception.
        Setting a file parameter to `None` disables the recording for the related stream part.
        """

        if self.pdraw is None:
            msg = "Cannot set streaming output file while the drone is not connected"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        self.pdraw.set_output_files(h264_data_file,
                                    h264_meta_file,
                                    raw_data_file,
                                    raw_meta_file)
        return makeReturnTuple(self.error_code_drones.OK, "Video stream paused")

    def set_streaming_callbacks(self,
                                h264_cb=None,
                                raw_cb=None,
                                end_cb=None):
        """
        Set the callback functions that will be called when a new video stream frame is available or
        when the video stream has ended.

        Video frame callbacks:
        - `h264_cb` is associated to the H264 encoded video stream
        - `raw_cb` is associated to the decoded video stream

        Each video frame callback function takes an :py:func:`~olympe.VideoFrame` parameter
        The `end_cb` callback function is called when the (replayed) video stream ends and takes
        no parameter.
        The return value of all these callback functions are ignored.
        If a callback is not desired, just set it to `None`.
        """

        if self.pdraw is None:
            msg = "Cannot set streaming callbacks while the drone is not connected"
            self.logging.logE(msg)
            return makeReturnTuple(ErrorCodeDrone.ERROR_BAD_STATE, msg)
        self.pdraw.set_callbacks(
            h264_cb=h264_cb, raw_cb=raw_cb, end_cb=end_cb
        )
        return makeReturnTuple(self.error_code_drones.OK, "Video stream set_callbacks")

    def get_streaming_session_metadata(self):
        if self.pdraw is None:
            return None
        return self.pdraw.get_session_metadata()

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
