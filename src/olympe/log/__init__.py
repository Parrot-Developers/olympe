import logging
import logness
import ulog


def _configure_ulog_bridge():
    # Get the ulog entries of this process into a Python logging.logger
    # This "ulog" logger has no log handler by default and should only
    # be activated for debugging
    ulog_logger = logging.getLogger("ulog")
    ulog.enable_bridge(ulog_logger, forward=False)


# backward compatibility
update_config = logness.update_config

logness.update_config(
    {
        "loggers": {
            "olympe": {
                "level": "INFO",
                "handlers": ["console"]
            },
            "ulog": {
                "handlers": ["console"],
                "level": "ERROR",
            },
        },
    },
    on_update=_configure_ulog_bridge
)


class LogMixin:
    def __init__(self, name, device_name, scope):
        self._name = name
        self._device_name = device_name
        self._logger_scope = scope
        self.update_logger()

    def set_device_name(self, device_name):
        self._device_name = device_name
        self.update_logger()

    def update_logger(self):
        if self._name is not None:
            self.logger = logging.getLogger(f"olympe.{self._name}.{self._logger_scope}")
        else:
            if self._device_name is not None:
                self.logger = logging.getLogger(
                    f"olympe.{self._logger_scope}.{self._device_name}")
            else:
                self.logger = logging.getLogger(f"olympe.{self._logger_scope}")
        return self.logger
