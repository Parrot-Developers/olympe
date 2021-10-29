import olympe
from olympe.messages.skyctrl.CoPiloting import setPilotingSource

SKYCTRL_IP = "192.168.53.1"

if __name__ == "__main__":
    drone = olympe.Drone(SKYCTRL_IP)
    drone.connect()
    drone(setPilotingSource(source="Controller")).wait()
    drone.disconnect()
