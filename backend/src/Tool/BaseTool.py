from typing import Any


class BaseTool:
    name: str
    description: str

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
