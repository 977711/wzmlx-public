import os
from asyncio import Lock as AsyncLock, sleep as asleep, wait_for, TimeoutError as AsyncTimeoutError
from contextlib import suppress
from secrets import token_hex

from aiofiles.os import makedirs
from mega import MegaApi, MegaCancelToken

from .... import LOGGER, task_dict, task_dict_lock, user_data
from ....core.config_manager import Config
from ...telegram_helper.message_utils import send_status_message, send_message
from ...ext_utils.bot_utils import sync_to_async
from ...ext_utils.task_manager import (
    check_running_tasks,
    limit_checker,
    stop_duplicate_check,
)
from ...ext_utils.files_utils import clean_download
from ...ext_utils.links_utils import get_mega_subfolder_handle, is_mega_folder_link
from ...listeners.mega_listener import (
    AsyncMega,
    MegaAppListener,
    MegaFolderListener,
    _mega_error_format,
)
from ...mirror_leech_utils.status_utils.mega_status import MegaDownloadStatus
from ...mirror_leech_utils.status_utils.queue_status import QueueStatus
from web.nodes import mega_node_children_to_list


_ACTIVE_MEGA_LINKS = set()
_ACTIVE_MEGA_LINKS_LOCK = AsyncLock()

# How long (seconds) to wait for the user to submit file selection via the web UI
_MEGA_SELECT_TIMEOUT = 300

_MEGA_BASE64_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _mega_base64_to_int(handle_str: str) -> int | None:
    if not handle_str:
        return None
    try:
        val = 0
        for c in handle_str:
            idx = _MEGA_BASE64_ALPHABET.find(c)
            if idx < 0:
                return None
            val = (val << 6) | idx
        return val & ((1 << 64) - 1)
    except Exception:
        return None


def _find_child_by_handle(api, parent_node, target_handle):
    if not parent_node or not target_handle:
        return None
    try:
        children = api.getChildren(parent_node)
        return _find_child_in_list(children, target_handle)
    except Exception as e:
        LOGGER.warning(f"_find_child_by_handle error: {e}")
    return None


def _find_child_in_list(children, target_handle):
    if not children:
        return None
    try:
        _to_handle = getattr(MegaApi, "base64ToHandle", None)
        target_int = _to_handle(target_handle) if callable(_to_handle) else None
    except Exception:
        target_int = None
    for i in range(children.size()):
        child = children.get(i)
        try:
            ch = child.getHandle()
            if ch == target_handle or (target_int is not None and ch == target_int):
                return child
        except Exception:
            pass
    return None


def _make_cancel_token():
    if MegaCancelToken is None:
        return None
    try:
        return MegaCancelToken.createInstance()
    except Exception as e:
        LOGGER.error(f"Mega: failed to create cancel token: {e}")
        return None


def _get_node_by_handle_str(api, handle_str: str):
    try:
        handle_int = int(handle_str)
        return api.getNodeByHandle(handle_int)
    except Exception:
        return None


async def _collect_selected_nodes(folder_api, selected_handles: list):
    """Return list of (MegaNode, name) for each selected file handle string."""
    matched = []
    for h in selected_handles:
        node = await sync_to_async(_get_node_by_handle_str, folder_api, h)
        if node and not node.isFolder():
            try:
                name = node.getName() or h
            except Exception:
                name = h
            matched.append((node, name))
    return matched


async def _reserve_link(link: str):
    async with _ACTIVE_MEGA_LINKS_LOCK:
        if link in _ACTIVE_MEGA_LINKS:
            return False
        _ACTIVE_MEGA_LINKS.add(link)
        return True


async def _release_link(link: str):
    async with _ACTIVE_MEGA_LINKS_LOCK:
        _ACTIVE_MEGA_LINKS.discard(link)


async def add_mega_download(listener, path):
    if Config.DISABLE_MEGA:
        await listener.on_download_error(
            "Mega Link downloads are currently disabled by the Bot Owner."
        )
        return

    user_dict = user_data.get(listener.user_id, {})
    mega_email = user_dict.get("MEGA_EMAIL") or Config.MEGA_EMAIL
    mega_password = user_dict.get("MEGA_PASSWORD") or Config.MEGA_PASSWORD

    if not await _reserve_link(listener.link):
        await listener.on_download_error(
            "This Mega link is already being downloaded! Wait for it to finish."
        )
        return

    async_api = None
    mega_base = ""
    is_folder = False  # declared early so finally-block can reference it safely
    try:
        sdk_gid = token_hex(5)
        await makedirs(path, exist_ok=True)
        mega_base = os.path.join(
            os.path.dirname(path.rstrip("/")), ".mega_sdk", sdk_gid
        )
        mega_dir = os.path.join(mega_base, "main")
        await makedirs(mega_dir, exist_ok=True)

        async_api = AsyncMega()
        async_api.api = api = MegaApi("", mega_dir, "WZML-X", 4)
        mega_listener = MegaAppListener(async_api, listener)
        async_api._mega_listener = mega_listener
        api.addListener(mega_listener)
        api._listener_ref = mega_listener

        is_folder = is_mega_folder_link(listener.link)
        subfolder_handle = get_mega_subfolder_handle(listener.link)

        if is_folder:
            async_api.folder_api = folder_api = MegaApi("", mega_dir, "WZML-X", 4)

            if mega_email and mega_password:
                LOGGER.info("Mega: authenticating premium account for folder download")
                await async_api.login(mega_email, mega_password)
                if listener.is_cancelled or async_api._mega_listener.is_cancelled:
                    return
                if async_api._mega_listener.error:
                    await listener.on_download_error(
                        _mega_error_format(async_api._mega_listener.error)
                    )
                    return

                account_auth = api.getAccountAuth()
                if not account_auth:
                    await listener.on_download_error(
                        "Failed to obtain MEGA account authentication."
                    )
                    return

                folder_api.setAccountAuth(account_auth)
                LOGGER.info("Mega: premium account auth applied to folder API")
                del account_auth

            folder_listener = MegaFolderListener(async_api, listener)
            async_api._folder_listener = folder_listener
            folder_api.addListener(folder_listener)
            folder_api._listener_ref = folder_listener
            dl_listener = folder_listener

            await async_api.loginToFolder(listener.link)
            if listener.is_cancelled or dl_listener.is_cancelled:
                return
            if dl_listener.error:
                await listener.on_download_error(_mega_error_format(dl_listener.error))
                return
            await async_api.fetchNodes(api=folder_api)
            await asleep(0)
            if listener.is_cancelled or dl_listener.is_cancelled:
                LOGGER.info("Mega: cancelled after fetchNodes")
                return
            if dl_listener.error:
                LOGGER.info("Mega: error after fetchNodes: %s", dl_listener.error)
                await listener.on_download_error(_mega_error_format(dl_listener.error))
                return
            if not dl_listener.node:
                LOGGER.info("Mega: no root node after fetchNodes")
                await listener.on_download_error(
                    "Failed to get root node for MEGA folder"
                )
                return

            if subfolder_handle:
                LOGGER.info("Mega: looking up subfolder handle=%s", subfolder_handle)
                target_int = _mega_base64_to_int(subfolder_handle)
                node = _find_child_in_list(dl_listener._children, subfolder_handle)
                if not node and target_int is not None:
                    try:
                        node = folder_api.getNodeByHandle(target_int)
                    except Exception as e:
                        LOGGER.error("Mega: getNodeByHandle failed: %s", e)
                if not node:
                    await listener.on_download_error(
                        "Subfolder not found in the MEGA link"
                    )
                    return
                dl_listener.node = node
                dl_listener._cache_node_data(node)
                LOGGER.info("Mega: subfolder name=%s", dl_listener._name)
                dl_listener._size = listener.size
                if not dl_listener._size:
                    try:
                        s = node.getSize()
                        dl_listener._size = s if s < (1 << 62) else -1
                    except Exception:
                        pass
                LOGGER.info("Mega: subfolder size=%s", dl_listener._size)
            else:
                node = dl_listener.node

            # ── File selection (only for folder links with -s flag) ────────────
            LOGGER.info("Mega: select flag=%s", getattr(listener, "select", False))
            if getattr(listener, "select", False):
                try:
                    LOGGER.info("Mega: calling mega_node_children_to_list node=%s", node)
                    raw_items = await sync_to_async(
                        mega_node_children_to_list, node, folder_api
                    )
                    LOGGER.info("Mega: raw_items count=%d", len(raw_items) if raw_items else 0)
                except Exception as e:
                    LOGGER.error("Mega: mega_node_children_to_list failed: %s", e, exc_info=True)
                    await listener.on_download_error(f"Mega file listing failed: {e}")
                    return

                if not raw_items:
                    await listener.on_download_error(
                        "Mega folder appears empty or could not be listed."
                    )
                    return

                try:
                    # mega_session_create() registers the asyncio.Event on the
                    # wserver bot_loop so mega_session_submit()'s
                    # call_soon_threadsafe correctly wakes our wait_for() below.
                    from web.wserver import mega_session_create, mega_session_get, mega_session_pop, _derive_pin
                    mgid = token_hex(5)
                    mega_session_create(mgid, raw_items)
                    pin = _derive_pin(mgid)
                    LOGGER.info("Mega: session created mgid=%s pin=%s", mgid, pin)
                except Exception as e:
                    LOGGER.error("Mega: session creation failed: %s", e, exc_info=True)
                    await listener.on_download_error(f"Mega session creation failed: {e}")
                    return

                try:
                    # /app/files serves page.html. engine=mega tells the frontend
                    # JS to hit /app/files/mega for data fetch and POST submission
                    # instead of the torrent endpoint.
                    base_url = getattr(Config, "BASE_URL", "").rstrip("/")
                    select_url = f"{base_url}/app/files?gid={mgid}&pin={pin}&engine=mega"
                    LOGGER.info("Mega: sending selection URL: %s", select_url)
                    await send_message(
                        listener.message,
                        f"📂 <b>Mega File Selection</b>\n\n"
                        f"Select which files to download:\n{select_url}\n\n"
                        f"⏳ You have {_MEGA_SELECT_TIMEOUT // 60} minutes to choose."
                    )
                    LOGGER.info("Mega: selection URL sent, waiting for user response...")
                except Exception as e:
                    LOGGER.error("Mega: failed to send selection URL: %s", e, exc_info=True)
                    mega_session_pop(mgid)
                    await listener.on_download_error(f"Mega failed to send selection link: {e}")
                    return

                # Wait for user to submit; use the Event from the session dict so
                # we wait on the exact object that mega_session_submit() will set.
                _sel_session = mega_session_get(mgid)
                try:
                    await wait_for(_sel_session["event"].wait(), timeout=_MEGA_SELECT_TIMEOUT)
                except AsyncTimeoutError:
                    mega_session_pop(mgid)
                    await listener.on_download_error(
                        "Mega file selection timed out. Please retry with -s flag."
                    )
                    return

                session = mega_session_get(mgid)
                selected_handles = (session.get("selected") or []) if session else []
                mega_session_pop(mgid)

                if not selected_handles:
                    await listener.on_download_error("No files were selected. Download cancelled.")
                    return

                LOGGER.info("Mega: user selected %d file(s) for mgid=%s", len(selected_handles), mgid)

                selected_nodes = await _collect_selected_nodes(folder_api, selected_handles)
                if not selected_nodes:
                    await listener.on_download_error(
                        "Could not resolve selected files. Download cancelled."
                    )
                    return

                listener.name = listener.name or dl_listener._name or f"MEGA_Download_{token_hex(5)}"
                download_path = os.path.join(path, listener.name)
                await makedirs(download_path, exist_ok=True)

                total_size = 0
                for sel_node, _ in selected_nodes:
                    try:
                        total_size += int(sel_node.getSize())
                    except Exception:
                        pass
                listener.size = total_size

                gid = token_hex(5)
                msg, button = await stop_duplicate_check(listener)
                if msg:
                    await listener.on_download_error(msg, button)
                    return
                if limit_exceeded := await limit_checker(listener):
                    await listener.on_download_error(limit_exceeded, is_limit=True)
                    return

                added_to_queue, event = await check_running_tasks(listener)
                if added_to_queue:
                    async with task_dict_lock:
                        task_dict[listener.mid] = QueueStatus(listener, gid, "dl")
                    await listener.on_download_start()
                    if listener.multi <= 1:
                        await send_status_message(listener.message)
                    await event.wait()
                    if listener.is_cancelled:
                        return

                async with task_dict_lock:
                    task_dict[listener.mid] = MegaDownloadStatus(listener, dl_listener, gid, "dl")

                await listener.on_download_start()
                if not added_to_queue and listener.multi <= 1:
                    await send_status_message(listener.message)

                for sel_node, file_name in selected_nodes:
                    if listener.is_cancelled or dl_listener.is_cancelled:
                        return
                    for attempt in range(5):
                        cancel_token = _make_cancel_token()
                        dl_listener._cancel_token = cancel_token
                        dl_listener.error = None
                        dl_listener.retryable_error = None
                        dl_listener._bytes_transferred = 0
                        dl_listener._total_downloaded_bytes = 0
                        dl_listener._caller_manages_completion = False

                        await async_api.startDownload(
                            sel_node, download_path, file_name, None, False,
                            cancel_token, 3, 2, False,
                        )
                        await async_api.wait_for_transfer()

                        if listener.is_cancelled or dl_listener.is_cancelled:
                            return
                        if not dl_listener.retryable_error:
                            break
                        if attempt >= 4:
                            await listener.on_download_error(
                                _mega_error_format(dl_listener.retryable_error)
                            )
                            return
                        await clean_download(os.path.join(download_path, file_name))
                        await asleep(2 ** attempt)

                if not listener.is_cancelled and not dl_listener.is_cancelled:
                    await listener.on_download_complete()
                return
            # ── end file-selection branch ─────────────────────────────────────

        else:
            dl_listener = mega_listener
            if mega_email and mega_password:
                await async_api.login(mega_email, mega_password)
                if listener.is_cancelled or mega_listener.is_cancelled:
                    return
                if mega_listener.error:
                    await listener.on_download_error(
                        _mega_error_format(mega_listener.error)
                    )
                    return
                await async_api.fetchNodes()
                if listener.is_cancelled or mega_listener.is_cancelled:
                    return
                if mega_listener.error:
                    await listener.on_download_error(
                        _mega_error_format(mega_listener.error)
                    )
                    return
            await async_api.getPublicNode(listener.link)
            if listener.is_cancelled or mega_listener.is_cancelled:
                return
            node = mega_listener.public_node
            if not node:
                await listener.on_download_error("Failed to resolve MEGA link")
                return

        listener.name = (
            listener.name or dl_listener._name or f"MEGA_Download_{token_hex(5)}"
        )
        listener.size = dl_listener._size if dl_listener._size < (1 << 62) else -1
        if listener.size <= 0 and node:
            try:
                s = node.getSize()
                listener.size = s if s < (1 << 62) else -1
            except Exception:
                pass
        gid = token_hex(5)
        msg, button = await stop_duplicate_check(listener)
        if msg:
            await listener.on_download_error(msg, button)
            return

        if limit_exceeded := await limit_checker(listener):
            await listener.on_download_error(limit_exceeded, is_limit=True)
            return

        added_to_queue, event = await check_running_tasks(listener)
        if added_to_queue:
            async with task_dict_lock:
                task_dict[listener.mid] = QueueStatus(listener, gid, "dl")
            await listener.on_download_start()
            if listener.multi <= 1:
                await send_status_message(listener.message)
            await event.wait()
            if listener.is_cancelled:
                return

        async with task_dict_lock:
            task_dict[listener.mid] = MegaDownloadStatus(
                listener, dl_listener, gid, "dl"
            )

        if added_to_queue:
            await listener.on_download_start()
        else:
            await listener.on_download_start()
            if listener.multi <= 1:
                await send_status_message(listener.message)

        if listener.is_cancelled or dl_listener.is_cancelled:
            return
        download_path = path
        if is_mega_folder_link(listener.link):
            download_path = os.path.join(path, listener.name)
            await makedirs(download_path, exist_ok=True)

        for attempt in range(5):
            cancel_token = _make_cancel_token()
            dl_listener._cancel_token = cancel_token
            dl_listener.error = None
            dl_listener.retryable_error = None
            dl_listener._bytes_transferred = 0
            dl_listener._total_downloaded_bytes = 0
            dl_listener._caller_manages_completion = False

            await async_api.startDownload(
                node,
                download_path,
                listener.name,
                None,
                False,
                cancel_token,
                3,
                2,
                False,
            )
            await async_api.wait_for_transfer()

            if listener.is_cancelled or dl_listener.is_cancelled:
                return
            if not dl_listener.retryable_error:
                return

            if dl_listener.retryable_error.startswith("-13"):
                local_size = 0
                if os.path.isdir(download_path):
                    for root, dirs, files in os.walk(download_path):
                        for filename in files:
                            try:
                                local_size += os.path.getsize(os.path.join(root, filename))
                            except OSError:
                                pass
                elif os.path.isfile(download_path):
                    try:
                        local_size = os.path.getsize(download_path)
                    except OSError:
                        pass

                expected_size = dl_listener._total_folder_size or dl_listener._size

                LOGGER.warning(
                    "MegaDownload: API_EINCOMPLETE local_size=%s expected_size=%s transferred=%s",
                    local_size,
                    expected_size,
                    dl_listener.downloaded_bytes,
                )

                if expected_size > 0:
                    missing = expected_size - local_size
                    tolerance = max(2 * 1024 * 1024, int(expected_size * 0.001))
                    LOGGER.warning(
                        "MegaDownload: API_EINCOMPLETE missing=%s tolerance=%s",
                        missing,
                        tolerance,
                    )
                    if missing <= tolerance:
                        LOGGER.warning(
                            "MegaDownload: treating API_EINCOMPLETE as complete; local data is within tolerance"
                        )
                        dl_listener.retryable_error = None
                        await listener.on_download_complete()
                        return

            if attempt >= 4:
                LOGGER.error(
                    "MegaDownload: transfer incomplete after 5 attempts: %s",
                    dl_listener.retryable_error,
                )
                await listener.on_download_error(
                    _mega_error_format(dl_listener.retryable_error)
                )
                return

            LOGGER.warning(
                "MegaDownload: transfer incomplete, retrying attempt %s/5: %s",
                attempt + 2,
                dl_listener.retryable_error,
            )
            await clean_download(download_path)
            await asleep(2**attempt)

    except Exception as e:
        LOGGER.error(f"Unexpected error in add_mega_download: {e}", exc_info=True)
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        if async_api is not None:
            if not is_folder:
                with suppress(Exception):
                    await async_api.logout()
                if (
                    async_api.api is not None
                    and async_api._mega_listener is not None
                ):
                    with suppress(Exception):
                        async_api.api.removeListener(async_api._mega_listener)
                if (
                    async_api.folder_api is not None
                    and async_api._folder_listener is not None
                ):
                    with suppress(Exception):
                        async_api.folder_api.removeListener(
                            async_api._folder_listener
                        )
        await _release_link(listener.link)
        await clean_download(mega_base)
