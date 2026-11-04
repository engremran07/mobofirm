from functools import lru_cache
from importlib.metadata import version


@lru_cache(maxsize=1)
def _release_version() -> str:
    return version("mobofirm")


def release_metadata(_request) -> dict[str, str]:
    return {"release_version": _release_version()}
