from collections.abc import AsyncIterator
from typing import Any


class BasePipeline:
    async def run(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError
