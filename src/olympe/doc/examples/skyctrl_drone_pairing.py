from collections import OrderedDict
import os

import olympe
from olympe.messages.drone_manager import (
    discover_drones,
    connection_state,
    connect,
    forget,
)

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

SKYCTRL_IP = os.environ.get("SKYCTRL_IP", "192.168.53.1")
DRONE_SSID = os.environ.get("DRONE_SSID", "Anafi_PC_000000")
DRONE_SECURITY_KEY = os.environ.get("DRONE_SECURITY_KEY", "")
DRONE_SERIAL = os.environ.get("DRONE_SERIAL", "000000")


class SkyControllerExample:
    def __init__(self):
        self.skyctrl = olympe.SkyController(SKYCTRL_IP)

    def skyctrl_connect(self):
        self.skyctrl.connect()

    def update_drones(self):
        discover_results = self.skyctrl(discover_drones()).wait(_timeout=10)
        assert discover_results.success(), "Update drone discovery timedout"
        drone_list_items = discover_results.received_events()
        known_drones = OrderedDict()
        visible_drones = OrderedDict()
        active_drone = None
        for drone_list_item in drone_list_items:
            if drone_list_item.args["visible"] == 1:
                visible_drones[drone_list_item.args["serial"]] = drone_list_item.args
            if drone_list_item.args["active"] == 1:
                active_drone = drone_list_item.args["serial"]
            if drone_list_item.args["connection_order"] != 0:
                known_drones[drone_list_item.args["serial"]] = drone_list_item.args

        self.active_drone = active_drone
        self.known_drones = known_drones
        self.visible_drones = visible_drones

        print("Active drone: ", self.active_drone)
        print("Known drones: ", ", ".join(self.known_drones))
        print("Visible drones: ", ", ".join(self.visible_drones))

    def connect_drone(self, drone_serial, drone_security_key=""):
        self.update_drones()
        if self.active_drone == drone_serial:
            print(f"SkyController is already connected to {drone_serial}")
            return True
        print(f"SkyController is not currently connected to {drone_serial}")
        if drone_serial in self.visible_drones:
            print(f"Connecting to {drone_serial}...")
            connection = self.skyctrl(
                connect(serial=drone_serial, key=drone_security_key)
                >> connection_state(state="connected", serial=drone_serial)
            ).wait(_timeout=10)
        elif drone_serial in self.known_drones:
            print(
                f"{drone_serial} is a known drone but is not currently visible"
            )
            return
        elif drone_serial is not None:
            print(
                f"{drone_serial} is an unknown drone and not currently visible"
            )
            return
        if connection.success():
            print(f"Connected to {drone_serial}")
            return True
        else:
            print(f"Failed to connect to {drone_serial}")

    def forget_drone(self, drone_serial):
        if drone_serial == self.active_drone:
            print(f"Forgetting {drone_serial} ...")
            self.skyctrl(
                forget(serial=drone_serial)
                >> connection_state(state="disconnecting", serial=drone_serial)
            ).wait(_timeout=10)
        elif drone_serial in self.known_drones:
            print(f"Forgetting {drone_serial} ...")
            self.skyctrl(forget(serial=drone_serial)).wait(_timeout=10)
        else:
            print(f"{drone_serial} is an unknown drone")

    def disconnect_skyctrl(self):
        self.skyctrl.disconnect()


def main():
    example = SkyControllerExample()
    print("@ Connection to SkyController")
    example.skyctrl_connect()
    example.update_drones()
    print("@ Connection to a drone")
    if example.connect_drone(DRONE_SERIAL, DRONE_SECURITY_KEY):
        example.update_drones()
        print("@ Forgetting a drone")
        example.forget_drone(DRONE_SERIAL)
        example.update_drones()
    print("@ Disconnection from SkyController")
    example.disconnect_skyctrl()


def test_skyctrl_drone_pairing():
    main()


if __name__ == "__main__":
    main()
