import os
import re
import time as time_module
from base64 import b64decode, b64encode
from cloudscraper import create_scraper
from hashlib import sha256
from http.cookiejar import MozillaCookieJar
from json import loads
from lxml.etree import HTML
from os import path as ospath
from random import choice
from re import findall, match, search
from requests import Session, post, get
from requests.adapters import HTTPAdapter
from time import sleep, time
from urllib.parse import parse_qs, quote, unquote, urlparse, urljoin
from urllib3.util.retry import Retry
from uuid import uuid4
from curl_cffi import Session as CurlSession

from ....core.config_manager import Config
from ...ext_utils.exceptions import DirectDownloadLinkException
from ...ext_utils.help_messages import PASSWORD_ERROR_MESSAGE
from ...ext_utils.links_utils import is_share_link
from ...ext_utils.status_utils import speed_string_to_bytes
from .url_shortener_bypass import bypass_shortener, is_url_shortener

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
)

TERABOX_PREMIUM_HOST = "d8.freeterabox.com"
BYPASSBOT_BASE_URL = "https://dl.bypassbot.workers.dev/"
GDFLIX_DOMAINN = "https://gdflix.dad"
HUBCLOUD_DOMAIN = "https://hubcloud.foo"

HUBCLOUD_HOST_MARKERS = (
    "hubcloud",
    "hubcloud.fit",
    "hubcloud.one",
    "hubcloud.pro",
    "hubcloud.cc",
    "hubcloud.link",
    "hubcloud.xyz",
    "hubcloud.in",
    "hubcloud.bz",
    "hubcloud.foo",
    "hubcloud.cx",
    "hubcloud.tips",
)
HUBDRIVE_HOST_MARKERS = (
    "hubdrive",
    "hubdrive.space",
    "hubdrive.tips",
    "hubdrive.fit",
    "hubdrive.dad",
)
DRIVESEED_HOST_MARKERS = (
    "driveseed",
    "driveseed.org",
)

PROXY_PREFIX = Config.PROXY_PREFIX if hasattr(Config, "PROXY_PREFIX") else ""
PROXY_URL = Config.PROXY_URL if hasattr(Config, "PROXY_URL") else ""
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}


def safe_int_size(size):
    """Convert size to integer safely, handling various formats"""
    if size is None:
        return 0
    try:
        if isinstance(size, (int, float)):
            return int(size)
        if isinstance(size, str):
            stripped = size.strip()
            if stripped.isdigit():
                return int(stripped)
            try:
                return int(float(stripped))
            except ValueError:
                try:
                    return speed_string_to_bytes(stripped)
                except Exception:
                    return 0
    except (ValueError, TypeError):
        pass
    return 0


def decode64(value):
    encoded = str(value).strip()
    encoded += "=" * (-len(encoded) % 4)
    return b64decode(encoded, altchars=b"-_").decode("utf-8")


def _wrap_bypassbot_download(url):
    if not url:
        return url
    if url.startswith(BYPASSBOT_BASE_URL):
        return url
    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    if "download.aspx" not in (parsed.path or "").lower():
        return url
    if not parsed.query:
        return url
    encoded = b64encode(url.encode("utf-8")).decode("utf-8")
    return f"{BYPASSBOT_BASE_URL}{encoded}"


def _rewrite_terabox_premium_url(raw_url):
    if not raw_url:
        return raw_url
    try:
        parsed = urlparse(str(raw_url).strip())
    except Exception:
        return raw_url
    if not parsed.scheme or not parsed.netloc:
        return raw_url
    host = (parsed.hostname or "").lower()
    if not host or host == TERABOX_PREMIUM_HOST:
        return raw_url
    premium_aliases = (
        "1024tera.com",
        "nephobox.com",
        "momerybox.com",
        "freeterabox.com",
    )
    if not any(
        host == alias or host.endswith(f".{alias}") for alias in premium_aliases
    ):
        return raw_url
    return parsed._replace(netloc=TERABOX_PREMIUM_HOST).geturl()


def _safe_json_response(response, source_name):
    try:
        return response.json()
    except ValueError:
        status_code = getattr(response, "status_code", "unknown")
        text = (getattr(response, "text", "") or "").strip()
        preview = " ".join(text.split())[:180]
        if preview:
            raise DirectDownloadLinkException(
                f"ERROR: {source_name} returned non-JSON response (status {status_code}): {preview}"
            )
        raise DirectDownloadLinkException(
            f"ERROR: {source_name} returned non-JSON response (status {status_code})."
        )


def _is_api_success(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) in (0, 1, 200)
    text = str(value).strip().lower()
    return text in (
        "success",
        "successfully",
        "ok",
        "true",
        "1",
        "0",
        "200",
        "valid",
        "completed",
        "done",
    )


real_debrid_sites = [
    "1fichier.com",
    "4shared.com",
    "4s.io",
    "4shared-china.com",
    "clicknupload.me",
    "dailymotion.com",
    "dailyuploads.net",
    "drop.download",
    "filenext.com",
    "filespace.com",
    "filextras.com",
    "gigapeta.com",
    "docs.google.com",
    "hexupload.net",
    "hitfile.net",
    "icloud.com",
    "isra.cloud",
    "katfile.com",
    "mediafire.com",
    "mega.co.nz",
    "mega.nz",
    "prefiles.com",
    "rapidgator.net",
    "rg.to",
    "redtube.com",
    "scribd.com",
    "send.cm",
    "sendit.cloud",
    "turbobit.net",
    "turbobit.cc",
    "vimeo.com",
    "voe.sx",
]

debrid_link_supported_sites = [
    "1fichier.com",
    "anonfiles.com",
    "bayfiles.com",
    "clicknupload.link",
    "clicknupload.org",
    "clicknupload.co",
    "clicknupload.cc",
    "clicknupload.download",
    "clicknupload.club",
    "dailyuploads.net",
    "ddl.to",
    "ddownload.com",
    "ddownload.link",
    "drop.download",
    "dropbox.com",
    "dropboxusercontent.com",
    "easyupload.io",
    "emload.com",
    "file.al",
    "fileaxa.com",
    "filecat.net",
    "filedot.to",
    "filedot.xyz",
    "filextras.com",
    "filer.net",
    "filespace.com",
    "filestore.me",
    "gigapeta.com",
    "gofile.io",
    "hexupload.net",
    "hitfile.net",
    "hulkshare.com",
    "isra.cloud",
    "katfile.com",
    "kshared.com",
    "mediafire.com",
    "mega.nz",
    "mega.co.nz",
    "mexashare.com",
    "mixdrop.co",
    "mixdrop.to",
    "mixdrop.sx",
    "mixdrop.club",
    "modsbase.com",
    "nelion.me",
    "pixeldrain.com",
    "prefiles.com",
    "racaty.net",
    "rapidgator.net",
    "rapidgator.asia",
    "rg.to",
    "scribd.com",
    "send.cm",
    "sharemods.com",
    "silkfiles.com",
    "soundcloud.com",
    "streamtape.com",
    "terabox.com",
    "teraboxapp.com",
    "tezfiles.com",
    "turb.cc",
    "turb.to",
    "turbobit.net",
    "turbobit.cc",
    "turbobit.pw",
    "turbobit.online",
    "turbobit.ru",
    "turbobit.live",
    "trubobit.com",
    "turboblt.co",
    "uloz.to",
    "ulozto.net",
    "ulozto.sk",
    "ulozto.cz",
    "upload.ee",
    "uploadhaven.com",
    "up-4ever.com",
    "up-4ever.net",
    "uptobox.com",
    "uptobox.fr",
    "uptobox.eu",
    "uptobox.link",
    "uptostream.com",
    "uptostream.fr",
    "uptostream.eu",
    "uptostream.link",
    "upvid.pro",
    "upvid.live",
    "upvid.host",
    "upvid.biz",
    "upvid.cloud",
    "uqload.com",
    "uqload.co",
    "uqload.io",
    "userload.co",
    "usersdrive.com",
    "vidoza.net",
    "voe.sx",
    "voe-unblock.com",
    "voeunblock1.com",
    "voeunblock2.com",
    "voeunblock3.com",
    "voeunbl0ck.com",
    "voeunblck.com",
    "voeunblk.com",
    "voe-un-block.com",
    "voeun-block.net",
    "workupload.com",
    "world-bytez.com",
    "worldbytez.com",
    "world-files.com",
    "wupfile.com",
    "zippyshare.com",
]


def direct_link_generator(link):
    """direct links generator"""
    link = str(link).strip()
    bypassed = _wrap_bypassbot_download(link)
    if bypassed != link:
        return bypassed
    domain = urlparse(link).hostname
    if not domain:
        raise DirectDownloadLinkException("ERROR: Invalid URL")
    elif is_url_shortener(domain):
        resolved = bypass_shortener(link)
        try:
            return direct_link_generator(resolved)
        except DirectDownloadLinkException as e:
            if str(e).startswith("ERROR: No Direct link function found"):
                return resolved
            raise
    elif Config.REAL_DEBRID_API and any(x in domain for x in real_debrid_sites):
        try:
            return real_debrid(link)
        except Exception:
            if Config.DEBRID_LINK_API and any(
                x in domain for x in debrid_link_supported_sites
            ):
                return debrid_link(link)
            else:
                raise
    elif Config.DEBRID_LINK_API and any(
        x in domain for x in debrid_link_supported_sites
    ):
        return debrid_link(link)
    elif "yadi.sk" in link or "disk.yandex." in link:
        return yandex_disk(link)
    elif (
        "gdflix.dad" in domain
        or "vifix.site/gdflix" in link
        or "gdflix.dev" in domain
        or "gdflix.app" in domain
        or "gdlink.dev" in domain
        or "new7.gdflix.net" in domain
        or "new10.gdflix.dad" in domain
        or "new10.gdflix.net" in domain
        or "new9.gdflix.net" in domain
    ):
        return gdflix(link)
    elif any(x in domain for x in DRIVESEED_HOST_MARKERS):
        return driveseed(link)
    elif any(x in domain for x in HUBDRIVE_HOST_MARKERS):
        return hubdrive(link)
    elif any(x in domain for x in HUBCLOUD_HOST_MARKERS) or "vifix.site/hubcloud" in link:
        return hubcloud(link)
    elif "buzzheavier.com" in domain:
        return buzzheavier(link)
    elif "devuploads" in domain:
        return devuploads(link)
    elif "lulacloud.com" in domain:
        return lulacloud(link)
    elif "uploadhaven" in domain:
        return uploadhaven(link)
    elif "fuckingfast.co" in domain:
        return fuckingfast_dl(link)
    elif "mediafile.cc" in domain:
        return mediafile(link)
    elif "mediafire.com" in domain:
        return mediafire(link)
    elif "osdn.net" in domain:
        return osdn(link)
    elif "sourceforge.net" in domain:
        return sourceforge(link)
    elif "github.com" in domain:
        return github(link)
    elif "transfer.it" in domain:
        return transfer_it(link)
    elif "hxfile.co" in domain:
        return hxfile(link)
    elif "1drv.ms" in domain:
        return onedrive(link)
    elif any(
        x in domain
        for x in [
            "pixeldrain.com",
            "pixeldra.in",
            "pixeldrain.net",
            "cdn.pixeldrain.eu.cc",
        ]
    ):
        return pixeldrain(link)
    elif "racaty" in domain:
        return racaty(link)
    elif "1fichier.com" in domain:
        return fichier(link)
    elif "solidfiles.com" in domain:
        return solidfiles(link)
    elif "krakenfiles.com" in domain:
        return krakenfiles(link)
    elif "upload.ee" in domain:
        return uploadee(link)
    elif "z-lib.gd" in domain:
        return zlib(link)
    elif "gofile.io" in domain:
        return gofile(link)
    elif "send.cm" in domain:
        return send_cm(link)
    elif "tmpsend.com" in domain:
        return tmpsend(link)
    elif "easyupload.io" in domain:
        return easyupload(link)
    elif "sharemods.com" in domain:
        return sharemods(link)
    elif "streamvid.net" in domain:
        return streamvid(link)
    elif "shrdsk.me" in domain:
        return shrdsk(link)
    elif "u.pcloud.link" in domain:
        return pcloud(link)
    elif "qiwi.gg" in domain:
        return qiwi(link)
    elif "mp4upload.com" in domain:
        return mp4upload(link)
    elif "berkasdrive.com" in domain:
        return berkasdrive(link)
    elif "swisstransfer.com" in domain:
        return swisstransfer(link)
    elif "instagram.com" in domain:
        return instagram(link)
    elif "apkadmin.com" in domain:
        return apkadmin(link)
    elif any(x in domain for x in ["akmfiles.com", "akmfls.xyz"]):
        return akmfiles(link)
    elif any(
        x in domain
        for x in [
            "dood.watch",
            "doodstream.com",
            "dood.to",
            "dood.so",
            "dood.cx",
            "dood.la",
            "dood.ws",
            "dood.sh",
            "doodstream.co",
            "dood.pm",
            "dood.wf",
            "dood.re",
            "dood.video",
            "dooood.com",
            "dood.yt",
            "doods.yt",
            "dood.stream",
            "doods.pro",
            "ds2play.com",
            "d0o0d.com",
            "ds2video.com",
            "do0od.com",
            "d000d.com",
        ]
    ):
        return doods(link)
    elif any(x in domain for x in ["vide10.com", "vide4.com", "vide9.com"]):
        return videq(link)
    elif any(
        x in domain
        for x in [
            "streamtape.com",
            "streamtape.co",
            "streamtape.cc",
            "streamtape.to",
            "streamtape.net",
            "streamta.pe",
            "streamtape.xyz",
        ]
    ):
        return streamtape(link)
    elif any(x in domain for x in ["wetransfer.com", "we.tl"]):
        return wetransfer(link)
    elif any(
        x in domain
        for x in [
            "terabox.com",
            "nephobox.com",
            "4funbox.com",
            "mirrobox.com",
            "momerybox.com",
            "teraboxapp.com",
            "1024tera.com",
            "terabox.app",
            "gibibox.com",
            "goaibox.com",
            "terasharelink.com",
            "teraboxlink.com",
            "freeterabox.com",
            "1024terabox.com",
            "teraboxshare.com",
            "terafileshare.com",
            "terabox.club",
        ]
    ):
        return terabox(link)
    elif any(
        x in domain
        for x in [
            "filelions.co",
            "filelions.site",
            "filelions.live",
            "filelions.to",
            "mycloudz.cc",
            "cabecabean.lol",
            "filelions.online",
            "embedwish.com",
            "kitabmarkaz.xyz",
            "wishfast.top",
            "streamwish.to",
            "kissmovies.net",
        ]
    ):
        return filelions_and_streamwish(link)
    elif any(x in domain for x in ["streamhub.ink", "streamhub.to"]):
        return streamhub(link)
    elif any(
        x in domain
        for x in [
            "linkbox.to",
            "lbx.to",
            "teltobx.net",
            "telbx.net",
            "linkbox.cloud",
        ]
    ):
        return linkBox(link)
    elif is_share_link(link):
        if "gdtot" in domain:
            return gdtot(link)
        elif "filepress" in domain:
            return filepress(link)
        else:
            return sharer_scraper(link)
    elif any(
        x in domain
        for x in [
            "anonfiles.com",
            "zippyshare.com",
            "letsupload.io",
            "hotfile.io",
            "bayfiles.com",
            "megaupload.nz",
            "letsupload.cc",
            "filechan.org",
            "myfile.is",
            "vshare.is",
            "rapidshare.nu",
            "lolabits.se",
            "openload.cc",
            "share-online.is",
            "upvid.cc",
            "uptobox.com",
            "uptobox.fr",
        ]
    ):
        raise DirectDownloadLinkException(f"ERROR: R.I.P {domain}")
    else:
        raise DirectDownloadLinkException(f"No Direct link function found for {link}")


def get_captcha_token(session, params):
    recaptcha_api = "https://www.google.com/recaptcha/api2"
    res = session.get(f"{recaptcha_api}/anchor", params=params)
    anchor_html = HTML(res.text)
    if not (anchor_token := anchor_html.xpath('//input[@id="recaptcha-token"]/@value')):
        return None
    params["c"] = anchor_token[0]
    params["reason"] = "q"
    res = session.post(f"{recaptcha_api}/reload", params=params)
    if token := findall(r'"rresp","(.*?)"', res.text):
        return token[0]


def transfer_it(url):
    resp = post("https://transfer-it-henna.vercel.app/post", json={"url": url})
    if resp.status_code == 200:
        return resp.json()["url"]
    else:
        raise DirectDownloadLinkException("ERROR: File Expired or File Not Found")


def buzzheavier(url):
    """
    Generate a direct download link for buzzheavier URLs.
    @param link: URL from buzzheavier
    @return: Direct download link
    """
    pattern = r"^https?://buzzheavier\.com/[a-zA-Z0-9]+$"
    if not match(pattern, url):
        return url

    def _bhscraper(session , url):
        if "/download" not in url:
            url += "/download"
        url = url.strip()
        try:
            response = session.get(url , allow_redirects=False)
            d_url = response.headers.get("location","").strip()
            if not d_url:
                return
            return d_url
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e

    with CurlSession(impersonate = "chrome") as session:
        response = session.get(url)
        tree = HTML(response.text)
        if link := tree.xpath("//a[contains(@hx-get, 'download')]"):
            hx_get = link[0].attrib.get("hx-get", "").strip()
            return _bhscraper(session , f"https://buzzheavier.com{hx_get}")
        elif folders := tree.xpath("//tbody[@id='tbody']/tr"):
            details = {"contents": [], "title": "", "total_size": 0}
            for data in folders:
                try:
                    filename = data.xpath(".//a")[0].text.strip()
                    _id = data.xpath(".//a")[0].attrib.get("href", "").strip()
                    size = data.xpath(".//td[@class='text-center']/text()")[0].strip()
                    url = buzzheavier(f"https://buzzheavier.com{_id}")
                    if not url:
                        raise DirectDownloadLinkException("ERROR: No download link found")
                    item = {
                        "path": "",
                        "filename": filename,
                        "url": url,
                    }
                    details["contents"].append(item)
                    size = speed_string_to_bytes(size)
                    details["total_size"] += size
                except Exception:
                    continue
            details["title"] = tree.xpath("//span/text()")[0].strip()
            return details
        else:
            raise DirectDownloadLinkException("ERROR: No download link found")

def fuckingfast_dl(url):
    """
    Generate a direct download link for fuckingfast.co URLs.
    @param url: URL from fuckingfast.co
    @return: Direct download link
    """
    url = url.strip()

    try:
        response = get(url)
        content = response.text
        pattern = r'window\.open\((["\'])(https://fuckingfast\.co/dl/[^"\']+)\1'
        if match := search(pattern, content):
            return match.group(2)
        else:
            raise DirectDownloadLinkException(
                "ERROR: Could not find download link in page"
            )

    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def lulacloud(url):
    """
    Generate a direct download link for www.lulacloud.com URLs.
    @param url: URL from www.lulacloud.com
    @return: Direct download link
    """
    try:
        res = post(url, headers={"Referer": url}, allow_redirects=False)
        return res.headers["location"]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def devuploads(url):
    """
    Generate a direct download link for devuploads.com URLs.
    @param url: URL from devuploads.com
    @return: Direct download link
    """
    with Session() as session:
        res = session.get(url)
        html = HTML(res.text)
        if not html.xpath("//input[@name]"):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        data = {i.get("name"): i.get("value") for i in html.xpath("//input[@name]")}
        res = session.post("https://gujjukhabar.in/", data=data)
        html = HTML(res.text)
        if not html.xpath("//input[@name]"):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        data = {i.get("name"): i.get("value") for i in html.xpath("//input[@name]")}
        resp = session.get(
            "https://du2.devuploads.com/dlhash.php",
            headers={
                "Origin": "https://gujjukhabar.in",
                "Referer": "https://gujjukhabar.in/",
            },
        )
        if not resp.text:
            raise DirectDownloadLinkException("ERROR: Unable to find ipp value")
        data["ipp"] = resp.text.strip()
        if not data.get("rand"):
            raise DirectDownloadLinkException("ERROR: Unable to find rand value")
        randpost = session.post(
            "https://devuploads.com/token/token.php",
            data={"rand": data["rand"], "msg": ""},
            headers={
                "Origin": "https://gujjukhabar.in",
                "Referer": "https://gujjukhabar.in/",
            },
        )
        if not randpost:
            raise DirectDownloadLinkException("ERROR: Unable to find xd value")
        data["xd"] = randpost.text.strip()
        res = session.post(url, data=data)
        html = HTML(res.text)
        if not html.xpath("//input[@name='orilink']/@value"):
            raise DirectDownloadLinkException("ERROR: Unable to find Direct Link")
        direct_link = html.xpath("//input[@name='orilink']/@value")
        return direct_link[0]


def uploadhaven(url):
    """
    Generate a direct download link for uploadhaven.com URLs.
    @param url: URL from uploadhaven.com
    @return: Direct download link
    """
    try:
        res = get(url, headers={"Referer": "http://steamunlocked.net/"})
        html = HTML(res.text)
        if not html.xpath('//form[@method="POST"]//input'):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        data = {
            i.get("name"): i.get("value")
            for i in html.xpath('//form[@method="POST"]//input')
        }
        sleep(15)
        res = post(url, data=data, headers={"Referer": url}, cookies=res.cookies)
        html = HTML(res.text)
        if not html.xpath('//div[@class="alert alert-success mb-0"]//a'):
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        a = html.xpath('//div[@class="alert alert-success mb-0"]//a')[0]
        return a.get("href")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def mediafile(url):
    """
    Generate a direct download link for mediafile.cc URLs.
    @param url: URL from mediafile.cc
    @return: Direct download link
    """
    try:
        res = get(url, allow_redirects=True)
        match = search(r"href='([^']+)'", res.text)
        if not match:
            raise DirectDownloadLinkException("ERROR: Unable to find link data")
        download_url = match[1]
        sleep(60)
        res = get(download_url, headers={"Referer": url}, cookies=res.cookies)
        postvalue = search(r"showFileInformation(.*);", res.text)
        if not postvalue:
            raise DirectDownloadLinkException("ERROR: Unable to find post value")
        postid = postvalue[1].replace("(", "").replace(")", "")
        response = post(
            "https://mediafile.cc/account/ajax/file_details",
            data={"u": postid},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        html = response.json()["html"]
        return [
            i for i in findall(r'https://[^\s"\']+', html) if "download_token" in i
        ][1]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {str(e)}") from e


def mediafire(url, session=None):
    if "/folder/" in url:
        return mediafireFolder(url)
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    if final_link := findall(
        r"https?:\/\/download\d+\.mediafire\.com\/\S+\/\S+\/\S+", url
    ):
        return final_link[0]

    def _repair_download(url, session):
        try:
            html = HTML(session.get(url).text)
            if new_link := html.xpath('//a[@id="continue-btn"]/@href'):
                return mediafire(f"https://mediafire.com/{new_link[0]}")
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e

    if session is None:
        session = create_scraper()
        parsed_url = urlparse(url)
        url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
    try:
        html = HTML(session.get(url).text)
    except Exception as e:
        session.close()
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if error := html.xpath('//p[@class="notranslate"]/text()'):
        session.close()
        raise DirectDownloadLinkException(f"ERROR: {error[0]}")
    if html.xpath("//div[@class='passwordPrompt']"):
        if not _password:
            session.close()
            raise DirectDownloadLinkException(
                f"ERROR: {PASSWORD_ERROR_MESSAGE}".format(url)
            )
        try:
            html = HTML(session.post(url, data={"downloadp": _password}).text)
        except Exception as e:
            session.close()
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if html.xpath("//div[@class='passwordPrompt']"):
            session.close()
            raise DirectDownloadLinkException("ERROR: Wrong password.")
    if not (final_link := html.xpath('//a[@aria-label="Download file"]/@href')):
        if repair_link := html.xpath("//a[@class='retry']/@href"):
            return _repair_download(repair_link[0], session)
        raise DirectDownloadLinkException(
            "ERROR: No links found in this page Try Again"
        )
    if final_link[0].startswith("//"):
        final_url = f"https://{final_link[0][2:]}"
        if _password:
            final_url += f"::{_password}"
        return mediafire(final_url, session)
    session.close()
    return final_link[0]


def osdn(url):
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not (direct_link := html.xpath('//a[@class="mirror_link"]/@href')):
            raise DirectDownloadLinkException("ERROR: Direct link not found")
        return f"https://osdn.net{direct_link[0]}"


def yandex_disk(url: str) -> str:
    """Yandex.Disk direct link generator
    Based on https://github.com/wldhx/yadisk-direct"""
    try:
        link = findall(r"\b(https?://(yadi\.sk|disk\.yandex\.(com|ru))\S+)", url)[0][0]
    except IndexError:
        return "No Yandex.Disk links found\n"
    api = "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={}"
    try:
        return get(api.format(link)).json()["href"]
    except KeyError as e:
        raise DirectDownloadLinkException(
            "ERROR: File not found/Download limit reached"
        ) from e


def github(url):
    """GitHub direct links generator"""
    try:
        findall(r"\bhttps?://.*github\.com.*releases\S+", url)[0]
    except IndexError as e:
        raise DirectDownloadLinkException("No GitHub Releases links found") from e
    with create_scraper() as session:
        _res = session.get(url, stream=True, allow_redirects=False)
        if "location" in _res.headers:
            return _res.headers["location"]
        raise DirectDownloadLinkException("ERROR: Can't extract the link")


def hxfile(url):
    if not ospath.isfile("hxfile.txt"):
        raise DirectDownloadLinkException("ERROR: hxfile.txt (cookies) Not Found!")
    try:
        jar = MozillaCookieJar()
        jar.load("hxfile.txt")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    cookies = {cookie.name: cookie.value for cookie in jar}
    try:
        if url.strip().endswith(".html"):
            url = url[:-5]
        file_code = url.split("/")[-1]
        html = HTML(
            post(
                url,
                data={"op": "download2", "id": file_code},
                cookies=cookies,
            ).text
        )
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[@class='btn btn-dow']/@href"):
        header = [f"Referer: {url}"]
        return direct_link[0], header
    raise DirectDownloadLinkException("ERROR: Direct download link not found")


def onedrive(link):
    """Onedrive direct link generator
    By https://github.com/junedkh"""
    with create_scraper() as session:
        try:
            link = session.get(link).url
            parsed_link = urlparse(link)
            link_data = parse_qs(parsed_link.query)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not link_data:
            raise DirectDownloadLinkException("ERROR: Unable to find link_data")
        folder_id = link_data.get("resid")
        if not folder_id:
            raise DirectDownloadLinkException("ERROR: folder id not found")
        folder_id = folder_id[0]
        authkey = link_data.get("authkey")
        if not authkey:
            raise DirectDownloadLinkException("ERROR: authkey not found")
        authkey = authkey[0]
        boundary = uuid4()
        headers = {"content-type": f"multipart/form-data;boundary={boundary}"}
        data = f"--{boundary}\r\nContent-Disposition: form-data;name=data\r\nPrefer: Migration=EnableRedirect;FailOnMigratedFiles\r\nX-HTTP-Method-Override: GET\r\nContent-Type: application/json\r\n\r\n--{boundary}--"
        try:
            resp = session.get(
                f'https://api.onedrive.com/v1.0/drives/{folder_id.split("!", 1)[0]}/items/{folder_id}?$select=id,@content.downloadUrl&ump=1&authKey={authkey}',
                headers=headers,
                data=data,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "@content.downloadUrl" not in resp:
        raise DirectDownloadLinkException("ERROR: Direct link not found")
    return resp["@content.downloadUrl"]


def pixeldrain(url):
    try:
        url = url.rstrip("/")
        code = url.split("/")[-1].split("?", 1)[0]
        response = get("https://cdn.pixeldrain.eu.cc/", allow_redirects=True)
        return response.url + code
    except Exception as e:
        raise DirectDownloadLinkException("ERROR: Direct link not found") from e


def streamtape(url):
    splitted_url = url.split("/")
    _id = splitted_url[4] if len(splitted_url) >= 6 else splitted_url[-1]
    try:
        html = HTML(get(url).text)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    script = html.xpath(
        "//script[contains(text(),'ideoooolink')]/text()"
    ) or html.xpath("//script[contains(text(),'ideoolink')]/text()")
    if not script:
        raise DirectDownloadLinkException("ERROR: requeries script not found")
    if not (link := findall(r"(&expires\S+)'", script[0])):
        raise DirectDownloadLinkException("ERROR: Download link not found")
    return f"https://streamtape.com/get_video?id={_id}{link[-1]}"


def racaty(url):
    with create_scraper() as session:
        try:
            url = session.get(url).url
            json_data = {"op": "download2", "id": url.split("/")[-1]}
            html = HTML(session.post(url, data=json_data).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[@id='uniqueExpirylink']/@href"):
        return direct_link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def fichier(link):
    """1Fichier direct link generator
    Based on https://github.com/Maujar
    """
    regex = r"^([http:\/\/|https:\/\/]+)?.*1fichier\.com\/\?.+"
    gan = match(regex, link)
    if not gan:
        raise DirectDownloadLinkException("ERROR: The link you entered is wrong!")
    if "::" in link:
        pswd = link.split("::")[-1]
        url = link.split("::")[-2]
    else:
        pswd = None
        url = link
    cget = create_scraper().request
    try:
        if pswd is None:
            req = cget("post", url)
        else:
            pw = {"pass": pswd}
            req = cget("post", url, data=pw)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if req.status_code == 404:
        raise DirectDownloadLinkException(
            "ERROR: File not found/The link you entered is wrong!"
        )
    html = HTML(req.text)
    if dl_url := html.xpath('//a[@class="ok btn-general btn-orange"]/@href'):
        return dl_url[0]
    if not (ct_warn := html.xpath('//div[@class="ct_warn"]')):
        raise DirectDownloadLinkException(
            "ERROR: Error trying to generate Direct Link from 1fichier!"
        )
    if len(ct_warn) == 3:
        str_2 = ct_warn[-1].text
        if "you must wait" in str_2.lower():
            if numbers := [int(word) for word in str_2.split() if word.isdigit()]:
                raise DirectDownloadLinkException(
                    f"ERROR: 1fichier is on a limit. Please wait {numbers[0]} minute."
                )
            else:
                raise DirectDownloadLinkException(
                    "ERROR: 1fichier is on a limit. Please wait a few minutes/hour."
                )
        elif "protect access" in str_2.lower():
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(link)}"
            )
        else:
            raise DirectDownloadLinkException(
                "ERROR: Failed to generate Direct Link from 1fichier!"
            )
    elif len(ct_warn) == 4:
        str_1 = ct_warn[-2].text
        str_3 = ct_warn[-1].text
        if "you must wait" in str_1.lower():
            if numbers := [int(word) for word in str_1.split() if word.isdigit()]:
                raise DirectDownloadLinkException(
                    f"ERROR: 1fichier is on a limit. Please wait {numbers[0]} minute."
                )
            else:
                raise DirectDownloadLinkException(
                    "ERROR: 1fichier is on a limit. Please wait a few minutes/hour."
                )
        elif "bad password" in str_3.lower():
            raise DirectDownloadLinkException(
                "ERROR: The password you entered is wrong!"
            )
    raise DirectDownloadLinkException(
        "ERROR: Error trying to generate Direct Link from 1fichier!"
    )


def solidfiles(url):
    """Solidfiles direct link generator
    Based on https://github.com/Xonshiz/SolidFiles-Downloader
    By https://github.com/Jusidama18"""
    with create_scraper() as session:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/36.0.1985.125 Safari/537.36"
            }
            pageSource = session.get(url, headers=headers).text
            mainOptions = str(
                search(r"viewerOptions\'\,\ (.*?)\)\;", pageSource).group(1)
            )
            return loads(mainOptions)["downloadUrl"]
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e


def krakenfiles(url):
    with Session() as session:
        try:
            _res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        html = HTML(_res.text)
        if post_url := html.xpath('//form[@id="dl-form"]/@action'):
            post_url = f"https://krakenfiles.com{post_url[0]}"
        else:
            raise DirectDownloadLinkException("ERROR: Unable to find post link.")
        if token := html.xpath('//input[@id="dl-token"]/@value'):
            data = {"token": token[0]}
        else:
            raise DirectDownloadLinkException("ERROR: Unable to find token for post.")
        try:
            _json = session.post(post_url, data=data).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While send post request"
            ) from e
    if _json["status"] != "ok":
        raise DirectDownloadLinkException(
            "ERROR: Unable to find download after post request"
        )
    return _json["url"]


def uploadee(url):
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if link := html.xpath("//a[@id='d_l']/@href"):
        return link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct Link not found")


def terabox(url):

    if "/file/" in url:
        return url

    COOKIE_DOMAINS = (
        "terabox",
        "1024tera",
        "freeterabox",
        "nephobox",
        "4funbox",
        "mirrobox",
        "momerybox",
        "gibibox",
        "goaibox",
        "teraboxapp",
        "terasharelink",
        "teraboxlink",
        "teraboxshare",
        "terafileshare",
    )
    API_PARAMS = {
        "app_id": "250528",
        "web": "1",
        "channel": "dubox",
        "clienttype": "0",
    }

    def __load_cookies():
        if not ospath.isfile("cookies.txt"):
            return None
        cookies = {}
        try:
            with open("cookies.txt") as f:
                for line in f:
                    line = line.rstrip("\r\n")
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_") :]
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) < 7:
                        continue
                    if any(k in parts[0].lower() for k in COOKIE_DOMAINS):
                        cookies[parts[5]] = parts[6]
        except Exception:
            return None
        if not cookies.get("BDUSS") and not cookies.get("ndus"):
            return None
        return cookies

    def __parse_share(share_url):
        parsed = urlparse(share_url)
        qs = parse_qs(parsed.query)
        password = (qs.get("pwd") or [""])[0]
        surl = ""
        if "surl" in qs:
            surl = qs["surl"][0]
        elif "/s/" in parsed.path:
            surl = parsed.path.split("/s/", 1)[1].split("/", 1)[0]
        if surl.startswith("1") and len(surl) > 20:
            surl = surl[1:]
        if not surl:
            raise DirectDownloadLinkException(
                "ERROR: Could not parse Terabox share URL"
            )
        return surl, password

    def __bootstrap(session, surl, password):
        try:
            resp = session.get(
                f"https://www.terabox.com/sharing/link?surl={surl}",
                timeout=30,
                allow_redirects=True,
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        html_text = resp.text
        m = search(r"fn%28%22([0-9A-F]+)%22%29", html_text) or search(
            r'fn\("([0-9A-F]+)"\)', html_text
        )
        if not m:
            raise DirectDownloadLinkException(
                "ERROR: jsToken not found (login expired?)"
            )
        js_token = m.group(1)
        pcf = search(r'pcftoken["\']?\s*[:=]\s*["\']([0-9a-f]+)', html_text)
        pcftoken = pcf[1] if pcf else "0"
        if password:
            try:
                v = session.post(
                    f"https://{resp.url.split('/')[2]}/share/verify",
                    params={**API_PARAMS, "surl": surl},
                    data={"pwd": password},
                    timeout=30,
                ).json()
                if v.get("errno") != 0:
                    raise DirectDownloadLinkException(
                        f"ERROR: Share password verification failed "
                        f"(errno={v.get('errno')})"
                    )
            except DirectDownloadLinkException:
                raise
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
        return js_token, pcftoken

    def __share_list(
        session, surl, js_token, pcftoken, *, dir_path=None, root=False, page=1, num=200
    ):
        params = {
            **API_PARAMS,
            "jsToken": js_token,
            "pcftoken": pcftoken,
            "shorturl": surl,
            "page": str(page),
            "num": str(num),
            "by": "name",
            "order": "asc",
            "scene": "",
        }
        if root:
            params["root"] = "1"
        if dir_path is not None:
            params["dir"] = dir_path
        try:
            data = session.get(
                "https://dm.terabox.com/share/list",
                params=params,
                timeout=30,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if data.get("errno") not in (0, None):
            raise DirectDownloadLinkException(
                f"ERROR: share/list errno={data.get('errno')}"
            )
        return data

    def __shorturlinfo(session, surl, js_token):
        try:
            data = session.get(
                "https://www.terabox.com/api/shorturlinfo",
                params={
                    **API_PARAMS,
                    "jsToken": js_token,
                    "shorturl": f"1{surl}",
                    "root": "1",
                    "page": "1",
                    "num": "20",
                },
                timeout=30,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if data.get("errno") not in (0, None):
            raise DirectDownloadLinkException(
                f"ERROR: shorturlinfo errno={data.get('errno')}"
            )
        return data

    def __resolve_dlinks(session, js_token, meta, fs_ids):
        out = {}
        for fid in fs_ids:
            try:
                data = session.post(
                    "https://www.terabox.com/share/download",
                    params={
                        **API_PARAMS,
                        "jsToken": js_token,
                        "sign": meta["sign"],
                        "timestamp": str(meta["timestamp"]),
                    },
                    data={
                        "shareid": str(meta["shareid"]),
                        "uk": str(meta["uk"]),
                        "product": "share",
                        "fid_list": f"[{str(fid)}]",
                        "primaryid": str(meta["shareid"]),
                        "type": "nolimit",
                    },
                    timeout=30,
                ).json()
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
            if data.get("errno") not in (0, None):
                raise DirectDownloadLinkException(
                    f"ERROR: share/download errno={data.get('errno')}"
                )
            if data.get("dlink"):
                out[fid] = data["dlink"]
            sleep(0.3)
        return out

    def __crawl_with_cookies(cookies):
        surl, password = __parse_share(url)
        session = Session()
        session.cookies.update(cookies)
        session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"https://www.terabox.com/sharing/link?surl={surl}",
            }
        )
        js_token, pcftoken = __bootstrap(session, surl, password)
        info = __shorturlinfo(session, surl, js_token)
        meta = {
            "sign": info.get("sign", ""),
            "timestamp": info.get("timestamp", ""),
            "shareid": info.get("shareid") or info.get("share_id"),
            "uk": info.get("uk"),
        }
        details = {"contents": [], "title": "", "total_size": 0}
        pending = []

        def __walk(dir_path=None, root=False):
            page = 1
            while True:
                data = __share_list(
                    session,
                    surl,
                    js_token,
                    pcftoken,
                    dir_path=dir_path,
                    root=root,
                    page=page,
                    num=200,
                )
                if root and page == 1 and not details["title"]:
                    details["title"] = (data.get("title") or surl).lstrip("/")
                items = data.get("list") or []
                if not items:
                    break
                for it in items:
                    if int(it.get("isdir") or 0):
                        __walk(dir_path=it["path"])
                    else:
                        entry = {
                            "path": ospath.dirname(it.get("path", "")).lstrip("/"),
                            "filename": it["server_filename"],
                            "url": it.get("dlink", ""),
                        }
                        details["contents"].append(entry)
                        details["total_size"] += int(it.get("size") or 0)
                        if not entry["url"]:
                            pending.append(
                                (int(it["fs_id"]), len(details["contents"]) - 1)
                            )
                if len(items) < 200:
                    break
                page += 1
                sleep(0.3)

        __walk(root=True)

        if pending:
            resolved = __resolve_dlinks(
                session, js_token, meta, [fid for fid, _ in pending]
            )
            for fid, idx in pending:
                if fid in resolved:
                    details["contents"][idx]["url"] = resolved[fid]
            missing = [
                details["contents"][idx]["filename"]
                for fid, idx in pending
                if fid not in resolved
            ]
            if missing:
                raise DirectDownloadLinkException(
                    f"ERROR: failed to resolve dlink for {len(missing)} "
                    f"file(s); first: {missing[0]}"
                )

        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: Empty share or invalid cookies")
        if not details["title"]:
            details["title"] = details["contents"][0]["filename"]

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        details["header"] = (
            f"Cookie: {cookie_header}\n"
            f"User-Agent: {user_agent}\n"
            f"Referer: https://www.terabox.com/"
        )
        if len(details["contents"]) == 1:
            return details["contents"][0]["url"], details["header"]
        return details

    cookies = __load_cookies()
    if cookies:
        try:
            return __crawl_with_cookies(cookies)
        except DirectDownloadLinkException:
            raise
        except Exception:
            pass

    api_url = "https://teraboxdl.site/api/proxy"
    headers = {"Referer": "https://teraboxdl.site/", "User-Agent": user_agent}
    payload = {"url": url}

    try:
        with Session() as session:
            req = session.post(
                api_url, json=payload, headers=headers, timeout=30
            ).json()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e

    details = {"contents": [], "title": "", "total_size": 0}

    if req.get("errno") != 0 or not req.get("list"):
        raise DirectDownloadLinkException("ERROR: File not found!")

    for data in req["list"]:
        item = {
            "path": data.get("path", ""),
            "filename": data["server_filename"],
            "url": data["direct_link"],
        }
        details["contents"].append(item)
        details["total_size"] += data.get("size", 0)

    details["title"] = req["list"][0]["server_filename"]

    if len(details["contents"]) == 1:
        return details["contents"][0]["url"]
    return details


def filepress(url):
    try:
        url = get(f"https://filebee.xyz/file/{url.split('/')[-1]}").url
        raw = urlparse(url)
        json_data = {
            "id": raw.path.split("/")[-1],
            "method": "publicDownlaod",
        }
        api = f"{raw.scheme}://{raw.hostname}/api/file/downlaod/"
        res2 = post(
            api,
            headers={"Referer": f"{raw.scheme}://{raw.hostname}"},
            json=json_data,
        ).json()
        json_data2 = {
            "id": res2["data"],
            "method": "publicDownlaod",
        }
        api2 = f"{raw.scheme}://{raw.hostname}/api/file/downlaod2/"
        res = post(
            api2,
            headers={"Referer": f"{raw.scheme}://{raw.hostname}"},
            json=json_data2,
        ).json()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e

    if "data" not in res:
        raise DirectDownloadLinkException(f'ERROR: {res["statusText"]}')
    return f'https://drive.google.com/uc?id={res["data"]}&export=download'


def sharer_scraper(url):
    cget = create_scraper().request
    try:
        url = cget("GET", url).url
        raw = urlparse(url)
        header = {
            "useragent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10"
        }
        res = cget("GET", url, headers=header)
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    key = findall(r'"key",\s+"(.*?)"', res.text)
    if not key:
        raise DirectDownloadLinkException("ERROR: Key not found!")
    key = key[0]
    if not HTML(res.text).xpath("//button[@id='drc']"):
        raise DirectDownloadLinkException(
            "ERROR: This link don't have direct download button"
        )
    boundary = uuid4()
    headers = {
        "Content-Type": f"multipart/form-data; boundary=----WebKitFormBoundary{boundary}",
        "x-token": raw.hostname,
        "useragent": "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/534.10 (KHTML, like Gecko) Chrome/7.0.548.0 Safari/534.10",
    }

    data = (
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action"\r\n\r\ndirect\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="key"\r\n\r\n{key}\r\n'
        f'------WebKitFormBoundary{boundary}\r\nContent-Disposition: form-data; name="action_token"\r\n\r\n\r\n'
        f"------WebKitFormBoundary{boundary}--\r\n"
    )
    try:
        res = cget("POST", url, cookies=res.cookies, headers=headers, data=data).json()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "url" not in res:
        raise DirectDownloadLinkException(
            "ERROR: Drive Link not found, Try in your browser"
        )
    if "drive.google.com" in res["url"] or "drive.usercontent.google.com" in res["url"]:
        return res["url"]
    try:
        res = cget("GET", res["url"])
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if (drive_link := HTML(res.text).xpath("//a[contains(@class,'btn')]/@href")) and (
        "drive.google.com" in drive_link[0]
        or "drive.usercontent.google.com" in drive_link[0]
    ):
        return drive_link[0]
    else:
        raise DirectDownloadLinkException(
            "ERROR: Drive Link not found, Try in your browser"
        )


def wetransfer(url):
    with create_scraper() as session:
        try:
            url = session.get(url).url
            splited_url = url.split("/")
            json_data = {"security_hash": splited_url[-1], "intent": "entire_transfer"}
            res = session.post(
                f"https://wetransfer.com/api/v4/transfers/{splited_url[-2]}/download",
                json=json_data,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "direct_link" in res:
        return res["direct_link"]
    elif "message" in res:
        raise DirectDownloadLinkException(f"ERROR: {res['message']}")
    elif "error" in res:
        raise DirectDownloadLinkException(f"ERROR: {res['error']}")
    else:
        raise DirectDownloadLinkException("ERROR: cannot find direct link")


def akmfiles(url):
    with create_scraper() as session:
        try:
            html = HTML(
                session.post(
                    url,
                    data={"op": "download2", "id": url.split("/")[-1]},
                ).text
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if direct_link := html.xpath("//a[contains(@class,'btn btn-dow')]/@href"):
        return direct_link[0]
    else:
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def shrdsk(url):
    with create_scraper() as session:
        try:
            _json = session.get(
                f'https://us-central1-affiliate2apk.cloudfunctions.net/get_data?shortid={url.split("/")[-1]}',
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if "download_data" not in _json:
            raise DirectDownloadLinkException("ERROR: Download data not found")
        try:
            _res = session.get(
                f"https://shrdsk.me/download/{_json['download_data']}",
                allow_redirects=False,
            )
            if "Location" in _res.headers:
                return _res.headers["Location"]
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    raise DirectDownloadLinkException("ERROR: cannot find direct link in headers")


def linkBox(url: str):
    parsed_url = urlparse(url)
    try:
        shareToken = parsed_url.path.split("/")[-1]
    except Exception:
        raise DirectDownloadLinkException("ERROR: invalid URL")

    details = {"contents": [], "title": "", "total_size": 0}

    def __singleItem(session, itemId):
        try:
            _json = session.get(
                "https://www.linkbox.to/api/file/detail",
                params={"itemId": itemId},
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        data = _json["data"]
        if not data:
            if "msg" in _json:
                raise DirectDownloadLinkException(f"ERROR: {_json['msg']}")
            raise DirectDownloadLinkException("ERROR: data not found")
        itemInfo = data["itemInfo"]
        if not itemInfo:
            raise DirectDownloadLinkException("ERROR: itemInfo not found")
        filename = itemInfo["name"]
        sub_type = itemInfo.get("sub_type")
        if sub_type and not filename.strip().endswith(sub_type):
            filename += f".{sub_type}"
        if not details["title"]:
            details["title"] = filename
        item = {
            "path": "",
            "filename": filename,
            "url": itemInfo["url"],
        }
        if "size" in itemInfo:
            size = itemInfo["size"]
            if isinstance(size, str) and size.isdigit():
                size = float(size)
            details["total_size"] += size
        details["contents"].append(item)

    def __fetch_links(session, _id=0, folderPath=""):
        params = {
            "shareToken": shareToken,
            "pageSize": 1000,
            "pid": _id,
        }
        try:
            _json = session.get(
                "https://www.linkbox.to/api/file/share_out_list",
                params=params,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        data = _json["data"]
        if not data:
            if "msg" in _json:
                raise DirectDownloadLinkException(f"ERROR: {_json['msg']}")
            raise DirectDownloadLinkException("ERROR: data not found")
        try:
            if data["shareType"] == "singleItem":
                return __singleItem(session, data["itemId"])
        except Exception:
            pass
        if not details["title"]:
            details["title"] = data["dirName"]
        contents = data["list"]
        if not contents:
            return None
        for content in contents:
            if content["type"] == "dir" and "url" not in content:
                if not folderPath:
                    newFolderPath = ospath.join(details["title"], content["name"])
                else:
                    newFolderPath = ospath.join(folderPath, content["name"])
                if not details["title"]:
                    details["title"] = content["name"]
                __fetch_links(session, content["id"], newFolderPath)
            elif "url" in content:
                if not folderPath:
                    folderPath = details["title"]
                filename = content["name"]
                if (
                    sub_type := content.get("sub_type")
                ) and not filename.strip().endswith(sub_type):
                    filename += f".{sub_type}"
                item = {
                    "path": ospath.join(folderPath),
                    "filename": filename,
                    "url": content["url"],
                }
                if "size" in content:
                    size = content["size"]
                    if isinstance(size, str) and size.isdigit():
                        size = float(size)
                    details["total_size"] += size
                details["contents"].append(item)

    try:
        with Session() as session:
            __fetch_links(session)
    except DirectDownloadLinkException as e:
        raise e
    return details


def gofile(url):
    try:
        if "::" in url:
            _password = url.split("::")[-1]
            _password = sha256(_password.encode("utf-8")).hexdigest()
            url = url.split("::")[-2]
        else:
            _password = ""
        _id = url.split("/")[-1]
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

    def __generate_website_token(account_token=""):
        time_slot = int(time()) // 14400
        raw = f"{user_agent}::en-US::{account_token}::{time_slot}::9844d94d963d30"
        return sha256(raw.encode()).hexdigest()

    def __get_token(session):
        config_token = (getattr(Config, "GOFILE_TOKEN", "") or "").strip()
        if config_token:
            return config_token

        wt = __generate_website_token("")
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "X-Website-Token": wt,
            "X-BL": "en-US",
        }
        __url = "https://api.gofile.io/accounts"
        try:
            __res = session.post(__url, headers=headers).json()
            status = __res.get("status", "")
            if status == "ok":
                token_data = (__res.get("data") or {}).get("token")
                if token_data:
                    return token_data
            if status == "error-rateLimit":
                raise DirectDownloadLinkException(
                    "ERROR: GoFile token API rate limited. Retry later or set GOFILE_TOKEN."
                )
            raise DirectDownloadLinkException(
                f"ERROR: Failed to get token ({status or 'unknown'})."
            )
        except Exception as e:
            raise e

    def __fetch_links(session, _id, folderPath=""):
        _url = f"https://api.gofile.io/contents/{_id}?cache=true"
        wt = __generate_website_token(token)
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Authorization": "Bearer" + " " + token,
            "X-Website-Token": wt,
            "X-BL": "en-US",
        }
        if _password:
            _url += f"&password={_password}"
        try:
            _json = session.get(_url, headers=headers).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")

        status = _json.get("status", "")
        if status == "error-notPremium":
            anon_url = f"https://api.gofile.io/contents/{_id}?cache=true&wt=9844d94d963d30"
            if _password:
                anon_url += f"&password={_password}"
            anon_headers = {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "*/*",
                "Connection": "keep-alive",
                "X-BL": "en-US",
            }
            try:
                _json = session.get(anon_url, headers=anon_headers).json()
                status = _json.get("status", "")
            except Exception:
                pass

        if status == "error-rateLimit":
            raise DirectDownloadLinkException(
                "ERROR: GoFile API rate limited. Please retry after a short while."
            )
        if status == "error-passwordRequired":
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        if status == "error-passwordWrong":
            raise DirectDownloadLinkException("ERROR: This password is wrong !")
        if status == "error-notFound":
            raise DirectDownloadLinkException(
                "ERROR: File not found on gofile's server"
            )
        if status == "error-notPublic":
            raise DirectDownloadLinkException("ERROR: This folder is not public")
        if status in (
            "error-notPremium",
            "error-token",
            "error-tokenInvalid",
            "error-unauth",
            "error-forbidden",
        ):
            raise DirectDownloadLinkException(
                f"ERROR: GoFile API blocked this link for current token ({status})."
            )
        if status != "ok":
            raise DirectDownloadLinkException(
                f"ERROR: GoFile API returned unexpected status ({status or 'unknown'})."
            )

        data = _json.get("data")
        if not isinstance(data, dict) or "children" not in data:
            raise DirectDownloadLinkException("ERROR: Invalid GoFile API response.")

        if not details["title"]:
            details["title"] = data.get("name") if data.get("type") == "folder" else _id

        contents = data.get("children") or {}
        for content in contents.values():
            if content["type"] == "folder":
                if not content["public"]:
                    continue
                if not folderPath:
                    newFolderPath = ospath.join(content["name"])
                else:
                    newFolderPath = ospath.join(folderPath, content["name"])
                __fetch_links(session, content["id"], newFolderPath)
            else:
                item = {
                    "path": ospath.join(folderPath) if folderPath else "",
                    "filename": content["name"],
                    "url": content["link"],
                }
                if "size" in content:
                    size = content["size"]
                    if isinstance(size, str) and size.isdigit():
                        size = float(size)
                    details["total_size"] += size
                details["contents"].append(item)

    details = {"contents": [], "title": "", "total_size": 0}
    with Session() as session:
        try:
            token = __get_token(session)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")
        details["header"] = [f"Cookie: accountToken={token}"]
        try:
            __fetch_links(session, _id)
        except Exception as e:
            raise DirectDownloadLinkException(e)

    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], details["header"])
    return details


def mediafireFolder(url):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    try:
        raw = url.split("/", 4)[-1]
        folderkey = raw.split("/", 1)[0]
        folderkey = folderkey.split(",")
    except Exception:
        raise DirectDownloadLinkException("ERROR: Could not parse ")
    if len(folderkey) == 1:
        folderkey = folderkey[0]
    details = {"contents": [], "title": "", "total_size": 0, "header": ""}

    session = create_scraper()
    adapter = HTTPAdapter(
        max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.3)
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session = create_scraper(
        browser={"browser": "firefox", "platform": "windows", "mobile": False},
        delay=10,
        sess=session,
    )
    folder_infos = []

    def __get_info(folderkey):
        try:
            if isinstance(folderkey, list):
                folderkey = ",".join(folderkey)
            _json = session.post(
                "https://www.mediafire.com/api/1.5/folder/get_info.php",
                data={
                    "recursive": "yes",
                    "folder_key": folderkey,
                    "response_format": "json",
                },
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While getting info"
            )
        _res = _json["response"]
        if "folder_infos" in _res:
            folder_infos.extend(_res["folder_infos"])
        elif "folder_info" in _res:
            folder_infos.append(_res["folder_info"])
        elif "message" in _res:
            raise DirectDownloadLinkException(f"ERROR: {_res['message']}")
        else:
            raise DirectDownloadLinkException("ERROR: something went wrong!")

    try:
        __get_info(folderkey)
    except Exception as e:
        raise DirectDownloadLinkException(e)

    details["title"] = folder_infos[0]["name"]

    def __scraper(url):
        session = create_scraper()
        parsed_url = urlparse(url)
        url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

        try:
            html = HTML(session.get(url).text)
        except Exception:
            return None
        if html.xpath("//div[@class='passwordPrompt']"):
            if not _password:
                raise DirectDownloadLinkException(
                    f"ERROR: {PASSWORD_ERROR_MESSAGE}".format(url)
                )
            try:
                html = HTML(session.post(url, data={"downloadp": _password}).text)
            except Exception:
                return None
            if html.xpath("//div[@class='passwordPrompt']"):
                return None
        try:
            final_link = __decode_url(html)
        except Exception:
            return None
        return final_link

    def __decode_url(html):
        enc_url = html.xpath('//a[@id="downloadButton"]')
        if enc_url:
            final_link = enc_url[0].attrib.get("href")
            scrambled = enc_url[0].attrib.get("data-scrambled-url")
            if final_link and scrambled:
                try:
                    final_link = b64decode(scrambled).decode("utf-8")
                    return final_link
                except Exception:
                    return None
            elif final_link.startswith("http"):
                return final_link
            elif final_link.startswith("//"):
                return __scraper(f"https:{final_link}")
            else:
                return None
        else:
            return None

    def __get_content(folderKey, folderPath="", content_type="folders"):
        try:
            params = {
                "content_type": content_type,
                "folder_key": folderKey,
                "response_format": "json",
            }
            _json = session.get(
                "https://www.mediafire.com/api/1.5/folder/get_content.php",
                params=params,
            ).json()
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While getting content"
            )
        _res = _json["response"]
        if "message" in _res:
            raise DirectDownloadLinkException(f"ERROR: {_res['message']}")
        _folder_content = _res["folder_content"]
        if content_type == "folders":
            folders = _folder_content["folders"]
            for folder in folders:
                if folderPath:
                    newFolderPath = ospath.join(folderPath, folder["name"])
                else:
                    newFolderPath = ospath.join(folder["name"])
                __get_content(folder["folderkey"], newFolderPath)
            __get_content(folderKey, folderPath, "files")
        else:
            files = _folder_content["files"]
            for file in files:
                item = {}
                if not (_url := __scraper(file["links"]["normal_download"])):
                    continue
                item["filename"] = file["filename"]
                if not folderPath:
                    folderPath = details["title"]
                item["path"] = ospath.join(folderPath)
                item["url"] = _url
                if "size" in file:
                    size = file["size"]
                    if isinstance(size, str) and size.isdigit():
                        size = float(size)
                    details["total_size"] += size
                details["contents"].append(item)

    try:
        for folder in folder_infos:
            __get_content(folder["folderkey"], folder["name"])
    except Exception as e:
        raise DirectDownloadLinkException(e)
    finally:
        session.close()
    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], [details["header"]])
    return details


def cf_bypass(url):
    "DO NOT ABUSE THIS"
    try:
        data = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
        _json = post(
            "https://cf.jmdkh.eu.org/v1",
            headers={"Content-Type": "application/json"},
            json=data,
        ).json()
        if _json["status"] == "ok":
            return _json["solution"]["response"]
    except Exception as e:
        e
    raise DirectDownloadLinkException("ERROR: Con't bypass cloudflare")


def send_cm_file(url, file_id=None):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    _passwordNeed = False
    with create_scraper() as session:
        if file_id is None:
            try:
                html = HTML(session.get(url).text)
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
            if html.xpath("//input[@name='password']"):
                _passwordNeed = True
            if not (file_id := html.xpath("//input[@name='id']/@value")):
                raise DirectDownloadLinkException("ERROR: file_id not found")
        try:
            data = {"op": "download2", "id": file_id}
            if _password and _passwordNeed:
                data["password"] = _password
            _res = session.post("https://send.cm/", data=data, allow_redirects=False)
            if "Location" in _res.headers:
                return (_res.headers["Location"], ["Referer: https://send.cm/"])
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if _passwordNeed:
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        raise DirectDownloadLinkException("ERROR: Direct link not found")


def send_cm(url):
    if "/d/" in url:
        return send_cm_file(url)
    elif "/s/" not in url:
        file_id = url.split("/")[-1]
        return send_cm_file(url, file_id)
    splitted_url = url.split("/")
    details = {
        "contents": [],
        "title": "",
        "total_size": 0,
        "header": "Referer: https://send.cm/",
    }
    if len(splitted_url) == 5:
        url += "/"
        splitted_url = url.split("/")
    if len(splitted_url) >= 7:
        details["title"] = splitted_url[5]
    else:
        details["title"] = splitted_url[-1]
    session = Session()

    def __collectFolders(html):
        folders = []
        folders_urls = html.xpath("//h6/a/@href")
        folders_names = html.xpath("//h6/a/text()")
        for folders_url, folders_name in zip(folders_urls, folders_names):
            folders.append(
                {
                    "folder_link": folders_url.strip(),
                    "folder_name": folders_name.strip(),
                }
            )
        return folders

    def __getFile_link(file_id):
        try:
            _res = session.post(
                "https://send.cm/",
                data={"op": "download2", "id": file_id},
                allow_redirects=False,
            )
            if "Location" in _res.headers:
                return _res.headers["Location"]
        except Exception:
            pass

    def __getFiles(html):
        files = []
        hrefs = html.xpath('//tr[@class="selectable"]//a/@href')
        file_names = html.xpath('//tr[@class="selectable"]//a/text()')
        sizes = html.xpath('//tr[@class="selectable"]//span/text()')
        for href, file_name, size_text in zip(hrefs, file_names, sizes):
            files.append(
                {
                    "file_id": href.split("/")[-1],
                    "file_name": file_name.strip(),
                    "size": speed_string_to_bytes(size_text.strip()),
                }
            )
        return files

    def __writeContents(html_text, folderPath=""):
        folders = __collectFolders(html_text)
        for folder in folders:
            _html = HTML(cf_bypass(folder["folder_link"]))
            __writeContents(_html, ospath.join(folderPath, folder["folder_name"]))
        files = __getFiles(html_text)
        for file in files:
            if not (link := __getFile_link(file["file_id"])):
                continue
            item = {"url": link, "filename": file["filename"], "path": folderPath}
            details["total_size"] += file["size"]
            details["contents"].append(item)

    try:
        mainHtml = HTML(cf_bypass(url))
    except DirectDownloadLinkException as e:
        raise e
    except Exception as e:
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} While getting mainHtml"
        )

    try:
        __writeContents(mainHtml, details["title"])
    except DirectDownloadLinkException as e:
        raise e
    except Exception as e:
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} While writing Contents"
        )
    finally:
        session.close()
    if len(details["contents"]) == 1:
        return (details["contents"][0]["url"], [details["header"]])
    return details


def doods(url):
    if "/e/" in url:
        url = url.replace("/e/", "/d/")
    parsed_url = urlparse(url)
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While fetching token link"
            ) from e
        if not (link := html.xpath("//div[@class='download-content']//a/@href")):
            raise DirectDownloadLinkException(
                "ERROR: Token Link not found or maybe not allow to download! open in browser."
            )
        link = f"{parsed_url.scheme}://{parsed_url.hostname}{link[0]}"
        sleep(2)
        try:
            _res = session.get(link)
        except Exception as e:
            raise DirectDownloadLinkException(
                f"ERROR: {e.__class__.__name__} While fetching download link"
            ) from e
    if not (link := search(r"window\.open\('(\S+)'", _res.text)):
        raise DirectDownloadLinkException("ERROR: Download link not found try again")
    return (link.group(1), [f"Referer: {parsed_url.scheme}://{parsed_url.hostname}/"])


def easyupload(url):
    if "::" in url:
        _password = url.split("::")[-1]
        url = url.split("::")[-2]
    else:
        _password = ""
    file_id = url.split("/")[-1]
    with create_scraper() as session:
        try:
            _res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}")
        first_page_html = HTML(_res.text)
        if (
            first_page_html.xpath("//h6[contains(text(),'Password Protected')]")
            and not _password
        ):
            raise DirectDownloadLinkException(
                f"ERROR:\n{PASSWORD_ERROR_MESSAGE.format(url)}"
            )
        if not (
            match := search(
                r"https://eu(?:[1-9][0-9]?|100)\.easyupload\.io/action\.php", _res.text
            )
        ):
            raise DirectDownloadLinkException(
                "ERROR: Failed to get server for EasyUpload Link"
            )
        action_url = match.group()
        session.headers.update({"referer": "https://easyupload.io/"})
        recaptcha_params = {
            "k": "6LfWajMdAAAAAGLXz_nxz2tHnuqa-abQqC97DIZ3",
            "ar": "1",
            "co": "aHR0cHM6Ly9lYXN5dXBsb2FkLmlvOjQ0Mw..",
            "hl": "en",
            "v": "0hCdE87LyjzAkFO5Ff-v7Hj1",
            "size": "invisible",
            "cb": "c3o1vbaxbmwe",
        }
        if not (captcha_token := get_captcha_token(session, recaptcha_params)):
            raise DirectDownloadLinkException("ERROR: Captcha token not found")
        try:
            data = {
                "type": "download-token",
                "url": file_id,
                "value": _password,
                "captchatoken": captcha_token,
                "method": "regular",
            }
            json_resp = session.post(url=action_url, data=data).json()
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if "download_link" in json_resp:
        return json_resp["download_link"]
    elif "data" in json_resp:
        raise DirectDownloadLinkException(
            f"ERROR: Failed to generate direct link due to {json_resp['data']}"
        )
    raise DirectDownloadLinkException(
        "ERROR: Failed to generate direct link from EasyUpload."
    )


def filelions_and_streamwish(url):
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname
    scheme = parsed_url.scheme
    if any(
        x in hostname
        for x in [
            "filelions.co",
            "filelions.live",
            "filelions.to",
            "filelions.site",
            "cabecabean.lol",
            "filelions.online",
            "mycloudz.cc",
        ]
    ):
        apiKey = Config.FILELION_API
        apiUrl = "https://vidhideapi.com"
    elif any(
        x in hostname
        for x in [
            "embedwish.com",
            "kissmovies.net",
            "kitabmarkaz.xyz",
            "wishfast.top",
            "streamwish.to",
        ]
    ):
        apiKey = Config.STREAMWISH_API
        apiUrl = "https://api.streamwish.com"
    if not apiKey:
        raise DirectDownloadLinkException(
            f"ERROR: API is not provided get it from {scheme}://{hostname}"
        )
    file_code = url.split("/")[-1]
    quality = ""
    if bool(file_code.strip().endswith(("_o", "_h", "_n", "_l"))):
        spited_file_code = file_code.rsplit("_", 1)
        quality = spited_file_code[1]
        file_code = spited_file_code[0]
    url = f"{scheme}://{hostname}/{file_code}"
    try:
        _res = get(
            f"{apiUrl}/api/file/direct_link",
            params={"key": apiKey, "file_code": file_code, "hls": "1"},
        ).json()
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if _res["status"] != 200:
        raise DirectDownloadLinkException(f"ERROR: {_res['msg']}")
    result = _res["result"]
    if not result["versions"]:
        raise DirectDownloadLinkException("ERROR: File Not Found")
    error = "\nProvide a quality to download the video\nAvailable Quality:"
    for version in result["versions"]:
        if quality == version["name"]:
            return version["url"]
        elif version["name"] == "l":
            error += "\nLow"
        elif version["name"] == "n":
            error += "\nNormal"
        elif version["name"] == "o":
            error += "\nOriginal"
        elif version["name"] == "h":
            error += "\nHD"
        error += f" <code>{url}_{version['name']}</code>"
    raise DirectDownloadLinkException(f"ERROR: {error}")


def streamvid(url: str):
    file_code = url.split("/")[-1]
    parsed_url = urlparse(url)
    url = f"{parsed_url.scheme}://{parsed_url.hostname}/d/{file_code}"
    quality_defined = bool(url.strip().endswith(("_o", "_h", "_n", "_l")))
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if quality_defined:
            data = {}
            if not (inputs := html.xpath('//form[@id="F1"]//input')):
                raise DirectDownloadLinkException("ERROR: No inputs found")
            for i in inputs:
                if key := i.get("name"):
                    data[key] = i.get("value")
            try:
                html = HTML(session.post(url, data=data).text)
            except Exception as e:
                raise DirectDownloadLinkException(
                    f"ERROR: {e.__class__.__name__}"
                ) from e
            if not (
                script := html.xpath(
                    '//script[contains(text(),"document.location.href")]/text()'
                )
            ):
                if error := html.xpath(
                    '//div[@class="alert alert-danger"][1]/text()[2]'
                ):
                    raise DirectDownloadLinkException(f"ERROR: {error[0]}")
                raise DirectDownloadLinkException(
                    "ERROR: direct link script not found!"
                )
            if directLink := findall(r'document\.location\.href="(.*)"', script[0]):
                return directLink[0]
            raise DirectDownloadLinkException(
                "ERROR: direct link not found! in the script"
            )
        elif (qualities_urls := html.xpath('//div[@id="dl_versions"]/a/@href')) and (
            qualities := html.xpath('//div[@id="dl_versions"]/a/text()[2]')
        ):
            error = "\nProvide a quality to download the video\nAvailable Quality:"
            for quality_url, quality in zip(qualities_urls, qualities):
                error += f"\n{quality.strip()} <code>{quality_url}</code>"
            raise DirectDownloadLinkException(f"ERROR: {error}")
        elif error := html.xpath('//div[@class="not-found-text"]/text()'):
            raise DirectDownloadLinkException(f"ERROR: {error[0]}")
        raise DirectDownloadLinkException("ERROR: Something went wrong")


def streamhub(url):
    file_code = url.split("/")[-1]
    parsed_url = urlparse(url)
    url = f"{parsed_url.scheme}://{parsed_url.hostname}/d/{file_code}"
    with create_scraper() as session:
        try:
            html = HTML(session.get(url).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if not (inputs := html.xpath('//form[@name="F1"]//input')):
            raise DirectDownloadLinkException("ERROR: No inputs found")
        data = {}
        for i in inputs:
            if key := i.get("name"):
                data[key] = i.get("value")
        session.headers.update({"referer": url})
        sleep(1)
        try:
            html = HTML(session.post(url, data=data).text)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if directLink := html.xpath(
            '//a[@class="btn btn-primary btn-go downloadbtn"]/@href'
        ):
            return directLink[0]
        if error := html.xpath('//div[@class="alert alert-danger"]/text()[2]'):
            raise DirectDownloadLinkException(f"ERROR: {error[0]}")
        raise DirectDownloadLinkException("ERROR: direct link not found!")


def pcloud(url):
    with create_scraper() as session:
        try:
            res = session.get(url)
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    if link := findall(r".downloadlink.:..(https:.*)..", res.text):
        return link[0].replace(r"\/", "/")
    raise DirectDownloadLinkException("ERROR: Direct link not found")


def tmpsend(url):
    parsed_url = urlparse(url)
    if any(x in parsed_url.path for x in ["thank-you", "download"]):
        query_params = parse_qs(parsed_url.query)
        if file_id := query_params.get("d"):
            file_id = file_id[0]
    elif not (file_id := parsed_url.path.strip("/")):
        raise DirectDownloadLinkException("ERROR: Invalid URL format")
    referer_url = f"https://tmpsend.com/thank-you?d={file_id}"
    header = [f"Referer: {referer_url}"]
    download_link = f"https://tmpsend.com/download?d={file_id}"
    return download_link, header


def qiwi(url):
    """qiwi.gg link generator
    based on https://github.com/aenulrofik"""
    file_id = url.split("/")[-1]
    try:
        res = get(url).text
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    tree = HTML(res)
    if name := tree.xpath('//h1[@class="page_TextHeading__VsM7r"]/text()'):
        ext = name[0].split(".")[-1]
        return f"https://spyderrock.com/{file_id}.{ext}"
    else:
        raise DirectDownloadLinkException("ERROR: File not found")


def mp4upload(url):
    with Session() as session:
        try:
            url = url.replace("embed-", "")
            req = session.get(url).text
            tree = HTML(req)
            inputs = tree.xpath("//input")
            header = ["Referer: https://www.mp4upload.com/"]
            data = {input.get("name"): input.get("value") for input in inputs}
            if not data:
                raise DirectDownloadLinkException("ERROR: File Not Found!")
            post = session.post(
                url,
                data=data,
                headers={
                    "User-Agent": user_agent,
                    "Referer": "https://www.mp4upload.com/",
                },
            ).text
            tree = HTML(post)
            inputs = tree.xpath('//form[@name="F1"]//input')
            data = {
                input.get("name"): input.get("value").replace(" ", "")
                for input in inputs
            }
            if not data:
                raise DirectDownloadLinkException("ERROR: File Not Found!")
            data["referer"] = url
            direct_link = session.post(url, data=data).url
            return direct_link, header
        except Exception:
            raise DirectDownloadLinkException("ERROR: File Not Found!")


def berkasdrive(url):
    """berkasdrive.com link generator
    by https://github.com/aenulrofik"""
    try:
        sesi = get(url).text
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    html = HTML(sesi)
    if link := html.xpath("//script")[0].text.split('"')[1]:
        return b64decode(link).decode("utf-8")
    else:
        raise DirectDownloadLinkException("ERROR: File Not Found!")


def swisstransfer(link):
    matched_link = match(
        r"https://www\.swisstransfer\.com/d/([\w-]+)(?:\:\:(\w+))?", link
    )
    if not matched_link:
        raise DirectDownloadLinkException(
            f"ERROR: Invalid SwissTransfer link format {link}"
        )

    transfer_id, password = matched_link.groups()
    password = password or ""

    def encode_password(password):
        return b64encode(password.encode("utf-8")).decode("utf-8") if password else ""

    def getfile(transfer_id, password):
        url = f"https://www.swisstransfer.com/api/links/{transfer_id}"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Authorization": encode_password(password) if password else "",
            "Content-Type": "" if password else "application/json",
        }
        response = get(url, headers=headers)

        if response.status_code == 200:
            try:
                return response.json(), [f"{k}: {v}" for k, v in headers.items() if v]
            except ValueError:
                raise DirectDownloadLinkException(
                    f"ERROR: Error parsing JSON response {response.text}"
                )
        raise DirectDownloadLinkException(
            f"ERROR: Error fetching file details {response.status_code}, {response.text}"
        )

    def gettoken(password, containerUUID, fileUUID):
        url = "https://www.swisstransfer.com/api/generateDownloadToken"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
        }
        body = {
            "password": password,
            "containerUUID": containerUUID,
            "fileUUID": fileUUID,
        }

        response = post(url, headers=headers, json=body)

        if response.status_code == 200:
            return response.text.strip().replace('"', "")
        raise DirectDownloadLinkException(
            f"ERROR: Error generating download token {response.status_code}, {response.text}"
        )

    data, _ = getfile(transfer_id, password)
    if not data:
        return None

    try:
        container_uuid = data["data"]["containerUUID"]
        download_host = data["data"]["downloadHost"]
        files = data["data"]["container"]["files"]
        folder_name = data["data"]["container"]["message"] or "unknown"
    except (KeyError, IndexError, TypeError) as e:
        raise DirectDownloadLinkException(f"ERROR: Error parsing file details {e}")

    total_size = sum(file["fileSizeInBytes"] for file in files)

    if len(files) == 1:
        file = files[0]
        file_uuid = file["UUID"]
        token = gettoken(password, container_uuid, file_uuid)
        download_url = f"https://{download_host}/api/download/{transfer_id}/{file_uuid}?token={token}"
        return download_url, ["User-Agent:Mozilla/5.0"]

    contents = []
    for file in files:
        file_uuid = file["UUID"]
        file_name = file["fileName"]
        #file_size = file["fileSizeInBytes"]

        token = gettoken(password, container_uuid, file_uuid)
        if not token:
            continue

        download_url = f"https://{download_host}/api/download/{transfer_id}/{file_uuid}?token={token}"
        contents.append({"filename": file_name, "path": "", "url": download_url})

    return {
        "contents": contents,
        "title": folder_name,
        "total_size": total_size,
        "header": "User-Agent:Mozilla/5.0",
    }


def instagram(link: str) -> str:
    """
    Fetches the direct video download URL from an Instagram post.

    Args:
        link (str): The Instagram post URL.

    Returns:
        str: The direct video URL.

    Raises:
        DirectDownloadLinkException: If any error occurs during the process.
    """
    api_url = Config.INSTADL_API or "https://instagramcdn.vercel.app"
    full_url = f"{api_url}/api/video?postUrl={link}"

    try:
        response = get(full_url)
        response.raise_for_status()
        data = response.json()

        if (
            data.get("status") == "success"
            and "data" in data
            and "videoUrl" in data["data"]
        ):
            return data["data"]["videoUrl"]

        raise DirectDownloadLinkException("ERROR: Failed to retrieve video URL.")

    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e}")


def debrid_link(url):
    cget = create_scraper().request
    resp = cget(
        "POST",
        f"https://debrid-link.com/api/v2/downloader/add?access_token={Config.DEBRID_LINK_API}",
        data={"url": url},
    ).json()
    if not resp["success"]:
        raise DirectDownloadLinkException(
            f"ERROR: {resp['error']} & ERROR ID: {resp['error_id']}"
        )
    if isinstance(resp["value"], dict):
        return resp["value"]["downloadUrl"]
    elif isinstance(resp["value"], list):
        details = {
            "contents": [],
            "title": ospath.basename(url.rstrip("/")),
            "total_size": 0,
        }
        for dl in resp["value"]:
            if dl.get("expired", False):
                continue
            item = {
                "path": ospath.join(details["title"]),
                "filename": dl["name"],
                "url": dl["downloadUrl"],
            }
            if "size" in dl:
                details["total_size"] += dl["size"]
            details["contents"].append(item)
        return details


def real_debrid(url: str, tor=False):
    """
    Real-Debrid Link Extractor (VPN Maybe Needed)
    Returns the generated Real-Debrid link or torrent details.
    All download links are prepended with the proxy prefix.
    """

    def __unrestrict(url, tor=False):
        cget = create_scraper().request
        resp = cget(
            "POST",
            f"https://api.real-debrid.com/rest/1.0/unrestrict/link?auth_token={Config.REAL_DEBRID_API}",
            data={"link": url},
            proxies=proxies,
        )
        if resp.status_code == 200:
            _res = resp.json()
            if tor:
                return (_res["filename"], PROXY_PREFIX + _res["download"])
            else:
                return PROXY_PREFIX + _res["download"]
        raise Exception(f"ERROR: {resp.json().get('error', 'Unknown error')}")

    def __addMagnet(magnet):
        cget = create_scraper().request
        hash_ = re.search(r"(?<=xt=urn:btih:)[a-zA-Z0-9]+", magnet).group(0)
        resp = cget(
            "GET",
            f"https://api.real-debrid.com/rest/1.0/torrents/instantAvailability/{hash_}?auth_token={Config.REAL_DEBRID_API}",
            proxies=proxies,
        )
        if resp.status_code != 200 or not resp.json()[hash_.lower()]["rd"]:
            return magnet
        resp = cget(
            "POST",
            f"https://api.real-debrid.com/rest/1.0/torrents/addMagnet?auth_token={Config.REAL_DEBRID_API}",
            data={"magnet": magnet},
            proxies=proxies,
        )
        if resp.status_code == 201:
            _id = resp.json()["id"]
        else:
            raise Exception(f"ERROR: {resp.json().get('error', 'Unknown error')}")
        if _id:
            _file = cget(
                "POST",
                f"https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{_id}?auth_token={Config.REAL_DEBRID_API}",
                data={"files": "all"},
                proxies=proxies,
            )
            if _file.status_code != 204:
                raise Exception(f"ERROR: {resp.json().get('error', 'Unknown error')}")

        contents = {"links": []}
        while not contents["links"]:
            _res = cget(
                "GET",
                f"https://api.real-debrid.com/rest/1.0/torrents/info/{_id}?auth_token={Config.REAL_DEBRID_API}",
                proxies=proxies,
            )
            if _res.status_code == 200:
                contents = _res.json()
            else:
                raise Exception(f"ERROR: {_res.json().get('error', 'Unknown error')}")
            sleep(0.5)

        details = {
            "contents": [],
            "title": contents["original_filename"],
            "total_size": contents["bytes"],
        }

        for file_info, link in zip(contents["files"], contents["links"]):
            link_info = __unrestrict(link, tor=True)
            item = {
                "path": ospath.join(
                    details["title"], ospath.dirname(file_info["path"]).lstrip("/")
                ),
                "filename": unquote(link_info[0]),
                "url": link_info[1],
                "size": file_info.get("bytes", 0),
            }
            details["contents"].append(item)
        return details

    try:
        if tor:
            details = __addMagnet(url)
            if isinstance(details, dict) and len(details["contents"]) == 1:
                return details["contents"][0]["url"]
            return details
        return __unrestrict(url)
    except Exception as e:
        raise Exception(str(e))


def driveseed(url):
    """
    DriveSeed.org pages are served from the same clone-script family as GDFlix
    and HubCloud, but the exact page template varies (sometimes GDFlix-style
    buttons, sometimes HubCloud-style buttons). Try both scrapers so it works
    either way.
    """
    errors = []
    try:
        return gdflix(url)
    except DirectDownloadLinkException as e:
        errors.append(str(e))
    try:
        return hubcloud(url)
    except DirectDownloadLinkException as e:
        errors.append(str(e))
    raise DirectDownloadLinkException(
        "ERROR: No download links found on DriveSeed page ("
        + " | ".join(errors)
        + ")"
    )


def gdflix(url):
    """
    Fetches downloadable links from a GDFlix page.
    Returns direct download links in the same format as gofile:
    - Single file: (url, headers) or url string
    - Pack/folder: {"contents": [...], "title": "...", "total_size": 0}
    Uses proxy from get_hubcloud_proxy().
    """
    from bs4 import BeautifulSoup

    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        raise DirectDownloadLinkException("curl_cffi not installed!")

    def _wrap(link):
        return (link, {"User-Agent": user_agent})

    code = url.split("/")[-1] if not url.endswith("/") else url.split("/")[-2]

    parsed_url = urlparse(url)
    original_domain = parsed_url.netloc
    scheme = parsed_url.scheme or "https"

    if "/file/" not in url and "/pack/" not in url:
        if any(x in original_domain for x in ["gdflix", "gdlink", "vifix", "driveseed"]):
            url = f"{scheme}://{original_domain}/file/{code}"
        else:
            url = f"{GDFLIX_DOMAINN}/file/{code}"

    client = c_requests.Session()
    host, port, username, password = get_random_proxy()
    if host and int(port or 0) > 0:
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"
        client.proxies.update({"http": proxy_url, "https": proxy_url})
    client.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    parsed = urlparse(url)
    if "gdlink" in parsed.netloc:
        res = client.get(url, verify=False, impersonate="chrome110")
        soup = BeautifulSoup(res.text, "html.parser")
        gdflix_btn = soup.find("a", href=lambda x: x and "gdflix" in x)
        if gdflix_btn:
            new_url = gdflix_btn["href"]
            if new_url.endswith(".net") or new_url.endswith(".dad"):
                new_url = f"{new_url}/file/{url.split('/')[-1]}"
            return gdflix(new_url)
        if "/c/s/" in res.url:
            url = "https://" + res.url.split("/c/s/")[-1]
        else:
            url = res.url

    try:
        res = client.get(url, timeout=30, verify=False, impersonate="chrome110")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Request failed: {e}")

    url = res.url
    domain = urlparse(url).netloc
    dcode = url.split("/")[-1]
    soup = BeautifulSoup(res.text, "html.parser")

    if "/pack/" in url:
        title_tag = soup.find("h3")
        title = title_tag.text if title_tag else f"GDFlix_Pack_{code}"
        details = {"contents": [], "title": title, "total_size": 0}

        all_links = soup.select('a[href^="/file/"]')
        for link in all_links:
            temp_url = f"https://{domain}{link['href']}"
            try:
                file_res = client.get(temp_url, timeout=30)
                file_soup = BeautifulSoup(file_res.text, "html.parser")

                name_elem = file_soup.find(
                    "li",
                    class_="list-group-item",
                    string=lambda text: text and "Name :" in text,
                )
                file_name = (
                    name_elem.text.split("Name : ")[-1]
                    if name_elem
                    else link.get_text(strip=True)
                    or f"File_{len(details['contents']) + 1}"
                )

                size_elem = file_soup.find(
                    "li",
                    class_="list-group-item",
                    string=lambda text: text and "Size :" in text,
                )
                size_str = size_elem.text.split("Size : ")[-1] if size_elem else "0"

                file_size = 0
                try:
                    size_str = size_str.strip().upper()
                    if "GB" in size_str:
                        file_size = int(float(size_str.replace("GB", "").strip()) * 1024 * 1024 * 1024)
                    elif "MB" in size_str:
                        file_size = int(float(size_str.replace("MB", "").strip()) * 1024 * 1024)
                    elif "KB" in size_str:
                        file_size = int(float(size_str.replace("KB", "").strip()) * 1024)
                    elif "B" in size_str:
                        file_size = int(float(size_str.replace("B", "").strip()))
                except (ValueError, TypeError):
                    file_size = 0

                result = gdflix(temp_url)
                dl_url = None

                if isinstance(result, tuple):
                    dl_url = result[0]
                elif isinstance(result, str):
                    dl_url = result
                elif isinstance(result, dict):
                    nested_contents = result.get("contents", [])
                    if nested_contents:
                        details["contents"].extend(nested_contents)
                        details["total_size"] += result.get("total_size", 0)
                    continue

                if dl_url:
                    details["contents"].append(
                        {"path": "", "filename": file_name, "url": dl_url}
                    )
                    details["total_size"] += file_size

            except Exception:
                continue

        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: No download links found in pack")
        return details

    title = None
    title_elem = soup.find(
        "li", class_="list-group-item", string=lambda text: text and "Name :" in text
    )
    if title_elem:
        title = title_elem.text.split("Name : ")[-1]
    if not title:
        h2_tag = soup.find("h2")
        if h2_tag:
            h2_text = h2_tag.get_text(strip=True)
            title = h2_text.split("File Size")[0].strip() if "File Size" in h2_text else h2_text.strip()
    if not title:
        h3_tag = soup.find("h3")
        if h3_tag:
            title = h3_tag.get_text(strip=True)
    if not title:
        title = f"GDFlix_File_{code}"

    cloud_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "cloud download" in tag.get_text(strip=True).lower()
            and ".dev" in tag.get("href", "")
        )
    )
    if cloud_dl:
        href = cloud_dl["href"]
        if "/?url=" in href:
            dl_link = href.split("/?url=", maxsplit=1)[1]
            if dl_link.startswith("https%3A"):
                dl_link = unquote(dl_link)
            return _wrap(dl_link)
        return _wrap(href)

    fast_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "fast cloud" in tag.get_text(strip=True).lower()
            and ("xfile" in tag.get("href", "") or "zfile" in tag.get("href", ""))
        )
    )
    if fast_dl:
        try:
            zfile_url = f"https://{domain}" + fast_dl["href"]
            res3 = client.get(zfile_url, timeout=30, verify=False, impersonate="chrome110")
            soup3 = BeautifulSoup(res3.text, "html.parser")
            if re.search(r"async function generate", res3.text):
                key_match = re.search(r'formData\.append\("key",\s*"([^"]+)"\)', res3.text)
                key = key_match.group(1) if key_match else ""
                post_data = {"action": "cloud", "key": key, "action_token": ""}
                post_headers = {"x-token": domain}
                post_res = client.post(
                    res3.url, data=post_data, headers=post_headers,
                    timeout=30, verify=False, impersonate="chrome110",
                )
                if post_res.status_code == 200:
                    try:
                        json_data = loads(post_res.text)
                        download_url = json_data.get("visit_url") or json_data.get("url")
                        if download_url:
                            if not download_url.startswith("http"):
                                download_url = f"https://{domain}{download_url}"
                            if "/zfile/" in download_url or "/xfile/" in download_url:
                                try:
                                    token_res = client.get(download_url, timeout=30, verify=False, impersonate="chrome110")
                                    token_soup = BeautifulSoup(token_res.text, "html.parser")
                                    if re.search(r"async function generate", token_res.text):
                                        key_match2 = re.search(r'formData\.append\("key",\s*"([^"]+)"\)', token_res.text)
                                        key2 = key_match2.group(1) if key_match2 else ""
                                        post_data2 = {"action": "cloud", "key": key2, "action_token": ""}
                                        post_headers2 = {"x-token": domain}
                                        post_res2 = client.post(
                                            token_res.url, data=post_data2, headers=post_headers2,
                                            timeout=30, verify=False, impersonate="chrome110",
                                        )
                                        if post_res2.status_code == 200:
                                            try:
                                                json_data2 = loads(post_res2.text)
                                                final_url = json_data2.get("visit_url") or json_data2.get("url")
                                                if final_url:
                                                    if not final_url.startswith("http"):
                                                        final_url = f"https://{domain}{final_url}"
                                                    return final_url
                                            except Exception:
                                                pass
                                    for a in token_soup.find_all("a", href=True):
                                        href = a.get("href", "")
                                        if any(x in href for x in ["drive.google.com", "googleapis.com", "gofile.io", "1fichier.com", "pixeldrain", "mega.nz", "workers.dev"]):
                                            return _wrap(href)
                                except Exception:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

    instant_dl = soup.find(
        lambda tag: (
            tag.name == "a"
            and "instant dl" in tag.get_text(strip=True).lower()
            and "cdn" in tag.get("href", "")
        )
    )
    if instant_dl:
        try:
            res4 = client.get(instant_dl["href"], timeout=30)
            final_url = res4.url.split("?url=")[-1]
            if final_url.startswith("http"):
                return _wrap(final_url)
        except Exception:
            pass

    goflix = soup.find(lambda tag: tag.name == "a" and "goflix.sbs" in tag.get("href", ""))
    if goflix and goflix.get("href"):
        try:
            goflix_res = client.get(goflix["href"], timeout=30, verify=False, impersonate="chrome110")
            if goflix_res.status_code == 200:
                goflix_soup = BeautifulSoup(goflix_res.text, "html.parser")
                for a in goflix_soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if "gofile.io" in href:
                        return _wrap(href)
                for a in goflix_soup.find_all("a", href=True):
                    href = a.get("href", "")
                    if any(h in href for h in ["1fichier.com", "pixeldrain"]):
                        return _wrap(href)
        except Exception:
            pass

    go_ = soup.find(lambda tag: tag.name == "a" and "gofile" in tag.get_text(strip=True).lower())
    if go_ and go_.get("href") and "multiup.php" not in go_["href"]:
        try:
            res2 = client.get(go_["href"], timeout=30)
            match_go = re.search(r"https://gofile\.io/d/\w+", res2.text)
            if match_go:
                return _wrap(match_go.group())
        except Exception:
            pass

    pixeldrain_lnk = soup.find(lambda tag: tag.name == "a" and "pixeldrain" in tag.get_text(strip=True).lower())
    if pixeldrain_lnk and pixeldrain_lnk.get("href"):
        return _wrap(pixeldrain_lnk["href"])

    mgt_server = soup.find(lambda tag: tag.name == "a" and "mgt" in tag.get_text(strip=True).lower())
    if mgt_server and mgt_server.get("href"):
        return _wrap(mgt_server["href"])

    try:
        lnks = f"https://{domain}/wfile/{dcode}"
        res5 = client.get(lnks, timeout=30)
        soup4 = BeautifulSoup(res5.text, "html.parser")
        d_j = soup4.find_all(
            lambda tag: (
                tag.name == "a"
                and "download" in tag.get_text(strip=True).lower()
                and ".dev" in tag.get("href", "")
            )
        )
        for i in d_j:
            if i.get("href"):
                return _wrap(i["href"])
    except Exception:
        pass

    raise DirectDownloadLinkException("ERROR: No valid download links found")


def get_hubcloud_proxy():
    """Returns proxy configuration for Hubcloud and Gdflix downloads."""
    try:
        from bot.modules.proxy import get_default_proxy, get_translate_proxy
        proxy_url = get_translate_proxy() or get_default_proxy()
        return {"http": proxy_url, "https": proxy_url} if proxy_url else {}
    except Exception:
        return {}


def get_random_proxy():
    """Returns proxy host, port, username, password for hubcloud/gdflix."""
    try:
        from bot.modules.proxy import get_default_proxy, get_translate_proxy
        proxy_url = get_translate_proxy() or get_default_proxy()
        if not proxy_url:
            return "", 0, "", ""
        parsed = urlparse(proxy_url)
        host = parsed.hostname or ""
        port = parsed.port or 0
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not host or not port:
            return "", 0, "", ""
        return host, int(port), username, password
    except Exception:
        return "", 0, "", ""


def get_cf_clearance(domain, prox_):
    """Returns cookies and headers for Cloudflare bypass."""
    import cloudscraper
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    try:
        host = (prox_ or {}).get("host")
        port = int((prox_ or {}).get("port") or 0)
        username = (prox_ or {}).get("username") or ""
        password = (prox_ or {}).get("password") or ""
    except Exception:
        host, port, username, password = "", 0, "", ""

    if host and port > 0:
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"
        scraper.proxies.update({"http": proxy_url, "https": proxy_url})

    try:
        scraper.get(f"https://{domain}/", timeout=30)
        cookies = dict(scraper.cookies)
        headers = {
            "User-Agent": scraper.headers.get("User-Agent", user_agent),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        return cookies, headers
    except Exception:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        return {}, headers


def _select_hubcloud_domain(url):
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if any(marker in host for marker in HUBCLOUD_HOST_MARKERS) or any(
        marker in host for marker in DRIVESEED_HOST_MARKERS
    ):
        scheme = parsed.scheme or "https"
        return f"{scheme}://{parsed.netloc}"
    return HUBCLOUD_DOMAIN


def hubdrive(url):
    """Resolves a HubDrive page to its HubCloud mirror link and hands off to hubcloud()."""
    try:
        session = create_scraper()
        response = session.get(
            url,
            headers={"User-Agent": user_agent},
            timeout=30,
            allow_redirects=True,
        )
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: HubDrive request failed: {e}")

    if response.status_code >= 400:
        raise DirectDownloadLinkException(
            f"ERROR: HubDrive page unavailable (HTTP {response.status_code})"
        )

    base_url = response.url
    page_html = response.text or ""
    hub_link = ""

    try:
        page = HTML(page_html)
    except Exception:
        page = None

    if page is not None:
        candidates = page.xpath(
            "//a[contains(translate(@href, 'HUBCLOUD', 'hubcloud'), 'hubcloud')]/@href"
        )
        if not candidates:
            candidates = page.xpath(
                "//a[contains(@href, '/drive/') or contains(@href, '/video/') or contains(@href, '/packs/')]/@href"
            )

        for candidate in candidates:
            normalized = urljoin(base_url, candidate)
            lowered = normalized.lower()
            if "hubcloud" in lowered or "/drive/" in lowered or "/video/" in lowered:
                hub_link = normalized
                break

    if not hub_link:
        for candidate in findall(r"https?://[^\s\"'<>]+", page_html):
            lowered = candidate.lower()
            if "hubcloud" in lowered and (
                "/drive/" in lowered or "/video/" in lowered or "/packs/" in lowered
            ):
                hub_link = candidate
                break

    if not hub_link:
        raise DirectDownloadLinkException("ERROR: HubDrive mirror link not found")

    return hubcloud(hub_link)


def hubcloud(url):
    """Fetches direct download links from HubCloud domains using curl_cffi."""
    from bs4 import BeautifulSoup

    try:
        from curl_cffi import requests as c_requests
    except ImportError:
        raise DirectDownloadLinkException("curl_cffi not installed!")

    parsed_input = urlparse(url)
    input_host = (parsed_input.netloc or "").lower()
    if any(marker in input_host for marker in HUBDRIVE_HOST_MARKERS):
        return hubdrive(url)

    hubcloud_domain = _select_hubcloud_domain(url)

    code = url.split("/")[-1] if not url.endswith("/") else url.split("/")[-2]

    if "/drive/packs/" in url:
        url = f"{hubcloud_domain}/drive/packs/{code}"
    elif "/video/packs/" in url:
        url = f"{hubcloud_domain}/video/packs/{code}"
    elif "/drive/" in url or "vifix" in url:
        url = f"{hubcloud_domain}/drive/{code}"
    elif "/video/" in url:
        url = f"{hubcloud_domain}/video/{code}"

    host, port, username, password = get_random_proxy()
    client = c_requests.Session()

    if host and int(port or 0) > 0:
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"http://{host}:{port}"
        client.proxies.update({"http": proxy_url, "https": proxy_url})

    try:
        res = client.get(url, timeout=30, verify=False, impersonate="chrome110")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Request failed: {e}")

    domain = urlparse(res.url).netloc
    soup = BeautifulSoup(res.text, "html.parser")

    if "/packs/" in url:
        file_type_match = re.search(r"window\.open\(\s*['\"]([^'\"]+)['\"]", res.text)
        if not file_type_match:
            raise DirectDownloadLinkException("ERROR: Could not determine pack file type")

        file_type_segment = re.search(r"(?:^|/)(drive|video)(?:/|$)", file_type_match.group(1))
        if not file_type_segment:
            raise DirectDownloadLinkException("ERROR: Invalid pack file type path")

        pack_file_type = file_type_segment.group(1)
        json_match = re.search(r"const\s+packData\s*=\s*JSON\.parse\(`({.+?})`\);", res.text, re.DOTALL)
        if not json_match:
            raise DirectDownloadLinkException("ERROR: Could not parse pack data")

        pack_info = loads(json_match.group(1))
        title = pack_info["pack"]["pack_name"]

        details = {
            "title": title,
            "total_size": 0,
            "contents": [],
            "header": f"Referer: {hubcloud_domain}/",
        }

        files_data = pack_info.get("files", [])
        for item in files_data:
            share_id = item.get("share_id")
            file_name = item.get("file_name", f"File_{share_id}")
            file_size = item.get("file_size", 0)

            if not share_id:
                continue

            link = f"https://{domain}/{pack_file_type}/{share_id}"
            try:
                result = hubcloud(link)
                dl_url = None

                if isinstance(result, str):
                    dl_url = result
                elif isinstance(result, tuple):
                    dl_url = result[0]
                elif isinstance(result, dict):
                    nested_contents = result.get("contents", [])
                    if nested_contents:
                        details["contents"].extend(nested_contents)
                        details["total_size"] += result.get("total_size", 0)
                    continue

                if dl_url:
                    details["contents"].append({"path": "", "filename": file_name, "url": dl_url})
                    try:
                        details["total_size"] += int(file_size)
                    except (ValueError, TypeError):
                        pass

            except Exception:
                continue

        if not details["contents"]:
            raise DirectDownloadLinkException("ERROR: No download links found in pack")
        return details

    card_header = soup.find("div", class_="card-header")
    title = card_header.text.strip() if card_header else f"HubCloud_File_{code}"

    anchor_href = ""
    anchor = soup.find("a", href=lambda x: x and "token" in x.lower())
    if anchor and anchor.get("href"):
        anchor_href = anchor["href"]

    if not anchor_href:
        anchor = soup.find("a", id="download", attrs={"x-href": True})
        if anchor:
            try:
                anchor["href"] = decode64(anchor["x-href"])
                anchor_href = anchor["href"]
            except (ValueError, TypeError, UnicodeDecodeError):
                pass

    if not anchor_href:
        candidates = []
        patterns = [
            r'href\s*=\s*["\']([^"\']*(?:\?|&)token[^"\']*)["\']',
            r'href\s*=\s*["\']([^"\'"]*/token/[^"\']*)["\']',
            r'(https?://[^\s"\']*(?:\?|&)token=[^\s"\']+)',
            r'(https?://[^\s"\']*/token/[^\s"\']+)',
        ]
        for pat in patterns:
            try:
                for m in re.findall(pat, res.text, flags=re.IGNORECASE):
                    if not m:
                        continue
                    low = m.lower()
                    if "csrf" in low or "turnstile" in low or "recaptcha" in low:
                        continue
                    candidates.append(m)
            except Exception:
                continue
        for cand in candidates:
            if "token=" in cand.lower() or "/token/" in cand.lower():
                anchor_href = cand
                break

    if not anchor_href:
        low_html = (res.text or "").lower()
        if any(k in low_html for k in ["just a moment", "cloudflare", "cf-chl", "cf-turnstile"]):
            raise DirectDownloadLinkException(
                "ERROR: HubCloud blocked/anti-bot page (no token link). Try enabling/using proxy and retry."
            )
        raise DirectDownloadLinkException("ERROR: No token link found")

    if not anchor_href.startswith("http"):
        anchor_href = f"https://{domain}" + anchor_href

    try:
        res1 = client.get(anchor_href, timeout=30, verify=False, impersonate="chrome110")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: Failed to get download page: {e}")

    soup1 = BeautifulSoup(res1.text, "html.parser")
    anchors = soup1.find_all("a")

    dl_links = {}
    for i in anchors:
        if not (i.get("href") or i.get("id") == "mega"):
            continue
        href = i.get("href", "")
        link_domain = urlparse(href).netloc
        text = i.get_text(strip=True)

        if "pixeldrain" in link_domain:
            dl_links["Pixeldrain"] = href
        elif "bzzhr.co" in link_domain:
            dl_links["BuzzServer"] = href
        elif "FSL Server" in text:
            dl_links["FSL Server"] = href
        elif "FSLv2 Server" in text:
            dl_links["FSLv2 Server"] = href
        elif "Download File" in text:
            dl_links["DL Server"] = href
        elif "ZipDisk" in text:
            dl_links["ZipDisk Server"] = href
        elif "Mega Server" in text:
            dl_links["Mega Server"] = href
        elif "TRS Server" in text:
            script = i.find_next("script")
            if script and script.string:
                location = re.search(r"window\.location\.href\s*=\s*'([^']+)'", script.string)
                if location:
                    try:
                        tresp = client.get(location.group(1), allow_redirects=False, timeout=10, verify=False, impersonate="chrome110")
                        loc = tresp.headers.get("Location", "")
                        if loc:
                            dl_links["TRS Server"] = loc
                    except Exception:
                        pass
        elif "10Gbps" in text:
            if "storage.googleapis.com/" in href:
                dl_links["10Gbps Server"] = href
                continue
            try:
                res1 = client.get(href, allow_redirects=False, timeout=10, verify=False, impersonate="chrome110")
                location = res1.headers.get("Location", "")
                if location.startswith("https://video-downloads"):
                    dl_links["10Gbps Server"] = location
                    continue
                if "?link=https://video-downloads" in location:
                    dl_links["10Gbps Server"] = location.split("?link=")[-1]
                    continue
                if location:
                    res111 = client.get(location, allow_redirects=False, timeout=10, verify=False, impersonate="chrome110")
                    location = res111.headers.get("Location")
                    if location:
                        dl_links["10Gbps Server"] = location.split("?link=")[-1]
            except Exception:
                pass

    if not dl_links:
        raise DirectDownloadLinkException("ERROR: No download links found")

    priority = [
        "10Gbps Server",
        "FSL Server",
        "FSLv2 Server",
        "DL Server",
        "BuzzServer",
        "Pixeldrain",
        "ZipDisk Server",
        "Mega Server",
        "TRS Server",
    ]
    for server in priority:
        if server in dl_links:
            return dl_links[server]

    return next(iter(dl_links.values()))


def gdtot(url):
    cget = create_scraper().request
    try:
        res = cget("GET", f"https://gdtot.pro/file/{url.split('/')[-1]}")
    except Exception as e:
        raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
    token_url = HTML(res.text).xpath(
        "//a[contains(@class,'inline-flex items-center justify-center')]/@href"
    )
    if not token_url:
        try:
            url = cget("GET", url).url
            p_url = urlparse(url)
            res = cget(
                "GET", f"{p_url.scheme}://{p_url.hostname}/ddl/{url.split('/')[-1]}"
            )
        except Exception as e:
            raise DirectDownloadLinkException(f"ERROR: {e.__class__.__name__}") from e
        if (
            drive_link := findall(r"myDl\('(.*?)'\)", res.text)
        ) and "drive.google.com" in drive_link[0]:
            return drive_link[0]
        else:
            raise DirectDownloadLinkException(
                "ERROR: Drive Link not found, Try in your browser"
            )
    token_url = token_url[0]
    try:
        token_page = cget("GET", token_url)
    except Exception as e:
        raise DirectDownloadLinkException(
            f"ERROR: {e.__class__.__name__} with {token_url}"
        ) from e
    path = findall(r'\("(.*?)"\)', token_page.text)
    if not path:
        raise DirectDownloadLinkException("ERROR: Cannot bypass this")
    path = path[0]
    raw = urlparse(token_url)
    final_url = f"{raw.scheme}://{raw.hostname}{path}"
    return sharer_scraper(final_url)


def zlib(url):
    return f"https://zlib.fasto.workers.dev/?url={url}"


def apkadmin(url: str) -> str:
    from bs4 import BeautifulSoup as B
    with create_scraper() as session:
        try:
            req = session.get(url).text
            soup = B(req, "lxml")
            op = soup.find("input", {"name": "op"})["value"]
            ids = soup.find("input", {"name": "id"})["value"]
            post_resp = session.post(
                url,
                data={
                    "op": op,
                    "id": ids,
                    "rand": " ",
                    "referer": " ",
                    "method_free": " ",
                    "method_premium": " ",
                },
            ).text
            soup = B(post_resp, "lxml")
            link = soup.find("div", {"class": "text text-center"})
            direct_link = link.find("a")["href"]
            return direct_link
        except Exception:
            session.close()
            raise DirectDownloadLinkException("ERROR: Link File tidak ditemukan!")


def sharemods(url: str) -> str:
    """Resolve sharemods links using standard form submission."""
    with create_scraper() as session:
        try:
            page = session.get(url).text
            tree = HTML(page)
            op = tree.xpath('//input[@name="op"]/@value')
            ids = tree.xpath('//input[@name="id"]/@value')
            if not op or not ids:
                raise DirectDownloadLinkException("ERROR: Unable to parse ShareMods form")
            payload = {
                "op": op[0],
                "id": ids[0],
                "rand": " ",
                "referer": " ",
                "method_free": " ",
                "method_premium": " ",
            }
            post_page = session.post(url, data=payload).text
            link = HTML(post_page).xpath('//a[@id="downloadbtn"]/@href')
            if not link:
                raise DirectDownloadLinkException("ERROR: ShareMods download link not found")
            return link[0]
        except DirectDownloadLinkException:
            raise
        except Exception as err:
            raise DirectDownloadLinkException(f"ERROR: {err}") from err


def sourceforge(url: str) -> str:
    from bs4 import BeautifulSoup as B
    with Session() as session:
        try:
            if "master.dl.sourceforge.net" in url:
                return f"{url}?viasf=1"
            if url.endswith("/download"):
                url = url.rsplit("/download", 1)[0]
            matches = findall(r"\bhttps?://sourceforge\.net\S+", url)
            if not matches:
                raise DirectDownloadLinkException("ERROR: SourceForge link not found")
            link = matches[0]
            file_id = findall(r"files(.*)", link)[0]
            project = findall(r"projects?/(.*?)/files", link)[0]
            response = session.get(
                "https://sourceforge.net/settings/mirror_choices",
                params={"projectname": project, "filename": file_id},
                timeout=30,
            ).content
            soup = B(response, "html.parser")
            mirror_list = soup.find("ul", {"id": "mirrorList"})
            if not mirror_list:
                raise DirectDownloadLinkException("ERROR: Unable to fetch mirror list")
            mirrors = [item["id"] for item in mirror_list.findAll("li") if item.get("id")]
            if not mirrors:
                raise DirectDownloadLinkException("ERROR: No mirrors available")
            preferred = "ixpeering" if "ixpeering" in mirrors else None
            if "autoselect" in mirrors:
                mirrors.remove("autoselect")
            chosen = preferred or choice(mirrors)
            return f"https://{chosen}.dl.sourceforge.net/project/{project}/{file_id}?viasf=1"
        except DirectDownloadLinkException:
            raise
        except Exception as err:
            raise DirectDownloadLinkException(f"ERROR: {err}") from err


def videq(url: str):
    """Scrape videq links using videq_scraper module; supports single files and folders."""
    from .videq_scraper import (
        videq as videq_scrape,
        videq_folder as videq_folder_scrape,
    )
    if "/f/" in url:
        return videq_folder_scrape(url)
    return videq_scrape(url)


def videq_folder(url: str):
    """Scrape videq folder links using videq_scraper module."""
    from .videq_scraper import videq_folder as videq_folder_scrape
    return videq_folder_scrape(url)
