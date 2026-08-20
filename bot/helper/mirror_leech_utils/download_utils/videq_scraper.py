import gzip
import re
import zlib

from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from bot.helper.ext_utils.exceptions import DirectDownloadLinkException

VIDEQ_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0"
)
VIDEQ_EMBED_CACHE = {}
VIDEQ_PAGE_REFERER = "https://vide20.com/"
VIDEQ_EMBED_REFERER = "https://embed.vidoycdn.com/"
VIDEQ_DIRECT_BASE_URL = "https://s3.vidoy-storage.com"


def videq_decode_js_string_literal(value):
    if not value:
        return value
    normalized = value
    for _ in range(2):
        normalized = re.sub(
            r"\\\\(u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2})",
            r"\\\1",
            normalized,
        )
    normalized = re.sub(
        r"\\x([0-9A-Fa-f]{2})",
        lambda m: chr(int(m.group(1), 16)),
        normalized,
    )
    normalized = re.sub(
        r"\\u([0-9A-Fa-f]{4})",
        lambda m: chr(int(m.group(1), 16)),
        normalized,
    )
    return normalized


def videq_get_default_headers():
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "id,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "DNT": "1",
        "Referer": VIDEQ_PAGE_REFERER,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": VIDEQ_USER_AGENT,
    }


def videq_get_embed_headers():
    headers = videq_get_default_headers()
    headers["Referer"] = VIDEQ_PAGE_REFERER
    headers["Sec-Fetch-Site"] = "cross-site"
    headers["Sec-Fetch-Dest"] = "iframe"
    headers["Sec-Fetch-Mode"] = "navigate"
    return headers


def videq_get_download_headers(referer):
    return {
        "Accept": "*/*",
        "Accept-Encoding": "identity;q=1, *;q=0",
        "Accept-Language": "id,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": referer,
        "User-Agent": VIDEQ_USER_AGENT,
    }


def videq_request_download(url, referer, range_header=None):
    if not url:
        return None
    headers = videq_get_download_headers(referer)
    if range_header:
        headers["Range"] = range_header
    return http_request(url, headers=headers)


def videq_extract_video_id(raw_url):
    try:
        path = urlparse(raw_url).path
        parts = [part for part in path.split("/") if part]
        return parts[-1] if parts else None
    except Exception:
        return None


def videq_build_direct_url(object_key):
    if not object_key:
        return None
    return f"{VIDEQ_DIRECT_BASE_URL}/{quote(object_key, safe='')}"


def videq_build_legacy_download_url(object_key):
    if not object_key:
        return None
    return (
        f"https://embed.vidoycdn.com/video-uploads.php?key={quote(object_key, safe='')}"
    )


def videq_get_object_key(video_id):
    meta = videq_load_embed_meta(video_id)
    return meta.get("objectKey") if meta else None


def videq_fetch_info(video_id, object_key):
    meta = videq_load_embed_meta(video_id)
    if not meta:
        return None
    title = meta.get("title")
    title = title.strip() if title else None
    bytes_value = None
    if object_key:
        try:
            bytes_value = videq_probe_file_size(object_key)
        except Exception:
            bytes_value = None
    size = format_bytes(bytes_value) if bytes_value else None
    return {"title": title, "size": size, "bytes": bytes_value}


def videq_get_folder_links(folder_url):
    url_obj = urlparse(folder_url)
    origin = f"{url_obj.scheme}://{url_obj.netloc}"
    folder_path = url_obj.path
    links = set()
    page = 1
    pattern = re.compile(
        r"<div[^>]*class=[\"'][^\"']*video-items[^\"']*[\"'][^>]*>\s*"
        r"<a[^>]*href=[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    while True:
        page_path = folder_path if page == 1 else f"{folder_path}?p={page}"
        resp = http_request(f"{origin}{page_path}", headers=videq_get_default_headers())
        if not resp_ok(resp):
            close_response(resp)
            break
        html = read_response_text(resp)
        close_response(resp)
        for match in pattern.finditer(html):
            href = match.group(1)
            full = href if href.startswith("http") else origin + href
            links.add(full)
        next_href = f"{folder_path}?p={page + 1}"
        if f'href="{next_href}"' in html or f"href='{next_href}'" in html:
            page += 1
            continue
        break
    return list(links)


def videq_load_embed_meta(video_id):
    if not video_id:
        return None
    cached = VIDEQ_EMBED_CACHE.get(video_id)
    if cached is not None:
        return cached
    embed_url = f"https://embed.vidoycdn.com/w.php?id={video_id}"
    resp = http_request(embed_url, headers=videq_get_embed_headers())
    if not resp_ok(resp):
        close_response(resp)
        return None
    html = read_response_text(resp)
    close_response(resp)
    object_match = re.search(r'objectKey:\s*"([^"]+)"', html, re.IGNORECASE)
    title_match = re.search(r'title:\s*"([^"]+)"', html, re.IGNORECASE)
    data = {
        "html": html,
        "objectKey": videq_decode_js_string_literal(object_match.group(1).strip())
        if object_match
        else None,
        "title": videq_decode_js_string_literal(title_match.group(1).strip())
        if title_match
        else None,
    }
    VIDEQ_EMBED_CACHE[video_id] = data
    return data


def videq_probe_file_size(object_key):
    if not object_key:
        return None
    range_header = "bytes=0-0"
    direct_url = videq_build_direct_url(object_key)
    resp = videq_request_download(direct_url, VIDEQ_PAGE_REFERER, range_header)
    if not resp_ok(resp):
        close_response(resp)
        legacy_url = videq_build_legacy_download_url(object_key)
        resp = videq_request_download(legacy_url, VIDEQ_EMBED_REFERER, range_header)
    if not resp_ok(resp):
        close_response(resp)
        return None
    content_range = resp.headers.get("Content-Range")
    if content_range:
        match = re.search(r"/\s*(\d+)\s*$", content_range)
        if match:
            value = int(match.group(1))
            close_response(resp)
            return value
    length = resp.headers.get("Content-Length")
    close_response(resp)
    if not length:
        return None
    value = int(length) if length.isdigit() else None
    return value


def normalize_input_url(raw):
    if not raw:
        return raw
    current = raw
    for _ in range(3):
        try:
            decoded = unquote(current)
        except Exception:
            break
        if decoded == current:
            break
        current = decoded
    return current


def format_bytes(bytes_value):
    if not bytes_value:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    value = float(bytes_value)
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    fixed = 0 if value >= 10 or idx == 0 else 1
    return f"{value:.{fixed}f} {units[idx]}"


def read_response_text(resp):
    raw = resp.read()
    encoding = (resp.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def http_request(url, headers=None, timeout=25):
    req = Request(url, headers=headers or {})
    try:
        return urlopen(req, timeout=timeout)
    except HTTPError as exc:
        return exc
    except URLError:
        return None


def resp_ok(resp):
    if not resp:
        return False
    try:
        code = resp.getcode()
    except Exception:
        return False
    return 200 <= code < 300


def close_response(resp):
    if not resp:
        return
    try:
        resp.close()
    except Exception:
        pass


def build_video_entry(page_url):
    video_id = videq_extract_video_id(page_url)
    if not video_id:
        return None
    object_key = videq_get_object_key(video_id)
    if not object_key:
        return None
    info = videq_fetch_info(video_id, object_key)
    if not info or not info.get("title"):
        return None
    decoded_key = videq_decode_js_string_literal(object_key)
    download = videq_build_legacy_download_url(decoded_key or object_key)
    if not download:
        return None
    return {
        "filename": info["title"],
        "size": info["size"],
        "bytes": info["bytes"],
        "directlink": download,
    }


def resolve_payload(raw_url):
    if not raw_url:
        raise ValueError("Missing url")
    input_url = normalize_input_url(raw_url)
    url_obj = urlparse(input_url)
    if not url_obj.scheme or not url_obj.netloc:
        raise ValueError("Invalid url")

    if "/f/" in url_obj.path:
        links = videq_get_folder_links(url_obj.geturl())
        entries = []
        for link in links:
            entry = build_video_entry(link)
            if entry:
                entries.append(entry)
        if not entries:
            raise ValueError("No videos found")
        return entries

    entry = build_video_entry(input_url)
    if not entry:
        raise ValueError("Video info not found")
    return entry


def videq_entries_to_details(entries, title=None):
    details = {"contents": [], "title": title or "", "total_size": 0}
    for entry in entries:
        if not entry:
            continue
        filename = entry.get("filename")
        download_url = entry.get("directlink")
        if not filename or not download_url:
            continue
        details["contents"].append(
            {"path": "", "filename": filename, "url": download_url}
        )
        if entry.get("bytes"):
            details["total_size"] += int(entry["bytes"])
    if not details["title"] and details["contents"]:
        details["title"] = details["contents"][0]["filename"]
    if details["contents"]:
        details["header"] = f"Referer: {VIDEQ_EMBED_REFERER}"
    return details


def videq(url: str) -> dict:
    """Scrape by https://github.com/pikaproject
    support single and folder link"""

    if "/f/" in url:
        return videq_folder(url)
    try:
        payload = resolve_payload(url)
        entries = payload if isinstance(payload, list) else [payload]
        details = videq_entries_to_details(entries)
        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: Video info not found")
        return details
    except DirectDownloadLinkException:
        raise
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e}")


def videq_folder(url: str) -> dict:
    try:
        payload = resolve_payload(url)
        if not isinstance(payload, list):
            raise DirectDownloadLinkException("ERROR: URL folder tidak valid.")
        folder_title = ""
        try:
            path = urlparse(normalize_input_url(url)).path
            if "/f/" in path:
                folder_title = path.split("/f/", 1)[1].strip("/")
        except Exception:
            folder_title = ""
        details = videq_entries_to_details(payload, folder_title)
        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: No videos found")
        return details
    except DirectDownloadLinkException:
        raise
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e}")
