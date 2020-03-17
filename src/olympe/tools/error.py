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
