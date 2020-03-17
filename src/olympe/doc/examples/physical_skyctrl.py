# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.skyctrl.CoPiloting import setPilotingSource

drone = olympe.Drone("192.168.53.1")
drone.connect()
drone(setPilotingSource(source="Controller")).wait()
