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

import colorama
from datetime import datetime
import functools
import inspect


# pylint: disable=E1101

# TODO: use a TBD logging system for all Parrot python modules
# and drop this legacy logging system used by olympe


class level:
    critical, error, warning, info, debug = range(5)


_COLOR_ENDC = colorama.Style.RESET_ALL
_COLOR_CRITICAL = colorama.Fore.RED + colorama.Style.BRIGHT
_COLOR_WARNING = colorama.Fore.CYAN + colorama.Style.BRIGHT
_COLOR_ERROR = colorama.Fore.MAGENTA + colorama.Style.BRIGHT
_COLOR_DEBUG = colorama.Fore.BLUE + colorama.Style.BRIGHT
_COLOR_INFO = ""
_COLUM_SIZE = 32

_COLORS = {
    level.critical: _COLOR_CRITICAL,
    level.error: _COLOR_ERROR,
    level.warning: _COLOR_WARNING,
    level.info: _COLOR_INFO,
    level.debug: _COLOR_DEBUG
}

_LEVEL_STR = {
    level.critical: "Critical",
    level.error: "Error",
    level.warning: "Warning",
    level.info: "Info",
    level.debug: "Debug",
}


class TraceLogger(object):

    level = level

    def __init__(self, log_level, _logfile=None):
        self.log_level = log_level
        self._logfile = _logfile
        self._is_a_tty = self._detect_terminal()

    def _detect_terminal(self):
        try:
            return self._logfile.isatty()
        except AttributeError:
            return False

    @staticmethod
    def _timestamp():
        return datetime.now().strftime("%d/%m/%Y %H:%M:%S.%f")

    def _log_term(self, scope, msg, color):
        line = color + self._timestamp() + "\t" + scope.ljust(_COLUM_SIZE) + msg + _COLOR_ENDC
        self._logfile.write(line + "\n")

    def _log_txt(self, scope, msg, ltype):
        line = self._timestamp() + "\t" + ltype.ljust(12) + scope.ljust(_COLUM_SIZE) + msg
        self._logfile.write(line + "\n")

    # log functions whose scope is automatically set to the caller function
    def _log_helper(_lvl=None):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(self, msg, lvl=None):
                if bool(lvl is None) == bool(_lvl is None):
                    raise ValueError(
                        "The 'lvl' parameter cannot be overriden for TraceLogger.{}".format(
                            func.__name__))
                if lvl is None:
                    lvl = _lvl
                if self._logfile is None:
                    return
                if self.log_level >= lvl:
                    scope = inspect.stack()[1][3]
                    if self._is_a_tty:
                        self._log_term(scope, msg, _COLORS.get(lvl, level.info))
                    else:
                        self._log_txt(scope, msg, _LEVEL_STR.get(lvl, level.info))
            return wrapper
        return decorator

    @_log_helper()
    def log(self, msg, lvl):
        pass

    @_log_helper(level.debug)
    def logD(self, msg):
        pass

    @_log_helper(level.info)
    def logI(self, msg):
        pass

    @_log_helper(level.warning)
    def logW(self, msg):
        pass

    @_log_helper(level.error)
    def logE(self, msg):
        pass

    @_log_helper(level.critical)
    def logC(self, msg):
        pass


class ErrorCodes(object):

    OK = 0

    ERROR_INVALID_PARAMS = 1
    ERROR_OUT_OF_BOUNDS = 2
    ERROR_BAD_STATE = 3
    ERROR_GENERIC_UNKNOWN = 10

    ERROR_CODES = {OK: "OK",
                   ERROR_INVALID_PARAMS: "Error: Invalid parameters!",
                   ERROR_OUT_OF_BOUNDS: "Error: Out of bounds parameters!",
                   ERROR_BAD_STATE: "Error: Bad state!",
                   ERROR_GENERIC_UNKNOWN: "Unknown error :-("}


class ErrorCodeDrone(ErrorCodes):

    ERROR_DRONES_NOT_SUPPORTED = 100
    ErrorCodes.ERROR_CODES[ERROR_DRONES_NOT_SUPPORTED] = "Error, not supported by the drone"
    ERROR_CALLBACK_NOT_CALLED = 101
    ErrorCodes.ERROR_CODES[ERROR_CALLBACK_NOT_CALLED] = "Error, callback wasn't called"
    ERROR_CONNECTION_TIMEOUT = 102
    ErrorCodes.ERROR_CODES[ERROR_CONNECTION_TIMEOUT] = "Error, controller connection timeout"
    ERROR_CONNECTION_ACCESS_DENIED = 103
    ErrorCodes.ERROR_CODES[ERROR_CONNECTION_ACCESS_DENIED] = "Error, controller connection access denied"
    ERROR_COMMAND_NOT_SEND = 104
    ErrorCodes.ERROR_CODES[ERROR_COMMAND_NOT_SEND] = "Error, command not send to drone or callback not received"
    ERROR_PILOTING_STATE = 105
    ErrorCodes.ERROR_CODES[ERROR_PILOTING_STATE] = "Error, piloting thread not launch"
    ERROR_PARAMETER = 106
    ErrorCodes.ERROR_CODES[ERROR_PARAMETER] = "Error, bad parameters"
    ERROR_CONNECTION = 107
    ErrorCodes.ERROR_CODES[ERROR_CONNECTION] = "Error, cannot make connection"


class DroneLogger(object):
    """
    This class is used only to retrieve the logger.logging of an RPC server
    and must not be initialized
    """
    # TODO: The following variable is global and we will have to come up with
    # a better solution when the logging system will be overhauled (DTT-536)
    LOGGER = None

    def __init__(self):
        raise NotImplementedError
