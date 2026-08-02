from src.Infrastructure.SearchClient import SearchClient
from src.Schema.Source import Source
from src.Tool.BaseTool import BaseTool


class SearchTool(BaseTool):
    name = "search"
    description = "搜索公开网络资料，并返回可供研究流程引用的来源。"

    def __init__(self, client: SearchClient):
        self.client = client

    async def run(self, query: str) -> list[Source]:
        return await self.client.search(query)
