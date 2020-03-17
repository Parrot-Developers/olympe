# -*- coding: UTF-8 -*-

import collections.abc
import copy
import logging
import logging.config
import ulog


_config = {
    "version": 1,
    "formatters": {
        "color_formatter": {
            "()": "colorlog.ColoredFormatter",
            "format": (
                "%(asctime)s %(log_color)s[%(levelname)s] "
                "%(reset)s\t%(name)s - %(funcName)s - %(message)s"
            ),
        },
        "default_formatter": {
            "format": (
                "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s - %(message)s"
            )
        },
    },
    "handlers": {
        "console": {"class": "colorlog.StreamHandler", "formatter": "color_formatter"}
    },
    "loggers": {"olympe": {"level": "INFO", "handlers": ["console"]}},
}


def _configure_ulog_bridge():
    # Get the ulog entries of this process into a Python logging.logger
    # This "ulog" logger has no log handler by default and should only
    # be activated for debugging
    ulog_logger = logging.getLogger("ulog")
    ulog.enable_bridge(ulog_logger, forward=False)


def get_config(config):
    """
    Returns the current logging configuration dictionary as previously set or
    updated by :py:func:`~olympe.log.set_config` or
    :py:func:`~olympe.log.update_config` respectively.

    See: `Logging config dictionary schema <https://docs.python.org/3/library/logging.config.html#logging-config-dictschema>`_
    """
    global _config
    return _config


def set_config(config):
    """
    Set the current logging configuration dictionary

    See: `Logging config dictionary schema <https://docs.python.org/3/library/logging.config.html#logging-config-dictschema>`_

    """
    global _config
    logging.config.dictConfig(config)
    _configure_ulog_bridge()
    _config = config


def _update_dict_recursive(res, update):
    for k, v in update.items():
        if isinstance(v, collections.abc.Mapping):
            res[k] = _update_dict_recursive(res.get(k, {}), v)
        else:
            res[k] = v
    return res


def update_config(update):
    """
    Update (recursively) the current logging condiguration dictionary.

    See: `Logging config dictionary schema <https://docs.python.org/3/library/logging.config.html#logging-config-dictschema>`_

    """
    global _config
    new_config = copy.deepcopy(_config)
    _update_dict_recursive(new_config, update)
    logging.config.dictConfig(new_config)
    _configure_ulog_bridge()
    _config = new_config


# set the default log configuration on import
set_config(_config)
