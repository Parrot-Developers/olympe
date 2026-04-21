# Handle that Pyright considers every MediaContext variables as 'None' because of how
# they are defined
# pyright: reportOptionalMemberAccess=false

from collections import deque
from hashlib import md5
from os import mkdir, path
from typing import cast, Optional, Union, Type, TYPE_CHECKING

from olympe.concurrent import Event as ConcurrentEvent, get_running_loop
from olympe.messaging.events import EventContext
from olympe.messaging.expectations import (
    CheckWaitStateExpectation,
    Expectation,
    ExpectPolicy,
    MultipleExpectation,
    WhenAllExpectations,
)
from olympe.controllers.scheduler import Scheduler

from .base import MediaInfo, ResourceInfo
from .events import MediaCommand, MediaEvent, ResourceDownloadProgressEvent
from .rest import (
    _RESTDeleteMediaExpectation,
    _RESTDeleteAllMediaExpectation,
    _RESTDeleteResourceExpectation,
)

if TYPE_CHECKING:
    from .media import Media


# General Media Event expectation
class _MediaEventExpectationBase(Expectation):
    def __init__(
        self,
        event_name: str,
        id_field: Optional[str] = None,
        id_value: Optional[str] = None,
        data_context: Optional[str] = None,
        _timeout: Optional[float] = None,
        **kwds,
    ):
        super().__init__()
        data = {**{id_field: id_value}, **kwds}
        self._data_context = data_context
        if data_context is not None:
            data = {data_context: data}
        self.set_timeout(_timeout)
        self._init_kwds = kwds

        self._expected_event = MediaEvent(event_name, data)
        self._event_name = event_name
        self._id_field = id_field
        self._id_value = id_value

        self._received_events = []
        self._matched_event = None

    def copy(self):
        return super().base_copy(
            self._event_name,
            id_field=self._id_field,
            id_value=self._id_value,
            data_context=self._data_context,
            _timeout=self._timeout,
            **self._init_kwds,
        )

    def _get_data(self, media_event: MediaEvent) -> dict:
        if self._data_context is None:
            return media_event.data
        return media_event.data[self._data_context]

    def check(self, media_event, *args, **kwds):
        # Called for each event received from the drone
        if self._success:
            # If the expectation has already been awaited (and satisfied), it becomes
            # passive and does not await for anything else
            return self
        if not isinstance(media_event, MediaEvent):
            # Keep events related to Media only
            return self
        if media_event.name != self._event_name:
            # There are plenty of different media events ; keep the correct Media event type
            return self
        # If the event is of the correct type, it is considered as an event received
        self._received_events.append(media_event)
        if self._id_field is None or self._id_value is None:
            # If there is no argument to match, consider the event matched, and the
            # expectation awaited and complete
            self._matched_event = media_event
            self.set_success()
            return self
        # If there are arguments to make match, extract them
        received_data = self._get_data(media_event)
        expected_data = self._get_data(self._expected_event)
        # Check that the field has the correct value to match, or return the expectation
        # as is
        if received_data[self._id_field] == self._id_value:
            # Compare all the other expected fields, whose value are not None. If one does
            # not match, exit without success
            for name, value in expected_data.items():
                if value is not None and (
                    name not in received_data or received_data[name] != value
                ):
                    break
            else:
                # All the fields that had to match have matched
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
            # copy self._received_events so that if someone modifies it externally, it
            # doesn't affect internal logic
            return EventContext(self._received_events[:])

    def matched_events(self):
        """
        Returns a collection of events that have matched this expectation
        (or a child expectation)
        """
        if self._success:
            # If the expectation has been awaited, everything to match has matched, there
            # is not event left to match, the expectation is satisfied
            return EventContext()
        else:
            # If the expectation is still awaited, there is still an event to match
            return EventContext([self._matched_event])

    def unmatched_events(self):
        """
        Returns a collection of events object that are still expected
        """
        if not self._success:
            # If the expectation is still awaited, this means that the expected event is
            # still expected
            return EventContext([self._expected_event])
        else:
            # If the expectation has been awaited, this means that all the events have
            # matched, there is no unmatched events
            return EventContext()


# Media Event expectation for a specific id
class _MediaEventExpectation(_MediaEventExpectationBase):
    def __init__(
        self,
        event_name: str,
        id_value: Optional[str] = None,
        data_context: Optional[str] = "media",
        _timeout: Optional[float] = None,
        **kwds,
    ):
        return super().__init__(
            event_name,
            id_field="media_id",
            id_value=id_value,
            data_context=data_context,
            _timeout=_timeout,
            **kwds,
        )

    def copy(self):
        return super().base_copy(
            self._event_name,
            media_id=self._id_value,
            data_context=self._data_context,
            _timeout=self._timeout,
            **self._init_kwds,
        )


class media_created(_MediaEventExpectation):
    def __init__(
        self,
        event_name: str = "media_created",
        media_id: Optional[str] = None,
        _timeout: Optional[float] = None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=media_id,
            _timeout=_timeout,
            **kwds,
        )


class media_removed(_MediaEventExpectation):
    def __init__(
        self,
        event_name: str = "media_removed",
        media_id: Optional[str] = None,
        _timeout: Optional[float] = None,
        data_context=None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=media_id,
            data_context=None,
            _timeout=_timeout,
            **kwds,
        )


class all_media_removed(_MediaEventExpectationBase):
    def __init__(
        self, event_name="all_media_removed", _timeout: Optional[float] = None, **kwds
    ):
        super().__init__(event_name=event_name, _timeout=_timeout, **kwds)


class media_updated(_MediaEventExpectation):
    def __init__(
        self,
        event_name="media_updated",
        media_id: Optional[str] = None,
        _timeout: Optional[float] = None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=media_id,
            _timeout=_timeout,
            **kwds,
        )


class storage_removed(_MediaEventExpectationBase):
    def __init__(
        self, event_name="storage_removed", _timeout: Optional[float] = None, **kwds
    ):
        super().__init__(event_name=event_name, _timeout=_timeout, **kwds)


class _ResourceEventExpectation(_MediaEventExpectationBase):
    def __init__(
        self,
        event_name: str,
        id_value: Optional[str] = None,
        data_context: Optional[str] = "resource",
        _timeout: Optional[float] = None,
        **kwds,
    ):
        return super().__init__(
            event_name,
            id_field="resource_id",
            id_value=id_value,
            data_context=data_context,
            _timeout=_timeout,
            **kwds,
        )

    def copy(self):
        return super().base_copy(
            self._event_name,
            resource_id=self._id_value,
            data_context=self._data_context,
            _timeout=self._timeout,
            **self._init_kwds,
        )


class resource_created(_ResourceEventExpectation):
    def __init__(
        self,
        event_name: str = "resource_created",
        resource_id: Optional[str] = None,
        media_id: Optional[str] = None,
        _timeout: Optional[float] = None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=resource_id,
            media_id=media_id,
            _timeout=_timeout,
            **kwds,
        )


class resource_removed(_ResourceEventExpectation):
    def __init__(
        self,
        event_name: str = "resource_removed",
        resource_id: Optional[str] = None,
        _timeout: Optional[float] = None,
        data_context=None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=resource_id,
            data_context=None,
            _timeout=_timeout,
            **kwds,
        )


class resource_downloaded(_ResourceEventExpectation):
    def __init__(
        self,
        event_name: str = "resource_downloaded",
        resource_id: Optional[str] = None,
        is_thumbnail: Optional[bool] = None,
        _timeout: Optional[float] = None,
        data_context=None,
        **kwds,
    ):
        super().__init__(
            event_name=event_name,
            id_value=resource_id,
            data_context=None,
            is_thumbnail=is_thumbnail,
            _timeout=_timeout,
            **kwds,
        )


class resource_download_progress(Expectation):
    def __init__(
        self,
        resource_id: Optional[str] = None,
        downloaded_percent: Optional[int] = None,
    ):
        super().__init__()
        self.resource_id = resource_id
        self.downloaded_percent = downloaded_percent
        self._expected_event = ResourceDownloadProgressEvent(
            self.resource_id, self.downloaded_percent
        )

    def copy(self):
        return super().base_copy(
            resource_id=self.resource_id, downloaded_percent=self.downloaded_percent
        )

    def check(self, resource_download_progress_event, *args, **kwds):
        if not isinstance(
            resource_download_progress_event, ResourceDownloadProgressEvent
        ):
            return self
        if self.resource_id is None:
            self.set_success()
            return self
        if self.resource_id != resource_download_progress_event.resource_id:
            return self
        self.set_success()
        return self

    def expected_events(self):
        # This expectation waits for a virtual event : no "download complete" will be sent
        # by the drone, since the download is done from the webserver API
        return EventContext([self._expected_event])

    def received_events(self):
        # Because there is no real event to wait for, if the download is complete, it means
        # that is has been virtually "matched"
        return self.matched_events()

    def matched_events(self):
        # Because there is no real event to receive, return the virtual event expected
        # when the expectation is complete
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        # If the expectation is not met yet, this means that the virtual event we wait
        # for is still unmatched
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class _download_resource(Expectation):
    # always_monitor is set to True because a download_resources expectation may by
    # interrupted each time a single resource is downloaded, but several may be in queue
    always_monitor = True

    def __init__(
        self,
        resource_id: str,
        download_dir: Optional[str] = None,
        integrity_check: Optional[bool] = None,
        thumbnail: bool = False,
        _timeout: Optional[float] = None,
    ):
        super().__init__()
        self.set_timeout(_timeout)
        self._resource_id = resource_id
        self._download_dir = download_dir
        self._integrity_check = integrity_check
        self._thumbnail = thumbnail

        self._expected_event = MediaCommand(
            "download_resource", dict(resource_id=resource_id)
        )

        self._resource_size: int
        self._resource_path = None
        self._md5_path = None
        self._media = None
        self._fd = None
        self._sock = None
        self._response = None
        self._resource = None
        self._resource_file = None
        self._md5 = md5()
        self._download_status = None
        self._downloaded_size = 0
        self._downloaded_percent = 0
        self._write_tasks = deque()

    def copy(self):
        return super().base_copy(
            self._resource_id,
            download_dir=self._download_dir,
            integrity_check=self._integrity_check,
            thumbnail=self._thumbnail,
            _timeout=self._timeout,
        )

    def _schedule(self, scheduler):
        # If the expection is already awaited, do not schedule it twice
        if self._awaited:
            return
        super()._schedule(scheduler)
        self._media = scheduler.context("olympe.media")
        if self._download_dir is None:
            # If download_dir has not been specified with the expectation, use the one
            # for Media
            self._download_dir = self._media._download_dir
        if self._download_dir is None:
            self._media.logger.error(
                f"Cannot download resource {self._resource_id}: "
                "no download directory is set"
            )
            # Expectation cancellation is required, resource cannot be download if there
            # is no recipient provided
            self.cancel()
            return
        if self._thumbnail:
            # Subdirectory for thumbnails
            self._download_dir = path.join(self._download_dir, "thumbnails")

        if self._integrity_check is None:
            self._integrity_check = self._media._integrity_check or False

        # There is no integrity check available for thumbnails because the drone
        # doesn't expose md5 for them
        self._integrity_check = not self._thumbnail and self._integrity_check
        # Async download
        self._media._loop.run_async(self._adownload)

    async def _adownload(self):
        # Copy self._resource_id value to prevent the variable from being modified while
        # download is still processing. It is useful in the case of a download since it
        # can happen that several downloads are requested with the same class
        # _download_resource, or because a download may take time
        resource_id = self._resource_id

        download_dir = str(self._download_dir)
        if not path.exists(download_dir):
            mkdir(download_dir)

        assert self._media, "Media context has not been retrieved"

        # Retrieve metadata
        self._resource = (
            await self._media.factory.ctx_media.resource_list._aresource_info(
                resource_id=resource_id,
                with_md5=self._integrity_check,
            )
        )

        # Check that the resource retrieved exists or cancels
        if self._resource is None:
            self._media.logger.error(f"Unknown resource '{resource_id}'")
            self.cancel()
            return
        # Check the expectation has not timedout
        if self.timedout():
            self._media.logger.error(f"Resource '{resource_id}' download timedout")
            # Scheduler will take care of the cancellation
            # The cancellation cannot be done by the timedout expectation itself, that
            # would appear as a cancellation error and would shaddow the reality behind
            return

        # Download
        if not self._thumbnail:
            self._response = (
                await self._media.factory.ctx_media.resource_list._aget_resource(
                    resource_id=resource_id
                )
            )
        else:
            self._response = (
                await self._media.factory.ctx_media.resource_list._aget_thumbnail(
                    resource_id=resource_id
                )
            )

        # Check that the download succeeds or cancels
        if self._response is None:
            self._media.logger.error(
                f"{'Thumbnail' if self._thumbnail else 'Resource'} download request failed for resource '{resource_id}'"
            )
            self.cancel()
            return

        # Recheck the expectation has still not timedout
        if self.timedout():
            self._media.logger.error(f"Resource download {resource_id} timedout")
            return

        if self._thumbnail:
            self._resource_path = path.join(
                str(self._download_dir), path.basename(self._resource.thumbnail)
            )
        else:
            self._resource_path = path.join(
                str(self._download_dir), path.basename(self._resource.url)
            )

        # Open the file downloaded
        try:
            self._resource_file = open(self._resource_path, "wb")
        except OSError as e:
            self._media.logger.error(
                f"Failed to open '{self._resource_path}' for writing: {e}"
            )
            self.cancel()
            return

        # Write in file
        try:
            self._resource_size = int(self._response.headers["content-length"])
            if not self._thumbnail and self._resource_size != self._resource.size:
                self._media.logger.warning(
                    f"HTTP response content-length header for resource '{self._resource_id}' "
                    "is not coherent with the media resource size as returned by the media REST "
                    f"API, expected {self._resource.size} and got {self._resource_size}"
                )
            async for chunk in self._response:
                await self._write_chunk(chunk)
                if self._downloaded_size < self._resource_size and self.timedout():
                    self._media.logger.error(
                        f"Resource download '{resource_id}' timedout"
                    )
                    return
        finally:
            if not await self._media._loop.complete_futures(
                *self._write_tasks, timeout=2.0
            ):
                self._media.logger.error(f"Resource '{resource_id}' disk i/o timedout")
                self._resource_file.close()
                self._resource_file = None
                return
            self._resource_file.close()
            self._resource_file = None

        # Check all the file has been supposedly downloaded
        if self._downloaded_size < self._resource_size:
            self._media.logger.error(
                f"Downloading resource '{self._resource.resource_id}' {'thumbnail' if self._thumbnail else ''} is not complete ({self._downloaded_percent}%)"
            )
            self.cancel()
            return

        # Verify integrity with md5 checksum
        await self._acheck_integrity_with_md5_checksum()

        self._media.logger.info(
            f"{'Thumbnail resource' if self._thumbnail else 'Resource'} '{self._resource_id}' download has been 100% successful"
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
        await self._media.aprocess_event(event)
        self.set_success()

    async def _acheck_integrity_with_md5_checksum(self):
        if self._integrity_check:
            md5 = self._md5.hexdigest()
            md5_ref = self._resource.md5
            resource_id = self._resource.resource_id
            self._md5_path = path.join(str(self._download_dir), resource_id + ".md5")
            with open(self._md5_path, "w") as f:
                await self._media._loop.run_in_executor(
                    f.write, md5_ref + " " + path.basename(self._resource.url)
                )
            if md5 != md5_ref:
                self._media.logger.error(
                    f"Resource '{resource_id}' integrity check after download has failed"
                )
                self._media._loop.remove_fd_from_loop(self._fd)
                self.cancel()
            self._media.logger.info(
                f"Resource '{resource_id}' integrity check after download has succeeded"
            )

    async def _write_chunk(self, chunk):
        self._write_tasks.append(
            self._media._loop.run_in_executor(self._resource_file.write, chunk)
        )
        if self._integrity_check:
            self._md5.update(chunk)
        self._downloaded_size += len(chunk)
        percent = int(100 * (self._downloaded_size / self._resource_size))
        if percent > self._downloaded_percent:
            await self._media.aprocess_event(
                ResourceDownloadProgressEvent(
                    self._resource.resource_id, self._downloaded_percent
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
        # This expectation waits for a virtual event : no "download complete" will be sent
        # by the drone, since the download is done from the webserver API
        return EventContext([self._expected_event])

    def received_events(self):
        # Because there is no real event to wait for, if the download is complete, it means
        # that is has been virtually "matched"
        return self.matched_events()

    def matched_events(self):
        # Because there is no real event to receive, return the virtual event expected
        # when the expectation is complete
        if self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()

    def unmatched_events(self):
        # If the expectation is not met yet, this means that the virtual event we wait
        # for is still unmatched
        if not self._success:
            return EventContext([self._expected_event])
        else:
            return EventContext()


class download_resource(_download_resource):
    def __init__(
        self,
        resource_id: str,
        download_dir: Optional[str] = None,
        integrity_check: Optional[bool] = None,
        _timeout: Optional[float] = None,
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
        self,
        resource_id: str,
        download_dir: Optional[str] = None,
        integrity_check: Optional[bool] = None,
        _timeout: Optional[float] = None,
    ):
        super().__init__(
            resource_id,
            download_dir=download_dir,
            integrity_check=integrity_check,
            thumbnail=True,
            _timeout=_timeout,
        )


class DownloadMediaExpectationBase(MultipleExpectation):
    # DownloadMediaExpectationBase inherits from MultipleExpectation, which means that
    # this expectation handles multiple subexpectations so to say. However, an expectation
    # stops being active as soon as it success, fails, or timeouts. A multiple expectation
    # thus cannot stop automatically when one media download expectation expires, it has
    # to keep on monitoring on future downloads
    always_monitor = True
    _last_resource_grace_period = 1.0

    def __init__(self, media_id, download_dir=None, _timeout=None):
        self._download_dir = download_dir
        self._media_id = media_id
        self._media = None
        self._media_event = ConcurrentEvent()
        self._last_resource_ts = None
        super().__init__()
        self.set_timeout(_timeout)

    def __getattr__(self, name):
        if self._media is None:
            raise AttributeError(
                f"'{self.__class__.__name__}' has no attribute '{name}'"
            )
        else:
            return getattr(self._media, name)

    def timedout(self) -> bool:
        # If any subexpectations or parent expectation has timedout, set them all timedout
        if super().timedout() or any(map(lambda e: e.timedout(), self.expectations)):
            for e in self.expectations:
                if not e.done():
                    e.set_timedout()
            self.set_timedout()
        return super().timedout()

    def cancelled(self) -> bool:
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
                self._add_matched_expectation(expectation)

        if self._last_resource_ts is None:
            return self

        if self._scheduler.time() < (
            self._last_resource_ts + self._last_resource_grace_period
        ):
            return self

        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()
        return self

    def _on_resource_created_base(self, event: MediaEvent, scheduler: Scheduler):
        if self.success() or self.cancelled():
            return
        for resource in self.expectations:
            if resource._resource_id == event.resource_id:
                return
        last_resource_ts = scheduler.time()
        new_grace_period = 2 * (last_resource_ts - self._last_resource_ts)
        self._last_resource_ts = last_resource_ts
        self._last_resource_grace_period = max(
            new_grace_period, self._last_resource_grace_period
        )
        expectation = self._on_resource_created(event, scheduler)
        # Handle the case if a thumbnail has to be downloaded but does not exist
        if expectation is None:
            return
        self.expectations.append(expectation)
        scheduler._schedule(self.expectations[-1])

    async def _download_resource_base(self, scheduler):
        media_context = scheduler.context("olympe.media")
        self._media = await media_context.aget_media(self._media_id)
        self._media_event.set()
        for resource_id, resource in self._media.resources.items():
            expectation = await self._download_resource(resource_id, resource)
            # Handle the case if a thumbnail has to be downloaded but does not exist
            if expectation is None:
                continue
            self.expectations.append(expectation)
            scheduler.schedule(self.expectations[-1])

    def _schedule(self, scheduler):
        super()._schedule(scheduler)
        media_context = scheduler.context("olympe.media")
        media_context.subscribe(
            lambda event, scheduler: scheduler.run(
                self._on_resource_created_base, event, scheduler
            ),
            resource_created(media_id=self._media_id),
        )
        media_context._loop.run_async(self._download_resource_base, scheduler)
        self._last_resource_ts = scheduler.time()

    def on_subexpectation_done(self, expectation):
        if not expectation.success():
            return

        self._add_matched_expectation(expectation)
        if len(self.expectations) == len(self.matched_expectations):
            self.set_success()

    async def _await_impl(self):
        await self._media_event.wait()
        grace_period = self._last_resource_grace_period - (
            self._scheduler.time() - self._last_resource_ts
        )
        if grace_period > 0:
            await get_running_loop().asleep(grace_period)

    def wait(self, _timeout=None):
        if self._scheduler is None:
            return self
        start_time = self._scheduler.time()
        media_context = self._scheduler.context("olympe.media")
        media_context._loop.run_async(self._await_impl).result_or_cancel(
            timeout=_timeout
        )
        end_time = self._scheduler.time()
        if _timeout is not None:
            _timeout -= end_time - start_time
        super().wait(_timeout=_timeout)
        return self

    def _combine_method(self):
        return "&"


class DownloadMediaExpectation(DownloadMediaExpectationBase):
    def __init__(self, *args, integrity_check: Optional[bool] = None, **kwds):
        self._integrity_check = integrity_check
        super().__init__(*args, **kwds)

    def copy(self):
        return super().base_copy(
            self._media_id,
            download_dir=self._download_dir,
            integrity_check=self._integrity_check,
            _timeout=self._timeout,
        )

    async def _download_resource(
        self, resource_id: str, resource: ResourceInfo
    ) -> download_resource:
        return download_resource(
            resource_id,
            download_dir=self._download_dir,
            integrity_check=self._integrity_check,
            _timeout=self._timeout,
        )

    def _on_resource_created(
        self, event: MediaEvent, _: Scheduler
    ) -> download_resource:
        assert (
            event.resource_id
        ), f"Resource id '{event.resource_id}' has to be defined when a resource is created for a media download"
        return download_resource(
            event.resource_id,
            download_dir=self._download_dir,
            integrity_check=self._integrity_check,
            _timeout=self._timeout,
        )


class DownloadThumbnailExpectation(DownloadMediaExpectationBase):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)

    def copy(self):
        return super().base_copy(
            self._media_id, download_dir=self._download_dir, _timeout=self._timeout
        )

    async def _download_resource(
        self, resource_id: str, resource: ResourceInfo
    ) -> Optional[download_resource_thumbnail]:
        if resource.thumbnail is None:
            return
        return download_resource_thumbnail(
            resource_id, download_dir=self._download_dir, _timeout=self._timeout
        )

    def _on_resource_created(
        self, event: MediaEvent, scheduler: Scheduler
    ) -> Optional[download_resource_thumbnail]:
        media_context = cast("Media", scheduler.context("olympe.media"))
        assert (
            media_context.factory.ctx_media.state._media_state is not None
        ), "Media library is empty"
        media = media_context.factory.ctx_media.state._media_state[event.media_id]
        resource = media.resources[event.resource_id]
        if resource.thumbnail is None:
            return

        assert (
            event.resource_id
        ), f"Resource id '{event.resource_id}' has to be defined when a resource is created for a thumbnail download"
        return download_resource_thumbnail(
            event.resource_id,
            download_dir=self._download_dir,
            _timeout=self._timeout,
        )


class _CheckStateExpectation(Expectation):
    def __init__(self, id):
        super().__init__()
        self._id = id
        self.field_id: str
        self.data_type: Type[Union[MediaInfo, ResourceInfo]]
        self._expected_events = self.data_type(**{self.field_id: self._id})
        self._matched_events = None

    def copy(self):
        return super().base_copy(self._id)

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
            self._matched_events = MediaEvent("media_present", media)
            self.set_success()
        else:
            self.cancel()
        return self

    def check(self, *args, **kwds):
        return self

    def expected_events(self):
        return EventContext([self._expected_events])

    def received_events(self):
        return self.matched_events()

    def matched_events(self):
        if self._success:
            return EventContext([self._matched_events])
        else:
            return EventContext()

    def unmatched_events(self):
        if not self._success:
            return EventContext([self._expected_events])
        else:
            return EventContext()


class _MediaCheckStateExpectation(_CheckStateExpectation):
    data_type = MediaInfo
    field_id = "media_id"


class _ResourceCheckStateExpectation(_CheckStateExpectation):
    data_type = ResourceInfo
    field_id = "resource_id"


def media_present(media_id, _timeout=None, _policy="check_wait"):
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _MediaCheckStateExpectation(id=media_id)
    elif policy == ExpectPolicy.wait:
        return media_created(media_id=media_id, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _MediaCheckStateExpectation(id=media_id),
            media_created(media_id=media_id, _timeout=_timeout),
        )


def resource_present(resource_id, _timeout=None, _policy="check_wait"):
    policy = ExpectPolicy[_policy]
    if policy == ExpectPolicy.check:
        return _ResourceCheckStateExpectation(id=resource_id)
    elif policy == ExpectPolicy.wait:
        return resource_created(resource_id=resource_id, _timeout=_timeout)
    else:
        return CheckWaitStateExpectation(
            _ResourceCheckStateExpectation(id=resource_id),
            resource_created(resource_id=resource_id, _timeout=_timeout),
        )


def delete_media(
    media_id: str, _timeout: Optional[float] = None, _no_expect: bool = False
) -> Expectation:
    if _no_expect:
        return _RESTDeleteMediaExpectation(media_id=media_id, _timeout=_timeout)
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteMediaExpectation(media_id=media_id, _timeout=_timeout),
                media_removed(media_id=media_id, _timeout=_timeout),
            ]
        )


def delete_all_media(
    _timeout: Optional[float] = None, _no_expect: bool = False
) -> Expectation:
    if _no_expect:
        return _RESTDeleteAllMediaExpectation(_timeout=_timeout)
    else:
        return WhenAllExpectations(
            [
                _RESTDeleteAllMediaExpectation(_timeout=_timeout),
                all_media_removed(_timeout=_timeout),
            ]
        )


def delete_resource(
    resource_id: str, _timeout: Optional[float] = None, _no_expect: bool = False
) -> Expectation:
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
