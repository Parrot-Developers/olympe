from .base import MediaInfo, ResourceInfo
from .events import MediaEvent
from .expectations import (
    media_created,
    media_removed,
    all_media_removed,
    media_updated,
    storage_removed,
    media_present,
    delete_media,
    delete_all_media,
    delete_resource,
    DownloadMediaExpectation,
    DownloadThumbnailExpectation,
    resource_present,
    resource_created,
    resource_removed,
    resource_downloaded,
    resource_download_progress,
    download_resource,
    download_resource_thumbnail,
)
from .indexing_state import indexing_state
from .media import Media

# Media instance methods exposed at package level
media_info = Media.media_info
resource_info = Media.resource_info
list_media = Media.list_media
last_media = Media.last_media
list_resources = Media.list_resources

# Backward compatibility aliases
download_media = DownloadMediaExpectation
download_media_thumbnail = DownloadThumbnailExpectation

__all__ = [
    "Media",
    "MediaInfo",
    "media_info",
    "ResourceInfo",
    "resource_info",
    "MediaEvent",
    "media_created",
    "media_removed",
    "all_media_removed",
    "media_updated",
    "storage_removed",
    "media_present",
    "delete_media",
    "delete_all_media",
    "delete_resource",
    "list_media",
    "last_media",
    "list_resources",
    "DownloadMediaExpectation",
    "DownloadThumbnailExpectation",
    "resource_present",
    "resource_created",
    "resource_removed",
    "resource_downloaded",
    "resource_download_progress",
    "download_resource",
    "download_resource_thumbnail",
    "indexing_state",
]
