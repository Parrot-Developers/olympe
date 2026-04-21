from enum import Enum


class MediaEnumBase(Enum):
    def __new__(cls, value, doc):
        self = object.__new__(cls)
        self._value_ = value
        # Each enum would be populated with its docstring
        self.__doc__ = doc
        return self

    @classmethod
    # Make it case-insensitive
    def _missing_(cls, label_name):
        lowered_label_name = label_name.lower()
        for label in cls:
            if lowered_label_name == label.name.lower():
                return label


class IndexingState(MediaEnumBase):
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


class MediaType(MediaEnumBase):
    photo = "PHOTO", "the media contains photo resources"
    video = "VIDEO", "the media contains video resources"


class PanoramaType(MediaEnumBase):
    horizontal_180 = "HORIZONTAL_180", "degrees on the horizontal plane"
    vertical_180 = "VERTICAL_180", "degrees on the vertical plane"
    spherical = "SPHERICAL", "360 degrees on the horizontal and vertical planes"
    super_wide = "SUPER_WIDE", "photos at different camera yaw for several camera pitch"


class PhotoMode(MediaEnumBase):
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


class ResourceFormat(MediaEnumBase):
    dng = "DNG", "the resource is a dng photo"
    jpg = "JPG", "the resource is a jpg photo"
    png = "PNG", "the resource is a png photo"
    mp4 = "MP4", "the resource is an mp4 video"
