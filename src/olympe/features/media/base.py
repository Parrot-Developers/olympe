from collections import namedtuple, OrderedDict
from collections.abc import Mapping
from logging import getLogger
from typing import Optional, Tuple

from olympe.utils.namedtuple import _replace_namedtuple, _get_namedtuple_from_mapping

from .enums import MediaType, PhotoMode, PanoramaType, ResourceFormat
from .gps import GPS

# Rather use namedtuple instead of a class with properties (or a dataclass), because
# namedtuple is faster (tuple-like access instead of __dict__), lighter (does not need a
# __setattr__ for each property), has immutable property (which is what is wanted for the
# media, data fields will be provided by pdraw), can easily be compared to a media
# mapping and more compact (that does matter when handling so many items)
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
# Define default values for each attribute of a new MediaInfo to None. namedtuple doesn't
# allow to not fill in every attribute when instantiating. This enables to instantiate a
# MediaInfo instance without filling it each and every attribute
MediaInfo.__new__.__defaults__ = (None,) * len(MediaInfo._fields)
# Populate MediaInfo docstring
MediaInfo.__doc__ = (
    "Namedtuple class "
    # Auto generated doc by default, which is not enriched enough but is worth detailing
    # the signature
    + (MediaInfo.__doc__ or "UKNOWN DOC")  # None management
    # By-hand enriched doc
    + """

  - media_id (str): unique id of the media
  - type (:py:class:`~olympe.enums.MediaType`): type of the media
  - title (str): title of the media
  - datetime (str) :iso8601 datetime of the media
  - boot_date (str) :iso8601 datetime of the drone boot
  - flight_date (str) :iso8601 datetime of the flight
  - size (int): size (in bytes) of the media (total size of all its resources)
  - run_id (str): run id of the media
  - custom_id (str): custom id of the media
  - resources (list( :py:class:`~olympe.resource.ResourceInfo` )): resource list of
    the media
  - duration (int): duration (in milliseconds) of the video media (total
    duration of all its resources)
  - thumbnail (str): relative url to be used in a GET request to download the
    media thumbnail (if available)
  - gps (:py:class:`~olympe.gps.GPS`): gps coordinates of the media (if
    available)
  - video_mode (:py:class:`~olympe.enums.VideoMode`): video mode of the media
    (if available and media is a video)
  - photo_mode (:py:class:`~olympe.enums.PhotoMode`): photo mode of the media
    (if available and media is a photo)
  - panorama_type (panorama_type enum): panorama type of the media (if
    available, media is a photo and photo_mode is panorama)
  - expected_count (int): expected number of resources in the media (
    if available, media is a photo and photo_mode is panorama)
  - replay_url (str): media rtsp replay url (prefixed by
    `rtsp://drone.ip.address:rtsp_port/`)
  - thermal (bool): media includes resources with thermal metadata (if value is
    true)

"""
)


def make_media(media) -> Tuple[Optional[str], MediaInfo]:
    """Generate a MediaInfo structure based on a given media argument

    :param media: media to build as a MediaInfo
    :return: MediaInfo structured with its media id
    :rtype: Tuple[Optional[str], MediaInfo]
    """
    # If media is already mapped, returns the intersection of its attribute and the
    # attributes we expect for a MediaInfo
    if isinstance(media, Mapping):
        media = _get_namedtuple_from_mapping(media, MediaInfo)

    # If media has no id, it is not possible to construct a MediaInfo object from it
    if not media.media_id:
        return None, media

    # Store old resources
    resources = media.resources
    # Construct a new media with empty resource base, before filling in below resources field
    media = _replace_namedtuple(media, resources=OrderedDict())

    # Convert fields to enums
    if media.type in MediaType._value2member_map_:
        media = _replace_namedtuple(media, type=MediaType(media.type))
    if isinstance(media.gps, Mapping):
        gps = _get_namedtuple_from_mapping(media.gps, GPS)
        media = _replace_namedtuple(media, gps=gps)
    if media.photo_mode in PhotoMode._value2member_map_:
        media = _replace_namedtuple(media, photo_mode=PhotoMode(media.photo_mode))
    if media.panorama_type in PanoramaType._value2member_map_:
        media = _replace_namedtuple(
            media, panorama_type=PanoramaType(media.panorama_type)
        )

    # Process resources if not None
    if resources is None:
        return media.media_id, media
    for resource in resources:
        # Build a resource
        resource_id, resource = make_resource(resource)
        if not resource_id:
            # Store resource with id only
            getLogger("olympe.media").error("Missing resource_id in webserver response")
            continue
        media.resources[resource_id] = resource

    # Return newly normalized media object
    return media.media_id, media


# Rather use namedtuple instead of a class with properties (or a dataclass), because
# namedtuple is faster (tuple-like access instead of __dict__), lighter (does not need a
# __setattr__ for each property), has immutable property (which is what is wanted for the
# resources, data fields will be provided by pdraw), can easily be compared to a resource
# mapping and more compact (that does matter when handling so many items)
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
# Define default values for each attribute of a new ResourceInfo to None. namedtuple doesn't
# allow to not fill in every attribute when instantiating. This enables to instantiate a
# ResourceInfo instance without filling it each and every attribute
ResourceInfo.__new__.__defaults__ = (None,) * len(ResourceInfo._fields)
# Populate ResourceInfo docstring
ResourceInfo.__doc__ = (
    "Namedtuple class "
    # Auto generated doc by default, which is not enriched enough but is worth detailing
    # the signature
    + (ResourceInfo.__doc__ or "UKNOWN DOC")  # None management
    # By-hand enriched doc
    + """

  - media_id (str): unique id of the media
  - resource_id (str): unique id of the resource
  - type (:py:class:`~olympe.enums.MediaType`): type of the resource
  - path (str): path to the resource on the file system, relative to the storage root path
  - format (:py:class:`~olympe.enums.ResourceFormat`): format of the resource
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
  - gps (:py:class:`~olympe.gps.GPS`): gps coordinates of the media (if
    available)
  - width (int): width (in pixels) of the resource
  - height (int): height (in pixels) of the resource
  - replay_url (str): media rtsp replay url (prefixed by `rtsp://drone.ip.address:rtsp_port/`)
  - thermal (bool): media includes resources with thermal metadata (if value is
    true)
  - md5 (str): media md5 checksum (if resource is photo)
  - video_mode (:py:class:`~olympe.enums.VideoMode`): video mode of the resource (if
    available and resource is a video)
  - storage (str): storage where the resource is located

"""
)

# Fields of ResourceInfo that are populated locally by resource_downloaded events
# and are absent from the drone's REST API response. They must be preserved
# explicitly when _init_media_state() re-syncs from the REST API so that a
# transient re-sync does not silently discard download information that the
# caller already holds.
_LOCAL_RESOURCE_FIELDS = (
    "download_path",
    "download_md5_path",
    "thumbnail_download_path",
    "thumbnail_download_md5_path",
)


def make_resource(resource) -> Tuple[Optional[str], ResourceInfo]:
    """Generate a ResourceInfo structure based on a given resource argument

    :param resource: resource to build as a ResourceInfo
    :type resource: ResourceInfo structured with its resource id
    :rtype: Tuple[Optional[str], ResourceInfo]
    """
    # If resource is already mapped, returns the intersection of its attribute and the
    # attributes we expect for a ResourceInfo
    if isinstance(resource, Mapping):
        resource = _get_namedtuple_from_mapping(resource, ResourceInfo)

    # If resource has no id, it is not possible to construct a ResourceInfo object from it
    if not resource.resource_id:
        return None, resource

    # Convert fields to enums
    if resource.format in ResourceFormat._value2member_map_:
        resource = _replace_namedtuple(resource, format=ResourceFormat(resource.format))
    if isinstance(resource.gps, Mapping):
        gps = _get_namedtuple_from_mapping(resource.gps, GPS)
        resource = _replace_namedtuple(resource, gps=gps)

    # Return newly normalized resource object
    return resource.resource_id, resource
