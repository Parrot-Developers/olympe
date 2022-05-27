from pathlib import Path
from typing import Union
from . import callonce
import logging
import os
import os.path
import requests
import shutil
import tempfile
import uuid
import urllib.parse


logger = logging.getLogger(__name__)


@callonce()
def olympe_data_dir():
    XDG_DATA_HOME = Path(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    )
    data_dir = XDG_DATA_HOME / "parrot" / "olympe"
    data_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
    return data_dir


@callonce()
def olympe_cache_dir():
    XDG_CACHE_HOME = Path(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    )
    cache_dir = XDG_CACHE_HOME / "parrot" / "olympe"
    cache_dir.mkdir(mode=0o750, exist_ok=True, parents=True)
    return cache_dir


@callonce()
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


def directory_is_writable(dir_path: Union[str, Path]):
    try:
        write_check = Path(dir_path) / ".write_check"
        if write_check.exists():
            write_check.unlink()
        write_check.touch()
        write_check.unlink()
        return True
    except OSError:
        return False


class Resource:
    def __init__(self, url: Union[str, Path], temporary=False):
        parsed_url = urllib.parse.urlparse(str(url))
        self._url = parsed_url.geturl()
        self._temporary = temporary
        if not parsed_url.scheme:
            self._scheme = "file"
        else:
            self._scheme = parsed_url.scheme
        if self.scheme not in ("file", "http", "https"):
            raise ValueError(f"Unsupported resource scheme '{self._scheme}'")
        self._path = None

    @property
    def url(self):
        return self._url

    @property
    def scheme(self):
        return self._scheme

    @property
    def temporary(self):
        return self._temporary

    def exists(self):
        if self.scheme == "file":
            return Path(self.url).exists()
        elif self.scheme in ("http", "https"):
            r = requests.head(self.url).raise_for_status()
            return r.status_code == requests.codes.ok

    def get(self, timeout=None):
        if self._path is not None:
            return self._path
        if self.scheme == "file":
            if not self.exists():
                raise RuntimeError(f"Resource '{self.url}' does not exists")
            self._path = Path(self.url)
        elif self.scheme in ("http", "https"):
            r = requests.get(self.url, stream=True, timeout=timeout)
            if r.status_code != requests.codes.ok:
                raise RuntimeError(
                    f"Resource '{self.url}' unexpected HTTP status:"
                    f" '{r.status_code}'"
                )
            r.raw.decode_content = True
            filename = Path(self.url).stem + Path(self.url).suffix
            random_uuid = str(uuid.uuid4())
            self._path = olympe_cache_dir() / "resources" / random_uuid / filename
            self._path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)
            with open(self._path, "wb") as f:
                shutil.copyfileobj(r.raw, f)
            self._temporary = True
        return self._path

    def copy(self, to_path: Union[str, Path]):
        if self.temporary:
            return self.move(to_path)
        return self._do_copy(to_path)

    def move(self, to_path: Union[str, Path]):
        from_path = self.get()
        to_path = Path(to_path)
        if to_path.exists():
            if from_path.samefile(to_path):
                return self
        else:
            to_path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)
        from_path.rename(to_path)
        self._path = to_path
        self._temporary = False
        return self

    def _do_copy(self, to_path: Union[str, Path]):
        from_path = self.get()
        to_path = Path(to_path)
        if to_path.exists():
            if from_path.samefile(to_path):
                return self
        else:
            to_path.parent.mkdir(mode=0o750, exist_ok=True, parents=True)
        with from_path.open("rb") as src:
            with to_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return Resource(to_path)
