#  Copyright (C) 2019-2021 Parrot Drones SAS
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions
#  are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the Parrot Company nor the names
#    of its contributors may be used to endorse or promote products
#    derived from this software without specific prior written
#    permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
#  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#  PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
#  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
#  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
#  OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
#  AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
#  OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.

import sys
import types
import warnings

import olympe_deps as od

import olympe.enums  # noqa
import olympe.log  # noqa
import olympe.module_loader
import olympe.messages  # noqa

from .controllers.mixins.cellular import Cellular
from .features.flightplan import Flightplan, FlightplanInfo
from .features.media import Media, MediaEvent, MediaInfo, ResourceInfo
from .features.mission import Mission, MissionController
from .messaging.arsdk_events import ArsdkMessageEvent
from .messaging.arsdk_expectations import MatchingMode
from .messaging.events import Event
from .messaging.expectations import Expectation
from .messaging.listener import EventListener, listen_event
from .utils import hashabledict

from .controller import (
    Drone,
    Anafi,
    AnafiUSA,
    AnafiAi,
    AnafiUKR,
    Chuck,
    LocalController,
    SkyController,
    SkyController3,
    SkyController4,
    SkyController4Black,
    SkyControllerUSA,
    SkyControllerUKR,
    SkyControllerUKR5G,
)

try:
    from .features.video import VMetaFrameType
    from .features.video.frame import VideoFrame
    from .features.video.pdraw import (
        PdrawState,
        Pdraw,
        h264_coded_data_format,
        video_frame_type,
        StreamInfo,
        Callbacks,
        Outfiles,
        OutfilesPath,
    )
    from .features.video.renderer import PdrawRenderer

    has_video_support = True
except ImportError:
    VMetaFrameType = None
    VideoFrame = None
    PdrawState = None
    Pdraw = None
    PdrawRenderer = None
    has_video_support = False

from .__version__ import __version__

if has_video_support:
    from olympe_deps import vdef_i420
    from olympe_deps import vdef_nv12

    VDEF_I420 = hashabledict(od.struct_vdef_raw_format.as_dict(vdef_i420))
    VDEF_NV12 = hashabledict(od.struct_vdef_raw_format.as_dict(vdef_nv12))
else:
    VDEF_I420 = hashabledict()
    VDEF_NV12 = hashabledict()

__version__ = __version__


# ---------------------------------------------------------------------------
# Backward-compatibility aliases for modules moved in this release.
# These aliases will be removed in the next major version.
# ---------------------------------------------------------------------------


def _deprecated_module(old_path, real_module):
    """Return a module wrapper that emits a DeprecationWarning on attribute access."""
    new_path = real_module.__name__

    class _Deprecated(types.ModuleType):
        def __getattr__(self, name):
            if name == "__path__":
                # Never advertise a __path__: it would make the import system
                # treat this wrapper as a real package and re-load submodules
                # under this deprecated dotted path as brand new, distinct
                # module/class objects instead of aliasing the real ones.
                raise AttributeError(name)
            warnings.warn(
                f"'{old_path}' is deprecated and will be removed in the next major version. "
                f"Use '{new_path}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return getattr(real_module, name)

        def __dir__(self):
            return dir(real_module)

    mod = _Deprecated(old_path)
    return mod


_deprecated_paths = {
    "olympe.expectations": "olympe.messaging.expectations",
    "olympe.event": "olympe.messaging.events",
    "olympe.listener": "olympe.messaging.listener",
    "olympe.subscriber": "olympe.messaging.subscriber",
    "olympe.http": "olympe.connection.http",
    "olympe.networking": "olympe.connection.networking",
    "olympe.scheduler": "olympe.controllers.scheduler",
    "olympe.flightplan": "olympe.features.flightplan",
    "olympe.media": "olympe.features.media",
    "olympe.mission": "olympe.features.mission",
    "olympe.event_marker": "olympe.messaging.event_marker",
    "olympe.types": "olympe.utils.types",
    "olympe.update": "olympe.features.update",
    "olympe.video": "olympe.features.video",
    "olympe.video.frame": "olympe.features.video.frame",
    "olympe.video.mp4": "olympe.features.video.mp4",
    "olympe.video.pdraw": "olympe.features.video.pdraw",
    "olympe.video.renderer": "olympe.features.video.renderer",
    "olympe.doc.examples": "olympe.examples",
    "olympe.arsdkng.json": "olympe.utils.json",
    "olympe.arsdkng.backend": "olympe.connection.backend",
    "olympe.arsdkng.expectations": "olympe.messaging.arsdk_expectations",
    "olympe.arsdkng._proto_message_helpers": "olympe.parser._proto_message_helpers",
    "olympe.arsdkng._xml_message_helpers": "olympe.parser._xml_message_helpers",
    "olympe.arsdkng.cmd_itf": "olympe.controllers.cmd_itf",
    "olympe.arsdkng.controller": "olympe.controllers.controller",
    "olympe.arsdkng.device": "olympe.controllers.device",
    "olympe.arsdkng.discovery": "olympe.connection.discovery",
    "olympe.arsdkng.doc": "olympe.parser.doc",
    "olympe.arsdkng.enums": "olympe.messaging.arsdk_enums",
    "olympe.arsdkng.events": "olympe.messaging.arsdk_events",
    "olympe.arsdkng.messages": "olympe.messaging.arsdk_messages",
    "olympe.arsdkng.proto": "olympe.parser.proto",
    "olympe.arsdkng.proto_this": "olympe.parser.proto_this",
    "olympe.arsdkng.xml": "olympe.parser.xml",
    "olympe.protobuf": "olympe.parser",
    "olympe.protobuf.json_format": "olympe.parser.json_format",
    "olympe.protobuf.proto_builder": "olympe.parser.proto_builder",
    "olympe.mixins": "olympe.controllers.mixins",
    "olympe.mixins.cellular": "olympe.controllers.mixins.cellular",
    "olympe.mixins.flightplan": "olympe.controllers.mixins.flightplan",
    "olympe.mixins.http": "olympe.controllers.mixins.http",
    "olympe.mixins.ipproxy": "olympe.controllers.mixins.ipproxy",
    "olympe.mixins.media": "olympe.controllers.mixins.media",
    "olympe.mixins.mission": "olympe.controllers.mixins.mission",
    "olympe.mixins.protocols": "olympe.controllers.mixins.protocols",
    "olympe.mixins.streaming": "olympe.controllers.mixins.streaming",
    "olympe.mixins.update": "olympe.controllers.mixins.update",
}

# Phase 1: build every deprecated module wrapper first. `_deprecated_module`
# relies on the real `types` module (via `types.ModuleType`), so none of
# these wrappers must be wired into the parent packages' namespace yet: one
# of the aliases below is itself named "olympe.types" and would otherwise
# shadow the `types` stdlib import used above for later iterations.
for _old, _new_path in _deprecated_paths.items():
    import importlib

    _real = importlib.import_module(_new_path)
    sys.modules.setdefault(_old, _deprecated_module(_old, _real))

# Phase 2: now that every wrapper exists, wire each one onto its parent
# module/package so that attribute access (e.g. `olympe.media`) works, not
# just `from olympe.media import ...` / `import olympe.media`.
for _old in _deprecated_paths:
    _parent_name, _, _child_name = _old.rpartition(".")
    if _parent_name in sys.modules:
        setattr(sys.modules[_parent_name], _child_name, sys.modules[_old])


__all__ = [
    "Anafi",
    "AnafiUSA",
    "AnafiAi",
    "AnafiUKR",
    "Chuck",
    "ArsdkMessageEvent",
    "Cellular",
    "Drone",
    "Event",
    "EventListener",
    "Expectation",
    "Flightplan",
    "FlightplanInfo",
    "LocalController",
    "MatchingMode",
    "Mission",
    "MissionController",
    "Pdraw",
    "PdrawRenderer",
    "PdrawState",
    "OutfilesPath",
    "Outfiles",
    "Callbacks",
    "StreamInfo",
    "video_frame_type",
    "h264_coded_data_format",
    "SkyController",
    "SkyController3",
    "SkyController4",
    "SkyController4Black",
    "SkyControllerUSA",
    "SkyControllerUKR",
    "SkyControllerUKR5G",
    "VDEF_I420",
    "VDEF_NV12",
    "VMetaFrameType",
    "VideoFrame",
    "has_video_support",
    "listen_event",
]
