# -*- coding: UTF-8 -*-

#  Copyright (C) 2020 Parrot Drones SAS
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

from collections import Mapping, namedtuple, OrderedDict
from logging import getLogger
from olympe._private import callback_decorator
from olympe._private.pomp_loop_thread import PompLoopThread, PompEvent
from olympe.arsdkng.events import Event, EventContext
from olympe.arsdkng.expectations import (
    CheckWaitStateExpectation,
    ExpectPolicy,
    Expectation,
    AbstractScheduler,
    StreamSchedulerMixin,
    Scheduler,
    MultipleExpectation,
    WhenAllExpectations,
)
import aenum
import hashlib
import json
import os
import requests
import socket
import websocket


MediaInfo = namedtuple(
    "MediaInfo",
    [
        "media_id",
        "type",
        "datetime",
        "size",
        "run_id",
        "resources",
        "duration",
        "thumbnail",
        "gps",
        "video_mode",
        "photo_mode",
        "panorama_type",
        "expected_count",
        "replay_url",
        "thermal",
    ],
)
MediaInfo.__new__.__defaults__ = (None,) * len(MediaInfo._fields)
MediaInfo.__doc__ = (
    "Namedtuple class "
    + MediaInfo.__doc__
    + """

  - media_id (str): unique id of the media
  - type ( :py:class:`~olympe.media.MediaType`): type of the media
  - datetime (str) :iso8601 datetime of the media
  - size (int): size (in bytes) of the media (total size of all its resources)
  - duration (int): duration (in milliseconds) of the video media (total
    duration of all its resources)
  - run_id (str): run id of the media
  - thumbnail (str): relative url to be used in a GET request to download the
    media thumbnail (if available)
  - gps (:py:class:`~olympe.media.GPS`): gps coordinates of the media (if
    available)
  - photo_mode (:py:class:`~olympe.media.PhotoMode`): photo mode of the media
    (if available and media is a photo)
  - panorama_type (panorama_type enum): panorama type of the media (if
    available, media is a photo and photo_mode is panorama)
  - expected_count (int): expected number of resources in the media (
    if available, media is a photo and photo_mode is panorama)
  - replay_url (str): media rtsp replay url (prefixed by
    `rtsp://drone.ip.address:rtsp_port/`)
  - resources (list( :py:class:`~olympe.ResourceInfo` )): resource list of
    the media
  - thermal (bool): media includes resources with thermal metadata (if value is
    true)

"""
)


def _replace_namedtuple(nt, **kwds):
    try:
        return nt._replace(**kwds)
    except ValueError as e:
        getLogger("olympe.media").warning(e)
        return nt


def _make_media(media):
    """
    :param media: a media object dictionary
    :return: a tuple of media_id (int) and a
        :py:class:`~olympe.MediaInfo` object
    :rtype: tuple(int, :py:class:`~olympe.MediaInfo`)
    """
    media = MediaInfo(**media)
    if not media.media_id:
        return None, media
    resources = media.resources
    media = _replace_namedtuple(media, resources=OrderedDict())
    for resource in resources:
        resource_id, resource = _make_resource(resource)
        if not resource_id:
            getLogger("olympe.media").error("Missing resource_id in webserver response")
            continue
        media.resources[resource_id] = resource
        if media.type in MediaType._value2member_map_:
            media = _replace_namedtuple(media, type=MediaType(media.type))
        if isinstance(media.gps, Mapping):
            media = _replace_namedtuple(media, gps=GPS(**media.gps))
        if media.photo_mode in PhotoMode._value2member_map_:
            media = _replace_namedtuple(media, photo_mode=PhotoMode(media.photo_mode))
        if media.panorama_type in PanoramaType._value2member_map_:
            media = _replace_namedtuple(
                media, panorama_type=PanoramaType(media.panorama_type)
            )
    return media.media_id, media


def _make_resource(resource):
    """
    :param media: a resource object dictionary
    :return: a tuple of resource_id (int) and a
        :py:class:`~olympe.ResourceInfo` object
    :rtype: tuple(int, :py:class:`~olympe.ResourceInfo`)
    """
    resource = ResourceInfo(**resource)
    if not resource.resource_id:
        return None, resource
    return resource.resource_id, resource


class MediaEnumBase(aenum.Enum):
    @classmethod
    def _missing_value_(cls, label_name):
        lowered_label_name = label_name.lower()
        for label in cls:
            if lowered_label_name == label.name.lower():
                return label


class MediaType(MediaEnumBase):
    _init_ = "value __doc__"
    photo = "PHOTO", "the media contains photo resources"
    video = "VIDEO", "the media contains video resources"


GPS = namedtuple("GPS", ["latitude", "longitude", "altitude"])
GPS.__doc__ = "Namedtuple class " + GPS.__doc__


class PhotoMode(MediaEnumBase):
    _init_ = "value __doc__"
    single = "SINGLE", "single shot mode"
    bracketing = (
        "BRACKETING",
        "bracketing mode (take a burst of 3 or 5 frames with a " "different exposure)",
    )
    burst = "BURST", "burst mode (take burst of frames)"
    panorama = (
        "PANORAMA",
        "panorama mode (take successive set of photos from one hovering "
        "point while rotating)",
    )
    timelapse = (
        "TIMELAPSE",
        "timelapse mode (take successive set of photos at a " "specific frequency)",
    )
    gpslapse = (
        "GPSLAPSE",
        "gpslapse mode (take successive set of photos at a specific distance "
        "from one another)",
    )


class PanoramaType(MediaEnumBase):
    _init_ = "value __doc__"
    horizontal_180 = "HORIZONTAL_180", "degrees on the horizontal plane"
    vertical_180 = "VERTICAL_180", "degrees on the vertical plane"
    spherical = "SPHERICAL", "360 degrees on the horizontal and vertical planes"


ResourceInfo = namedtuple(
    "ResourceInfo",
    [
        "media_id",
        "resource_id",
        "type",
        "format",
        "datetime",
        "size",
        "url",
        "width",
        "height",
        "duration",
        "thumbnail",
        "gps",
        "video_mode",
        "replay_url",
        "thermal",
        "md5",
        "download_path",
        "thumbnail_download_path",
    ],
)

ResourceInfo.__new__.__defaults__ = (None,) * len(ResourceInfo._fields)
ResourceInfo.__doc__ = "Namedtuple class " + ResourceInfo.__doc__


class IndexingState(MediaEnumBase):
    _init_ = "value __doc__"
    not_indexed = (
        "NOT_INDEXED",
        "media are not indexed and no indexing is in progress "
        "(media requests will result in 541 error)",
    )
    indexing = (
        "INDEXING",
        "media indexing is in progress (media requests will result in " "541 error)",
    )
    indexed = "INDEXED", "media are indexed (media requests are possible)"

    @classmethod
    def _missing_name_(cls, name):
        for member in cls:
            if member.name.lower() == name.lower():
                return member


class MediaEvent(Event):
    def __init__(self, name, data, policy=None, type_="media_event"):
        super().__init__(policy=policy)
        self._name = name
        self._media = None
        self._resource = None
        self._type = type_
        if isinstance(data, MediaInfo):
            self._data = dict()
            self._data["media"] = data._asdict()
        elif isinstance(data, ResourceInfo):
            self._data = dict()
            self._data["resource"] = data._asdict()
        else:
            self._data = data
        self._is_thumbnail = self._data.get("is_thumbnail", None)
        if "media" in self._data:
            _, self._media = _make_media(self._data["media"])
        if "resource" in self._data:
            _, self._resource = _make_resource(self._data["resource"])

        self._media_id = self._media.media_id if self._media else None
        self._resource_id = None
        if self._media_id is None:
            self._media_id = self._data.get("media_id")
        self._resource_id = None
        if self._media_id is None:
            self._media_id = self._resource.media_id if self._resource else None
            self._resource_id = self._resource.resource_id if self._resource else None
        if self._resource_id is None:
            self._resource_id = self._data.get("resource_id")
        if self._media_id or self._resource_id:
            self._id = self._media_id, self._resource_id
        else:
            self._id = self.uuid

    @property
    def name(self):
        return self._name

    @property
    def data(self):
        return self._data

    @property
    def media(self):
        return self._media

    @property
    def resource(self):
        return self._resource

    @property
    def is_thumbnail(self):
        return self._is_thumbnail

    @property
    def id(self):
        return self._id

    @property
    def media_id(self):
        return self._media_id

    @property
    def resource_id(self):
        return self._resource_id

    def __str__(self):
        args = ""
        if self._media_id:
            args += ", media_id={}".format(self._media_id)
        if self._resource_id:
            args += ", resource_id={}".format(self._resource_id)
        if self._is_thumbnail:
            args += ", is_thumbnail={}".format(self._is_thumbnail)
        if "media" in self._data:
            media = self._data["media"]
            if "photo_mode" in media:
                args += ", photo_mode={}".format(media["photo_mode"])
            if "panorama_type" in media:
                args += ", panorama_type={}".format(media["panorama_type"])
        elif "resource" in self._data:
            resource = self._data["resource"]
            if "height" in resource:
                args += ", height={}".format(resource["height"])
            if "width" in resource:
                args += ", width={}".format(resource["width"])
            if "duration" in resource:
                args += ", duration={}".format(resource["duration"])
            if "replay_url" in resource:
                args += ", replay_url={}".format(resource["replay_url"])
        return "{}(name={}{})".format(self._type, self._name, args)


class _MediaCommand(MediaEvent):
    def __init__(self, *args, **kwds):
        super().__init__(*args, type_="media_command", **kwds)


class _MediaEventExpectationBase(Expectation):
    def __init__(self, event_name, id_field=None, id_value=None, _timeout=None, **kwds):
        super().__init__()
        self.set_timeout(_timeout)
        self._expected_event = MediaEvent(event_name, {**{id_field: id_value}, **kwds})
        self._event_name = event_name
        self._id_field = id_field
        self._id_value = id_value
        self._received_events = []
        self._matched_event = None

    def copy(self):
        return super().base_copy(
            self._event_name, self._id_field, self._id_value, self._timeout
        )

    def check(self, media_event, *args, **kwds):
        if self._success:
            return self
        if not isinstance(media_event, MediaEvent):
            return self
        if media_event.name != self._event_name:
            return self
        self._received_events.append(media_event)
        if (self._id_field is None or self._id_value is None):
            self._matched_event = media_event
            self.set_success()
        elif media_event.data[self._id_field] == self._id_value:
            for name, value in self._expected_event.data.items():
                if value is not None and (
                        name not in media_event.data or
                        media_event.data[name] != value):
                    break
            else:
                self._matched_event = media_event
                self.set_success()
        return self

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        """
        Returns a collection of events that have matched at least one of the
        messages ID monitored by this expectation.
        """
        if not self._received_events:
            return EventContext()
        else:
            return EventContext(self._received_events[:])

    def matched_events(self):
        """
        Returns a collection of events that have matched this expectation
        (or a child expectation)
        """
        if self._success:
            return EventContext()
        else:
            return EventContext([self._matched_event])

    def unmatched_events(self):
        """
        Returns a collection of events object that are still expected
        """
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class _MediaEventExpectation(_MediaEventExpectationBase):
    def __init__(self, event_name, media_id=None, _timeout=None, **kwds):
        return super().__init__(
            event_name,
            id_field="media_id",
            id_value=media_id,
            _timeout=_timeout,
            **kwds
        )

    def copy(self):
        return super().base_copy(self._event_name, self._id_value, self._timeout)


class _ResourceEventExpectation(_MediaEventExpectationBase):
    def __init__(self, event_name, resource_id=None, _timeout=None, **kwds):
        return super().__init__(
            event_name,
            id_field="resource_id",
            id_value=resource_id,
            _timeout=_timeout,
            **kwds
        )

    def copy(self):
        return super().base_copy(self._event_name, self._id_value, self._timeout)


class _CheckStateExpectation(Expectation):
    def __init__(self, id_):
        super().__init__()
        self._id = id_
        self._expected_event = self.data_type(**{self.field_id: self._id})
        self._matched_event = None

    def copy(self):
        return super().base_copy(self._id)

    def check(self, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media_context = scheduler.context("olympe.media")
        if self._id is None:
            return self
        info = media_context.resource_info(**{self.field_id: self._id})
        if info:
            if isinstance(info, list):
                media = info[0]
            else:
                media = info
            self._matched_event = MediaEvent("media_present", media)
            self.set_success()
        else:
            self.cancel()
        return self

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._matched_event])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class _MediaCheckStateExpectation(_CheckStateExpectation):
    data_type = MediaInfo
    field_id = "media_id"


class _ResourceCheckStateExpectation(_CheckStateExpectation):
    data_type = ResourceInfo
    field_id = "resource_id"


class media_created(_MediaEventExpectation):
    def __init__(self, media_id=None, _timeout=None):
        super().__init__("media_created", media_id=media_id, _timeout=_timeout)

    def copy(self):
        return super().base_copy(self._id_value, self._timeout)


class media_removed(_MediaEventExpectation):
    def __init__(self, media_id=None, _timeout=None):
        super().__init__("media_removed", media_id=media_id, _timeout=_timeout)

    def copy(self):
        return super().base_copy(self._id_value, self._timeout)


class all_media_removed(_MediaEventExpectationBase):
    def __init__(self, _timeout=None):
        super().__init__("all_media_removed", _timeout=_timeout)

    def copy(self):
        return super().base_copy(self._timeout)


class resource_created(_ResourceEventExpectation):
    def __init__(self, resource_id=None, _timeout=None):
        super().__init__("resource_created", resource_id=resource_id, _timeout=_timeout)

    def copy(self):
        return super().base_copy(self._id_value, self._timeout)


class resource_removed(_ResourceEventExpectation):
    def __init__(self, resource_id=None, _timeout=None):
        super().__init__("resource_removed", resource_id=resource_id, _timeout=_timeout)

    def copy(self):
        return super().base_copy(self._id_value, self._timeout)


class resource_downloaded(_ResourceEventExpectation):
    def __init__(self, resource_id=None, _timeout=None, is_thumbnail=None):
        super().__init__(
            "resource_downloaded",
            resource_id=resource_id,
            _timeout=_timeout,
            is_thumbnail=is_thumbnail,
        )

    def copy(self):
        return super().base_copy(self._id_value, self._timeout)


def media_present(media_id, _timeout=None, _policy="check_wait"):
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _MediaCheckStateExpectation(id_=media_id)
    elif policy == ExpectPolicy.wait:
        return media_created(media_id=media_id, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _MediaCheckStateExpectation(id_=media_id),
            media_created(media_id=media_id, _timeout=_timeout),
        )


def resource_present(resource_id, _timeout=None, _policy="check_wait"):
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _ResourceCheckStateExpectation(id_=resource_id)
    elif policy == ExpectPolicy.wait:
        return resource_created(resource_id=resource_id, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _ResourceCheckStateExpectation(id_=resource_id),
            resource_created(resource_id=resource_id, _timeout=_timeout),
        )


class indexing_state_changed(_MediaEventExpectationBase):
    def __init__(self, new_state=None, old_state=None, _timeout=None):
        super().__init__("indexing_state_changed", _timeout=_timeout)
        if new_state is not None:
            self._new_state = IndexingState(new_state)
        else:
            self._new_state = None
        if old_state is not None:
            self._old_state = IndexingState(old_state)
        else:
            self._old_state = None

    def check(self, media_event, *args, **kwds):
        if self._success:
            return self
        if not isinstance(media_event, MediaEvent):
            return self
        if media_event.name != self._event_name:
            return self
        if self._new_state is not None and self._new_state is not IndexingState(
            media_event.data["new_state"]
        ):
            return self
        elif self._old_state is not None and self._old_state is not IndexingState(
            media_event.data["old_state"]
        ):
            return self
        else:
            self._success = True
            return self


class _IndexingStateCheck(Expectation):
    def __init__(self, state):
        super().__init__()
        self._expected_state = IndexingState(state)
        self._expected_event = MediaEvent(
            "indexing_state_changed", {"new_state": state}
        )

    def copy(self):
        return super().base_copy(self._expected_state)

    def check(self, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media = scheduler.context("olympe.media")
        if media.indexing_state is self._expected_state:
            self.set_success()
        else:
            self.cancel()
        return

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


def indexing_state(state, _timeout=None, _policy="check_wait"):
    state = IndexingState(state)
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _IndexingStateCheck(state)
    elif policy == ExpectPolicy.wait:
        return indexing_state_changed(new_state=state, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _IndexingStateCheck(state),
            indexing_state_changed(new_state=state, _timeout=_timeout),
        )


class _RESTExpectation(Expectation):
    def __init__(self, rest_name, rest_method, **rest_args):
        super().__init__()
        self._rest_name = rest_name
        self._rest_method = rest_method
        self._rest_args = rest_args
        self._expected_event = _MediaCommand(self._rest_name, rest_args)

    def copy(self):
        return super().base_copy(self._rest_name, self._rest_method, **self._rest_args)

    def check(self, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        if not self._awaited:
            super()._schedule(scheduler)
            media = scheduler.context("olympe.media")
            rest_method = getattr(media, self._rest_method)
            try:
                if rest_method(**self._rest_args) is not None:
                    self.set_success()
                else:
                    self.cancel()
            except Exception as e:
                self.set_exception(e)

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class _RESTDeleteMediaExpectation(_RESTExpectation):
    def __init__(self, media_id):
        super().__init__("delete_media", "_delete_media", media_id=media_id)


class _RESTDeleteAllMediaExpectation(_RESTExpectation):
    def __init__(self):
        super().__init__("delete_all_media", "_delete_all_media")


class _download_resource(Expectation):

    always_monitor = True

    def __init__(
        self, resource_id, download_dir=None, integrity_check=None, thumbnail=False
    ):
        super().__init__()
        self._download_dir = download_dir
        self._resource_id = resource_id
        self._integrity_check = integrity_check
        self._thumbnail = thumbnail
        self._expected_event = _MediaCommand(
            "download_resource", dict(resource_id=resource_id)
        )
        self._resource_path = None
        self._resource_size = None
        self._media = None
        self._fd = None
        self._sock = None
        self._response = None
        self._resource = None
        self._resource_file = None
        self._md5 = hashlib.md5()
        self._download_status = None
        self._downloaded_size = 0
        self._downloaded_percent = 0
        self.add_done_callback(self._on_done)

    def _on_done(self, _):
        if self._resource_file is not None:
            self._resource_file.close()

    def copy(self):
        return super().base_copy(
            self._download_dir,
            self._resource_id,
            self._integrity_check,
            self._thumbnail,
        )

    def _schedule(self, scheduler):
        if self._awaited:
            return
        super()._schedule(scheduler)
        self._media = scheduler.context("olympe.media")
        if self._download_dir is None:
            self._download_dir = self._media._download_dir
        if self._download_dir is None:
            self._media._logging(
                "Cannot download resource {}, "
                "there is no download directory set".format(self._resource_id)
            )
            self.cancel()
            return
        if self._thumbnail:
            self._download_dir = os.path.join(self._download_dir, "thumbnails")
        self._resource_path = os.path.join(self._download_dir, self._resource_id)

        if self._integrity_check is None:
            self._integrity_check = self._media._integrity_check or False

        # There is no integrity check available for thumbnails because the drone
        # doesn't expose md5 for them
        self._integrity_check = not self._thumbnail and self._integrity_check
        # The `_start_downloading` method is using some blocking API so we
        # have to run it from the media pomp_loop thread since we can't block
        # the scheduler thread here.
        self._media._pomp_loop_thread.run_async(self._start_downloading)

    @callback_decorator()
    def _start_downloading(self):
        download_dir = str(self._download_dir)
        resource_id = self._resource_id
        if not os.path.exists(download_dir):
            os.mkdir(download_dir)

        # Download the resource in chunk and compute the checksum on the fly if
        # requested

        # Getting the md5 might need a blocking HTTP request here
        self._resource = self._media.resource_info(
            resource_id=resource_id, with_md5=self._integrity_check
        )
        if self._resource is None:
            self._media._logging.error(
                "Unknown resource {}".format(resource_id))
            self.cancel()
            return
        try:
            self._resource_file = open(self._resource_path, "wb")
        except OSError as e:
            self._media._logging.error(
                "Failed to open {} for writing: {}".format(self._resource_path, e)
            )
            self.cancel()
            return

        # The python-requests API is blocking but we want to support multiple
        # opened HTTP connection progressing in parallel (for example: one or
        # more long running media download and a sequence of small HTTP
        # request/websocket events) without spawning additional threads.
        # We are going to hijack requests/urllib3/http.client underlying socket
        # to perform the download asynchronously. This is OK-ish since urllib3
        # doesn't handle neither HTTP/1.1 pipelines nor HTTP/2 (this socket is
        # currently dedicated to our HTTP request).
        # Here we download the resource (HTTP response content) asynchronously
        # but we are still using a blocking API for sending and receiving the
        # HTTP request and HTTP response headers.
        # We could be going in a fully non-blocking mode by hacking
        # requests/urllib3/http.client to make them use a custom socket object
        # with an overridden "socket.makefile" that would return prefetched HTTP
        # response headers.... Cool, but I don't think this would buy us much.
        # This non-trivial and potentially fragile development **might**
        # improve the HTTP/websocket media API performance on a poor connection
        # though. We will be keeping things "simple" for now: single-thread,
        # asynchronous/partially blocking implementation.
        if not self._thumbnail:
            self._response, chunks = self._media._stream_resource(
                resource_id=resource_id, chunk_size=4096
            )
        else:
            self._response, chunks = self._media._stream_thumbnail(
                resource_id=resource_id, chunk_size=4096
            )
        # We will have to restitute this socket unharmed to the urllib3
        # connection pool but in the meantime we will set it as non-blocking
        self._fd = self._response.raw.fileno()
        self._sock = socket.fromfd(
            self._fd, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        self._sock.setblocking(False)
        self._resource_size = int(self._response.headers["Content-length"])
        if not self._thumbnail and self._resource_size != self._resource.size:
            self._media._logging.warning(
                "HTTP response Content-length header for {} is not coherent "
                "with the media resource size as returned by the media REST API, "
                "expected {} and got {}".format(
                    self._resource_id, self._resource.size, self._resource_size
                )
            )

        # http.client may already have inadvertently buffered some HTTP response
        # content data right after the response headers. We have to check there
        # first for their presence!
        initial_chunk = self._response.raw._fp.fp.read()
        if initial_chunk is not None and self._write_chunk(initial_chunk):
            # We're already done with this socket, the whole HTTP response
            # content was buffered and has been written to disk.
            # self._write_chunk has already released the connection to the
            # urllib pool.
            pass
        else:
            # If there is no initial buffered data retrieved or if we are still
            # waiting for more data, let's register an asynchronous input event
            # callback on this socket
            events = PompEvent.IN | PompEvent.ERR | PompEvent.HUP
            self._media._logging.info(
                "Start downloading {} {}".format(
                    self._resource.resource_id, "thumbnail" if self._thumbnail else ""
                )
            )
            self._media._pomp_loop_thread.add_fd_to_loop(
                self._fd,
                lambda fd, event, userdata: self._download_resource_cb(event),
                events,
            )

    def _release_conn(self):
        try:
            # Restore the original socket blocking behavior
            self._sock.setblocking(True)
            # The following only close the duplicated socket fd on our side
            self._sock.close()
        except OSError:
            self._media._logging.exception(
                "Failed to release socket {}".format(self._fd))
        # The original socket (that has been dup'ed) is still owned by the
        # urllib connection pool urllib will decide what to do with this
        # connection and this is not our business
        self._response.raw.release_conn()

    @callback_decorator()
    def _download_resource_cb(self, event):
        event = PompEvent(event)
        if event is not PompEvent.IN:
            self._media._logging.error(
                "Unexpected resource download event {} {}".format(
                    self._resource_id, event
                )
            )
            self._media._pomp_loop_thread.remove_fd_from_loop(self._fd)
            self._release_conn()
            self.cancel()
            return
        while True:
            remaining_bytes = self._resource_size - self._downloaded_size
            try:
                chunk = self._sock.recv(min(4096, remaining_bytes))
            except BlockingIOError:
                break
            if not chunk:
                self._media._logging.error(
                    "Unexpected end of resource download {} at {}% "
                    "missing {} bytes".format(
                        self._resource_id, self._downloaded_percent, remaining_bytes
                    )
                )
                self._media._pomp_loop_thread.remove_fd_from_loop(self._fd)
                self._release_conn()
                self.cancel()
                return
            if self._write_chunk(chunk):
                return

    def _write_chunk(self, chunk):
        self._resource_file.write(chunk)
        if self._integrity_check:
            self._md5.update(chunk)
        self._downloaded_size += len(chunk)
        percent = int(100 * (self._downloaded_size / self._resource_size))
        if percent > self._downloaded_percent:
            self._downloaded_percent = percent
            self._media._logging.debug(
                "Downloading {} {} {}%".format(
                    self._resource.resource_id,
                    "thumbnail" if self._thumbnail else "",
                    self._downloaded_percent,
                )
            )
        if self._downloaded_size < self._resource_size:
            return False
        self._release_conn()
        if self._integrity_check:
            md5 = self._md5.hexdigest()
            md5_ref = self._resource.md5
            resource_id = self._resource.resource_id
            with open(os.path.join(self._download_dir, resource_id + ".md5"), "w") as f:
                f.write(md5_ref + " " + resource_id)
            if md5 != md5_ref:
                self._media._logging.error(
                    "Download media integrity check failed for {}".format(resource_id)
                )
                self._media._pomp_loop_thread.remove_fd_from_loop(self._fd)
                self.cancel()
                return False
        self._media._logging.info(
            "Download {} {} 100% done".format(
                self._resource_id, "thumbnail" if self._thumbnail else ""
            )
        )
        self._media._pomp_loop_thread.remove_fd_from_loop(self._fd)
        event = MediaEvent(
            "resource_downloaded",
            {
                "media_id": self._resource.media_id,
                "resource_id": self._resource.resource_id,
                "md5": self._resource.md5,
                "download_path": self._resource_path,
                "is_thumbnail": self._thumbnail,
            },
        )
        self._media._process_event(event)
        self.set_success()
        return True

    def check(self, *args, **kwds):
        return self

    def expected_events(self):
        return EventContext([self._expected_event])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class download_resource(_download_resource):
    def __init__(self, resource_id, download_dir=None, integrity_check=None):
        super().__init__(
            resource_id,
            download_dir=download_dir,
            integrity_check=integrity_check,
            thumbnail=False,
        )


class download_resource_thumbnail(_download_resource):
    def __init__(self, resource_id, download_dir=None, integrity_check=None):
        super().__init__(
            resource_id,
            download_dir=download_dir,
            integrity_check=integrity_check,
            thumbnail=True,
        )


class MultipleDownloadMixin(MultipleExpectation):

    always_monitor = True

    def timedout(self):
        if super().timedout():
            return True
        elif any(map(lambda e: e.timedout(), self.expectations)):
            self.set_timedout()
        return super().timedout()

    def cancelled(self):
        if super().cancelled():
            return True
        elif any(map(lambda e: e.cancelled(), self.expectations)):
            self.cancel()
            return True
        else:
            return False

    def check(self, *args, **kwds):
        for expectation in self.expectations:
            if expectation.check(*args, **kwds).success():
                self.matched_expectations.add(expectation)

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def _combine_method(self):
        return "&"


class download_media(MultipleDownloadMixin):
    def __init__(self, media_id, download_dir=None, integrity_check=None):
        self._download_dir = download_dir
        self._media_id = media_id
        self._integrity_check = integrity_check
        super().__init__()

    def copy(self):
        return super().base_copy(
            self._download_dir, self._media_id, self._integrity_check
        )

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media_context = scheduler.context("olympe.media")
        media = media_context._get_media(self._media_id)
        for resource_id in media.resources.keys():
            self.expectations.append(
                download_resource(
                    resource_id,
                    download_dir=self._download_dir,
                    integrity_check=self._integrity_check,
                )
            )
            scheduler._schedule(self.expectations[-1])


class download_media_thumbnail(MultipleDownloadMixin):
    def __init__(self, media_id, download_dir=None):
        self._download_dir = download_dir
        self._media_id = media_id
        super().__init__()

    def copy(self):
        return super().base_copy(self._download_dir, self._media_id)

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media_context = scheduler.context("olympe.media")
        media = media_context._get_media(self._media_id)
        for resource_id in media.resources.keys():
            self.expectations.append(
                download_resource_thumbnail(
                    resource_id, download_dir=self._download_dir
                )
            )
            scheduler._schedule(self.expectations[-1])


def delete_media(media_id, _timeout=None, _no_expect=False):
    if _no_expect:
        return _RESTDeleteMediaExpectation(media_id=media_id)
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteMediaExpectation(media_id=media_id),
                media_removed(media_id=media_id, _timeout=_timeout),
            ]
        )


def delete_all_media(_timeout=None, _no_expect=False):
    if _no_expect:
        return _RESTDeleteAllMediaExpectation()
    else:
        return WhenAllExpectations(
            [_RESTDeleteAllMediaExpectation(), all_media_removed(_timeout=_timeout)]
        )


class MediaSchedulerMixin(StreamSchedulerMixin):

    __slots__ = ()

    def __init__(self, *args, **kwds):
        StreamSchedulerMixin.__init__(
            self, *args, stream_timeout=kwds.pop("stream_timeout", 60), **kwds
        )

    @callback_decorator()
    def _schedule(self, expectations, **kwds):
        if isinstance(expectations, _download_resource):
            # We don't want to download too many resources in parallel so
            # we use the StreamSchedulerMixin as a queuing discipline for this
            # kind of expectations objects.
            super()._schedule(expectations, **kwds)
        else:
            # other types of expectations inherit from the default scheduler
            # behavior
            super(StreamSchedulerMixin, self)._schedule(expectations, **kwds)
        return expectations

    def wait_for_pending_downloads(self, timeout=None):
        super().stream_join(timeout=timeout)
        download = resource_downloaded()
        self.schedule(download)
        download.wait(_timeout=1.0)


class Media(AbstractScheduler):
    """
    Drone Media API class

    This class automatically connects to the drone web media interface (REST and
    websocket API) and synchronizes the drone media information in a background
    thread.

    Media info:
      - :py:func:`~olympe.Media.media_info`,
      - :py:func:`~olympe.Media.resource_info`,
      - :py:func:`~olympe.Media.list_media`,
      - :py:func:`~olympe.Media.list_resources`,
      - :py:func:`~olympe.Media.indexing_state`)

    Media monitoring with the Subscriber/listener API:
      - :py:func:`~olympe.Media.subscribe`
      - :py:func:`~olympe.Media.unsubscribe`

    See usage example in doc/examples/media.py
    """

    # pool_maxsize: the maximum number of HTTP request TCP connection in the
    # pool. Since the websocket TCP connection is not part of the connection
    # pool, this class opens a maximum of `pool_maxsize` + 1 active TCP
    # connections.
    pool_maxsize = 3

    def __init__(
        self,
        hostname,
        version=1,
        name=None,
        device_name=None,
        scheduler=None,
        download_dir=None,
        integrity_check=None,
    ):
        self._hostname = hostname
        self._version = version
        self._name = name
        self._device_name = device_name
        if self._name is not None:
            self._logging = getLogger("olympe.{}.media".format(self._name))
        elif self._device_name is not None:
            self._logging = getLogger("olympe.media.{}".format(self._device_name))
        else:
            self._logging = getLogger("olympe.media")

        # REST API endpoints
        self._media_api_url = "http://{}/api/v{}/media/medias".format(
            self._hostname, self._version
        )
        self._resources_url = "http://{}/data/media".format(self._hostname)
        self._thumbnails_api_url = "http://{}/data/thumbnails".format(self._hostname)
        self._md5_api_url = "http://{}/api/v{}/media/md5".format(
            self._hostname, self._version
        )
        self._websocket_url = "ws://{}/api/v{}/media/notifications".format(
            self._hostname, self._version
        )

        # Internal state
        self._media_state = None
        self._indexing_state = IndexingState.not_indexed
        self._download_dir = download_dir
        self._integrity_check = integrity_check
        self._session = requests.Session()
        self._websocket = None
        self._websocket_fd = None
        self._pomp_loop_thread = PompLoopThread(self._logging)

        self._pomp_loop_thread.register_cleanup(self._shutdown)

        if scheduler is not None:
            self._scheduler = scheduler
        else:
            self._scheduler = Scheduler(self._pomp_loop_thread, name=self._name)
        self._scheduler.add_context("olympe.media", self)
        self._scheduler.decorate(
            "MediaScheduler",
            MediaSchedulerMixin,
            max_parallel_processing=self.pool_maxsize,
        )
        self._pomp_loop_thread.start()

    def async_connect(self):
        return self._pomp_loop_thread.run_async(self._websocket_connect_cb)

    def async_disconnect(self):
        return self._pomp_loop_thread.run_async(self._websocket_disconnect_cb)

    def connect(self, timeout=5):
        return self.async_connect().result_or_cancel(timeout=timeout)

    def disconnect(self, timeout=5):
        return self.async_disconnect().result_or_cancel(timeout=timeout)

    def shutdown(self):
        """
        Properly close and stop the websocket connection and the media API
        background thread
        """
        # _shutdown is called by the pomp loop registered cleanup method
        self._pomp_loop_thread.stop()

    def _shutdown(self):
        self._websocket_disconnect_cb()
        if not self._scheduler.remove_context("olympe.media"):
            self._logging.info(
                "olympe.media expectation context has already been removed"
            )
        self._logging.info("olympe.media shutdown")

    @property
    def download_dir(self):
        return self._download_dir

    @download_dir.setter
    def download_dir(self, value):
        self._download_dir = value

    @property
    def integrity_check(self):
        return self._integrity_check

    @integrity_check.setter
    def integrity_check(self, value):
        self._integrity_check = value

    def wait_for_pending_downloads(self, timeout=None):
        self._scheduler.wait_for_pending_downloads(timeout=timeout)

    def _init_media_state(self):
        """
        Initialize the internal media state from the drone REST API
        """
        if self._media_state is not None:
            return True
        media_state = self._get_all_media()
        if media_state is None:
            return False
        else:
            self._media_state = media_state
            return True

    def _update_media_state(self, media_event):
        """
        Update the internal media state from a websocket media event
        """
        if media_event.name == "media_created":
            media_id, media = _make_media(media_event.data["media"])
            if not media_id:
                self._logging.error("Missing media_id in media_created event")
                return
            self._media_state[media_id] = media
        elif media_event.name == "resource_created":
            _, resource = _make_resource(media_event.data["resource"])
            if not resource.media_id or not resource.resource_id:
                self._logging.error(
                    "Missing media_id or resource_id in resource_created event"
                )
                return
            if resource.media_id not in self._media_state:
                self._logging.error(
                    "ResourceInfo created with a (yet) unknown media_id"
                )
                return
            self._media_state[resource.media_id].resources[
                resource.resource_id
            ] = resource
        elif media_event.name == "media_removed":
            if "media_id" not in media_event.data:
                self._logging.error("Missing media_id in media removed event message")
                return
            self._media_state.pop(media_event.data["media_id"], None)
        elif media_event.name == "resource_removed":
            if "resource_id" not in media_event.data:
                self._logging.error(
                    "Missing resource_id in resource removed event message"
                )
                return
            resource_id = media_event.data["resource_id"]
            for media in self._media_state.values():
                media.resources.pop(resource_id, None)
        elif media_event.name == "all_media_removed":
            self._media_state = OrderedDict()
        elif media_event.name == "indexing_state_changed":
            self._indexing_state = IndexingState(media_event.data["new_state"])
            if self._indexing_state is IndexingState.indexed and (
                not self._init_media_state()
            ):
                self._logging.error("Indexed media initialization failed")
        elif media_event.name == "resource_downloaded":
            try:
                resource = self.resource_info(resource_id=media_event.resource_id)
            except Exception:
                self._logging.exception("Unable to handle resource_downloaded event")
                return
            if resource is None:
                self._logging.error("Unable to handle resource_downloaded event")
                return
            media = self._media_state[resource.media_id]
            if not media_event.is_thumbnail:
                media.resources[resource.resource_id] = _replace_namedtuple(
                    resource, download_path=media_event.data["download_path"]
                )
            else:
                media.resources[resource.resource_id] = _replace_namedtuple(
                    resource, thumbnail_download_path=media_event.data["download_path"]
                )
        else:
            self._logging.error("Unknown event {}".format(media_event))

    def _websocket_exc_handler(method):
        def wrapper(self, *args, **kwds):
            try:
                return method(self, *args, **kwds)
            except (TimeoutError, websocket.WebSocketTimeoutException):
                self._logging.warning("Websocket timeout")
            except (ConnectionError, websocket.WebSocketException) as e:
                # If we lose the connection we must reinitialize our state
                self._logging.error(str(e))
                self._reset_state()
            except Exception as e:
                self._logging.exception("Websocket callback unhandled exception")
                self._reset_state()

        return wrapper

    def _reset_state(self):
        self._media_state = None
        if self._websocket_fd is not None:
            self._pomp_loop_thread.remove_fd_from_loop(self._websocket_fd)
            self._websocket_fd = None
        if self._websocket is not None:
            try:
                self._websocket.close()
            except websocket.WebSocketException:
                pass
            finally:
                self._websocket = None

    @_websocket_exc_handler
    def _websocket_connect_cb(self):
        """
        websocket timer callback
        """
        # This callback is called periodically by a background
        # pomp timer.

        # In case of websocket connection error, we will try to
        # re-establish the connection the next time.
        if self._websocket is None:
            try:
                self._websocket = websocket.create_connection(
                    self._websocket_url, timeout=2.0
                )
                self._websocket_fd = self._websocket.fileno()
            except (TimeoutError, websocket.WebSocketTimeoutException):
                self._logging.error("Websocket connection timeout")
                return False
            except (ConnectionError, websocket.WebSocketException):
                self._logging.exception("Websocket connection exception")
                return False

        # Initialize the media state from the REST API
        if not self._init_media_state():
            self._logging.warning("Media are not yet indexed")
        else:
            # If init_media_state succeeded, media resources are already indexed
            # but the websocket won't notify us with the `indexing_state_changed`
            # event so we have to fake it.
            event = MediaEvent(
                "indexing_state_changed",
                {"new_state": "INDEXED", "old_state": "NOT_INDEXED"},
            )
            self._process_event(event)

        if not self._pomp_loop_thread.has_fd(self._websocket_fd):
            # The connection is established: register a websocket event handler
            events = PompEvent.IN | PompEvent.ERR | PompEvent.HUP
            self._pomp_loop_thread.add_fd_to_loop(
                self._websocket_fd,
                lambda *args, **kwds: self._websocket_event_cb(*args, **kwds),
                events,
            )
        return True

    @_websocket_exc_handler
    def _websocket_disconnect_cb(self):
        try:
            if self._websocket:
                self._websocket.close()
        finally:
            self._websocket = None

    @_websocket_exc_handler
    def _websocket_event_cb(self, fd, event, userdata):
        event = PompEvent(event)
        if event is not PompEvent.IN:
            # HUP or ERR events: we must reset our state
            self._logging.warning("Websocket event {}".format(event))
            self._reset_state()
            return
        # We have an input event on the websocket
        data = self._websocket.recv()
        if not data:
            # No new event this time
            return
        # Parse and handle the received media event
        data = json.loads(data)
        event = MediaEvent(data["name"], data["data"])
        self._process_event(event)
        self._logging.info(str(event))

    def _process_event(self, event):
        self._update_media_state(event)
        self._scheduler.process_event(event)

    def __call__(self, expectations):
        """
        Olympe expectation DSL handler
        """
        return self.schedule(expectations)

    def schedule(self, expectations):
        self._scheduler.schedule(expectations)
        return expectations

    @property
    def indexing_state(self):
        """
        Returns the current media indexing state
        :rtype IndexingState:
        """
        return self._indexing_state

    def resource_info(self, media_id=None, resource_id=None, with_md5=False):
        """
        Returns a list resources info associated to a `media_id` or a specific
        resource info associated to a `resource_id`.
        This function raises a `ValueError` if `media_id` and `resource_id` are
        both left to `None`.

        :rtype: list(:py:class:`~olympe.ResourceInfo`) or
            :py:class:`~olympe.ResourceInfo`
        """
        if media_id is None and resource_id is None:
            raise ValueError("resource_info: missing media_id or resource_id")
        if self._media_state is None:
            raise RuntimeError(
                "resource_info: not currently connected the the drone media API"
            )
        try:
            if media_id is not None:
                media = self._media_state[media_id]
                if resource_id is None:
                    return list(media.resources.values())
                if not media.resources[resource_id].md5 and with_md5:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        md5=self._get_resource_md5(resource_id).md5,
                    )
                return media.resources[resource_id]
            else:
                for media in self._media_state.values():
                    if resource_id in media.resources:
                        if not media.resources[resource_id].md5 and with_md5:
                            media.resources[resource_id] = _replace_namedtuple(
                                media.resources[resource_id],
                                md5=self._get_resource_md5(resource_id).md5,
                            )
                        return media.resources[resource_id]
        except KeyError:
            self._logging.error(
                "No such media/resource: media_id={}, resource_id={}".format(
                    media_id, resource_id
                )
            )
        return None

    def media_info(self, media_id=None):
        """
        Returns a media info object if `media_id` is `None` or a list of
        all available media info otherwise.

        :rtype: list(:py:class:`~olympe.MediaInfo`) or :py:class:`~olympe.MediaInfo`
        """
        if media_id is not None:
            try:
                return self._media_state[media_id]
            except KeyError:
                self._logging.error("No such media: media_id={}".format(media_id))
                return None
        else:
            return self._media_state

    def list_media(self):
        """
        Returns a list of all available media id
        """
        return list(self._media_state.keys())

    def list_resources(self, media_id=None):
        """
        Returns a list of all available resource id if media_id is `None` or a
        list of resource id associated to the given media_id otherwise.
        """
        if media_id is not None:
            media = self._media_state.get(media_id)
            if media:
                return list(media.resources.keys())
            else:
                return list()
        else:
            return list(
                resource_id
                for media in self._media_state.values()
                for resource_id in media.resources.keys()
            )

    def _get_all_media(self):
        """
        Returns an array of media objects from the REST API
        HTTP GET /api/v<version>/media/medias
        """
        response = self._session.get(self._media_api_url)
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            self._logging.error(str(e))
            return None
        except Exception as e:
            self._logging.error(str(e))
            return None
        data = response.json()
        media_state = OrderedDict()
        for media in data:
            media_id, media = _make_media(media)
            if not media_id:
                self._logging.error("Missing media_id in webserver response")
                continue
            media_state[media_id] = media
        return media_state

    def _get_media(self, media_id):
        """
        Returns a single media objects from the REST API
        HTTP GET /api/v<version>/media/medias/<media_id>
        """
        if media_id in self._media_state:
            # We shouldn't have to make this REST API call since the internal
            # media dictionary is maintained up to date thanks to the websocket
            # events
            return self._media_state[media_id]
        self._logging.warning(
            "Missing media_id {} in olympe media database".format(media_id)
        )
        response = self._session.get(os.path.join(self._media_api_url, media_id))
        response.raise_for_status()
        data = response.json()
        media_id, media = _make_media(data)
        if not media_id:
            self._logging.error(
                "Missing media_id {} in webserver response".format(media_id)
            )
            return None
        self._media_state[media_id] = media
        return media

    def _get_resource_md5(self, resource_id):
        """
        Returns a (media_id, resource_id, md5) namedtuple for the given resource_id
        from the REST API
        HTTP GET /api/v<version>/media/md5/<resource_id>
        """
        response = self._session.get(os.path.join(self._md5_api_url, resource_id))
        response.raise_for_status()
        data = response.json()
        return ResourceInfo(**data)

    def _delete_media(self, media_id):
        """
        Request the deletion of a single media through the REST API
        HTTP DELETE /api/v<version>/media/medias/<media_id>
        """
        response = self._session.delete(os.path.join(self._media_api_url, media_id))
        response.raise_for_status()
        return True

    def _delete_all_media(self):
        """
        Request the deletion of all media through the REST API
        HTTP DELETE /api/v<version>/media/medias
        """
        response = self._session.delete(self._media_api_url)
        response.raise_for_status()
        return True

    def _do_stream_resource(self, url, chunk_size):
        """
        Downloads a remote resource in chunk and returns the associated
        generator object
        """
        response = self._session.get(url, stream=True)
        response.raise_for_status()
        return response, response.iter_content(chunk_size=chunk_size)

    def _stream_resource(self, resource_id, chunk_size=4096):
        """
        Get a remote resource in chunk and returns the associated
        generator object
        """
        return self._do_stream_resource(
            os.path.join(self._resources_url, resource_id), chunk_size=chunk_size
        )

    def _stream_thumbnail(self, resource_id, chunk_size):
        """
        Get a remote resource thumbnail in chunk and returns the associated
        generator object
        """
        return self._do_stream_resource(
            os.path.join(self._thumbnails_api_url, resource_id), chunk_size=chunk_size
        )

    @property
    def scheduler(self):
        return self._scheduler

    def subscribe(self, *args, **kwds):
        """
        See: :py:func:`~olympe.expectations.Scheduler.subscribe`
        """
        return self._scheduler.subscribe(*args, **kwds)

    def unsubscribe(self, subscriber):
        """
        Unsubscribe a previously registered subscriber

        :param subscriber: the subscriber previously returned by
            :py:func:`~olympe.Media.subscribe`
        :type subscriber: Subscriber
        """
        return self._scheduler.unsubscribe(subscriber)

    def _subscriber_overrun(self, subscriber, event):
        self._scheduler._subscriber_overrun(subscriber, event)
