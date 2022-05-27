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


import datetime
from collections import OrderedDict, defaultdict


class DeviceConnStatus:
    """
    Save info about connection/device
    """

    def __init__(self):
        self.reset_status()

    def reset_status(self):
        # Set to True when connected callback is called
        # and to False when disconnected callback is called
        self.connected = False
        # Keep all device info in a dictionary
        self.device_infos = {}


class DeviceStates:
    """
    Save all device states in a dictionary
    """

    def __init__(self):
        self.reset_all_states()

    def reset_all_states(self):
        self.states = defaultdict(OrderedDict)


class PilotingCommand:
    """
    Manage piloting commands values that will be send to the drone when piloting has been started
    """

    def __init__(self):
        self.set_default_piloting_command()

    def update_piloting_command(self, roll, pitch, yaw, gaz, piloting_time):
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.gaz = gaz
        self.piloting_time = piloting_time
        self.initial_time = datetime.datetime.now()

    def set_default_piloting_command(self):
        self.roll = 0
        self.pitch = 0
        self.yaw = 0
        self.gaz = 0
        self.piloting_time = 0
        self.initial_time = 0


class ControllerState:

    def __init__(self):
        self.device_conn_status = DeviceConnStatus()
        self.device_states = DeviceStates()
        self.piloting_command = PilotingCommand()
