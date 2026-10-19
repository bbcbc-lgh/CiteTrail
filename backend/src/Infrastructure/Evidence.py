import html
import re
from urllib.parse import urlparse

from src.Schema.Source import Source


def clean_source(source: Source) -> Source:
    text = html.unescape(re.sub(r"<[^>]+>", " ", source.snippet))
    blocks = re.findall(r"```([^\n`]*)\n(.*?)```", text, flags=re.S)
    code = "\n\n".join(f"```{language.strip()}\n{body.strip()}\n```" for language, body in blocks
                         if body.strip() and not re.search(r"…|\.\.\.|\[DONE\]", body))
    text = re.sub(r"```.*?(?:```|$)", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(
        r"#{1,6}\s*|Image\s*\d+|当前文章被以下社区和专栏收录[：:]?|"
        r"分享到?\s*(?:微信|QQ|新浪微博)?|分享复制链接|扫一扫|"
        r"Table of Contents|目录|登录|注册|展开全文|复制代码|"
        r"余额是钱包充值的虚拟货币[^。！？]*[。！？]",
        " ",
        text,
        flags=re.I,
    )
    # ponytail: conservative fragment rules; use full-page extraction if search snippets stay too noisy.
    fragments = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n+", text)
    readable = []
    for fragment in fragments:
        fragment = re.sub(r"^(?:\s*\.{3}|\s*…)+\s*", "", fragment).strip(" \t-*#")
        if len(fragment) < 10 or len(fragment) > 360 or re.search(
            r"commented on|Activity\b|@[\w-]+|登录|点赞|收藏|展开全文|复制代码|"
            r"分享|扫一扫|Table of Contents|目录|余额|VIP|付费专栏|"
            r"让我(?:展示|给你)|如有问题.{0,8}欢迎指正|文章被收录|专栏|"
            r"个人博客|的博客|personal blog|\d+\s*(?:words|mins)|"
            r"(?:below is an example|full source code can be found|ready to implement)|"
            r"//|[{};]|\"\"\"|'''|^(?:['\"]|Conclusion\b)|"
            r"(?:const|async def|def|except|raise|finally|import|yield|while True|break)\b|"
            r"if await|request\.|state\s*=|random\.|HELLOWORLD|"
            r"\w+\s*\(|(?:…|\.\.\.)$", fragment, re.I
        ):
            continue
        if not re.search(r"[。！？.!?]$", fragment):
            continue
        fragment = re.sub(r"^(?:\d+\.)*\d+\s+", "", fragment)
        readable.append(fragment)
    parsed = urlparse(source.url)
    host = (parsed.hostname or "").lower()
    community = bool(re.search(r"/(?:issues|discussions|questions|forum)/", parsed.path)) or host in {"stackoverflow.com", "www.reddit.com", "reddit.com"}
    official = host.startswith("docs.") or host in {"fastapi.tiangolo.com", "developer.mozilla.org", "www.w3.org", "html.spec.whatwg.org", "docs.python.org"}
    selected = []
    for fragment in dict.fromkeys(readable):
        if len(" ".join(selected + [fragment])) > 700 or len(selected) == 6:
            break
        selected.append(fragment)
    cleaned = " ".join(selected)
    return source.model_copy(update={"snippet": cleaned or "搜索结果未提供可直接引用的正文摘要。", "code": code,
                                     "source_type": "community" if community else "official" if official else "article"})


def markdown_label(value: str) -> str:
    return re.sub(r"[\[\]\\\r\n]", " ", value).strip()
