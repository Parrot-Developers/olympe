from logging import getLogger
from typing import NamedTuple, TypeVar

T = TypeVar("T", bound=NamedTuple)


def _replace_namedtuple(namedtuple: T, **kwds) -> T:
    try:
        return namedtuple._replace(**kwds)
    except ValueError as e:
        getLogger("olympe.utils.namedtuple").warning(e)
        return namedtuple


def _get_namedtuple_from_mapping(mapping, namedtuple_type):
    # returns the junction between fields of 'namedtuple_type' and fields of
    # mapping, and get the namedtuple from this junction
    return namedtuple_type(
        **{k: mapping[k] for k in namedtuple_type._fields if k in mapping}
    )
