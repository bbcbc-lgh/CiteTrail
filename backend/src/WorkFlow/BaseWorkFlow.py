from collections.abc import AsyncIterator
from typing import Any


class BaseWorkFlow:
    async def run(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError
