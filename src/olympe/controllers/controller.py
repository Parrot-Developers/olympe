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
import datetime
import json
import pprint
import time

from concurrent.futures import CancelledError
from typing import List, Optional, Tuple
from tzlocal import get_localzone
from warnings import warn

import olympe_deps as od

from olympe.concurrent import TimeoutError as FutureTimeoutError
from olympe.concurrent.sync import Event
from olympe.connection import SKYCTRL_DEVICE_TYPE_LIST, DeviceInfo
from olympe.connection.backend import BackendType, CtrlBackendNet, CtrlBackendMuxIp
from olympe.connection.discovery import (
    Discovery,
    DiscoveryNet,
    DiscoveryNetRaw,
    DiscoveryMux,
    _DISCOVERY_PORT,
)
from olympe.connection.networking import Connection, ConnectionListener
from olympe.messaging.arsdk_messages import ArsdkMessageType
from olympe.utils import callback_decorator
from olympe.utils.types import PointerType
from .cmd_itf import (
    CommandInterfaceBase,
    DisconnectedEvent,
    ConnectedEvent,
    ConnectionState,
)

from olympe.enums import drone_manager as drone_manager_enums
from olympe.enums import backuplink as backuplink_enums
from olympe.enums import logsync as logsync_enums
from olympe.messages import antiflicker
from olympe.messages import ardrone3
from olympe.messages import camera2
from olympe.messages import connectivity
from olympe.messages import controllerNetwork
from olympe.messages import common
from olympe.messages import devicemanager
from olympe.messages import drone_manager
from olympe.messages import developer
from olympe.messages import esim
from olympe.messages import logsync
from olympe.messages import microhard
from olympe.messages import mission
from olympe.messages import network
from olympe.messages import pointnfly
from olympe.messages import privacy
from olympe.messages import skyctrl

TIMEOUT_CONNECTION = 45.0  # in seconds
TIMEOUT_DISCONNECTION = 30.0  # in seconds
TIMEOUT_OPTIONAL_CMD = 3.0 # in seconds

# Wait backoff period before attempting to connect again.
# It doubles after every connection retry
BACKOFF_PERIOD = 2.0  # in seconds

NB_CONNECTION_RETRIES = 1  # just one connection attempt by default


class PilotingCommand:
    def __init__(self, time_function=None):
        self.set_default_piloting_command()
        if time_function:
            self.time_function = time_function
        else:
            self.time_function = time.time

    def update_piloting_command(self, roll, pitch, yaw, gaz, piloting_time):
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.gaz = gaz
        self.piloting_time = piloting_time
        self.initial_time = self.time_function()

    def set_default_piloting_command(self):
        self.roll = 0
        self.pitch = 0
        self.yaw = 0
        self.gaz = 0
        self.piloting_time = 0
        self.initial_time = 0


class ControllerBackendConnectionListener(ConnectionListener):
    def __init__(self, controller: "ControllerBase"):
        self._controller = controller

    def disconnected(self, _: Connection):
        self._controller._loop.run_later(self._controller._on_device_removed)


class ControllerBase(CommandInterfaceBase):
    DEVICE_TYPES: Optional[List[int]] = None

    def __init__(
        self,
        ip_addr,
        *,
        name=None,
        drone_type=0,
        proto_v_min=1,
        proto_v_max=3,
        is_skyctrl=None,
        video_buffer_queue_size=8,
        backend=BackendType.Net,
        time_function=None,
        discovery_port: int = _DISCOVERY_PORT,
    ):
        self._logger_scope = "controller"
        self._ip_addr_str = str(ip_addr)
        self._ip_addr = ip_addr.encode("utf-8")
        self._is_skyctrl = is_skyctrl
        self._time_function = time_function

        self._timeout = TIMEOUT_CONNECTION
        self._deadline : float

        self._connect_sync_event = None
        self._disconnect_sync_event = None

        self._piloting = False
        self._piloting_command = PilotingCommand(time_function=self._time_function)

        self._device = None
        self._device_name = None
        self._discovery = None

        self._backend_type = backend
        if backend is BackendType.Net:
            self._backend_class = CtrlBackendNet
            self._discovery_class = DiscoveryNet
            self._discovery_port = discovery_port
        elif backend is BackendType.MuxIp:
            self._backend_class = CtrlBackendMuxIp
            self._discovery_class = DiscoveryMux
        else:
            raise ValueError(f"Unknown backend type {backend}")
        super().__init__(
            name=name,
            drone_type=drone_type,
            proto_v_min=1,
            proto_v_max=3,
            device_addr=self._ip_addr,
            connection_listener=ControllerBackendConnectionListener(self),
        )

        if self._time_function is not None:
            self._scheduler.set_time_function(self._time_function)

        self._last_disconnection_time = None
        # Setup piloting commands timer
        self._piloting_timer = self._loop.create_timer(self._piloting_timer_cb)

        self._declare_callbacks()
        self._drone_manager_subscriber = self.subscribe(
            self._on_drone_manager_connection_state_changed,
            drone_manager.connection_state(_policy="wait"),
        )
        self._device_manager_subscriber = self.subscribe(
            self._on_device_manager_connection_state_changed,
            devicemanager.Event.State(_policy="wait"),
        )
        self._cameras_list_subscriber = self.subscribe(
            self._on_cameras_list,
            camera2.Event.CameraList(_policy="wait"),
        )
        self._radios_list_subscriber = self.subscribe(
            self._on_radios_list,
            connectivity.Event.RadioList(_policy="wait"),
        )
        self._logsync_event_subscriber = self.subscribe(
            self._on_logsync_identifier,
            logsync.Event.Identifier(_policy="wait"),
        )
        self._logsync_command_subscriber = self.subscribe(
            self._on_logsync_sync_request,
            logsync.Command.SyncRequest.as_event(_policy="wait"),
        )

        # Flag if the device is already connected to drone (or connected in backup link)
        # and prevents all states/all settings commands to be sent at each 'connected'
        # state change
        self._already_connected = False

        # Flag to monitor the backup link activeness. It is essential to determine if
        # the device manager (Skycontroller case only) is in backup link, because some
        # features must be deactivated where applicable
        self._is_backup_link_active = False

        self._loop.register_cleanup(self._cleanup)

    def _on_drone_manager_connection_state_changed(self, message_event, _):
        # Handle drone connection_state events
        if not self._is_skyctrl:
            return
        if (
            message_event._args["state"]
            == drone_manager_enums.connection_state.connected
        ):
            self._on_skyctrl_connection_changed(True)
        elif (
            message_event._args["state"]
            == drone_manager_enums.connection_state.disconnecting
        ):
            self._on_skyctrl_connection_changed(False)

    # Handle drone connection_state events
    def _on_device_manager_connection_state_changed(self, message_event, _):
        # Connection state changes may only be dictated by a SkyController
        assert self._is_skyctrl is True
        # If the connection state between the SkyController and the drone is 'connecting'
        # and the SkyController warns that SDK is ready, this means the drone is
        # "SDK-connected" to the SkyController and ready to receive commands from a
        # controller. It is then time to resend all states/all settings
        # if backup link mode is inactive (that is why it is necessary to retrieve its
        # value). If sdk_ready is False, then the SDK connection between the drone and
        # the MPP is not established yet and connection has not changed
        if "connecting" in message_event._args:
            backup_link_state = message_event._args["connecting"].get(
                "backup_link", backuplink_enums.LinkState.off
            )
            self._is_backup_link_active = (
                backup_link_state == backuplink_enums.LinkState.active
            )
            sdk_ready = message_event._args["connecting"].get("sdk_ready", False)
            if sdk_ready:
                self._on_skyctrl_connection_changed(True)
        # If the connection state is 'connected', resend all states/all settings if
        # backup link mode is inactive only. Otherwise, backup link activated mode is not
        # considered a real 'connected' state
        if "connected" in message_event._args:
            backup_link_state = message_event._args["connected"].get(
                "backup_link", backuplink_enums.LinkState.off
            )
            self._is_backup_link_active = (
                backup_link_state == backuplink_enums.LinkState.active
            )
            self._on_skyctrl_connection_changed(True)
        # If the connection state is 'disconneting', log that the state is
        # disconnecting and reset the flag self._already_connected
        elif "disconnecting" in message_event._args:
            self._on_skyctrl_connection_changed(False)

    def _on_cameras_list(self, message_event, _):
        # For each available camera of the drone, send a GetState command. Olympe has
        # to wait for the camera2.Event.CameraList expectation from the drone with a
        # subscriber before sending those commands with the appropriate camera id
        for camera in message_event.args["cameras"]:
            self._send_command_raw(
                camera2.Command.GetState,
                dict(camera_id=camera, include_default_capabilities=True),
            )

    def _on_radios_list(self, message_event, _):
        # For each available radio of the drone, send a GetState command. Olympe has
        # to wait for the connectivity.Event.RadioList expectation from the drone with a
        # subscriber before sending those commands with the appropriate radio id
        for radio in message_event.args["radios"]:
            self._send_command_raw(
                connectivity.Command.GetState,
                dict(
                    radio_id=radio,
                    include_default_capabilities=True,
                    supports_packed_channels=True,
                ),
            )

    def _on_logsync_identifier(self, message_event, _):
        self.logger.info(
            "Received a logsync message from a Node, whose charateristics are "
            f"boot_id:{message_event.args['boot_id']}, "
            f"model:{message_event.args['model']}, role:{message_event.args['role']}"
        )

    def _on_logsync_sync_request(self, message_event, _):
        self._send_command_raw(
            logsync.Event.Identifier.as_command().command_message,
            dict(
                boot_id="0",
                model=logsync_enums.Model.computer,
                role=logsync_enums.Role.controller,
            ),
        )

    def is_skyctrl(self):
        if self._is_skyctrl is None:
            return self._device_type in SKYCTRL_DEVICE_TYPE_LIST
        else:
            return self._is_skyctrl

    def _recv_message_type(self):
        return ArsdkMessageType.CMD

    def _create_backend(
        self,
        name,
        proto_v_min,
        proto_v_max,
        *,
        device_addr=None,
        connection_listener=None,
    ):
        self._backend = self._backend_class(
            name=name,
            proto_v_min=proto_v_min,
            proto_v_max=proto_v_max,
            device_addr=device_addr,
            connection_listener=connection_listener,
        )

    def _declare_callbacks(self):
        """
        Define all callbacks
        """
        super()._declare_callbacks()

        self._device_cbs_cfg = od.struct_arsdk_device_conn_cbs.bind(
            {
                "connecting": self._connecting_cb,
                "connected": self._connected_cb,
                "disconnected": self._disconnected_cb,
                "canceled": self._canceled_cb,
                "link_status": self._link_status_cb,
            }
        )

    def _on_skyctrl_connection_changed(self, connected: bool):
        # If the connection state change is 'disconnecting', notice that the drone is
        # not connected to the SkyController anymore and do not send commands
        if not connected:
            self.logger.info("Skycontroller is disconnected from the drone")
            self._already_connected = False
            return

        # If the connection is of type backup_link, consider the drone not connected and
        # do not send commands. Reset the flag, because it is not considered
        # a connected state
        if self._is_backup_link_active:
            self.logger.info("Skycontroller is in backup link mode")
            self._already_connected = False
            return

        # If the drone was already connected, do no resend commands
        if self._already_connected:
            return

        # At the end, the SkyController is properly connected to the drone (backup link
        # is thus not active)
        self.logger.info("Skycontroller is connected to drone")

        # Synchronize drone's clock through this SkyController (re)connection
        self._synchronize_clock(is_skyctrl=False)

        # If the connection to the SkyController is not already done yet,
        # request for all states/all settings. This way, they are requested only once -
        # on a successful 'no connected' to 'connected' state - and not everytime the
        # connection state changes
        all_states_settings_commands = [
            common.Common.AllStates,
            common.Settings.AllSettings,
        ]
        for all_states_settings_command in all_states_settings_commands:
            self._send_command_raw(all_states_settings_command, dict())
        # Update flag so that it won't be necessary to resend all these commands at the
        # next connection state change that won't be a disconnection
        self._already_connected = True

        # Enable airsdk mission support from the drone
        self._send_command_raw(mission.custom_msg_enable, dict())
        self._send_command_raw(developer.Command.GetState, dict())

        # Request for the GetState commands
        commands = [
            antiflicker.Command.GetState,
            microhard.Command.GetState,
            network.Command.GetState,
            pointnfly.Command.GetState,
            privacy.Command.GetState,
        ]
        if self._device_type in (
            od.ARSDK_DEVICE_TYPE_ANAFI_3_MIL,
            od.ARSDK_DEVICE_TYPE_ANAFI_3_GOV,
        ):
            commands.append(
                esim.Command.GetState
            )
        for command in commands:
            self._send_command_raw(command, dict(include_default_capabilities=True))
        self._send_command_raw(camera2.Command.ListCameras, dict())
        self._send_command_raw(connectivity.Command.ListRadios, dict())

    @callback_decorator()
    def _create_command_interface(
        self,
    ) -> Optional[PointerType[od.struct_arsdk_cmd_itf]]:
        """
        Create a command interface to send command to the device
        """

        cmd_itf = od.POINTER_T(od.struct_arsdk_cmd_itf)()

        if self._device is None:
            self.logger.error("Cannot create a command interface for 'None' device")
            return None
        res = od.arsdk_device_create_cmd_itf(
            self._device.arsdk_device, self._cmd_itf_cbs, ctypes.pointer(cmd_itf)
        )

        if res != 0:
            self.logger.error(f"Error while creating command interface: {res}")
            cmd_itf = None
        else:
            self.logger.info("Command interface has been created")

        return cmd_itf

    def _synchronize_clock(self, is_skyctrl: bool = False):
        date_time = datetime.datetime.now(get_localzone()).strftime("%Y%m%dT%H%M%S%z")
        if not is_skyctrl:
            current_date_time = common.Common.CurrentDateTime
        else:
            current_date_time = skyctrl.Common.CurrentDateTime
        res = self(current_date_time(datetime=date_time, _timeout=2))

        def _on_sync_done(res):
            if not res.success():
                if self._is_skyctrl:
                    if is_skyctrl:
                        msg = f"Time synchronization failed for {self._device_name}"
                    else:
                        msg = (
                            "Time synchronization failed for "
                            f"the device connected to {self._device_name}"
                        )
                else:
                    msg = f"Time synchronization failed for {self._device_name}"
                self.logger.warning(msg)
            else:
                if self._is_skyctrl:
                    if is_skyctrl:
                        msg = f"Synchronization of {self._device_name} at {date_time}"
                    else:
                        msg = (
                            "Synchronization of the device connected to "
                            f"{self._device_name} at {date_time}"
                        )
                else:
                    msg = f"Synchronization of {self._device_name} at {date_time}"
                self.logger.info(msg)

        res.add_done_callback(_on_sync_done)

    @callback_decorator()
    def _start_piloting_impl(self):
        delay = 100
        period = 25

        ok = self._loop.set_timer(self._piloting_timer, delay, period)

        if ok:
            self._piloting = True
            self.logger.info("Piloting interface has been correctly launched")
        else:
            self.logger.error("Unable to launch piloting interface")
        return self._piloting

    @callback_decorator()
    def _stop_piloting_impl(self):
        # Stop the drone movements
        self._piloting_command.set_default_piloting_command()
        time.sleep(0.1)

        ok = self._loop.clear_timer(self._piloting_timer)
        if ok:
            # Reset piloting state value to False
            self._piloting = False
            self.logger.info("Piloting interface stopped")
        else:
            self.logger.error("Unable to stop piloting interface")

    @callback_decorator()
    def _piloting_timer_cb(self, timer, _user_data):
        self.logger.debug(f"piloting timer callback: {timer}")
        if self.connected:
            self._send_piloting_command()

    def _send_piloting_command(self):
        # When piloting time is 0 => send default piloting commands
        if self._piloting_command.piloting_time:
            # Check if piloting time since last pcmd order has been reached
            diff_time = (
                self._piloting_command.time_function()
                - self._piloting_command.initial_time
            )
            if diff_time >= self._piloting_command.piloting_time:
                self._piloting_command.set_default_piloting_command()

        # Flag to activate movement on roll and pitch. 1 activate, 0 deactivate
        if self._piloting_command.roll or (self._piloting_command.pitch):
            activate_movement = 1
        else:
            activate_movement = 0

        self._send_command_impl(
            ardrone3.Piloting.PCMD,
            dict(
                flag=activate_movement,
                roll=self._piloting_command.roll,
                pitch=self._piloting_command.pitch,
                yaw=self._piloting_command.yaw,
                gaz=self._piloting_command.gaz,
                timestampAndSeqNum=0,
            ),
            quiet=True,
        )

    def start_piloting(self):
        """
        Start interface to send piloting commands

        :rtype: bool
        """
        if self._piloting:
            self.logger.debug("Piloting interface already started")
            return True

        f = self._loop.run_async(self._start_piloting_impl)

        try:
            ok = f.result_or_cancel(timeout=2)
        except (FutureTimeoutError, CancelledError):
            self.logger.error("Unable to launch piloting interface")
            return False
        if not ok:
            self.logger.error("Unable to launch piloting interface")
            return False

        self.logger.info("Piloting started")
        return True

    def stop_piloting(self):
        """
        Stop interface to send piloting commands

        :rtype: bool
        """
        # Check piloting interface is running
        if not self._piloting:
            self.logger.debug("Piloting interface already stopped")
            return True

        f = self._loop.run_async(self._stop_piloting_impl)

        try:
            ok = f.result_or_cancel(timeout=2)
        except (FutureTimeoutError, CancelledError):
            self.logger.error("Unable to stop piloting interface")
            return False
        if not ok:
            self.logger.error("Unable to stop piloting interface")
            return False

        self.logger.info("Piloting stopped")
        return True

    def piloting(self, roll, pitch, yaw, gaz, piloting_time):
        """
        Send manual piloting commands to the drone.
        This function is a non-blocking.

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
        :rtype: bool

        """
        if not self.start_piloting():
            return False
        self._piloting_command.update_piloting_command(
            roll, pitch, yaw, gaz, piloting_time
        )
        return True

    def piloting_pcmd(self, roll, pitch, yaw, gaz, piloting_time):
        warn(
            "ControllerBase.piloting_pcmd is deprecated, "
            "please use ControllerBase.piloting instead",
            DeprecationWarning,
        )
        return self.piloting(roll, pitch, yaw, gaz, piloting_time)

    async def _async_discover_device(
        self, deadline: float
    ) -> Tuple[Optional[DeviceInfo], Optional[Discovery]]:
        # Try to identify the device type we are attempting to connect to...
        await self._backend.ready()
        timeout = (deadline - time.time()) / 2

        if self._backend_type is BackendType.Net:
            discovery = self._discovery_class(
                self._backend,
                ip_addr=self._ip_addr,
                discovery_port=self._discovery_port,
                devices_types=self.DEVICE_TYPES,
                timeout=timeout,
            )
        elif self._backend_type is BackendType.MuxIp:
            discovery = self._discovery_class(
                self._backend,
                ip_addr=self._ip_addr,
                devices_types=self.DEVICE_TYPES,
                timeout=timeout,
            )

        device = await discovery.async_get_device()
        if device is not None:
            return device, discovery
        assert await discovery._astop()
        if self._backend_type is BackendType.Net:
            self.logger.warning(f"Net discovery failed for {self._ip_addr}")
            self.logger.warning(f"Trying 'NetRaw' discovery for {self._ip_addr} ...")
            timeout = (deadline - time.time()) / 4
            discovery = DiscoveryNetRaw(
                self._backend, ip_addr=self._ip_addr, timeout=timeout
            )
            device = await discovery.async_get_device()
            if device is None:
                assert await discovery._astop()
        return device, discovery

    async def _async_get_device(self, deadline: float) -> bool:
        if self._device is not None:
            return True
        device, discovery = await self._async_discover_device(deadline)

        if device is None:
            self.logger.info(f"Cannot discover the device '{self._ip_addr}'")
            return False

        # Save device related info
        self._device = device
        self._discovery = discovery
        self._device_type = self._device.type
        if self._is_skyctrl is None:
            if self._device_type in SKYCTRL_DEVICE_TYPE_LIST:
                self._is_skyctrl = True
            else:
                self._is_skyctrl = False
        return True

    @callback_decorator()
    def _connecting_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection initiation.
        """
        device_name = od.string_cast(arsdk_device_info.contents.name)
        self.logger.info(f"Connecting to device '{device_name}'")
        self.connection_state = ConnectionState.Connecting

    @callback_decorator()
    def _connected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify connection completion.
        """
        self._loop.run_async(self._aconnected_cb, arsdk_device_info)

    async def _aconnected_cb(self, arsdk_device_info):
        device_name = od.string_cast(arsdk_device_info.contents.name)
        self.set_device_name(device_name)
        self.logger.info(f"Connected to device '{device_name}'")

        # Retrieve the device info with its json contents
        json_info = od.string_cast(arsdk_device_info.contents.json)
        try:
            json_info = json.loads(json_info)
            self.logger.info(pprint.pformat(json_info))
        except ValueError:
            self.logger.error(
                f"Json containing '{device_name}' info cannot be parsed: {json_info}"
            )

        # Create the arsdk command interface
        if self._cmd_itf is None:
            self.logger.debug(
                f"Creating a command interface for device '{device_name}' '{self._ip_addr_str}' ..."
            )
            self._cmd_itf = self._create_command_interface()
            if self._cmd_itf is None:
                self.logger.error(
                    f"Command interface for device '{device_name}' "
                    f"'{self._ip_addr_str}' could not be created"
                )
                self.connection_state = ConnectionState.Error
                Event.release_event(self._connect_sync_event)
                return

        if not await self._on_connected():
            self.connection_state = ConnectionState.Error
            Event.release_event(self._connect_sync_event)
            return

        self.connection_state = ConnectionState.Connected
        Event.release_event(self._connect_sync_event)

        # Process the ConnectedEvent
        event = ConnectedEvent()
        self.logger.info(str(event))
        self._scheduler.process_event(event)

    @callback_decorator()
    def _disconnected_cb(self, _arsdk_device, arsdk_device_info, _user_data):
        """
        Notify disconnection.
        """
        device_name = od.string_cast(arsdk_device_info.contents.name)
        self.logger.info(f"Disconnected from device '{device_name}'")

        self.connection_state = ConnectionState.Disconnected
        Event.release_event(self._disconnect_sync_event)

        # Process the Disconnected Event
        event = DisconnectedEvent()
        self.logger.info(str(event))
        self._scheduler.process_event(event)

        self._loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _canceled_cb(self, _arsdk_device, arsdk_device_info, reason, _user_data):
        """
        Notify connection cancellation. Either because 'disconnect' was
        called before 'connected' callback or remote aborted/rejected the
        request.
        """
        device_name = od.string_cast(arsdk_device_info.contents.name)
        reason = od.string_cast(od.arsdk_conn_cancel_reason_str(reason))
        self.logger.info(
            f"Connection to device '{device_name}' has been canceled for reason '{reason}'"
        )
        # If this callback is called but the controller is already disconnecting, there
        # is no need to disconnect once more
        if self.connection_state is not ConnectionState.Disconnecting:
            self.connection_state = ConnectionState.Error
            Event.release_event(self._connect_sync_event)
            self._loop.run_later(self._adisconnect)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_device, _arsdk_device_info, status, _user_data):
        """
        Notify link status. At connection completion, it is assumed to be
        initially OK. If called with KO, user is responsible to take action.
        It can either wait for link to become OK again or disconnect
        immediately. In this case, call arsdk_device_disconnect and the
        'disconnected' callback will be called.
        """
        self.logger.info(
            f"Arsdk link with device '{_arsdk_device}' has status: '{status}'"
        )
        # If link has been lost, we must start disconnection procedure
        if status == od.ARSDK_LINK_STATUS_KO and self.connected:
            # The device has been disconnected
            self.logger.error(
                f"Arsdk link with device '{_arsdk_device}' has status: 'KO'. Disconnecting"
            )
            self.connection_state = ConnectionState.Error
            Event.release_event(self._connect_sync_event)
            self._loop.run_later(self._adisconnect)

    async def _send_states_settings_cmd(self, command):
        res = await self(command)
        if not res.success():
            self.logger.error(
                f"Cannot get command '{command}' for device '{self._ip_addr}'"
            )
            return False
        return True

    async def _on_connected(self) -> bool:
        if not self._ip_addr_str.startswith("10.202") and (
            not self._ip_addr_str.startswith("127.0")
        ):
            # When connected to a device, whether it is a SkyController or a Drone,
            # synchronize clock
            self._synchronize_clock(is_skyctrl=self._is_skyctrl)

        # We're connected to the device, get all device states and settings
        commands = []
        if not self._is_skyctrl:
            self._already_connected = True

            all_states_settings_commands = [
                common.Common.AllStates(),
                common.Settings.AllSettings(),
            ]

            if self._device_type not in (
                od.ARSDK_DEVICE_TYPE_ANAFI4K,
                od.ARSDK_DEVICE_TYPE_ANAFI_THERMAL,
                od.ARSDK_DEVICE_TYPE_ANAFI_UA,
                od.ARSDK_DEVICE_TYPE_ANAFI_USA,
            ):
                commands = [
                    antiflicker.Command.GetState(include_default_capabilities=True),
                    developer.Command.GetState(),
                    network.Command.GetState(include_default_capabilities=True),
                    pointnfly.Command.GetState(include_default_capabilities=True),
                    privacy.Command.GetState(include_default_capabilities=True),
                    mission.custom_msg_enable(),
                    # Request to list all available cameras to the drone. Subscriber
                    # self._cameras_list_subscriber will handle the reception of
                    # camera2.Event.CameraList once the drone is ready to send it to
                    # Olympe, in response to camera2.Command.ListCameras
                    camera2.Command.ListCameras(),
                    # Request to list all available radios to the drone. Subscriber
                    # self._radios_list_subscriber will handle the reception of
                    # connectivity.Event.RadioList once the drone is ready to send it to
                    # Olympe, in response to connectivity.Command.ListRadios
                    connectivity.Command.ListRadios(),
                ]
                if self._device_type in (
                    od.ARSDK_DEVICE_TYPE_ANAFI_3_MIL,
                    od.ARSDK_DEVICE_TYPE_ANAFI_3_GOV,
                ):
                    commands.append(
                        esim.Command.GetState(include_default_capabilities=True),
                    )
        else:
            all_states_settings_commands = [
                skyctrl.Common.AllStates(),
                skyctrl.Settings.AllSettings(),
            ]

            if self._device_type not in [
                od.ARSDK_DEVICE_TYPE_SKYCTRL,
                od.ARSDK_DEVICE_TYPE_SKYCTRL_NG,
                od.ARSDK_DEVICE_TYPE_SKYCTRL_2,
                od.ARSDK_DEVICE_TYPE_SKYCTRL_2P,
                od.ARSDK_DEVICE_TYPE_SKYCTRL_3,
                od.ARSDK_DEVICE_TYPE_SKYCTRL_UA,
            ]:
                # In case of a SkyController, notify the SkyController that the
                # olympe controller is able or not to handle a all settings request
                # from the SKyController to send to the drone, or if the SkyController
                # can't wait for the controller and request those settings itself
                commands = [
                    controllerNetwork.Command.GetState(),
                    devicemanager.Command.GetState(supports_fast_connection=True),
                ]

        # Get device specific states and settings
        for states_settings_command in all_states_settings_commands:
            try:
                res = await self._loop.await_for(
                    self._timeout,
                    self._send_states_settings_cmd,
                    states_settings_command,
                )
            except FutureTimeoutError:
                return False
            if not res:
                return False

        # Get specific optional states
        for command in commands:
            remaining_time = self._deadline-time.time()
            if remaining_time <= 0.0:
                # There is no time wait for optional states
                self._loop.run_async(self._send_states_settings_cmd, command)
                continue
            try:
                res = await self._loop.await_for(
                    min(remaining_time, TIMEOUT_OPTIONAL_CMD),
                    self._send_states_settings_cmd, command
                )
            except FutureTimeoutError:
                # Protobuf Command.GetState are optional
                continue
            if not res:
                return False

        try:
            await self._send_command_raw(logsync.Command.SyncRequest, dict())
        except FutureTimeoutError as e:
            self.logger.error(f"Error while sending SyncRequest command ({e})")
            # Not mandatory so no need to raise an error. An error log is enough

        return True

    def _connect_impl(self):
        self._connect_sync_event = Event(self._loop)

        req = bytes("{}", "utf-8")
        device_id = b""
        device_conn_cfg = od.struct_arsdk_device_conn_cfg(
            ctypes.create_string_buffer(b"olympe"),
            ctypes.create_string_buffer(b"desktop"),
            ctypes.create_string_buffer(bytes(device_id)),
            ctypes.create_string_buffer(req),
        )
        if self._device is None:
            self.logger.error("Cannot connect to a 'None' device")
            self.connection_state = ConnectionState.Error
            Event.release_event(self._connect_sync_event)
            return
        res = od.arsdk_device_connect(
            self._device.arsdk_device,
            device_conn_cfg,
            self._device_cbs_cfg,
            self._loop.pomp_loop,
        )
        if res != 0:
            self.logger.error(
                f"Connection to device '{self._ip_addr_str}' has failed ({res})"
            )
            self.connection_state = ConnectionState.Error
            Event.release_event(self._connect_sync_event)
        return

    async def _aconnect(self, timeout: float, retry: int) -> bool:
        if self.connection_state is ConnectionState.Error:
            self.logger.error(
                f"Cannot connect to device '{self._ip_addr_str}' as is because the "
                "device is in an 'Error' state"
            )
            return False

        if self.connected:
            self.logger.info(f"Already connected to device '{self._ip_addr_str}'")
            return True

        if self.connecting:
            self.logger.warning(
                f"A connection process to device '{self._ip_addr_str}' is already "
                "ongoing. Aborting this one"
            )
            return False

        if self.disconnecting:
            self.logger.warning(
                f"A disconnection process to device '{self._ip_addr_str}' is already "
                "ongoing. Aborting this connection"
            )
            return False

        self._timeout = timeout
        # To be doubled after discovery failed, to get more breathing room before the
        # next connection attempt
        backoff = BACKOFF_PERIOD
        self._deadline = time.time() + timeout

        for i in range(retry):
            if time.time() < self._deadline:
                if not await self._async_get_device(self._deadline):
                    self.logger.warning(
                        f"Discovery for device '{self._ip_addr_str}' has failed. Retrying"
                    )

                    # Sleep before attempting another discovery because maybe the drone
                    # was not ready, or any other possible reason. There's still time
                    # left to wait for it
                    await self._loop.asleep(backoff)
                    backoff *= 2.0
                    continue

                # If discovery has succeeded
                self.logger.debug(
                    f"Connection to device '{self._ip_addr_str}' is ongoing..."
                )
                self._connect_impl()
                assert self._connect_sync_event is not None
                await self._connect_sync_event.wait()

                # 'self._connect_sync_event' has been released, whether it means good or
                # bad news. In any case, the boolean value of self.connected will help
                # knowing if we're connected or not
                break
            else:
                # If deadline has already been overpassed
                break

        if not self.connected:
            self._on_device_removed()

        return self.connected

    def connect(
        self,
        *,
        timeout: float = TIMEOUT_CONNECTION,
        retry: int = NB_CONNECTION_RETRIES,
        later: bool = False,
    ) -> bool:
        """
        Establish the connection between the device and the controller.

        :param timeout: the global connection timeout in seconds  (including the
         retried connection attempt duration when `retry` > 1) (default to `TIMEOUT_CONNECTION`)
        :rtype: float
        :param retry: the number of connection attempts (default to `1`)
        :rtype: int
        :param later: the priority in the async event loop, if true, delay the connection
         process
        :rtype: bool: True if the device is correctly connected, False otherwise.
        """
        # Prevent a third-party from reducing the timeout value from its default value
        if timeout < TIMEOUT_CONNECTION:
            timeout = TIMEOUT_CONNECTION
            self.logger.warning(
                f"Connection timeout value cannot be lower than {TIMEOUT_CONNECTION}s. Timeout value set to default: {timeout}"
            )

        if later:
            connected_future = self._loop.run_later(self._aconnect, timeout, retry)
        else:
            connected_future = self._loop.run_async(self._aconnect, timeout, retry)

        try:
            _ = connected_future.result_or_cancel(timeout=timeout)
            # If the controller is in 'Error' state but the device is still defined,
            # a proper disconnection can be done
            if (
                self.connection_state == ConnectionState.Error
                and self._device is not None
            ):
                raise CancelledError
            # If no device has been defined, the controller instances can be redefined
            if self.connection_state == ConnectionState.Error and self._device is None:
                self._on_device_removed()
        except (FutureTimeoutError, CancelledError) as exception:
            if isinstance(exception, FutureTimeoutError):
                self.connection_state = ConnectionState.Timeout
            self.logger.error(
                f"Connection to device '{self._ip_addr_str}' has failed. Disconnecting..."
            )
            self.disconnect()
            return False

        self.logger.info(
            f"Connection to device '{self._ip_addr_str}' has "
            f"{'succeeded' if self.connected else 'failed'}"
        )
        return self.connected

    def _disconnect_impl(self):
        self._disconnect_sync_event = Event(self._loop)
        if self._device is None:
            self.logger.warning("There is no device to disconnect from")
            self.connection_state = ConnectionState.Error
            Event.release_event(self._disconnect_sync_event)
            return False
        res = od.arsdk_device_disconnect(self._device.arsdk_device)
        if res != 0:
            self.logger.error(
                f"Disconnecting from device '{self._ip_addr}' has failed ({res})"
            )
            self.connection_state = ConnectionState.Error
            Event.release_event(self._disconnect_sync_event)
        return

    async def _adisconnect(self) -> bool:
        if self.disconnected or self.created:
            self.logger.info("Already disconnected")
            return True

        if self.disconnecting:
            self.logger.warning(
                f"A disconnection process to device '{self._ip_addr_str}' is already "
                "ongoing. Aborting this one"
            )
            return False

        if self.connecting:
            self.logger.error("Cannot disconnect while a connection is in progress")
            return False

        self.connection_state = ConnectionState.Disconnecting
        self._disconnect_impl()
        assert self._disconnect_sync_event is not None
        await self._disconnect_sync_event.wait()

        return self.disconnected or self.created

    def disconnect(self, *, timeout: float = TIMEOUT_DISCONNECTION) -> bool:
        """
        Disconnect current device (if any)
        Block until it is done or abandoned

        :rtype: bool: True if device is correctly disconnected
            or in created state, False otherwise.
        """
        # Prevent a third-party from reducing the timeout value from its default value
        if timeout < TIMEOUT_DISCONNECTION:
            timeout = TIMEOUT_DISCONNECTION
            self.logger.warning(
                f"Disconnection timeout value cannot be lower than {TIMEOUT_DISCONNECTION}s. Timeout value set to default: {timeout}"
            )

        disconnect_future = self._loop.run_async(self._adisconnect)
        try:
            _ = disconnect_future.result_or_cancel(timeout=timeout)

        except (FutureTimeoutError, CancelledError):
            self.logger.error(f"Disconnection from device '{self._ip_addr}' has failed")
            self.connection_state = ConnectionState.Error
            return False

        self.logger.info(
            f"Disconnection from device '{self._ip_addr}' has "
            f"{'succeeded' if self.disconnected or self.created else 'failed'}"
        )
        return self.disconnected or self.created

    def _on_device_removed(self):
        if self._discovery:
            self._discovery.stop()
        if self._piloting:
            self._stop_piloting_impl()
        self._last_disconnection_time = time.time()
        self._piloting_command = PilotingCommand(time_function=self._time_function)
        super()._on_device_removed()

    def _reset_instance(self):
        self._piloting = False
        self._piloting_command = PilotingCommand(time_function=self._time_function)
        self._device = None
        self._device_name = None
        self._discovery = None
        self._connect_sync_event = None
        self._disconnect_sync_event = None
        super()._reset_instance()

    def _cleanup(self):
        self._drone_manager_subscriber.unsubscribe()
        self._device_manager_subscriber.unsubscribe()
        self._cameras_list_subscriber.unsubscribe()
        self._radios_list_subscriber.unsubscribe()
        self._logsync_command_subscriber.unsubscribe()
        self._logsync_event_subscriber.unsubscribe()
        super()._cleanup()
