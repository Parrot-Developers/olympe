# Handle that Pyright considers every MediaContext variables as 'None' because of how
# they are defined
# pyright: reportOptionalMemberAccess=false

from logging import Logger
from os import path
from typing import List, Optional, TYPE_CHECKING, Union

from olympe.concurrent import Loop
from olympe.utils.namedtuple import _replace_namedtuple, _get_namedtuple_from_mapping

from .base import ResourceInfo

if TYPE_CHECKING:
    from .media import MediaContext


class ResourceList:
    def __init__(
        self,
        logger: Logger,
        loop: Loop,
    ):
        self.logger = logger
        self._loop = loop

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: "MediaContext"):
        self.context = ctx

    def resource_info(
        self,
        media_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        with_md5: bool = False,
        with_signature: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[ResourceInfo]:
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
            self._aresource_info,
            media_id=media_id,
            resource_id=resource_id,
            with_md5=with_md5,
            with_signature=with_signature,
        ).result_or_cancel(timeout=timeout)

    async def _aresource_info(
        self,
        media_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        with_md5: bool = False,
        with_signature: bool = False,
    ) -> Union[List[ResourceInfo], ResourceInfo, None]:
        if media_id is None and resource_id is None:
            raise ValueError(
                "Unable to retrieve a ResourceInfo without media_id or resource_id"
            )
        if self.context.state._media_state is None:
            raise RuntimeError(
                "Unable to retrieve a ResourceInfo if the media library is empty. You"
                "may not be currently connected to the drone media API"
            )
        try:
            if media_id is not None:
                media = self.context.state._media_state[media_id]
                # If no resource id is precised, return all the resources related to the media
                if resource_id is None:
                    return list(media.resources.values())
                # If the resource id has md5
                if not media.resources[resource_id].md5 and with_md5:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        md5=(
                            await self.context.resource_rest._aget_md5_with_mediaid_resourceid(
                                resource_id
                            )
                        ).md5,
                    )
                # If the resource id has a signature
                if media.resources[resource_id].signature and with_signature:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        signature=(
                            await self.context.resource_rest._aget_signature_with_mediaid_resourceid(
                                media.resources[resource_id]
                            )
                        ).signature,
                    )
                # If the resource id has neither md5 nor signature
                else:
                    media.resources[resource_id] = _replace_namedtuple(
                        media.resources[resource_id],
                        # Re init signature when with_signature=False
                        signature=None,
                    )
                return media.resources[resource_id]
            else:
                # resource_id can't be none in that case
                for media in self.context.state._media_state.values():
                    if resource_id in media.resources:
                        if not media.resources[resource_id].md5 and with_md5:
                            media.resources[resource_id] = _replace_namedtuple(
                                media.resources[resource_id],
                                md5=(
                                    await self.context.resource_rest._aget_md5_with_mediaid_resourceid(
                                        resource_id  # type: ignore reportArgumentType ; Linter failure
                                    )
                                ).md5,
                            )
                        return media.resources[resource_id]
        except KeyError:
            self.logger.error(
                f"No such media/resource: media_id={media_id}, resource_id={resource_id}"
            )
        return None

    async def _aget_resource(
        self, resource_id: str, timeout: Optional[float] = None
    ) -> Optional[ResourceInfo]:
        """
        Get a remote resource in chunk and returns the associated
        generator object
        """
        resource_info = await self._aresource_info(resource_id=resource_id)
        if resource_info is None or not resource_info.url:  # type: ignore reportAttributeAccessIssue
            # because _aresource_info never returns a list if a resource_id is provided
            return None

        return await self.context.resource_rest._aget_resource_from_url(
            f"{self.context.url._base_url}{resource_info.url}",  # type: ignore reportAttributeAccessIssue
            # because _aresource_info never returns a list if a resource_id is provided
            timeout=timeout,
        )

    async def _aget_thumbnail(
        self, resource_id: str, timeout: Optional[float] = None
    ) -> Optional[ResourceInfo]:
        """
        Get a remote resource thumbnail in chunk and returns the associated
        generator object
        """
        resource_info = await self._aresource_info(resource_id=resource_id)
        if resource_info is None or not resource_info.thumbnail:  # type: ignore reportAttributeAccessIssue
            # because _aresource_info never returns a list if a resource_id is provided
            return None

        return await self.context.resource_rest._aget_resource_from_url(
            f"{self.context.url._base_url}{resource_info.thumbnail}",  # type: ignore reportAttributeAccessIssue
            # because _aresource_info never returns a list if a resource_id is provided
            timeout=timeout,
        )


class ResourceREST:
    def __init__(self, logger: Logger):
        self.logger = logger

        # Context to get variable members from another Media subdivided class
        self.context: Optional[MediaContext] = None

    def set_context(self, ctx: "MediaContext"):
        self.context = ctx

    async def _aget_resource_from_url(
        self, url: str, timeout: Optional[float] = None
    ) -> Optional[ResourceInfo]:
        """
        Downloads a remote resource in chunk and returns the associated
        generator object
        """
        response_data = await self.context.rest.atest_webserver_response(
            "get", url, timeout
        )
        if not response_data:
            self.logger.error("No response from the webserver")
            return None

        # return _get_namedtuple_from_mapping(response_data, ResourceInfo)
        return response_data

    async def _aget_signature_with_mediaid_resourceid(
        self, resource: ResourceInfo, timeout: Optional[float] = None
    ) -> ResourceInfo:
        """
        Returns a (media_id, resource_id, signature) namedtuple for the given resource
        """
        response = await self.context.rest.atest_webserver_response(
            "get", resource.signature, timeout
        )
        signature = await response.text()
        data = dict(
            media_id=resource.media_id,
            resource_id=resource.resource_id,
            signature=signature,
        )
        return _get_namedtuple_from_mapping(data, ResourceInfo)

    async def _aget_md5_with_mediaid_resourceid(
        self, resource_id: str, timeout: Optional[float] = None
    ) -> Optional[ResourceInfo]:
        """
        Returns a (media_id, resource_id, md5) namedtuple for the given resource_id
        from the REST API
        HTTP GET /api/v<version>/media/md5/<resource_id>
        """
        assert (
            self.context.url._md5_api_url
        ), "Media API URLs have not been set yet when requesting get resource from REST API"
        response = await self.context.rest.atest_webserver_response(
            "get",
            path.join(self.context.url._md5_api_url, str(resource_id)),
            timeout,
        )
        response_data = await response.json()
        if not response_data:
            self.logger.error("No response from the webserver")
            return None

        return _get_namedtuple_from_mapping(response_data, ResourceInfo)

    async def _adelete_resource(
        self, resource_id: str, timeout: Optional[float] = None
    ) -> bool:
        """
        Request the deletion of a single resource through the REST API
        HTTP DELETE /api/v<version>/media/resources/<resource_id>
        """
        assert (
            self.context.url._resource_api_url
        ), "Media API URLs have not been set yet when deleting a resource from REST API"
        response = await self.context.rest.atest_webserver_response(
            "delete",
            path.join(self.context.url._resource_api_url, str(resource_id)),
            timeout,
        )
        if not response:
            self.logger.error("No response from the webserver")
            return False
        return True
