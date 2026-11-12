"""搜索公开网页、读取原文并记录来源。"""

import http.client
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from .contracts import Evidence
from .documents import search_documents


class WebSourceError(RuntimeError):
    """公开来源不可读取或不符合访问范围。"""


def _public_address(url: str):
    if any(ord(char) < 33 for char in url) or "\\" in url:
        raise WebSourceError("网页地址格式无效。")
    try:
        parsed = urlsplit(url)
        port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or (parsed.scheme, port) not in {("https", 443), ("http", 80)}):
            raise WebSourceError("仅可访问使用标准端口的公开 HTTP(S) 网页。")
        host = parsed.hostname.encode("idna").decode("ascii")
        entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        raise WebSourceError("无法解析公开网页地址。") from None
    addresses = tuple(dict.fromkeys(entry[4][0] for entry in entries))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        effective = getattr(ip, "ipv4_mapped", None) or ip
        if not effective.is_global or effective.is_multicast:
            raise WebSourceError("该网页地址指向本地或保留网络。")
    if not addresses:
        raise WebSourceError("未找到可访问的公开地址。")
    return parsed, host, port, addresses


def fetch_public(url: str, limit: int = 1_500_000) -> tuple[str, str, bytes]:
    for _ in range(4):
        parsed, host, port, addresses = _public_address(url)
        conn = http.client.HTTPConnection(host, port, timeout=7)
        try:
            # 连接已核验的地址，TLS 仍核对原主机名。每次重定向独立校验。
            last_error = None
            for address in sorted(addresses, key=lambda value: ":" in value)[:3]:
                try:
                    conn.sock = socket.create_connection((address, port), timeout=7)
                    break
                except OSError as error:
                    last_error = error
            if conn.sock is None:
                raise WebSourceError("无法连接公开网页。") from last_error
            if parsed.scheme == "https":
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=host)
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            conn.request("GET", path, headers={"User-Agent": "analysis-agent/1.0 (public source reader)", "Accept-Encoding": "identity"})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308} and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise WebSourceError(f"公开网页返回 HTTP {response.status}。")
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise WebSourceError("公开网页超过单页读取上限。")
            return urlunsplit(parsed._replace(fragment="")), response.getheader("Content-Type", ""), raw
        except (OSError, UnicodeError, http.client.HTTPException):
            raise WebSourceError("公开网页连接失败或响应无法读取。") from None
        finally:
            conn.close()
    raise WebSourceError("公开网页重定向次数超过上限。")


def _search_urls(query: str) -> list[str]:
    domain = re.search(r"(?:^|\s)site:([a-zA-Z0-9.-]+)(?=\s|/|$)", query)
    scope = domain.group(1).casefold().strip(".") if domain else None

    def allowed(url: str) -> bool:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
        return scope is None or host == scope or host.endswith("." + scope)

    rss = "https://www.bing.com/search?" + urlencode({"format": "rss", "q": query})
    try:
        _, _, raw = fetch_public(rss, 350_000)
        root = ElementTree.fromstring(raw)
        links = [item.text for item in root.findall("./channel/item/link") if item.text and allowed(item.text)]
        if links:
            return links[:4]
    except (WebSourceError, ElementTree.ParseError):
        pass
    if scope and scope not in {"wikipedia.org", "en.wikipedia.org", "zh.wikipedia.org"}:
        return []
    language = "zh" if re.search(r"[\u3400-\u9fff]", query) else "en"
    endpoint = f"https://{language}.wikipedia.org/w/api.php?" + urlencode({
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 4,
        "prop": "info", "inprop": "url", "format": "json", "formatversion": 2,
    })
    try:
        _, _, raw = fetch_public(endpoint, 350_000)
        pages = json.loads(raw).get("query", {}).get("pages", [])
        return [page["fullurl"] for page in sorted(pages, key=lambda item: item.get("index", 0))
                if page.get("fullurl") and allowed(page["fullurl"])]
    except (WebSourceError, ValueError, TypeError, AttributeError) as error:
        raise WebSourceError("公开搜索入口暂时无法返回结果。") from error


class PageText(HTMLParser):
    _skip = {"script", "style", "nav", "footer", "aside", "svg", "noscript"}
    _blocks = {"p", "h1", "h2", "h3", "h4", "li", "dd", "dt", "td", "pre", "blockquote", "div", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.published_at = None
        self.paragraphs: list[str] = []
        self._buffer: list[str] = []
        self._blocked: list[str] = []
        self._title = False

    def _flush(self):
        text = " ".join("".join(self._buffer).split())
        self._buffer.clear()
        if len(text) >= 20:
            self.paragraphs.extend(text[index:index + 1000] for index in range(0, len(text), 1000))

    def handle_starttag(self, tag, attrs):
        if tag in self._skip:
            if not self._blocked:
                self._flush()
            self._blocked.append(tag)
        if self._blocked:
            return
        if tag in self._blocks:
            self._flush()
        if tag == "title":
            self._title = True
        if tag == "meta":
            meta = dict(attrs)
            name = meta.get("property") or meta.get("name") or meta.get("itemprop")
            if name in {"article:published_time", "datePublished", "DC.date.issued"}:
                value = meta.get("content") or ""
                if re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                    self.published_at = value[:10]

    def handle_endtag(self, tag):
        if self._blocked:
            if tag == self._blocked[-1]:
                self._blocked.pop()
            return
        if tag == "title":
            self._title = False
        if tag in self._blocks:
            self._flush()

    def handle_data(self, data):
        if not self._blocked:
            if self._title:
                self.title += data
            else:
                self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def _page_evidence(url: str) -> tuple[Evidence, ...]:
    final, mime, raw = fetch_public(url)
    if not any(kind in mime.lower() for kind in ("text/html", "text/plain", "application/xhtml+xml")):
        raise WebSourceError("网页来源需要可读取的 HTML 或纯文本。")
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", mime, re.IGNORECASE)
    try:
        text = raw.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    parser = PageText()
    if "text/plain" in mime.lower():
        parser.paragraphs = [line for line in text.splitlines() if line.strip()]
    else:
        parser.feed(text)
        parser.close()
    version = sha256(raw).hexdigest()
    page_id = sha256(f"{final}\n{version}".encode()).hexdigest()[:12]
    accessed = datetime.now(timezone.utc).date().isoformat()
    title = " ".join(parser.title.split())[:240] or urlsplit(final).hostname
    return tuple(
        Evidence(f"W-{page_id}-{index}", final, None, paragraph, version, f"网页段落 {index}", title, parser.published_at, accessed)
        for index, paragraph in enumerate(dict.fromkeys(parser.paragraphs), start=1)
    )


def web_evidence(query: str, *, hybrid: bool, url: str | None = None) -> tuple[tuple[Evidence, ...], str]:
    urls = [url] if url else _search_urls(query)
    evidence: list[Evidence] = []
    read, failed = 0, 0
    terms = re.findall(r"[A-Za-z0-9]{2,}|[\u3400-\u9fff]{2,}", query.casefold())
    for target in dict.fromkeys(urls):
        try:
            rows = _page_evidence(target)
        except WebSourceError:
            failed += 1
            continue
        if not rows:
            continue
        read += 1
        ranked = sorted(rows, key=lambda row: -sum(term in row.text.casefold() for term in terms))
        semantic = search_documents(query, query, tuple(ranked[:70]), hybrid=True)[:2] if hybrid else ()
        selected = tuple({row.evidence_id: row for row in (*ranked[:3], *semantic)}.values())
        evidence.extend(selected)
        if read >= 2:
            break
    return tuple(evidence), f"读取 {read} 个公开网页，取得 {len(evidence)} 段原文，{failed} 个网页无法读取。"
