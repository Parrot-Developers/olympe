from typing import Optional, Protocol, TypeVar

PointeeType = TypeVar("PointeeType")


class PointerType(Protocol[PointeeType]):
    def __init__(self, pointee: Optional[PointeeType] = None):
        ...

    def __bool__(self) -> bool:
        ...

    def contents(self) -> Optional[PointeeType]:
        ...
