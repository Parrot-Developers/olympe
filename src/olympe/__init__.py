# -*- coding: UTF-8 -*-


import sys

import olympe.arsdkng.module_loader

sys.meta_path.append(olympe.arsdkng.module_loader.ModuleLoader())

from .arsdkng.drone import Drone
from .arsdkng.expectations import ArsdkExpectationBase
Drone = Drone
Expectation = ArsdkExpectationBase
import olympe.messages
import olympe.enums
from ._private.__version__ import __version__
from ._private.return_tuple import ReturnTuple
ReturnTuple = ReturnTuple

import faulthandler
faulthandler.enable()

__version__ = __version__
