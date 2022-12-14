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
import olympe_deps as od

from . import messages, PeerInfo
from .backend import BackendType, DeviceBackendNet, DeviceBackendMuxIp
from .cmd_itf import CommandInterfaceBase, DisconnectedEvent
from olympe.utils import callback_decorator


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
        self._backend_type = backend
        if backend is BackendType.Net:
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_net)()
            self._backend_class = DeviceBackendNet
        elif backend is BackendType.MuxIp:
            self._publisher = od.POINTER_T(od.struct_arsdk_publisher_mux)()
            self._backend_class = DeviceBackendMuxIp
        super().__init__(name=name, drone_type=drone_type, proto_v_min=1, proto_v_max=3)

    def _recv_message_type(self):
        return messages.ArsdkMessageType.EVT

    def _create_backend(self, name, proto_v_min, proto_v_max):
        self._backend = self._backend_class(
            name=name, proto_v_min=proto_v_min, proto_v_max=proto_v_max
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
                "type": od.ARSDK_DEVICE_TYPE_ANAFI_2,
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
            self._thread_loop.pomp_loop,
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
        self._cmd_itf = self._create_command_interface(arsdk_peer)
        if self._cmd_itf is None:
            msg = f"Unable to create command interface: {peer_name}"
            self.logger.error(msg)
            self._thread_loop.run_later(self._disconnection_impl)
        self.connected = True

        if self._connect_future is not None:
            self._connect_future.set_result(True)

    @callback_decorator()
    def _disconnected_cb(self, _arsdk_peer, arsdk_peer_info, _user_data):
        peer_name = od.string_cast(arsdk_peer_info.contents.ctrl_name)
        self.logger.info(f"Disconnected from peer: {peer_name}")
        self.connected = False

        if self._disconnect_future is not None:
            self._disconnect_future.set_result(True)
        self._thread_loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _canceled_cb(self, _arsdk_peer, arsdk_peer_info, reason, _user_data):
        reason = od.string_cast(od.arsdk_conn_cancel_reason_str(reason))
        peer_name = od.string_cast(arsdk_peer_info.contents.ctrl_name)
        self.logger.info(
            f"Connection to peer: {peer_name} has been canceled "
            f"for reason: {reason}"
        )
        if self._connect_future is not None:
            self._connect_future.set_result(False)
        self._thread_loop.run_later(self._on_device_removed)

    @callback_decorator()
    def _link_status_cb(self, _arsdk_peer, arsdk_peer_info, status, _user_data):
        self.logger.info(f"Link status: {status}")
        # If link has been lost, we must start disconnection procedure
        if status == od.ARSDK_LINK_STATUS_KO:
            # the device has been disconnected
            self.connected = False
            self._thread_loop.run_later(self._on_device_removed)

    def listen(self):
        return self._thread_loop.run_async(self.alisten)

    async def alisten(self):
        if self._listening:
            self.logger.info("Already listening")
            return True

        if self._backend_type is BackendType.Net:
            return await self._listen_net()
        else:
            return await self._listen_mux()

    async def _listen_net(self):
        res = od.arsdk_publisher_net_new(
            self._backend._info.backend,
            self._thread_loop.pomp_loop,
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

    async def _listen_mux(self):
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

    def stop(self):
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
        self.connected = False
        self._listening = False
        return ret

    def _on_device_removed(self):
        if not self._disconnection_impl():
            return
        for message in self.messages.values():
            message._reset_state()
        event = DisconnectedEvent()
        self.logger.info(str(event))
        self._scheduler.process_event(event)
        self.connected = False

    @callback_decorator()
    def _disconnection_impl(self):
        if not self._peer:
            return False
        peer = self._peer
        self._peer = None
        res = od.arsdk_peer_disconnect(peer.arsdk_peer)
        if res < 0:
            self.logger.error(f"arsdk_peer_disconnect {res}")
        return True

    def destroy(self):
        """
        explicit destructor
        """
        self.stop()
        super().destroy()
        self._backend.destroy()
