# -*- coding: UTF-8 -*-

import olympe
from olympe.messages.skyctrl.CoPiloting import setPilotingSource

drone = olympe.Drone("192.168.53.1", mpp=True)
drone.connection()
drone(setPilotingSource(source="Controller")).wait()
