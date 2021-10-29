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

from olympe.log import LogMixin
from olympe.utils.pomp_loop_thread import PompLoopThread
from olympe.utils import callback_decorator


class CtrlBackend(LogMixin):
    def __init__(self, name=None, proto_v_min=1, proto_v_max=3):
        super().__init__(name, None, "backend")
        self._proto_v_min = proto_v_min
        self._proto_v_max = proto_v_max
        self._device_handler = []
        self._thread_loop = PompLoopThread(self.logger)
        self._thread_loop.register_cleanup(self._destroy)
        self._thread_loop.start()
        (
            self._arsdk_ctrl,
            self._arsdk_ctrl_device_cbs,
            self._backend_net,
            self._backend_net_socket_callback,
        ) = self._create()

    def add_device_handler(self, device_handler):
        self._device_handler.append(device_handler)

    def remove_device_handler(self, device_handler):
        try:
            self._device_handler.remove(device_handler)
        except ValueError:
            # ignore missing device handler
            pass

    def _create(self):
        f = self._thread_loop.run_async(self._do_create)
        return f.result_or_cancel(timeout=1.0)

    @callback_decorator()
    def _do_create(self):
        # default userdata callback argument
        self.userdata = ctypes.c_void_p()

        # Create the arsdk_ctrl
        arsdk_ctrl = od.POINTER_T(od.struct_arsdk_ctrl)()
        res = od.arsdk_ctrl_new(self._thread_loop.pomp_loop, ctypes.byref(arsdk_ctrl))
        backend_net = od.POINTER_T(od.struct_arsdkctrl_backend_net)()

        arsdk_ctrl_device_cbs = od.struct_arsdk_ctrl_device_cbs.bind(
            {"added": self._device_added_cb, "removed": self._device_removed_cb}
        )
        # Send a command to add callbacks to the arsdk_ctrl
        res = od.arsdk_ctrl_set_device_cbs(arsdk_ctrl, arsdk_ctrl_device_cbs)

        if res != 0:
            raise RuntimeError("arsdk_ctrl_set_device_cbs: {}".format(res))

        self.logger.info("device callbacks have been added to arsdk_ctrl")

        # Create the net backend
        cfg = od.struct_arsdkctrl_backend_net_cfg.bind({
            "iface": ctypes.create_string_buffer(b"net_config"),
            "stream_supported": 1,
            "proto_v_min": self._proto_v_min,
            "proto_v_max": self._proto_v_max,
        })

        res = od.arsdkctrl_backend_net_new(
            arsdk_ctrl, ctypes.pointer(cfg), ctypes.byref(backend_net)
        )

        if res != 0:
            raise RuntimeError("arsdkctrl_backend_net_new: {}".format(res))

        self.logger.debug("New net backend has been created")

        backend_net_socket_callback = od.arsdkctrl_backend_net_socket_cb_t(
            lambda *args: self._socket_cb(*args)
        )

        res_set_socket = od.arsdkctrl_backend_net_set_socket_cb(
            backend_net, backend_net_socket_callback, self.userdata
        )
        if res_set_socket != 0:
            raise RuntimeError("arsdkctrl_backend_net_set_socket_cb: {}".format(res))

        self.logger.debug("Set backend socket callback OK")

        return (
            arsdk_ctrl,
            arsdk_ctrl_device_cbs,
            backend_net,
            backend_net_socket_callback,
        )

    def destroy(self):
        self._thread_loop.stop()

    @callback_decorator()
    def _destroy(self):
        self.logger.debug("Destroying backend...")
        if self._backend_net is not None:

            res = od.arsdkctrl_backend_net_destroy(self._backend_net)

            if res != 0:
                self.logger.error("Error while destroying net backend: {}".format(res))
            else:
                self._backend_net = None
                self.logger.debug("Net backend has been destroyed")

        if self._arsdk_ctrl is not None:
            res = od.arsdk_ctrl_destroy(self._arsdk_ctrl)

            if res != 0:
                self.logger.error("Error while destroying arsdk_ctrl: {}".format(res))
            else:
                self._arsdk_ctrl = None
                self.logger.info("Manager has been destroyed")
        self._thread_loop.unregister_cleanup(self._destroy, ignore_error=True)
        self._thread_loop.stop()
        self._thread_loop.destroy()

    @callback_decorator()
    def _socket_cb(self, backend_net, socket_fd, socket_kind, userdata):
        self.logger.debug(
            "backend_pointer {} socket_fd {} socket_kind {} userdate_pointer {}".format(
                backend_net, socket_fd, socket_kind, userdata
            )
        )

    @callback_decorator()
    def _device_added_cb(self, arsdk_device, _user_data):
        for device_handler in self._device_handler:
            device_handler._device_added_cb(arsdk_device, _user_data)

    @callback_decorator()
    def _device_removed_cb(self, arsdk_device, _user_data):
        for device_handler in self._device_handler:
            device_handler._device_removed_cb(arsdk_device, _user_data)


class DeviceBackend(LogMixin):
    def __init__(self, name=None, proto_v_min=1, proto_v_max=3):
        super().__init__(name, None, "backend")
        self._proto_v_min = proto_v_min
        self._proto_v_max = proto_v_max
        self._device_handler = []
        self._thread_loop = PompLoopThread(self.logger)
        self._thread_loop.register_cleanup(self._destroy)
        self._thread_loop.start()
        (
            self._arsdk_mngr,
            self._arsdk_mngr_device_cbs,
            self._backend_net,
            self._backend_net_socket_callback,
        ) = self._create()

    def add_device_handler(self, device_handler):
        self._device_handler.append(device_handler)

    def remove_device_handler(self, device_handler):
        try:
            self._device_handler.remove(device_handler)
        except ValueError:
            # ignore missing device handler
            pass

    def _create(self):
        f = self._thread_loop.run_async(self._do_create)
        return f.result_or_cancel(timeout=1.0)

    @callback_decorator()
    def _do_create(self):
        # default userdata callback argument
        self.userdata = ctypes.c_void_p()

        # Create the arsdk_mngr
        arsdk_mngr = od.POINTER_T(od.struct_arsdk_mngr)()
        res = od.arsdk_mngr_new(self._thread_loop.pomp_loop, ctypes.byref(arsdk_mngr))
        backend_net = od.POINTER_T(od.struct_arsdk_backend_net)()

        arsdk_mngr_peer_cbs = od.struct_arsdk_mngr_peer_cbs.bind(
            {"added": self._peer_added_cb, "removed": self._peer_removed_cb}
        )
        # Send a command to add callbacks to the arsdk_mngr
        res = od.arsdk_mngr_set_peer_cbs(arsdk_mngr, arsdk_mngr_peer_cbs)

        if res != 0:
            raise RuntimeError("arsdk_mngr_set_device_cbs: {}".format(res))

        self.logger.info("device callbacks have been added to arsdk_mngr")

        # Create the net backend
        cfg = od.struct_arsdk_backend_net_cfg.bind({
            "iface": od.POINTER_T(ctypes.c_char)(),
            "stream_supported": 1,
            "proto_v_min": self._proto_v_min,
            "proto_v_max": self._proto_v_max,
        })

        res = od.arsdk_backend_net_new(
            arsdk_mngr, ctypes.pointer(cfg), ctypes.byref(backend_net)
        )

        if res != 0:
            raise RuntimeError("arsdk_backend_net_new: {}".format(res))

        self.logger.debug("New net backend has been created")

        backend_net_socket_callback = od.arsdk_backend_net_socket_cb_t(
            lambda *args: self._socket_cb(*args)
        )

        res_set_socket = od.arsdk_backend_net_set_socket_cb(
            backend_net, backend_net_socket_callback, self.userdata
        )
        if res_set_socket != 0:
            raise RuntimeError("arsdk_backend_net_set_socket_cb: {}".format(res))

        self.logger.debug("Set backend socket callback OK")

        return (
            arsdk_mngr,
            arsdk_mngr_peer_cbs,
            backend_net,
            backend_net_socket_callback,
        )

    def destroy(self):
        self._thread_loop.stop()

    @callback_decorator()
    def _destroy(self):
        self.logger.debug("Destroying backend...")
        if self._backend_net is not None:

            res = od.arsdk_backend_net_destroy(self._backend_net)

            if res != 0:
                self.logger.error("Error while destroying net backend: {}".format(res))
            else:
                self._backend_net = None
                self.logger.debug("Net backend has been destroyed")

        self._thread_loop.unregister_cleanup(self._destroy, ignore_error=True)
        self._thread_loop.stop()
        self._thread_loop.destroy()

    @callback_decorator()
    def _socket_cb(self, backend_net, socket_fd, socket_kind, userdata):
        self.logger.debug(
            "backend_pointer {} socket_fd {} socket_kind {} userdate_pointer {}".format(
                backend_net, socket_fd, socket_kind, userdata
            )
        )

    @callback_decorator()
    def _peer_added_cb(self, arsdk_device, _user_data):
        for device_handler in self._device_handler:
            device_handler._device_added_cb(arsdk_device, _user_data)

    @callback_decorator()
    def _peer_removed_cb(self, arsdk_device, _user_data):
        for device_handler in self._device_handler:
            device_handler._device_removed_cb(arsdk_device, _user_data)
