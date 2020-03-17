# -*- coding: UTF-8 -*-

import olympe
import time
from olympe.messages.ardrone3.Piloting import TakeOff, Landing

olympe.log.update_config({
    "handlers": {
        "olympe_log_file": {
            "class": "logging.FileHandler",
            "formatter": "file_formatter",
            "filename": "olympe.log"
        },
        "ulog_log_file": {
            "class": "logging.FileHandler",
            "formatter": "file_formatter",
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

drone = olympe.Drone("10.202.0.1", name="toto")
drone.connect()
drone(TakeOff()).wait()
drone.start_video_streaming()
time.sleep(10)
drone.stop_video_streaming()
drone(Landing()).wait()
drone.disconnect()
