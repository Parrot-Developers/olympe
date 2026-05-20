from __future__ import annotations

import os
import os.path
import re
import shutil
import tempfile
import uuid
import urllib.parse

from pathlib import Path
from typing import Optional, Union

from olympe.connection.http import Session

MAX_NB_CACHE_FILES = 100


def olympe_data_dir():
    XDG_DATA_HOME = Path(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    )
    data_dir = XDG_DATA_HOME / "parrot" / "olympe"
    data_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
    return data_dir


def olympe_cache_dir():
    XDG_CACHE_HOME = Path(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    )
    cache_dir = XDG_CACHE_HOME / "parrot" / "olympe"
    cache_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
    return cache_dir


def olympe_tmp_dir():
    XDG_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    tmp_dir = XDG_RUNTIME_DIR / "parrot" / "olympe"
    tmp_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
    return tmp_dir


def mkstemp(suffix=None, prefix=None, text=False):
    return tempfile.mkstemp(
        suffix=suffix, prefix=prefix, text=text, dir=olympe_tmp_dir()
    )


def TemporaryFile(*args, **kwds):
    kwds.pop("dir", None)
    kwds["dir"] = olympe_tmp_dir()
    return tempfile.TemporaryFile(*args, **kwds)


def is_directory_writable(dir_path: Union[str, Path]) -> bool:
    try:
        if not Path(dir_path).is_dir():
            return False
        write_check = Path(dir_path) / ".write_check"
        if write_check.exists():
            write_check.unlink()
        write_check.touch()
        write_check.unlink()
        return True
    except OSError:
        return False


class Resource:
    def __init__(
        self,
        url_or_path: Union[str, Path],
        session: Session,
        timeout: Optional[float] = None,
    ):
        parsed_url_or_path = urllib.parse.urlparse(str(url_or_path))
        # Retrieve the scheme of the resource given, either it is a path, an url or
        # something else
        if not parsed_url_or_path.scheme:
            self._scheme = "file"
        else:
            self._scheme = parsed_url_or_path.scheme
        if self._scheme not in ("file", "http", "https"):
            raise ValueError(f"Unsupported resource scheme '{self._scheme}'")
        # Retrieve the whole initial url, before getting parsed and split with urllib.parse.urlparse
        self._url_or_path = parsed_url_or_path.geturl()

        self._path = None
        self._dir_path = olympe_cache_dir() / "resources"

        self._session = session
        self._timeout = timeout

    @property
    def timeout(self) -> Optional[float]:
        return self._timeout

    async def exists(self) -> bool:
        if self._scheme == "file":
            return Path(self._url_or_path).exists()
        elif self._scheme in ("http", "https"):
            r = await self._session.head(self._url_or_path)
            return r.status_code == 200
        return False

    async def get(self) -> Path:
        # If the resource has already been requested, there is no need to do it all over
        # again
        if self._path is not None:
            return self._path

        # self.scheme is required to be in ("file", "http", "https"), otherwise a
        # ValueError is raised at initialization

        # If the resource is a file, and if that file exists, then return merely the
        # filepath of the resource
        if self._scheme == "file":
            if not await self.exists():
                raise RuntimeError(f"Resource '{self._url_or_path}' does not exists")
            self._path = Path(self._url_or_path)
        # If the resource is a url, it is recommanded to check first if it already
        # exists in cache
        elif self._scheme in ("http", "https"):
            # HEAD Request to get its ETag and check if the url has already been cached
            r = await self._session.head(self._url_or_path)
            r.raise_for_status()

            filename = Path(self._url_or_path).stem + Path(self._url_or_path).suffix

            etag = r.headers.get("etag")
            # In case the server does not provide an ETag, a random identifier has to be
            # picked up instead
            if etag is None:
                etag = str(uuid.uuid4())
                self._path = self._dir_path / etag / filename
            else:
                # Parse the ETag header provided by the server
                pattern = rb'(?:W/)?"(?P<etag>[^"]*)"'
                result = re.search(pattern, etag)
                if result is None:
                    raise RuntimeError("The ETag has not been parsed successfully")
                etag = result.group("etag")
                # Compare the etag with the others in the cache directory
                self._path = self._dir_path / str(etag, encoding="utf-8") / filename
                if self._path.exists():
                    return self._path
            self._dir_path.mkdir(mode=0o750, exist_ok=True, parents=True)
            # Before downloading a new resource, empty the cache folder if too big
            cache_counter = len(list(self._dir_path.iterdir()))
            # Threshold is set by MAX_NB_CACHE_FILES
            if cache_counter >= MAX_NB_CACHE_FILES:
                for path in self._dir_path.iterdir():
                    if path.is_dir():
                        # Empty and delete the others
                        shutil.rmtree(self._dir_path / path)

            # If no etag matches nor is given, it means that the mission is not in the
            # cache directory and has to be downloaded with a GET request
            r = await self._session.get(self._url_or_path)
            r.raise_for_status()
            # Get the path of the payload to create a new directory to host the future
            # payload
            self._path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)
            # Get the payload from the mission to move into the new self._dir_path/etag
            # folder
            with self._path.open("wb") as file:
                async for chunk in r:
                    file.write(chunk)
        assert self._path
        return self._path

    async def copy(self, to_path: Union[str, Path]) -> Resource:
        from_path = await self.get()
        to_path = Path(to_path)
        if to_path.exists():
            if from_path.samefile(to_path):
                return self
        else:
            to_path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)

        with from_path.open("rb") as src:
            with to_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return Resource(to_path, self._session)

    async def move(self, to_path: Union[str, Path]) -> Resource:
        from_path = await self.get()
        to_path = Path(to_path)
        if to_path.exists():
            if from_path.samefile(to_path):
                return self
        else:
            to_path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)

        from_path.rename(to_path)
        self._path = to_path
        return self
