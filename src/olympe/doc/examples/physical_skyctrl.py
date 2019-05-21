# -*- coding: UTF-8 -*-

import olympe
import olympe_deps as od
from olympe.messages.skyctrl.CoPiloting import setPilotingSource

drone = olympe.Drone("192.168.53.1", mpp=True, drone_type=od.ARSDK_DEVICE_TYPE_ANAFI4K)
drone.connection()
drone(setPilotingSource(source="Controller")).wait()
