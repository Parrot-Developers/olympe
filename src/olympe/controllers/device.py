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
import json

from concurrent.futures import CancelledError

import olympe_deps as od

from olympe.concurrent import TimeoutError as FutureTimeoutError
from olympe.concurrent.sync import Event
from olympe.connection import PeerInfo
from olympe.connection.backend import BackendType, DeviceBackendNet, DeviceBackendMuxIp
from olympe.connection.networking import Connection, ConnectionListener
from olympe.messaging.arsdk_messages import ArsdkMessageType
from olympe.utils import callback_decorator
from .cmd_itf import CommandInterfaceBase, ConnectionState

TIMEOUT_DISCONNECTION = 30.0  # in seconds

class DeviceBackendConnectionListener(ConnectionListener):
    def __init__(self, device: "DeviceBase"):
        self._device = device

    def disconnected(self, _: Connection):
        self._device._loop.run_later(self._device._on_device_removed)


class DeviceBase(CommandInterfaceBase):
    def __init__(
        self,
        name=None,
        dcport=44444,
        drone_type=0,
        proto_v_min=1,
        proto_v_max=3,
        backend=BackendType.Net,
    ):
        self._logger_scope = "device"
        self._dcport = dcport
        self._peer = None
        self._listening = False
        self._drone_type = drone_type or od.ARSDK_DEVICE_TYPE_ANAFI_2

        # As for in controller, this event will be created before arsdk_peer_disconnect,
        # and released when _disconnected_cb is called (ie when the disconnection is
        # successful)
        self._disconnect_sync_event=None

        self._backend_type = backend
        if backend is BackendType.Net:
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_net)()
            self._backend_class = DeviceBackendNet
        elif backend is BackendType.MuxIp:
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_mux)()
            self._backend_class = DeviceBackendMuxIp
        super().__init__(
            name=name,
            drone_type=drone_type,
            proto_v_min=1,
            proto_v_max=3,
            connection_listener=DeviceBackendConnectionListener(self),
        )

        self._loop.register_cleanup(self._cleanup)

    def _recv_message_type(self):
        return ArsdkMessageType.EVT

    def _create_backend(self, name, proto_v_min, proto_v_max, connection_listener):
        self._backend = self._backend_class(
            name=name,
            proto_v_min=proto_v_min,
            proto_v_max=proto_v_max,
            connection_listener=connection_listener,
        )

    def _declare_callbacks(self):
        super()._declare_callbacks()
        self._listen_cbs_userdata = None
        self._listen_cbs = od.struct_arsdk_backend_listen_cbs.bind(
            {
                "userdata": ctypes.cast(
                    ctypes.pointer(ctypes.py_object(self._listen_cbs_userdata)),
                    ctypes.c_void_p,
                ),
                "conn_req": self._conn_req_cb,
            }
        )

        self._publisher_cfg = od.struct_arsdk_publisher_cfg.bind(
            {
                "name": od.char_pointer_cast("olympe.device"),
                "type": self._drone_type,
                "id": od.char_pointer_cast("12345678"),
            }
        )
        if self._backend_type is BackendType.Net:
            self._publisher_net_cfg = od.struct_arsdk_publisher_net_cfg.bind(
                {
                    "base": self._publisher_cfg,
                    "port": self._dcport,
                }
            )

        self._peer_conn_cfg = od.struct_arsdk_peer_conn_cfg.bind(
            {
                "json": od.char_pointer_cast(
                    json.dumps(
                        {
                            "arstream_fragment_size": 65000,
                            "arstream_fragment_maximum_number": 4,
                            "c2d_update_port": 51,
                            "c2d_user_port": 21,
                        }
                    )
                ),
            }
        )
        self._peer_conn_cbs = od.struct_arsdk_peer_conn_cbs.bind(
            {
                "connected": self._connected_cb,
                "disconnected": self._disconnected_cb,
                "canceled": self._canceled_cb,
                "link_status": self._link_status_cb,
            }
        )

    @callback_decorator()
    def _conn_req_cb(self, peer, info, userdata):
        # Only one peer at a time */
        if self._peer:
            res = od.arsdk_peer_reject(peer)
            if res < 0:
                self.logger.error(f"arsdk_peer_reject: {res}")
            return

        # Save peer
        self._peer = PeerInfo.from_arsdk_peer(peer)

        # Accept connection
        res = od.arsdk_peer_accept(
            peer,
            ctypes.byref(self._peer_conn_cfg),
            ctypes.byref(self._peer_conn_cbs),
            self._loop.pomp_loop,
        )
        if res < 0:
            self.logger.error(f"arsdk_peer_accept: {res}")

    @callback_decorator()
    def _create_command_interface(self, peer):
        """
        Create a command interface to send command to the device
        """

        cmd_itf = od.POINTER_T(od.struct_arsdk_cmd_itf)()

        res = od.arsdk_peer_create_cmd_itf(
            peer, self._cmd_itf_cbs, ctypes.pointer(cmd_itf)
        )

        if res != 0:
            self.logger.error(f"Error while creating command interface: {res}")
            cmd_itf = None
        else:
            self.logger.info("Command interface has been created")

        return cmd_itf

    @callback_decorator()
    def _connected_cb(self, arsdk_peer, arsdk_peer_info, _user_data):
        peer_name = od.string_cast(arsdk_peer_info.contents.ctrl_name)
        self.logger.info(f"Connected to peer: {peer_name}")

        if self._cmd_itf is None:
            self.logger.debug(
                f"Creating a command interface for device {peer_name} ..."
            )
            self._cmd_itf = self._create_command_interface(arsdk_peer)
            if self._cmd_itf is None:
                self.logger.error(
                    f"Command interface for device  {peer_name}could not be created"
                )
                self.connection_state = ConnectionState.Error
                return

        self.connection_state = ConnectionState.Connected

    @callback_decorator()
    def _disconnected_cb(self, _arsdk_peer, arsdk_peer_info, _user_data):
        peer_name = od.string_cast(arsdk_peer_info.contents.ctrl_name)
        self.logger.info(f"Disconnected from peer '{peer_name}'")

        self.connection_state = ConnectionState.Disconnected
        # Disconnection is successful, release event to unblock _adisconnect that still
        # awaits
        Event.release_event(self._disconnect_sync_event)

        self._loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _canceled_cb(self, _arsdk_peer, arsdk_peer_info, reason, _user_data):
        peer_name = od.string_cast(arsdk_peer_info.contents.ctrl_name)
        reason = od.string_cast(od.arsdk_conn_cancel_reason_str(reason))
        self.logger.info(
            f"Connection to peer '{peer_name}' has been canceled "
            f"for reason '{reason}'"
        )

        # If this callback is called but the device is already disconnecting, there
        # is no need to disconnect once more
        if self.connection_state is not ConnectionState.Disconnecting:
            self.connection_state = ConnectionState.Error
            self._loop.run_later(self._adisconnect)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_peer, arsdk_peer_info, status, _user_data):
        self.logger.info(
            f"Arsdk link with device '{_arsdk_peer}' has status: '{status}'"
        )
        # If link has been lost, we must start disconnection procedure
        if status == od.ARSDK_LINK_STATUS_KO and self.connected:
            # The device has been disconnected
            self.logger.error(
                f"Arsdk link with device '{_arsdk_peer}' has status: 'KO'. Disconnecting"
            )
            self.connection_state = ConnectionState.Error
            self._loop.run_later(self._adisconnect)

    def listen(self) -> bool:
        return self._loop.run_async(self.alisten)

    async def alisten(self):
        if self._listening:
            self.logger.info("Already listening")
            return True

        if self._backend_type is BackendType.Net:
            return await self._listen_net()
        else:
            return await self._listen_mux()

    async def _listen_net(self) -> bool:
        res = od.arsdk_publisher_net_new(
            self._backend._info.backend,
            self._loop.pomp_loop,
            ctypes.c_char_p(),
            ctypes.byref(self._publisher),
        )
        if res < 0:
            self.logger.error(f"arsdk_publisher_net_new: {res}")
            return False

        res = od.arsdk_backend_net_start_listen(
            self._backend._info.backend, ctypes.byref(self._listen_cbs), self._dcport
        )
        if res < 0:
            od.arsdk_publisher_net_destroy(self._publisher)
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_net)()
            self.logger.error(f"arsdk_backend_net_start_listen: {res}")
            return False

        # start net publisher
        res = od.arsdk_publisher_net_start(
            self._publisher, ctypes.byref(self._publisher_net_cfg)
        )
        if res < 0:
            od.arsdk_backend_net_stop_listen(self._backend._info.backend)
            od.arsdk_publisher_net_destroy(self._publisher)
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_net)()
            self.logger.error(f"arsdk_publisher_net_start: {res}")
            return False
        self._listening = True
        return True

    async def _listen_mux(self) -> bool:
        await self._backend.ready()
        res = od.arsdk_publisher_mux_new(
            self._backend._info.backend,
            self._backend._info.mux_ctx,
            ctypes.byref(self._publisher),
        )
        if res < 0:
            self.logger.error(f"arsdk_publisher_mux_new: {res}")
            return False

        res = od.arsdk_backend_mux_start_listen(
            self._backend._info.backend, ctypes.byref(self._listen_cbs)
        )
        if res < 0:
            od.arsdk_publisher_mux_destroy(self._publisher)
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_mux)()
            self.logger.error(f"arsdk_backend_mux_start_listen: {res}")
            return False

        # start mux publisher
        res = od.arsdk_publisher_mux_start(
            self._publisher, ctypes.byref(self._publisher_cfg)
        )
        if res < 0:
            od.arsdk_backend_mux_stop_listen(self._backend._info.backend)
            od.arsdk_publisher_mux_destroy(self._publisher)
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_mux)()
            self.logger.error(f"arsdk_publisher_mux_start: {res}")
            return False
        self._listening = True
        return True

    def stop(self) -> bool:
        ret = True
        if not self._listening:
            self.logger.info("Not currently listening")
            return True
        if self._backend_type is BackendType.Net:
            if self._publisher:
                res = od.arsdk_publisher_net_stop(self._publisher)
                if res < 0:
                    self.logger.error(f"arsdk_publisher_net_stop: {res}")
                    ret = False
                res = od.arsdk_publisher_net_destroy(self._publisher)
                if res < 0:
                    self.logger.error(f"arsdk_publisher_net_destroy: {res}")
                    ret = False
                self._publisher = od.POINTER_T(od.struct_arsdk_publisher_net)()
                res = od.arsdk_backend_net_stop_listen(self._backend._info.backend)
                if res < 0:
                    self.logger.error(f"arsdk_backend_net_stop_listen: {res}")
                    ret = False
        else:
            if self._publisher:
                res = od.arsdk_publisher_mux_stop(self._publisher)
                if res < 0:
                    self.logger.error(f"arsdk_publisher_mux_stop: {res}")
                    ret = False
                res = od.arsdk_publisher_mux_destroy(self._publisher)
                if res < 0:
                    self.logger.error(f"arsdk_publisher_mux_destroy: {res}")
                    ret = False
                self._publisher = od.POINTER_T(od.struct_arsdk_publisher_mux)()
                res = od.arsdk_backend_mux_stop_listen(self._backend._info.backend)
                if res < 0:
                    self.logger.error(f"arsdk_backend_mux_stop_listen: {res}")
                    ret = False
        self._listening = False
        return ret

    def _on_device_removed(self):
        for message in self.messages.values():
            message._reset_state()
        super()._on_device_removed()


    async def _adisconnect(self)->bool:
        if self.disconnected or self.created:
            self.logger.info("Already disconnected")
            return True

        self.connection_state = ConnectionState.Disconnecting
        # Effective arsdk disconnection
        self._disconnect_impl()

        assert self._disconnect_sync_event is not None
        # wait for _disconnected_cb to be called and tag the success of the disconnection
        await self._disconnect_sync_event.wait()

        return self.disconnected or self.created

    def disconnect(self)->bool:
        disconnect_future = self._loop.run_async(self._adisconnect)
        try:
            _ = disconnect_future.result_or_cancel(timeout=TIMEOUT_DISCONNECTION)

        except (FutureTimeoutError, CancelledError):
            self.logger.error(f"Disconnection from peer '{self._peer}' has failed")
            self.connection_state = ConnectionState.Error
            return False

        return self.disconnected or self.created


    @callback_decorator()
    def _disconnect_impl(self) -> bool:
        # Create the disconnection event before the disconnection request, it will be
        # released in _disconnected_cb
        self._disconnect_sync_event=Event(self._loop)

        if self._peer is None:
            self.logger.warning("There is no device to disconnect from")
            # Release event asap if there's no peer
            Event.release_event(self._disconnect_sync_event)
            return False

        res = od.arsdk_peer_disconnect(self._peer.arsdk_peer)
        if res < 0:
            self.logger.error(
                f"Disconnecting from device '{self._peer}' has failed ({res})"
            )
            self.connection_state = ConnectionState.Error
            # Release event to not block the rest, even if arsdk fails disconnecting ;
            # because _disconnected_cb will never be called anyway
            Event.release_event(self._disconnect_sync_event)

        self._peer = None
        return True

    def _reset_instance(self):
        super()._reset_instance()

    def _cleanup(self):
        super()._cleanup()

    def destroy(self):
        """
        explicit destructor
        """
        # If a drone is connected, handles its disconnection properly before freeing
        # anything, or stop() would destroy C mux while the drone still has access to it
        # -> segfault
        if self.connected or self.connecting:
            self.disconnect()

        # peer is now disconnected, C memory can be freed alongside arsdk publisher
        self.stop()

        self._loop.run_async(self._backend._destroy).result_or_cancel(timeout=5.0)

        super().destroy()
