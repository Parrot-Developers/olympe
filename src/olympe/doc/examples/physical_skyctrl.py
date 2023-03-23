import olympe
from olympe.messages.skyctrl.CoPiloting import setPilotingSource

if __name__ == "__main__":
    skyctrl = olympe.SkyController("192.168.53.1")
    skyctrl.connect()
    skyctrl(setPilotingSource(source="Controller")).wait()
    skyctrl.disconnect()
