import olympe
import os
import time
import logness
from olympe.messages.ardrone3.Piloting import TakeOff, Landing

logness.update_config({
    "handlers": {
        "olympe_log_file": {
            "class": "logness.FileHandler",
            "formatter": "default_formatter",
            "filename": "olympe.log"
        },
        "ulog_log_file": {
            "class": "logness.FileHandler",
            "formatter": "default_formatter",
            "filename": "ulog.log"
        },
    },
    "loggers": {
        "olympe": {
            "handlers": ["console", "olympe_log_file"]
        },
        "ulog": {
            "level": "DEBUG",
            "handlers": ["console", "ulog_log_file"],
        }
    }
})

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT")


def test_log():
    drone = olympe.Drone(DRONE_IP, name="toto")
    drone.connect()
    if DRONE_RTSP_PORT is not None:
        drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"
    assert drone(TakeOff()).wait().success()
    assert drone.streaming.play()
    time.sleep(10)
    assert drone.streaming.stop()
    assert drone(Landing()).wait().success()
    drone.disconnect()


if __name__ == "__main__":
    test_log()
