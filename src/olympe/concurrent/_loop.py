# Copyright © 2022 Parrot SAS; All Rights Reserved
# Copyright © 2001-2022 Python Software Foundation; All Rights Reserved
#
# SPDX-License-Identifier: PSF-2.0
#
# Licensed under the PSF License Agreement, Version 2 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://docs.python.org/3.9/license.html#psf-license
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os
import threading


# A TLS for the running event loop, used by _get_running_loop.
class _RunningLoop(threading.local):
    loop_pid = (None, None)


_running_loop = _RunningLoop()


def get_running_loop():
    """Return the running event loop.  Raise a RuntimeError if there is none.

    This function is thread-specific.
    """
    loop = _get_running_loop()
    if loop is None:
        raise RuntimeError("no running event loop")
    return loop


def _get_running_loop():
    """Return the running event loop or None.

    This is a low-level function intended to be used by event loops.
    This function is thread-specific.
    """
    running_loop, pid = _running_loop.loop_pid
    if running_loop is not None and pid == os.getpid():
        return running_loop


def _set_running_loop(loop):
    """Set the running event loop.

    This is a low-level function intended to be used by event loops.
    This function is thread-specific.
    """
    _running_loop.loop_pid = (loop, os.getpid())


class _LoopBoundMixin:
    def _get_loop(self):
        loop = _get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif loop is not None:
            assert self._loop is loop
        else:
            raise RuntimeError("Using {self!r} from thread not bound to any loop")
            # This condition is not an error per-se but should probably be avoided
        return self._loop
