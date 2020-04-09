# -*- coding: UTF-8 -*-

import olympe
import time
from olympe.messages.ardrone3.Piloting import TakeOff, Landing

olympe.log.update_config({
    "handlers": {
        "olympe_log_file": {
            "class": "logging.FileHandler",
            "formatter": "default_formatter",
            "filename": "olympe.log"
        },
        "ulog_log_file": {
            "class": "logging.FileHandler",
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

DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP, name="toto")
    drone.connect()
    assert drone(TakeOff()).wait().success()
    assert drone.start_video_streaming()
    time.sleep(10)
    assert drone.stop_video_streaming()
    assert drone(Landing()).wait().success()
    drone.disconnect()
