from os import environ

import olympe

from olympe.messages.skyctrl.CoPiloting import setPilotingSource

SKYCTRL_IP = environ.get("DRONE_IP", "192.168.53.1")

if __name__ == "__main__":
    skyctrl = olympe.SkyController(SKYCTRL_IP)
    assert skyctrl.connect()

    # optional, as an example
    skyctrl(setPilotingSource(source="Controller")).wait()

    assert skyctrl.disconnect()
