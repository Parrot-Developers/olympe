# Handle that Pyright considers every MediaContext variables as 'None' because of how
# they are defined
# pyright: reportOptionalMemberAccess=false

import json

from collections import OrderedDict
from concurrent.futures import CancelledError
from logging import getLogger, Logger
from os import path
from typing import Any, Optional, Union, TYPE_CHECKING

from olympe.concurrent import (
    Loop,
    TimeoutError,
    Event as ConcurrentEvent,
    get_running_loop,
)
from olympe.connection.http import Session, ConnectionClosedError, HTTPError
from olympe.controllers.scheduler import AbstractScheduler, StreamScheduler, Scheduler
from olympe.messaging.expectations import (
    ExpectationBase,
    FailedExpectation,
)
from olympe.messaging.subscriber import Subscriber
from olympe.utils.namedtuple import _replace_namedtuple

from .base import MediaInfo, make_media, make_resource, _LOCAL_RESOURCE_FIELDS
from .enums import IndexingState
from .events import MediaEvent
from .expectations import (
    resource_downloaded,
    resource_download_progress,
    _download_resource,
    _MediaEventExpectationBase,
)
from .resource import ResourceREST, ResourceList

if TYPE_CHECKING:
    from .resource import ResourceInfo

MAX_HTTP_CONNECTION_RETRY = 5
HTTP_CONNECTION_TIMEOUT = 0.5


class MediaScheduler(StreamScheduler):
    __slots__ = ()

    def __init__(self, *args, **kwds):
        super().__init__(
            *args,
            # stream_timeout has to be detailed for its default value to be 60 and not None
            # as defined in StreamScheduler
            stream_timeout=kwds.pop("stream_timeout", 60),
            **kwds,
        )

    def _schedule(self, expectations, **kwds):
        if isinstance(expectations, _download_resource):
            # We don't want to download too many resources in parallel so we use
            # StreamScheduler as a queuing discipline for this kind of expectations
            # objects. Thus, the _schedule method of StreamScheduler is called
            super()._schedule(expectations, **kwds)
        else:
            # Other types of expectations inherit from the default scheduler behavior.
            # Thus, the _schedule method of Scheduler (after StreamScheduler in MRO) is
            # called
            super(StreamScheduler, self)._schedule(expectations, **kwds)
        return expectations

    def _wait_for_pending_downloads(self, timeout=None):
        # Async handling of media download
        deadline = None
        if timeout is not None:
            deadline = self._scheduler.time() + timeout
        # Wait for all pending expectations to complete, and wait for queue to empty
        # It enables to start afresh for the next downloading expectations
        # It doesn't necessarily wait for media downloads to end
        super().stream_join(timeout=timeout)
        while True:
            download = resource_downloaded() | resource_download_progress()
            # Notify if a resource is being downloaded or is downloaded
            self.schedule(download)
            # Wait for the downloads to complete
            download.wait(_timeout=2.0)
            # If there is no download in queue within 2 seconds, break
            if not download:
                break
            # If deadline is over, break
            if deadline is not None and self._scheduler.time() > deadline:
                break


class MediaFactory:
    def __init__(
        self,
        logger: Logger,
        loop: Loop,
        scheduler: Optional[Scheduler],
        session: Optional[Session],
        hostname: Optional[str],
        version: int,
    ):
        media_machine = MediaMachine(logger, loop, scheduler)
        media_url = MediaURL(hostname, version)
        media_connection = MediaConnection(logger, loop, session)
        media_state = MediaState(logger, loop)
        media_list = MediaList(logger)
        media_rest = MediaREST(logger)
        resource_list = ResourceList(logger, loop)
        resource_rest = ResourceREST(logger)

        self.ctx_media = MediaContext()
        self.ctx_media.machine = media_machine
        self.ctx_media.url = media_url
        self.ctx_media.connection = media_connection
        self.ctx_media.state = media_state
        self.ctx_media.list = media_list
        self.ctx_media.rest = media_rest
        self.ctx_media.resource_list = resource_list
        self.ctx_media.resource_rest = resource_rest

        media_machine.set_context(self.ctx_media)
        media_url.set_context(self.ctx_media)
        media_connection.set_context(self.ctx_media)
        media_state.set_context(self.ctx_media)
        media_list.set_context(self.ctx_media)
        media_rest.set_context(self.ctx_media)
        resource_list.set_context(self.ctx_media)
        resource_rest.set_context(self.ctx_media)


class MediaContext:
    def __init__(self):
        self.media: Media
        self.machine: Optional[MediaMachine] = None
        self.connection: Optional[MediaConnection] = None
        self.url: Optional[MediaURL] = None
        self.state: Optional[MediaState] = None
        self.list: Optional[MediaList] = None
        self.rest: Optional[MediaREST] = None
        self.resource_list: Optional[ResourceList] = None
        self.resource_rest: Optional[ResourceREST] = None


class Media(AbstractScheduler):
    """
    Drone Media API class

    This class connects to the drone web media interface (REST and websocket API) and
    synchronizes the drone media information in a background thread.
    """

    def __init__(
        self,
        hostname: Optional[str] = None,
        version: int = 1,
        name=None,
        scheduler: Optional[Scheduler] = None,
        session: Optional[Session] = None,
        download_dir: Optional[str] = None,
        integrity_check: bool = False,
    ):
        # Logging
        if name is None:
            name = "olympe.media"
            self.logger = getLogger(f"{name}")
        else:
            self.logger = getLogger(f"olympe.{name}.media")

        # Properties declaration
        self._download_dir = download_dir
        self._integrity_check = integrity_check

        # Loop instantiation
        # Loop max_workers below is set to 1 because the one and only worker is used to
        # sequentially write downloaded resources chunks. If more than one worker is used,
        # chunks may be written to disk out of order.
        self._loop = Loop(self.logger, max_workers=1)

        # Scheduler instantiation
        if scheduler is not None:
            self._scheduler = scheduler
        else:
            self._scheduler = Scheduler(self._loop, name=name)
        self._scheduler.add_context("olympe.media", self)

        # Maximum number of HTTP requests for TCP connections in the pool. Since the TCP
        # websocket connection is not part of the connection pool, this class opens a
        # maximum of `pool_maxsize` + 1 active TCP connections.
        pool_maxsize = 3
        # Add a dynamic heritage cover over self._scheduler, to give MediaSchuler
        # properties, and keep Scheduler properties at the end of the MRO
        self._scheduler.decorate(
            "MediaScheduler",
            MediaScheduler,
            max_parallel_processing=pool_maxsize,
        )

        # Factory to build all members from other Media subdivided classes
        self.factory = MediaFactory(
            self.logger, self._loop, self._scheduler, session, hostname, version
        )

        # Loop cleanup registration and start
        self._loop.register_cleanup(self.factory.ctx_media.connection._acleanup)
        self._loop.start()

    def __call__(self, expectations):
        """
        Olympe expectation DSL handler
        """
        return self.schedule(expectations)

    def __exit__(self, *args, **kwds):
        if not self._scheduler.remove_context("olympe.media"):
            self.logger.info(
                "olympe.media expectation context has already been removed"
            )

    @property
    def indexing_state(self) -> IndexingState:
        """Returns the current media indexing state

        :return: the current media indexing state
        :rtype: IndexingState
        """
        return self.factory.ctx_media.state.indexing_state

    @property
    def download_dir(self) -> Optional[str]:
        return self._download_dir

    @download_dir.setter
    def download_dir(self, value: str):
        self._download_dir = value

    @property
    def integrity_check(self) -> bool:
        return self._integrity_check

    @integrity_check.setter
    def integrity_check(self, value: bool):
        self._integrity_check = value

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    def wait_for_pending_downloads(self, timeout: Optional[float] = None):
        self.factory.ctx_media.machine._scheduler._wait_for_pending_downloads(
            timeout=timeout
        )

    # Abstract method needs definition
    def schedule(self, expectations: ExpectationBase):
        return self.factory.ctx_media.machine._schedule(expectations=expectations)

    def schedule_hook(
        self, expectations: ExpectationBase, **kwds
    ) -> Optional[ExpectationBase]:
        return self.factory.ctx_media.machine._schedule_hook(
            expectations=expectations, **kwds
        )

    # Abstract method needs definition
    def subscribe(self, *args, **kwds) -> Subscriber:
        """Subscribe a new subscriber

        :return: the new subsriber
        :rtype: Subscriber
        """
        return self.factory.ctx_media.machine._scheduler.subscribe(*args, **kwds)

    # Abstract method needs definition
    def unsubscribe(self, subscriber: Subscriber):
        """Unsubscribe a previously registered subscriber

        :param subscriber: subscriber previously registered to unsubscribe
        :type subscriber: Subscriber
        """
        return self.factory.ctx_media.machine._scheduler.unsubscribe(subscriber)

    async def _aconnect(self) -> bool:
        return await self.factory.ctx_media.connection._aconnect()

    def connect(self, timeout: Optional[float] = None) -> bool:
        """Connect a Media instance for the current session

        :param timeout: connection timeout, defaults to self._mediaconnection.TIMEOUT_MEDIA
        :type timeout: float, optional
        :return: True if connection succeeds, False otherwise
        :rtype: bool
        """
        if timeout is None:
            timeout = self.factory.ctx_media.connection.TIMEOUT_MEDIA
        try:
            return self._loop.run_async(self._aconnect).result_or_cancel(
                timeout=timeout
            )
        except (TimeoutError, CancelledError):
            self.logger.error("Fail to connect a Media instance")
            return False

    async def _adisconnect(self) -> bool:
        return await self.factory.ctx_media.connection._adisconnect()

    def disconnect(self, timeout: Optional[float] = None) -> bool:
        """Disconnect the Media instance of the current session

        :param timeout: disconnection timeout, defaults to self._mediaconnection.TIMEOUT_MEDIA
        :type timeout: float, optional
        :return: True if disconnection succeeds, False otherwise
        :rtype: bool
        """
        if timeout is None:
            timeout = self.factory.ctx_media.connection.TIMEOUT_MEDIA

        try:
            return self._loop.run_async(self._adisconnect).result_or_cancel(
                timeout=timeout
            )
        except (TimeoutError, CancelledError) as e:
            self.logger.error(f"Fail to disconnect the Media instance ({str(e)})")
            return False

    def stop(self) -> bool:
        """Properly close and stop the websocket connection and the media API background
        thread

        :return: True if the loop has correctly been stopped, False otherwise
        :rtype: bool
        """
        # _acleanup is automatically called by the pomp loop when destroying. There is
        # no need to call it once again here
        return self._loop.stop()

    def reset_state(self):
        return self.factory.ctx_media.state._reset_state()

    def get_hostname(self) -> Optional[str]:
        return self.factory.ctx_media.url.get_hostname()

    def set_hostname(self, hostname: Optional[str] = None):
        return self.factory.ctx_media.url._set_hostname(hostname)

    def get_set_hostname_event(self) -> ConcurrentEvent:
        return self.factory.ctx_media.connection._get_set_hostname_event()

    async def aget_media(
        self, media_id: str, timeout: Optional[float] = None
    ) -> Optional[MediaInfo]:
        return await self.factory.ctx_media.rest._aget_media(media_id)

    async def aprocess_event(self, event: MediaEvent):
        return await self.factory.ctx_media.machine._aprocess_event(event)

    def media_info(
        self, media_id: Optional[str] = None
    ) -> Union[MediaInfo, dict[str, MediaInfo], None]:
        return self.factory.ctx_media.list.media_info(
            media_id=media_id,
        )

    def resource_info(
        self,
        media_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        with_md5: bool = False,
        with_signature: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional["ResourceInfo"]:
        return self.factory.ctx_media.resource_list.resource_info(
            media_id=media_id,
            resource_id=resource_id,
            with_md5=with_md5,
            with_signature=with_signature,
            timeout=timeout,
        )

    def list_media(self) -> list[str]:
        return self.factory.ctx_media.list.list_media()

    def last_media(self) -> Optional[MediaInfo]:
        return self.factory.ctx_media.list.last_media()

    def list_resources(self, media_id: Optional[str] = None) -> Optional[list[str]]:
        return self.factory.ctx_media.list.list_resources(media_id)


class MediaMachine:
    def __init__(
        self,
        logger: Logger,
        loop: Loop,
        scheduler: Optional[Scheduler] = None,
    ):
        self.logger = logger
        self._loop = loop
        self._scheduler = scheduler

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    def _schedule_hook(
        self, expectations: ExpectationBase, **kwds
    ) -> Optional[ExpectationBase]:
        if not isinstance(expectations, _MediaEventExpectationBase):
            return None
        if self.context.connection._websocket is None:
            return FailedExpectation(
                "Not connected to any device to schedule a media event expectation"
            )
        return None

    def _schedule(self, expectations: ExpectationBase):
        return self._scheduler.schedule(expectations)

    async def _aprocess_event(self, event: MediaEvent):
        await self.context.state._update_media_state(event)
        self._scheduler.process_event(event)

    # Abstract method needs definition
    def subscribe(self, *args, **kwds) -> Subscriber:
        return self._scheduler.subscribe(*args, **kwds)

    # Abstract method needs definition
    def unsubscribe(self, subscriber: Subscriber):
        return self._scheduler.unsubscribe(subscriber)


class MediaURL:
    def __init__(self, hostname: Optional[str] = None, version: Optional[int] = 1):
        self._hostname = hostname
        self._version = version

        # The API URLs are defined once the hostname is set
        self._base_url: Optional[str] = None
        self._api_url: Optional[str] = None
        self._media_api_url: Optional[str] = None
        self._resource_api_url: Optional[str] = None
        self._md5_api_url: Optional[str] = None
        self._websocket_url: Optional[str] = None
        # Set the hostname as soon as Media instance is created
        self._set_hostname(hostname)

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    def get_hostname(self) -> Optional[str]:
        return self._hostname

    def _set_hostname(self, hostname: Optional[str] = None):
        self._hostname = hostname

        if self._hostname is not None:
            # REST API endpoints
            self._base_url = f"http://{self._hostname}"
            self._api_url = f"{self._base_url}/api/v{self._version}"
            self._media_api_url = f"{self._api_url}/media/medias"
            self._resource_api_url = f"{self._api_url}/media/resources"
            self._md5_api_url = f"{self._api_url}/media/md5"
            self._websocket_url = (
                f"ws://{self._hostname}/api/v{self._version}/media/notifications"
            )
        # In case hostname is reset to None, reset the API URLs too
        else:
            self._base_url = None
            self._api_url = None
            self._media_api_url = None
            self._resource_api_url = None
            self._md5_api_url = None
            self._websocket_url = None


class MediaConnection:
    TIMEOUT_MEDIA = 5

    def __init__(
        self,
        logger: Logger,
        loop: Loop,
        session: Optional[Session] = None,
    ):
        self.logger = logger
        self._loop = loop

        # In case no session is transmitted through the mixins, Media builts its own
        # with its own loop.
        if session is None:
            self._session = Session(self._loop)
        else:
            self._session = session
        self._websocket = None

        # Event to monitor the hostname setting once the proxy is created
        self._set_hostname_event = None

        # Flag to indicate whether a cleanup action is already in progress,
        # to prevent cleanup from running twice in parallel
        self._cleanup_ongoing = False

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    def _websocket_exc_handler(method):
        async def wrapper(self, *args, **kwds):
            try:
                return await method(self, *args, **kwds)
            except CancelledError:
                pass
            except ConnectionClosedError:
                # Remote side closed the websocket cleanly: preserve media state so a
                # reconnect can resume without a full re-sync, just mark it stale.
                self.logger.info("Websocket closed")
                self._websocket = None
                self.context.state._media_state_need_sync = True
            except ConnectionError as e:
                self.logger.error(f"Websocket error '{type(e)}': {e}")
                await self._adisconnect()
                await self.context.state._areset_state()
            except Exception as e:
                self.logger.exception(f"Websocket callback unhandled exception: {e}")
                await self._adisconnect()
                await self.context.state._areset_state()

        return wrapper

    @_websocket_exc_handler
    async def _websocket_event_reader(self):
        data = ""
        while self._websocket and self._loop.running:
            try:
                chunk = await self._websocket.aget_media_event()
            except TimeoutError:
                continue
            if chunk is None:
                self.logger.info("Websocket closed")
                await self._adisconnect()
                await self.context.state._areset_state()
                return
            data += str(chunk)
            # Parse and handle the received media event
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                # Reset data if there is one invalid chunk
                data = ""
                continue
            # Reset data once parsed
            data = ""
            event = MediaEvent(event["name"], event["data"])
            await self.context.machine._aprocess_event(event)

    def _get_set_hostname_event(self) -> ConcurrentEvent:
        if self._set_hostname_event is None:
            self._set_hostname_event = ConcurrentEvent(self._loop)
        return self._set_hostname_event

    async def _aconnect(self) -> bool:
        if self._websocket is not None:
            return True  # already connected

        # Open a websocket for connection
        try:
            # Wait for the proxy hostame to be fully set before attempting to connect
            await self._get_set_hostname_event().wait()
            self._websocket = await self._session.websocket(
                self.context.url._websocket_url,
                # _websocket_url is necessarily not
                # None after self._set_hostname_event
                # has been awaited
                timeout=HTTP_CONNECTION_TIMEOUT,
                retry=MAX_HTTP_CONNECTION_RETRY,
            )
        except TimeoutError:
            self.logger.error("Unable to connect to the media websocket (timeout)")
            return False
        except (ConnectionError, OSError) as e:
            self.logger.error(f"Unable to connect to the media websocket ({e})")
            return False

        # Initialize the media state from the REST API *before* starting
        # the websocket event reader task.  If the reader were started
        # first, it would race against this REST call: the reader could
        # update _media_state in-place while _init_media_state() holds the
        # REST future, and those updates would be silently discarded when
        # the dict is replaced by the REST snapshot.  Websocket events that
        # arrive before the reader starts are buffered in the event queue
        # and will be replayed in order once the reader begins.
        if not await self.context.state._init_media_state():
            self.logger.warning("Unable to index media")
        else:
            # Media are already INDEXED.  The websocket will not send an
            # `indexing_state_changed` event for the current state, so
            # update _indexing_state and notify the scheduler directly
            # instead of going through _update_media_state().  Routing
            # through _update_media_state() would unconditionally set
            # _media_state_need_sync=True and call _init_media_state()
            # again, triggering a redundant REST re-fetch on top of the one
            # we just completed — and during that second fetch the reader
            # would already be running, reintroducing exactly the race
            # we avoided above.
            event = MediaEvent(
                "indexing_state_changed",
                {"new_state": "INDEXED", "old_state": "NOT_INDEXED"},
            )
            self.context.state._indexing_state = IndexingState(event.data["new_state"])
            self.context.machine._scheduler.process_event(event)

        # Start the reader only after the initial state snapshot is
        # coherent, so that its incremental updates are applied on top of
        # a clean baseline.
        self._loop.run_later(self._websocket_event_reader)
        return True

    async def _adisconnect(self) -> bool:
        # Prevent disconnection from runnning twice
        if self._cleanup_ongoing:
            self.logger.info(
                "A Media disconnection process is already ongoing. Let it complete"
            )
            return True

        # Notify that a cleanup process is ongoing, and prevent it to run twice in parallel
        self._cleanup_ongoing = True

        # Close the websocket that has been opened during connection
        if self._websocket:
            try:
                if not await self._websocket.aclose():
                    self.logger.error("Unable to close the media websocket correctly")
                # Clear media indexing
                self.context.state._indexing_state = IndexingState("not_indexed")
                event = MediaEvent(
                    "indexing_state_changed",
                    {"new_state": "NOT_INDEXED", "old_state": "INDEXED"},
                )
                await self.context.machine._aprocess_event(event)
            except ConnectionError:
                self._websocket = None
                self._cleanup_ongoing = False
                return False

        self._websocket = None
        self._cleanup_ongoing = False
        return True

    async def _acleanup(self):
        if self._cleanup_ongoing:
            self.logger.info(
                "A Media cleanup process is already ongoing. Let it complete"
            )
            return True

        assert await self._adisconnect()
        if not self.context.machine._scheduler.remove_context("olympe.media"):
            self.logger.info(
                "olympe.media expectation context has already been removed"
            )
        self.logger.info("Media successfully cleaned")


class MediaState:
    def __init__(self, logger: Logger, loop: Loop):
        self.logger = logger
        self._loop = loop

        self._media_state: Optional[dict] = None
        self._media_state_need_sync: bool = True
        self._indexing_state = IndexingState.not_indexed

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    @property
    def indexing_state(self) -> IndexingState:
        return self._indexing_state

    @indexing_state.setter
    def indexing_state(self, indexing_state: IndexingState):
        self._indexing_state = indexing_state

    async def _init_media_state(self) -> bool:
        if self._media_state is not None and not self._media_state_need_sync:
            return True

        # Fetch the full media library from the drone's REST API.
        media_state = await self.context.rest._aget_all_media()
        if media_state is None:
            return False

        # Preserve local fields (e.g. download_path) that come from resource_downloaded
        # events and are absent from the REST API response. Without this, a re-sync
        # would silently discard download info already held by callers.
        if self._media_state is not None:
            for media_id, media in media_state.items():
                existing_media = self._media_state.get(media_id)
                if existing_media is None:
                    continue
                for res_id in list(media.resources):
                    existing_res = existing_media.resources.get(res_id)
                    if existing_res is None:
                        continue
                    # Collect non-None local fields from the previous state.
                    preserved = {
                        field: getattr(existing_res, field)
                        for field in _LOCAL_RESOURCE_FIELDS
                        if getattr(existing_res, field) is not None
                    }
                    # Re-inject them into the freshly synced resource.
                    if preserved:
                        media.resources[res_id] = _replace_namedtuple(
                            media.resources[res_id], **preserved
                        )

        self._media_state = media_state
        self._media_state_need_sync = False
        return True

    async def _update_media_state(self, media_event: MediaEvent):
        if self._media_state is None:
            # We've been disconnected while this event was pending for processing.
            # There is nothing left to do with it now
            return

        if not isinstance(media_event, MediaEvent):
            return

        if media_event.name == "media_created":
            media_id, media = make_media(media_event.data["media"])
            if not media_id:
                self.logger.error(
                    f"Missing 'media_id' ({media_id}) in 'media_created' event. Unable to update media state"
                )
                return
            if media_id in self._media_state:
                self.logger.warning(
                    f"A media with id '{media_id}' is already in Media library. Will replace it"
                )
            # Update media library with the media created
            self._media_state[media_id] = media

        elif media_event.name == "resource_created":
            _, resource = make_resource(media_event.data["resource"])
            if not resource.media_id or not resource.resource_id:
                self.logger.error(
                    f"Missing 'media_id' ({resource.media_id}) and 'resource_id' ({resource.resource_id}) in 'resource_created' event. Unable to update media state"
                )
                return
            if resource.media_id not in self._media_state:
                self.logger.error(
                    f"A resource with id '{resource.resource_id}' is created with a not already known media id "
                    f"({resource.media_id}). Unable to update media state"
                )
                return
            # Update media library with the resource created associated to its media
            self._media_state[resource.media_id].resources[
                resource.resource_id
            ] = resource

        elif media_event.name == "media_removed":
            if "media_id" not in media_event.data:
                self.logger.error(
                    "Missing 'media_id' in 'media_removed' event. Unable to update media state"
                )
                return
            # Update media library with the media removed
            self._media_state.pop(media_event.data["media_id"], None)

        elif media_event.name == "all_media_removed":
            self._media_state = OrderedDict()

        elif media_event.name == "resource_removed":
            if "resource_id" not in media_event.data:
                self.logger.error(
                    "Missing 'resource_id' in 'resource_removed' event. Unable to update media state"
                )
                return
            resource_id = media_event.data["resource_id"]
            # Update media library with the resource removed for all the media concerned
            # with this resource
            for media in self._media_state.values():
                media.resources.pop(resource_id, None)

        elif media_event.name == "media_updated":
            media = self._media_state.get(media_event.media_id)
            if media is None:
                self.logger.error(
                    f"A media with id '{media_event.media_id}' is requested to update, but "
                    "it has never been registered in the media library before. Unable to update media state"
                )
                return
            # Build a media with the data from the 'media_removed' event
            media_data = make_media(media_event.data["media"])[1]._asdict()
            # Clean old resources to start a new media afresh
            media_data.pop("resources", None)
            # Update media library with the media updated
            self._media_state[media_event.media_id] = _replace_namedtuple(
                media, **media_data
            )

        elif media_event.name == "resource_downloaded":
            try:
                resource = self.context.resource_list.resource_info(
                    resource_id=media_event.resource_id
                )
            except Exception:
                self.logger.exception("Unable to handle 'resource_downloaded' event")
                return
            if resource is None:
                self.logger.error(
                    "Unable to get ResourceInfo for 'resource_downloaded' event"
                )
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

        elif media_event.name == "storage_removed":
            self._media_state_need_sync = True
            if not await self._init_media_state():
                self.logger.error("Failed to initialize media indexing")

        elif media_event.name == "indexing_state_changed":
            self._indexing_state = IndexingState(media_event.data["new_state"])
            if self._indexing_state is IndexingState.indexed:
                self._media_state_need_sync = True
                if not await self._init_media_state():
                    self.logger.error("Failed to initialize media indexing")

        else:
            self.logger.error(f"Unknown MediaEvent '{media_event}'")

    async def _areset_state(self):
        self._media_state = None
        self._media_state_need_sync = True

        if self._indexing_state is not IndexingState.not_indexed:
            event = MediaEvent(
                "indexing_state_changed",
                {"new_state": "NOT_INDEXED"},
            )
            await self.context.machine._aprocess_event(event)

        if self.context.connection._websocket is not None:
            try:
                await self.context.connection._websocket.aclose()
            except ConnectionError:
                pass
            finally:
                self.context.connection._websocket = None

    def _reset_state(self):
        try:
            self._loop.run_async(self._areset_state).result_or_cancel(
                timeout=self.context.connection.TIMEOUT_MEDIA
            )
        except TimeoutError:
            pass


class MediaList:
    def __init__(self, logger: Logger):
        self.logger = logger

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    def media_info(
        self, media_id: Optional[str] = None
    ) -> Union[MediaInfo, dict[str, MediaInfo], None]:
        """Returns a MediaInfo object if `media_id` is requested, or a list of all
        available media info otherwise.

        :param media_id: Media id to request info for, defaults to None
        :type media_id: Optional[str], optional
        :return: A MediaInfo of the media id, or a dict of all the available media ids
        with their info
        :rtype: Union[MediaInfo, dict[str, MediaInfo], None]
        """
        if self.context.state._media_state is not None:
            if media_id is not None:
                try:
                    return self.context.state._media_state[media_id]
                except KeyError:
                    self.logger.error(f"No such media ({media_id})")
                    return None
            else:
                return self.context.state._media_state
        else:
            self.logger.warning(
                "No media has been registered in Olympe media database yet"
            )

    def list_media(self) -> list[str]:
        """Returns a list of all available media id

        :return: List of all available media ids
        :rtype: list[str]
        """
        if self.context.state._media_state is not None:
            return list(self.context.state._media_state.keys())
        else:
            return list()

    def list_resources(self, media_id: Optional[str] = None) -> Optional[list[str]]:
        """Returns a list of all available resources ids if media_id is `None` or a
        list of resources ids associated to the given media_id otherwise.

        :param media_id: media id to provide to get its specific resources, defaults to None
        :type media_id: Optional[str], optional
        :return: list of all available resources ids if media_id is `None` or a
        list of resources ids associated to the given media_id otherwise.
        :rtype: Optional[list[str]]
        """
        if self.context.state._media_state:
            if media_id is not None:
                media = self.context.state._media_state.get(media_id)
                if media:
                    return list(media.resources.keys())
                else:
                    return list()
            else:
                return list(
                    resource_id
                    for media in self.context.state._media_state.values()
                    for resource_id in media.resources.keys()
                )
        else:
            self.logger.warning(
                "No resource has been registered in Olympe media database yet"
            )

    def last_media(self) -> Optional[MediaInfo]:
        """Returns the last media, sorted on its id

        :return: Returns the last media, sorted on its id
        :rtype: Optional[MediaInfo]
        """
        if self.context.state._media_state is None:
            return None
        try:
            return list(
                sorted(self.context.state._media_state.items(), key=lambda t: t[0])
            )[-1][1]
        except IndexError:
            return None

    def last_media_id(self) -> str:
        """Returns the last media id

        :return: Returns the last media id
        :rtype: str
        """
        last_media = self.last_media()
        if last_media is None:
            return "-1"
        return last_media.media_id

    def next_media_id(self) -> str:
        """Returns the media after the last media id

        :return: Returns the media after the last media id
        :rtype: str
        """
        return str(int(self.last_media_id()) + 1)


class MediaREST:
    def __init__(self, logger: Logger):
        self.logger = logger

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: MediaContext):
        self.context = ctx

    async def atest_webserver_response(
        self, action: str, url: str, timeout: Optional[float]
    ) -> Optional[Any]:
        if action == "get":
            response = await self.context.connection._session.get(url, timeout=timeout)
        elif action == "delete":
            response = await self.context.connection._session.delete(
                url, timeout=timeout
            )

        for _ in range(3):
            try:
                response.raise_for_status()
            except HTTPError as e:
                self.logger.warning(str(e))
                await get_running_loop().asleep(1.0)
                continue
            except CancelledError:
                raise
            except Exception:
                self.logger.exception("Unhandled exception")
                return None
            else:
                break
        else:
            raise HTTPError(f"The webserver is unavailable when getting '{url}'")
        return response

    async def _aget_media(
        self, media_id: Optional[str], timeout: Optional[float] = None
    ) -> Optional[MediaInfo]:
        """
        Returns a single media object from the REST API
        HTTP GET /api/v<version>/media/medias/<media_id>
        """
        if timeout is None:
            timeout = self.context.connection.TIMEOUT_MEDIA

        if (
            self.context.state._media_state
            and media_id in self.context.state._media_state
        ):
            # There is no need to call the REST API if the media_state event is populated
            # with the correct media id
            return self.context.state._media_state[media_id]

        self.logger.warning(
            f"Media '{media_id}' is not registered in Olympe media database yet. Picking it from the webserver"
        )
        assert (
            self.context.url._media_api_url
        ), "Media API URLs have not been set yet when requesting get media from REST API"
        try:
            response = await self.atest_webserver_response(
                "get",
                path.join(self.context.url._media_api_url, str(media_id)),
                timeout,
            )
            response_data = await response.json()
        except HTTPError:
            self.logger.error("No response from the webserver")
            return None

        media_id, media = make_media(response_data)
        if not media_id:
            self.logger.error(
                f"Media '{media_id}' is not registered on the webserver neither"
            )
            return None

        # Store value in cache for next fetch
        self.context.state._media_state[media_id] = media

        return media

    async def _aget_all_media(
        self, timeout: Optional[float] = None
    ) -> Optional[dict[str, MediaInfo]]:
        """
        Returns an array of media objects from the REST API
        HTTP GET /api/v<version>/media/medias
        """
        if timeout is None:
            timeout = self.context.connection.TIMEOUT_MEDIA

        assert (
            self.context.url._media_api_url
        ), "Media API URLs have not been set yet when requesting all media from REST API"
        try:
            response = await self.atest_webserver_response(
                "get",
                self.context.url._media_api_url,
                timeout,
            )
            response_data = await response.json()
        except HTTPError:
            self.logger.error("No response from the webserver")
            return None

        media_state = OrderedDict()
        for media in response_data or []:
            media_id, media = make_media(media)
            if not media_id:
                self.logger.error(
                    f"Media '{media_id}' is not registered on the webserver"
                )
                continue
            media_state[media_id] = media
        return media_state

    async def _adelete_media(
        self, media_id: str, timeout: Optional[float] = None
    ) -> bool:
        """
        Request the deletion of a single media through the REST API
        HTTP DELETE /api/v<version>/media/medias/<media_id>
        """
        if timeout is None:
            timeout = self.context.connection.TIMEOUT_MEDIA

        assert (
            self.context.url._media_api_url
        ), "Media API URLs have not been set yet when deleting a media from REST API"
        try:
            await self.atest_webserver_response(
                "delete",
                path.join(self.context.url._media_api_url, media_id),
                timeout=timeout,
            )
        except HTTPError:
            self.logger.error("No response from the webserver")
            return False
        return True

    async def _adelete_all_media(self, timeout: Optional[float] = None) -> bool:
        """
        Request the deletion of all media through the REST API
        HTTP DELETE /api/v<version>/media/medias
        """
        if timeout is None:
            timeout = self.context.connection.TIMEOUT_MEDIA

        assert (
            self.context.url._media_api_url
        ), "Media API URLs have not been set yet when deleting all media from REST API"
        try:
            await self.atest_webserver_response(
                "delete",
                self.context.url._media_api_url,
                timeout=timeout,
            )
        except HTTPError:
            self.logger.error("No response from the webserver")
            return False
        return True
