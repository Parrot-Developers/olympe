from uuid import UUID
from typing import Optional, Union, TYPE_CHECKING

from olympe.messaging.events import Event
from olympe.messaging.expectations import ExpectPolicy

from .base import make_media, make_resource

if TYPE_CHECKING:
    from .media import MediaInfo
    from .resource import ResourceInfo


class MediaEvent(Event):
    def __init__(
        self,
        name: str,
        data: dict,
        policy: Optional[ExpectPolicy] = None,
        type: str = "media_event",
        error=None,
    ):
        super().__init__(policy=policy)
        self._name = name
        self._type = type
        self._error = error

        from .media import MediaInfo
        from .resource import ResourceInfo

        self._media: Optional["MediaInfo"] = None
        self._media_id: Optional[str] = None
        self._resource: Optional["ResourceInfo"] = None
        self._resource_id: Optional[str] = None
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
            self._media_id, self._media = make_media(self._data["media"])
        if "resource" in self._data:
            self._resource_id, self._resource = make_resource(self._data["resource"])

        if self._media_id is None:
            self._media_id = self._data.get(
                "media_id", self._resource.media_id if self._resource else None
            )
        if self._resource_id is None:
            self._resource_id = self._data.get(
                "resource_id", self._resource.resource_id if self._resource else None
            )

        if self._media_id or self._resource_id:
            self._id = self._media_id, self._resource_id
        else:
            self._id = self.uuid

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

    def copy(self):
        return self.__class__(
            self._name,
            self._data,
            policy=self._policy,
            type=self._type,
            error=self._error,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def data(self) -> dict:
        return self._data

    @property
    def media(self) -> Optional["MediaInfo"]:
        return self._media

    @property
    def resource(self) -> Optional["ResourceInfo"]:
        return self._resource

    @property
    def is_thumbnail(self) -> Optional[bool]:
        return self._is_thumbnail

    @property
    def id(self) -> Union[tuple[Optional[str], Optional[str]], UUID]:
        return self._id

    @property
    def media_id(self) -> Optional[str]:
        return self._media_id

    @property
    def resource_id(self) -> Optional[str]:
        return self._resource_id


class MediaCommand(MediaEvent):
    def __init__(self, *args, **kwds):
        kwds["type"] = "media_command"
        super().__init__(*args, **kwds)


class ResourceDownloadProgressEvent(Event):
    def __init__(
        self,
        resource_id: Optional[str] = None,
        download_percent: Optional[int] = None,
        policy: Optional[ExpectPolicy] = None,
    ):
        super().__init__(policy=policy)
        self.resource_id = resource_id
        self.download_percent = download_percent

    def copy(self):
        return self.__class__(
            self.resource_id, self.download_percent, policy=self._policy
        )

    def __str__(self):
        return (
            "resource_downloaded_progress("
            f"resource_id={self.resource_id}, downloaded_percent={self.download_percent})"
        )
