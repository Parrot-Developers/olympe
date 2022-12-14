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

from collections import deque, namedtuple, OrderedDict
from collections.abc import Mapping
from logging import getLogger
from .utils import callback_decorator
from .concurrent import (
    Loop, TimeoutError, CancelledError, Event as ConcurrentEvent, get_running_loop
)
from .event import Event, EventContext, EventMarker
from .expectations import (
    CheckWaitStateExpectation,
    ExpectPolicy,
    Expectation,
    FailedExpectation,
    MultipleExpectation,
    WhenAllExpectations,
)
from .http import Session, ConnectionClosedError, HTTPError
from .scheduler import AbstractScheduler, StreamSchedulerMixin, Scheduler
import aenum
import hashlib
import json
import os


MediaInfo = namedtuple(
    "MediaInfo",
    [
        "media_id",
        "type",
        "title",
        "datetime",
        "boot_date",
        "flight_date",
        "size",
        "run_id",
        "custom_id",
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
  - title (str): title of the media
  - datetime (str) :iso8601 datetime of the media
  - boot_date (str) :iso8601 datetime of the drone boot
  - flight_date (str) :iso8601 datetime of the flight
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


def _namedtuple_from_mapping(mapping, namedtuple_type):
    return namedtuple_type(
        **{k: mapping[k] for k in namedtuple_type._fields if k in mapping}
    )


def _make_media(media):
    """
    :param media: a media object dictionary
    :return: a tuple of media_id (int) and a
        :py:class:`~olympe.MediaInfo` object
    :rtype: tuple(int, :py:class:`~olympe.MediaInfo`)
    """
    if isinstance(media, Mapping):
        media = _namedtuple_from_mapping(media, MediaInfo)
    if not media.media_id:
        return None, media
    resources = media.resources
    media = _replace_namedtuple(media, resources=OrderedDict())
    if resources is None:
        return media.media_id, media
    for resource in resources:
        resource_id, resource = _make_resource(resource)
        if not resource_id:
            getLogger("olympe.media").error("Missing resource_id in webserver response")
            continue
        media.resources[resource_id] = resource
    if media.type in MediaType._value2member_map_:
        media = _replace_namedtuple(media, type=MediaType(media.type))
    if isinstance(media.gps, Mapping):
        gps = _namedtuple_from_mapping(media.gps, GPS)
        media = _replace_namedtuple(media, gps=gps)
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
    if isinstance(resource, Mapping):
        resource = _namedtuple_from_mapping(resource, ResourceInfo)
    if not resource.resource_id:
        return None, resource
    if resource.format in ResourceFormat._value2member_map_:
        resource = _replace_namedtuple(resource, format=ResourceFormat(resource.format))
    if isinstance(resource.gps, Mapping):
        gps = _namedtuple_from_mapping(resource.gps, GPS)
        resource = _replace_namedtuple(resource, gps=gps)
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
        "bracketing mode (take a burst of 3 or 5 frames with a different exposure)",
    )
    burst = "BURST", "burst mode (take burst of frames)"
    panorama = (
        "PANORAMA",
        "panorama mode (take successive set of photos from one hovering "
        "point while rotating)",
    )
    timelapse = (
        "TIMELAPSE",
        "timelapse mode (take successive set of photos at a specific frequency)",
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


class ResourceFormat(MediaEnumBase):
    _init_ = "value __doc__"
    dng = "DNG", "the resource is a dng photo"
    jpg = "JPG", "the resource is a jpg photo"
    mp4 = "MP4", "the resource is an mp4 video"


ResourceInfo = namedtuple(
    "ResourceInfo",
    [
        "media_id",
        "resource_id",
        "type",
        "path",
        "format",
        "datetime",
        "size",
        "url",
        "width",
        "height",
        "duration",
        "thumbnail",
        "preview",
        "signature",
        "gps",
        "video_mode",
        "replay_url",
        "thermal",
        "md5",
        "storage",
        "download_path",
        "download_md5_path",
        "thumbnail_download_path",
        "thumbnail_download_md5_path",
    ],
)

ResourceInfo.__new__.__defaults__ = (None,) * len(ResourceInfo._fields)
ResourceInfo.__doc__ = (
    "Namedtuple class "
    + ResourceInfo.__doc__
    + """
  - media_id (str): unique id of the media
  - resource_id (str): unique id of the resource
  - type ( :py:class:`~olympe.media.MediaType`): type of the resource
  - path (str): path to the resource on the file system, relative to the storage root path
  - format ( :py:class:`~olympe.media.ResourceFormat`): format of the resource
  - datetime (str): iso8601 datetime of the media
  - size (int): size (in bytes) of the media (total size of all its resources)
  - duration (int): duration (in milliseconds) of the video media (total
    duration of all its resources)
  - url (str): relative url to be used in a GET request to download the resource
  - thumbnail (str): relative url to be used in a GET request to download the
    resource thumbnail (if available)
  - preview (str): elative url to be used in a GET request to download the resource preview
    (if available)
  - signature (str): resource signature (optional)
  - gps (:py:class:`~olympe.media.GPS`): gps coordinates of the media (if
    available)
  - width (int): width (in pixels) of the resource
  - height (int): height (in pixels) of the resource
  - replay_url (str): media rtsp replay url (prefixed by `rtsp://drone.ip.address:rtsp_port/`)
  - thermal (bool): media includes resources with thermal metadata (if value is
    true)
  - md5 (str): media md5 checksum (if resource is photo)
  - video_mode (str): video mode of the resource (if available and resource is a video)
  - storage (str): storage where the resource is located

"""
)


class IndexingState(MediaEnumBase):
    _init_ = "value __doc__"
    not_indexed = (
        "NOT_INDEXED",
        "media are not indexed and no indexing is in progress "
        "(media requests will result in 541 error)",
    )
    indexing = (
        "INDEXING",
        "media indexing is in progress (media requests will result in 541 error)",
    )
    indexed = "INDEXED", "media are indexed (media requests are possible)"

    @classmethod
    def _missing_name_(cls, name):
        for member in cls:
            if member.name.lower() == name.lower():
                return member


class MediaEvent(Event):
    def __init__(self, name, data, policy=None, type_="media_event", error=None):
        super().__init__(policy=policy)
        self._name = name
        self._media = None
        self._resource = None
        self._type = type_
        self._error = error
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

    def copy(self):
        return self.__class__(
            self._name, self._data, policy=self._policy, type_=self._type, error=self._error
        )

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
            args += f", media_id={self._media_id}"
        if self._resource_id:
            args += f", resource_id={self._resource_id}"
        if self._is_thumbnail:
            args += f", is_thumbnail={self._is_thumbnail}"
        if "media" in self._data:
            media = self._data["media"]
            if "photo_mode" in media:
                args += f", photo_mode='{media['photo_mode']}'"
            if "panorama_type" in media:
                args += f", panorama_type='{media['panorama_type']}'"
        elif "resource" in self._data:
            resource = self._data["resource"]
            if "height" in resource:
                args += f", height={resource['height']}"
            if "width" in resource:
                args += f", width={resource['width']}"
            if "duration" in resource:
                args += f", duration={resource['duration']}"
            if "replay_url" in resource:
                args += f", replay_url='{resource['replay_url']}'"
        if self._error is not None:
            args += f", error={self._error}"
        return f"{self._type}(name={self._name}{args})"


class _MediaCommand(MediaEvent):
    def __init__(self, *args, **kwds):
        kwds["type_"] = "media_command"
        super().__init__(*args, **kwds)


class _MediaEventExpectationBase(Expectation):
    def __init__(
        self,
        event_name,
        id_field=None,
        id_value=None,
        data_context=None,
        _timeout=None,
        **kwds,
    ):
        super().__init__()
        self.set_timeout(_timeout)
        data = {**{id_field: id_value}, **kwds}
        if data_context is not None:
            data = {data_context: data}
        self._expected_event = MediaEvent(event_name, data)
        self._event_name = event_name
        self._id_field = id_field
        self._id_value = id_value
        self._data_context = data_context
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
        if self._id_field is None or self._id_value is None:
            self._matched_event = media_event
            self.set_success()
            return self
        received_data = self._get_data(media_event)
        expected_data = self._get_data(self._expected_event)
        if received_data[self._id_field] == self._id_value:
            for name, value in expected_data.items():
                if value is not None and (
                    name not in received_data or received_data[name] != value
                ):
                    break
            else:
                self._matched_event = media_event
                self.set_success()
        return self

    def _get_data(self, media_event):
        if self._data_context is None:
            return media_event.data
        return media_event.data[self._data_context]

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
    def __init__(
        self, event_name, data_context=None, media_id=None, _timeout=None, **kwds
    ):
        return super().__init__(
            event_name,
            id_field="media_id",
            id_value=media_id,
            data_context=data_context,
            _timeout=_timeout,
            **kwds,
        )

    def copy(self):
        return super().base_copy(self._event_name, self._id_value, self._timeout)


class _ResourceEventExpectation(_MediaEventExpectationBase):
    def __init__(
        self, event_name, data_context=None, resource_id=None, _timeout=None, **kwds
    ):
        return super().__init__(
            event_name,
            id_field="resource_id",
            id_value=resource_id,
            data_context=data_context,
            _timeout=_timeout,
            **kwds,
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
        super().__init__(
            "media_created", data_context="media", media_id=media_id, _timeout=_timeout
        )

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
    def __init__(self, resource_id=None, media_id=None, _timeout=None):
        super().__init__(
            "resource_created",
            data_context="resource",
            resource_id=resource_id,
            media_id=media_id,
            _timeout=_timeout,
        )

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

    always_monitor = True

    def __init__(self, rest_name, rest_method, *, _timeout=None, **rest_args):
        super().__init__()
        self.set_timeout(_timeout)
        self._rest_name = rest_name
        self._rest_method = rest_method
        self._rest_args = rest_args
        self._expected_event = _MediaCommand(self._rest_name, rest_args)

    def copy(self):
        return super().base_copy(
            self._rest_name,
            self._rest_method,
            _timeout=self._timeout,
            **self._rest_args,
        )

    def check(self, *args, **kwds):
        return self

    def _schedule(self, scheduler):
        if not self._awaited:
            super()._schedule(scheduler)
            media = scheduler.context("olympe.media")
            media._scheduler.expectation_loop.run_async(self._aschedule, scheduler)

    async def _do_request(self, media):
        rest_method = getattr(media, self._rest_method)
        try:
            if await rest_method(timeout=self._timeout, **self._rest_args) is not None:
                self.set_success()
            else:
                self.cancel()
        except Exception as e:
            self.set_exception(e)

    async def _aschedule(self, scheduler):
        media = scheduler.context("olympe.media")
        media._loop.run_async(self._do_request, media)

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

    def marked_events(self, default_marked_events=EventMarker.unmatched):
        """
        Returns a collection of events with matched/unmatched markers.
        """
        if self._success:
            return self.expected_events()._set_marker(EventMarker.matched)
        elif self.done() and self.exception():
            error_event = self._expected_event.copy()
            error_event._error = repr(self.exception())
            return EventContext([error_event])._set_marker(default_marked_events)
        else:
            return self.expected_events()._set_marker(default_marked_events)


class _RESTDeleteResourceExpectation(_RESTExpectation):
    def __init__(self, resource_id, _timeout=None):
        super().__init__(
            "delete_resource",
            "_delete_resource",
            resource_id=resource_id,
            _timeout=_timeout,
        )


class _RESTDeleteMediaExpectation(_RESTExpectation):
    def __init__(self, media_id, _timeout=None):
        super().__init__(
            "delete_media", "_delete_media", media_id=media_id, _timeout=_timeout
        )


class _RESTDeleteAllMediaExpectation(_RESTExpectation):
    def __init__(self, _timeout=None):
        super().__init__("delete_all_media", "_delete_all_media", _timeout=_timeout)


class ResourceDownloadProgressEvent(Event):
    def __init__(self, resource_id, download_percent, policy=None):
        super().__init__(policy=policy)
        self.resource_id = resource_id
        self.download_percent = download_percent

    def copy(self):
        return self.__class__(
            self.resource_id, self.download_percent, policy=self._policy
        )

    def __str__(self):
        return ("resource_downloaded_progress_event("
                f"resource_id={self.resource_id}, downloaded_percent={self.download_percent})")


class resource_download_progress(Expectation):
    def __init__(self, resource_id=None, downloaded_percent=None):
        super().__init__()
        self.resource_id = resource_id
        self.downloaded_percent = downloaded_percent
        self._expected_event = ResourceDownloadProgressEvent(
            self.resource_id, self.downloaded_percent)

    def copy(self):
        return super().base_copy(self.resource_id, self.downloaded_percent)

    def check(self, resource_download_progress_event, *args, **kwds):
        if not isinstance(resource_download_progress_event, ResourceDownloadProgressEvent):
            return self
        if self.resource_id is None:
            self.set_success()
            return self
        if self.resource_id != resource_download_progress_event.resource_id:
            return self
        self.set_success()
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


class _download_resource(Expectation):

    always_monitor = True

    def __init__(
        self,
        resource_id,
        download_dir=None,
        integrity_check=None,
        thumbnail=False,
        _timeout=None,
    ):
        super().__init__()
        self.set_timeout(_timeout)
        self._download_dir = download_dir
        self._resource_id = resource_id
        self._integrity_check = integrity_check
        self._thumbnail = thumbnail
        self._expected_event = _MediaCommand(
            "download_resource", dict(resource_id=resource_id)
        )
        self._resource_path = None
        self._md5_path = None
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
        self._write_tasks = deque()

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
            self._media.logger.error(
                "Cannot download resource {}, "
                "there is no download directory set".format(self._resource_id)
            )
            self.cancel()
            return
        if self._thumbnail:
            self._download_dir = os.path.join(self._download_dir, "thumbnails")

        if self._integrity_check is None:
            self._integrity_check = self._media._integrity_check or False

        # There is no integrity check available for thumbnails because the drone
        # doesn't expose md5 for them
        self._integrity_check = not self._thumbnail and self._integrity_check
        self._media._loop.run_async(self._adownload)

    async def _adownload(self):
        download_dir = str(self._download_dir)
        resource_id = self._resource_id
        if not os.path.exists(download_dir):
            os.mkdir(download_dir)

        # Download the resource in chunk and compute the checksum on the fly if
        # requested

        # Getting the md5 might need a blocking HTTP request here
        self._resource = await self._media.aresource_info(
            resource_id=resource_id,
            with_md5=self._integrity_check,
        )

        if self._resource is None:
            self._media.logger.error(f"Unknown resource {resource_id}")
            self.cancel()
            return
        if self.timedout():
            self._media.logger.error(
                f"Resource download {resource_id} timedout"
            )
            return

        if not self._thumbnail:
            self._response = await self._media._stream_resource(resource_id=resource_id)
        else:
            self._response = await self._media._stream_thumbnail(resource_id=resource_id)

        if self._response is None:
            self._media.logger.error(
                f"Resource download request failed for {resource_id} "
            )
            self.cancel()
            return

        if self.timedout():
            self._media.logger.error(
                f"Resource download {resource_id} timedout"
            )
            return

        if self._thumbnail:
            self._resource_path = os.path.join(
                self._download_dir, os.path.basename(self._resource.thumbnail)
            )
        else:
            self._resource_path = os.path.join(
                self._download_dir, os.path.basename(self._resource.url)
            )

        try:
            self._resource_file = open(self._resource_path, "wb")
        except OSError as e:
            self._media.logger.error(
                f"Failed to open {self._resource_path} for writing: {e}"
            )
            self.cancel()
            return

        try:
            self._resource_size = int(self._response.headers["content-length"])
            if not self._thumbnail and self._resource_size != self._resource.size:
                self._media.logger.warning(
                    "HTTP response Content-length header for {} is not coherent "
                    "with the media resource size as returned by the media REST API, "
                    "expected {} and got {}".format(
                        self._resource_id, self._resource.size, self._resource_size
                    )
                )
            async for chunk in self._response:
                await self._write_chunk(chunk)
                if self._downloaded_size < self._resource_size and self.timedout():
                    self._media.logger.error(
                        f"Resource download {resource_id} timedout"
                    )
                    return
        finally:
            if not await self._media._loop.complete_futures(*self._write_tasks, timeout=2.0):
                self._media.logger.error(
                    f"Resource {resource_id} disk i/o timedout"
                )
                self._resource_file.close()
                self._resource_file = None
                return
            self._resource_file.close()
            self._resource_file = None

        if self._downloaded_size < self._resource_size:
            self._media.logger.error(
                "Downloading {} {} {}% unexpected end of response".format(
                    self._resource.resource_id,
                    "thumbnail" if self._thumbnail else "",
                    self._downloaded_percent,
                )
            )
            self.cancel()
            return

        if self._integrity_check:
            md5 = self._md5.hexdigest()
            md5_ref = self._resource.md5
            resource_id = self._resource.resource_id
            self._md5_path = os.path.join(self._download_dir, resource_id + ".md5")
            with open(self._md5_path, "w") as f:
                await self._media._loop.run_in_executor(
                    f.write, md5_ref + " " + os.path.basename(self._resource.url)
                )
            if md5 != md5_ref:
                self._media.logger.error(
                    f"Download media integrity check failed for {resource_id}"
                )
                self._media._loop.remove_fd_from_loop(self._fd)
                self.cancel()
            self._media.logger.info(f"{resource_id} integrity check done")

        self._media.logger.info(
            "Download {} {} 100% done".format(
                self._resource_id, "thumbnail" if self._thumbnail else ""
            )
        )
        self._resource = self._resource._replace(download_path=self._resource_path)
        event = MediaEvent(
            "resource_downloaded",
            {
                "resource": self._resource,
                "media_id": self._resource.media_id,
                "resource_id": self._resource.resource_id,
                "md5": self._resource.md5,
                "download_path": self._resource_path,
                "download_md5_path": self._md5_path,
                "is_thumbnail": self._thumbnail,
            },
        )
        await self._media._process_event(event)
        self.set_success()

    async def _write_chunk(self, chunk):
        self._write_tasks.append(
            self._media._loop.run_in_executor(self._resource_file.write, chunk)
        )
        if self._integrity_check:
            self._md5.update(chunk)
        self._downloaded_size += len(chunk)
        percent = int(100 * (self._downloaded_size / self._resource_size))
        if percent > self._downloaded_percent:
            await self._media._process_event(
                ResourceDownloadProgressEvent(
                    self._resource.resource_id,
                    self._downloaded_percent
                )
            )
            self._downloaded_percent = percent
            self._media.logger.info(
                "Downloading {} {} {}%".format(
                    self._resource.resource_id,
                    "thumbnail" if self._thumbnail else "",
                    self._downloaded_percent,
                )
            )

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
    def __init__(
        self, resource_id, download_dir=None, integrity_check=None, _timeout=None
    ):
        super().__init__(
            resource_id,
            download_dir=download_dir,
            integrity_check=integrity_check,
            thumbnail=False,
            _timeout=_timeout,
        )

    def __getattr__(self, name):
        if self._resource is None:
            raise AttributeError(
                f"'{self.__class__.__name__}' has no attribute '{name}'"
            )
        else:
            return getattr(self._resource, name)


class download_resource_thumbnail(_download_resource):
    def __init__(
        self, resource_id, download_dir=None, integrity_check=None, _timeout=None
    ):
        super().__init__(
            resource_id,
            download_dir=download_dir,
            integrity_check=integrity_check,
            thumbnail=True,
            _timeout=_timeout,
        )


class MultipleDownloadMixin(MultipleExpectation):

    always_monitor = True
    _last_resource_grace_period = 1.

    def __init__(self):
        self._media = None
        self._media_event = ConcurrentEvent()
        self._last_resource_ts = None
        super().__init__()

    def timedout(self):
        if super().timedout() or any(map(lambda e: e.timedout(), self.expectations)):
            for e in self.expectations:
                if not e.done():
                    e.set_timedout()
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

        if self._last_resource_ts is None:
            return self

        if self._scheduler.time() < (self._last_resource_ts + self._last_resource_grace_period):
            return self

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return

        self.matched_expectations.add(expectation)
        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()

    def wait(self, _timeout=None):
        if self._scheduler is None:
            return self
        start_time = self._scheduler.time()
        media_context = self._scheduler.context("olympe.media")
        media_context._loop.run_async(
            self._await_impl).result_or_cancel(timeout=_timeout)
        end_time = self._scheduler.time()
        if _timeout is not None:
            _timeout -= end_time - start_time
        super().wait(_timeout=_timeout)
        return self

    async def _await_impl(self):
        await self._media_event.wait()
        grace_period = (
            self._last_resource_grace_period - (
                self._scheduler.time() - self._last_resource_ts)
        )
        if grace_period > 0:
            await get_running_loop().asleep(grace_period)

    def _combine_method(self):
        return "&"


class download_media(MultipleDownloadMixin):
    def __init__(
        self, media_id, download_dir=None, integrity_check=None, _timeout=None
    ):
        self._download_dir = download_dir
        self._media_id = media_id
        self._integrity_check = integrity_check
        self._media = None
        super().__init__()
        self.set_timeout(_timeout)

    def copy(self):
        return super().base_copy(
            self._download_dir, self._media_id, self._integrity_check, self._timeout
        )

    def _schedule(self, scheduler):
        media_context = scheduler.context("olympe.media")
        media_context.subscribe(
            lambda event, scheduler: scheduler.run(
                self._on_resource_created, event, scheduler
            ),
            resource_created(media_id=self._media_id),
        )
        super()._schedule(scheduler)
        media_context._loop.run_async(
            self._adownload_and_schedule,
            scheduler
        )

    async def _adownload_and_schedule(self, scheduler):
        media_context = scheduler.context("olympe.media")
        self._media = await media_context._get_media(self._media_id)
        self._last_resource_ts = scheduler.time()
        self._media_event.set()
        for resource_id in list(self._media.resources.keys()):
            self.expectations.append(
                (
                    lambda id_: download_resource(
                        id_,
                        download_dir=self._download_dir,
                        integrity_check=self._integrity_check,
                        _timeout=self._timeout,
                    )
                )(resource_id)
            )
            scheduler.schedule(self.expectations[-1])

    def __getattr__(self, name):
        if self._media is None:
            raise AttributeError(
                f"'{self.__class__.__name__}' has no attribute '{name}'"
            )
        else:
            return getattr(self._media, name)

    def _on_resource_created(self, event, scheduler):
        if self.success() or self.cancelled():
            return
        for resource in self.expectations:
            if resource._resource_id == event.resource_id:
                return
        last_resource_ts = scheduler.time()
        new_grace_period = 2 * (last_resource_ts - self._last_resource_ts)
        self._last_resource_ts = last_resource_ts
        self._last_resource_grace_period = max(
            new_grace_period, self._last_resource_grace_period)
        self.expectations.append(
            download_resource(
                event.resource_id,
                download_dir=self._download_dir,
                integrity_check=self._integrity_check,
                _timeout=self._timeout,
            )
        )
        scheduler._schedule(self.expectations[-1])


class download_media_thumbnail(MultipleDownloadMixin):
    def __init__(self, media_id, download_dir=None, _timeout=None):
        self._download_dir = download_dir
        self._media_id = media_id
        super().__init__()
        self.set_timeout(_timeout)

    def copy(self):
        return super().base_copy(self._download_dir, self._media_id, self._timeout)

    def _schedule(self, scheduler):
        media_context = scheduler.context("olympe.media")
        media_context.subscribe(
            lambda event, scheduler: scheduler.run(
                self._on_resource_created, event, scheduler
            ),
            resource_created(media_id=self._media_id),
        )
        super()._schedule(scheduler)
        media_context._loop.run_async(
            self._adownload_and_schedule, scheduler
        )

    async def _adownload_and_schedule(self, scheduler):
        media_context = scheduler.context("olympe.media")
        self._media = await media_context._get_media(self._media_id)
        self._last_resource_ts = scheduler.time()
        self._media_event.set()
        for resource_id in self._media.resources.keys():
            self.expectations.append(
                download_resource_thumbnail(
                    resource_id, download_dir=self._download_dir, _timeout=self._timeout
                )
            )
            scheduler.schedule(self.expectations[-1])

    def __getattr__(self, name):
        if self._media is None:
            raise AttributeError(
                f"'{self.__class__.__name__}' has no attribute '{name}'"
            )
        else:
            return getattr(self._media, name)

    def _on_resource_created(self, event, scheduler):
        if self.success() or self.cancelled():
            return
        for resource in self.expectations:
            if resource._resource_id == event.resource_id:
                return
        last_resource_ts = scheduler.time()
        new_grace_period = 2 * (last_resource_ts - self._last_resource_ts)
        self._last_resource_ts = last_resource_ts
        self._last_resource_grace_period = max(
            new_grace_period, self._last_resource_grace_period)
        self.expectations.append(
            download_resource_thumbnail(
                event.resource_id,
                download_dir=self._download_dir,
                _timeout=self._timeout,
            )
        )

        scheduler._schedule(self.expectations[-1])


def delete_resource(resource_id, _timeout=None, _no_expect=False):
    if _no_expect:
        return _RESTDeleteResourceExpectation(
            resource_id=resource_id, _timeout=_timeout
        )
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteResourceExpectation(
                    resource_id=resource_id, _timeout=_timeout
                ),
                resource_removed(resource_id=resource_id, _timeout=_timeout),
            ]
        )


def delete_media(media_id, _timeout=None, _no_expect=False):
    if _no_expect:
        return _RESTDeleteMediaExpectation(media_id=media_id, _timeout=_timeout)
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteMediaExpectation(media_id=media_id, _timeout=_timeout),
                media_removed(media_id=media_id, _timeout=_timeout),
            ]
        )


def delete_all_media(_timeout=None, _no_expect=False):
    if _no_expect:
        return _RESTDeleteAllMediaExpectation(_timeout=_timeout)
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteAllMediaExpectation(_timeout=_timeout),
                all_media_removed(_timeout=_timeout),
            ]
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
        deadline = None
        if timeout is not None:
            deadline = self._scheduler.time() + timeout
        super().stream_join(timeout=timeout)
        while True:
            download = resource_downloaded() | resource_download_progress()
            self.schedule(download)
            download.wait(_timeout=2.0)
            if not download:
                break
            if deadline is not None and self._scheduler.time() > deadline:
                break


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

    # HTTP connection or data read timeout
    timeout = 5.0

    def __init__(
        self,
        hostname=None,
        version=1,
        name=None,
        device_name=None,
        scheduler=None,
        download_dir=None,
        integrity_check=None,
    ):
        self._name = name
        self._device_name = device_name
        if self._name is not None:
            self.logger = getLogger(f"olympe.{self._name}.media")
        elif self._device_name is not None:
            self.logger = getLogger(f"olympe.media.{self._device_name}")
        else:
            self.logger = getLogger("olympe.media")

        self._version = version
        self.set_hostname(hostname)

        # Internal state
        self._media_state = None
        self._media_state_need_sync = True
        self._indexing_state = IndexingState.not_indexed
        self._download_dir = download_dir
        self._integrity_check = integrity_check
        # Loop max_workers below is set to 1 because the one and only worker is used to
        # sequentially write downloaded resources chunks. If more than one worker is used
        # chunks may be written to disk out of order.
        self._loop = Loop(self.logger, max_workers=1)
        self._session = Session(loop=self._loop)
        self._websocket = None

        self._loop.register_cleanup(self._shutdown)

        if scheduler is not None:
            self._scheduler = scheduler
        else:
            self._scheduler = Scheduler(self._loop, name=self._name)
        self._scheduler.add_context("olympe.media", self)
        self._scheduler.decorate(
            "MediaScheduler",
            MediaSchedulerMixin,
            max_parallel_processing=self.pool_maxsize,
        )
        self._loop.start()

    def get_hostname(self):
        return self._hostname

    def set_hostname(self, hostname):
        self._hostname = hostname
        if self._hostname is not None:
            # REST API endpoints
            self._base_url = f"http://{self._hostname}"
            self._api_url = f"{self._base_url}/api/v{self._version}"
            self._media_api_url = f"{self._api_url}/media/medias"
            self._resource_api_url = f"{self._api_url}/media/resources"
            self._md5_api_url = f"{self._api_url}/media/md5"
            self._websocket_url = "ws://{}/api/v{}/media/notifications".format(
                self._hostname, self._version
            )

    def async_connect(self, **kwds):
        return self._loop.run_async(self.aconnect)

    def async_disconnect(self):
        return self._loop.run_async(self.adisconnect)

    def connect(self, *, timeout=5, **kwds):
        try:
            return self.async_connect(**kwds).result_or_cancel(timeout=timeout)
        except (TimeoutError, CancelledError):
            return False

    def disconnect(self, *, timeout=5):
        try:
            return self.async_disconnect().result_or_cancel(timeout=timeout)
        except (TimeoutError, CancelledError):
            return False

    def shutdown(self):
        """
        Properly close and stop the websocket connection and the media API
        background thread
        """
        # _shutdown is called by the pomp loop registered cleanup method
        self._loop.stop()
        self._loop.destroy()
        return True

    async def _shutdown(self):
        self._loop.unregister_cleanup(self._shutdown)
        await self.adisconnect()
        if not self._scheduler.remove_context("olympe.media"):
            self.logger.info(
                "olympe.media expectation context has already been removed"
            )
        self.logger.info("olympe.media shutdown")

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

    async def _init_media_state(self):
        """
        Initialize the internal media state from the drone REST API
        """
        if self._media_state is not None and not self._media_state_need_sync:
            return True
        media_state = await self._get_all_media()
        if media_state is None:
            return False
        else:
            self._media_state = media_state
            self._media_state_need_sync = False
            return True

    async def _update_media_state(self, media_event):
        """
        Update the internal media state from a websocket media event
        """
        if not isinstance(media_event, MediaEvent):
            return
        if media_event.name == "media_created":
            media_id, media = _make_media(media_event.data["media"])
            if not media_id:
                self.logger.error("Missing media_id in media_created event")
                return
            self._media_state[media_id] = media
        elif media_event.name == "resource_created":
            _, resource = _make_resource(media_event.data["resource"])
            if not resource.media_id or not resource.resource_id:
                self.logger.error(
                    "Missing media_id or resource_id in resource_created event"
                )
                return
            if resource.media_id not in self._media_state:
                self.logger.error("ResourceInfo created with a (yet) unknown media_id")
                return
            self._media_state[resource.media_id].resources[
                resource.resource_id
            ] = resource
        elif media_event.name == "media_removed":
            if "media_id" not in media_event.data:
                self.logger.error("Missing media_id in media removed event message")
                return
            self._media_state.pop(media_event.data["media_id"], None)
        elif media_event.name == "resource_removed":
            if "resource_id" not in media_event.data:
                self.logger.error(
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
                not await self._init_media_state()
            ):
                self.logger.error("Indexed media initialization failed")
        elif media_event.name == "resource_downloaded":
            try:
                resource = self.resource_info(resource_id=media_event.resource_id)
            except Exception:
                self.logger.exception("Unable to handle resource_downloaded event")
                return
            if resource is None:
                self.logger.error("Unable to handle resource_downloaded event")
                return
            media = self._media_state[resource.media_id]
            if not media_event.is_thumbnail:
                media.resources[resource.resource_id] = _replace_namedtuple(
                    resource,
                    download_path=media_event.data["download_path"],
                    download_md5_path=media_event.data["download_md5_path"],
                )
            else:
                media.resources[resource.resource_id] = _replace_namedtuple(
                    resource,
                    thumbnail_download_path=media_event.data["download_path"],
                    thumbnail_download_md5_path=media_event.data["download_md5_path"],
                )
        else:
            self.logger.error(f"Unknown event {media_event}")

    def _websocket_exc_handler(method):
        async def wrapper(self, *args, **kwds):
            try:
                return await method(self, *args, **kwds)
            except ConnectionClosedError:
                self.logger.info("Websocket closed")
                self._websocket = None
                self._media_state_need_sync = True
            except TimeoutError:
                self.logger.warning("Websocket timeout")
            except ConnectionError as e:
                # If we lose the connection we must reinitialize our state
                self.logger.error(f"{type(e)}: {e}")
                self._media_state_need_sync = True
                self._websocket = None
            except Exception as e:
                self.logger.exception(f"Websocket callback unhandled exception: {e}")
                await self._areset_state()

        return wrapper

    async def _areset_state(self):
        self._media_state = None
        if self._websocket is not None:
            try:
                await self._websocket.aclose()
            except ConnectionError:
                pass
            finally:
                self._websocket = None

    def _reset_state(self):
        try:
            self._loop.run_async(self._areset_state).result_or_cancel(timeout=5.)
        except TimeoutError:
            pass

    @_websocket_exc_handler
    async def aconnect(self):
        if self._websocket is None:
            try:
                self._websocket = await self._session.websocket(self._websocket_url)
            except TimeoutError:
                self.logger.error("Websocket connection timeout")
                return False
            except (ConnectionError, OSError) as e:
                self.logger.error(f"Websocket connection error {e}")
                return False

            self._loop.run_later(self._websocket_event_reader)

        # Initialize the media state from the REST API
        if not await self._init_media_state():
            self.logger.warning("Media are not yet indexed")
        else:
            # If init_media_state succeeded, media resources are already indexed
            # but the websocket won't notify us with the `indexing_state_changed`
            # event so we have to fake it.
            event = MediaEvent(
                "indexing_state_changed",
                {"new_state": "INDEXED", "old_state": "NOT_INDEXED"},
            )
            await self._process_event(event)

        return True

    @_websocket_exc_handler
    async def adisconnect(self):
        try:
            if self._websocket:
                await self._websocket.aclose()
        except ConnectionError:
            pass
        finally:
            self._websocket = None

    @_websocket_exc_handler
    async def _websocket_event_reader(self):
        data = ''
        while self._websocket:
            chunk = await self._websocket.aread()
            if chunk is None:
                self.logger.info("websocket closed")
                await self._areset_state()
                return
            data += chunk
            # Parse and handle the received media event
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            data = ''
            event = MediaEvent(event["name"], event["data"])
            await self._process_event(event)
            self.logger.info(str(event))

    async def _process_event(self, event):
        await self._update_media_state(event)
        self._scheduler.process_event(event)

    def __call__(self, expectations):
        """
        Olympe expectation DSL handler
        """
        return self.schedule(expectations)

    def schedule_hook(self, expectations, **kwds):
        if not isinstance(expectations, _MediaEventExpectationBase):
            return None
        if self._websocket is None:
            return FailedExpectation("Not connected to any device")
        return None

    def schedule(self, expectations):
        return self._scheduler.schedule(expectations)

    @property
    def indexing_state(self):
        """
        Returns the current media indexing state
        :rtype IndexingState:
        """
        return self._indexing_state

    def resource_info(
        self, media_id=None, resource_id=None, with_md5=False, with_signature=False, timeout=None
    ):
        """
        Returns a list resources info associated to a `media_id` or a specific
        resource info associated to a `resource_id`.
        This function raises a `ValueError` if `media_id` and `resource_id` are
        both left to `None`.

        :rtype: list(:py:class:`~olympe.ResourceInfo`) or
            :py:class:`~olympe.ResourceInfo`
        """
        if timeout is None:
            timeout = 3.0
        return self._loop.run_async(
            self.aresource_info,
            media_id=media_id,
            resource_id=resource_id,
            with_md5=with_md5,
            with_signature=with_signature
        ).result_or_cancel(timeout=timeout)

    async def aresource_info(
        self, media_id=None, resource_id=None, with_md5=False, with_signature=False
    ):
        if media_id is None and resource_id is None:
            raise ValueError("resource_info: missing media_id or resource_id")
        if self._media_state is None:
            raise RuntimeError(
                "resource_info: not currently connected to the drone media API"
            )
        try:
            if media_id is not None:
                media = self._media_state[media_id]
                if resource_id is None:
                    return list(media.resources.values())
                if not media.resources[resource_id].md5 and with_md5:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        md5=(await self._get_resource_md5(resource_id)).md5,
                    )
                if media.resources[resource_id].signature and with_signature:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        signature=(await self._get_resource_signature(
                            media.resources[resource_id]
                        )).signature,
                    )
                else:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        signature=None,
                    )
                return media.resources[resource_id]
            else:
                for media in self._media_state.values():
                    if resource_id in media.resources:
                        if not media.resources[resource_id].md5 and with_md5:
                            media.resources[resource_id] = _replace_namedtuple(
                                media.resources[resource_id],
                                md5=(await self._get_resource_md5(resource_id)).md5,
                            )
                        return media.resources[resource_id]
        except KeyError:
            self.logger.error(
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
                self.logger.error(f"No such media: media_id={media_id}")
                return None
        else:
            return self._media_state

    def list_media(self):
        """
        Returns a list of all available media id
        """
        if self._media_state is not None:
            return list(self._media_state.keys())
        else:
            return list()

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

    def last_media(self):
        try:
            return list(sorted(self._media_state.items(), key=lambda t: t[0]))[-1][1]
        except IndexError:
            return None

    def last_media_id(self):
        last_media = self.last_media()
        if last_media is None:
            return "-1"
        return last_media.media_id

    def next_media_id(self):
        return str(int(self.last_media_id()) + 1)

    async def _get_all_media(self):
        """
        Returns an array of media objects from the REST API
        HTTP GET /api/v<version>/media/medias
        """
        response = await self._session.get(self._media_api_url, timeout=self.timeout)
        for _ in range(3):
            try:
                response.raise_for_status()
                data = await response.json()
            except HTTPError as e:
                self.logger.warning(str(e))
                await get_running_loop().asleep(1.0)
                continue
            except CancelledError:
                return None
            except Exception:
                self.logger.exception("Unhandled exception")
                return None
            else:
                break
        else:
            self.logger.error("The webserver is unavailable")
            return None
        media_state = OrderedDict()
        for media in data:
            media_id, media = _make_media(media)
            if not media_id:
                self.logger.error("Missing media_id in webserver response")
                continue
            media_state[media_id] = media
        return media_state

    async def _get_media(self, media_id):
        """
        Returns a single media objects from the REST API
        HTTP GET /api/v<version>/media/medias/<media_id>
        """
        if media_id in self._media_state:
            # We shouldn't have to make this REST API call since the internal
            # media dictionary is maintained up to date thanks to the websocket
            # events
            return self._media_state[media_id]
        self.logger.warning(
            f"Missing media_id {media_id} in olympe media database"
        )
        response = await self._session.get(
            os.path.join(self._media_api_url, media_id), timeout=self.timeout
        )
        response.raise_for_status()
        data = await response.json()
        media_id, media = _make_media(data)
        if not media_id:
            self.logger.error(
                f"Missing media_id {media_id} in webserver response"
            )
            return None
        self._media_state[media_id] = media
        return media

    async def _get_resource_md5(self, resource_id):
        """
        Returns a (media_id, resource_id, md5) namedtuple for the given resource_id
        from the REST API
        HTTP GET /api/v<version>/media/md5/<resource_id>
        """
        response = await self._session.get(
            os.path.join(self._md5_api_url, resource_id), timeout=self.timeout
        )
        response.raise_for_status()
        data = await response.json()
        return _namedtuple_from_mapping(data, ResourceInfo)

    async def _get_resource_signature(self, resource):
        """
        Returns a (media_id, resource_id, signature) namedtuple for the give resource
        """
        response = await self._session.get(resource.signature, timeout=self.timeout)
        response.raise_for_status()
        signature = await response.text()
        data = dict(
            media_id=resource.media_id,
            resource_id=resource.resource_id,
            signature=signature,
        )
        return _namedtuple_from_mapping(data, ResourceInfo)

    async def _delete_resource(self, resource_id, timeout=None):
        """
        Request the deletion of a single resource through the REST API
        HTTP DELETE /api/v<version>/media/resources/<resource_id>
        """
        if timeout is None:
            timeout = self.timeout
        response = await self._session.delete(
            os.path.join(self._resource_api_url, resource_id), timeout=timeout
        )
        response.raise_for_status()
        return True

    async def _delete_media(self, media_id, timeout=None):
        """
        Request the deletion of a single media through the REST API
        HTTP DELETE /api/v<version>/media/medias/<media_id>
        """
        if timeout is None:
            timeout = self.timeout
        response = await self._session.delete(
            os.path.join(self._media_api_url, media_id), timeout=timeout
        )
        response.raise_for_status()
        return True

    async def _delete_all_media(self, timeout=None):
        """
        Request the deletion of all media through the REST API
        HTTP DELETE /api/v<version>/media/medias
        """
        if timeout is None:
            timeout = self.timeout
        response = await self._session.delete(self._media_api_url, timeout=timeout)
        response.raise_for_status()
        return True

    async def _do_stream_resource(self, url, timeout=None):
        """
        Downloads a remote resource in chunk and returns the associated
        generator object
        """
        if timeout is None:
            timeout = self.timeout
        response = await self._session.get(url, timeout=timeout)
        try:
            response.raise_for_status()
        except Exception as e:
            self.logger.error(f"Failed to initiate download of {url}: {e}")
            return None
        return response

    async def _stream_resource(self, resource_id, timeout=None):
        """
        Get a remote resource in chunk and returns the associated
        generator object
        """
        resource_info = await self.aresource_info(resource_id=resource_id)
        if resource_info is None or not resource_info.url:
            return None, None

        return await self._do_stream_resource(
            f"{self._base_url}{resource_info.url}",
            timeout=timeout,
        )

    async def _stream_thumbnail(self, resource_id, timeout=None):
        """
        Get a remote resource thumbnail in chunk and returns the associated
        generator object
        """
        resource_info = await self.aresource_info(resource_id=resource_id)
        if resource_info is None or not resource_info.thumbnail:
            return None, None

        return await self._do_stream_resource(
            f"{self._base_url}{resource_info.thumbnail}",
            timeout=timeout,
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
