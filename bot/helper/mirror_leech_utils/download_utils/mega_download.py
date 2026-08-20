import os
from asyncio import Lock as AsyncLock, sleep as asleep
from contextlib import suppress
from secrets import token_hex

from aiofiles.os import makedirs
from mega import MegaApi, MegaCancelToken

from .... import LOGGER, task_dict, task_dict_lock, user_data
from ....core.config_manager import Config
from ...telegram_helper.message_utils import send_status_message
from ...ext_utils.task_manager import (
    check_running_tasks,
    limit_checker,
    stop_duplicate_check,
)
from ...ext_utils.bot_utils import sync_to_async
from ...ext_utils.files_utils import clean_download
from ...ext_utils.links_utils import get_mega_subfolder_handle, is_mega_folder_link
from ...listeners.mega_listener import AsyncMega, MegaAppListener, MegaFolderListener, _mega_error_format
from ...mirror_leech_utils.status_utils.mega_status import MegaDownloadStatus
from ...mirror_leech_utils.status_utils.queue_status import QueueStatus
from ...telegram_helper.message_utils import send_message
from web.wserver import _derive_pin, mega_cleanup, mega_get_selection, mega_register_task


_ACTIVE_MEGA_LINKS = set()
_ACTIVE_MEGA_LINKS_LOCK = AsyncLock()


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


async def _reserve_link(link: str):
    async with _ACTIVE_MEGA_LINKS_LOCK:
        if link in _ACTIVE_MEGA_LINKS:
            return False
        _ACTIVE_MEGA_LINKS.add(link)
        return True


async def _release_link(link: str):
    async with _ACTIVE_MEGA_LINKS_LOCK:
        _ACTIVE_MEGA_LINKS.discard(link)


def _collect_mega_files(api, node, prefix=""):
    """
    Recursively walk the MEGA node tree and return a flat list of file dicts.
    Each dict: {name, size, handle, selected, progress}
    """
    file_list = []
    try:
        children = api.getChildren(node)
        if children is None:
            return file_list
        for i in range(children.size()):
            child = children.get(i)
            try:
                child_name = child.getName() or f"unknown_{i}"
                rel_path = f"{prefix}/{child_name}".lstrip("/")
                # MegaNode.TYPE_FOLDER == 1, TYPE_FILE == 0
                if child.getType() == 1:
                    file_list.extend(_collect_mega_files(api, child, rel_path))
                else:
                    try:
                        handle = api.handleToBase64(child.getHandle())
                    except Exception:
                        handle = str(child.getHandle())
                    file_list.append({
                        "name": rel_path,
                        "size": child.getSize() or 0,
                        "handle": handle,
                        "selected": True,
                        "progress": 0,
                    })
            except Exception as e:
                LOGGER.warning(f"Mega _collect_mega_files child error: {e}")
    except Exception as e:
        LOGGER.warning(f"Mega _collect_mega_files error: {e}")
    return file_list


async def _maybe_await_mega_selection(listener, node, correct_api, gid):
    """
    If the resolved MEGA node has more than 1 file, register the file list with
    the web server, send the user a selection URL, and wait up to 5 minutes.

    Returns (selected_handles, unselected_handles) if the user submitted a
    selection, or (None, None) to mean "download everything".
    """
    if not Config.BASE_URL:
        return None, None

    file_list = await sync_to_async(_collect_mega_files, correct_api, node)
    if len(file_list) <= 1:
        return None, None

    mega_register_task(gid, file_list, name=listener.name)

    pin = _derive_pin(gid)
    sel_url = f"{Config.BASE_URL.rstrip('/')}/app/files?gid={gid}&pin={pin}"

    await send_message(
        listener.message,
        f"<b>📂 MEGA File Selection</b>\n\n"
        f"<b>{len(file_list)} files</b> found in this folder.\n"
        f"Choose which ones to download:\n\n"
        f"<a href='{sel_url}'>🔗 Open File Selector</a>\n\n"
        f"<i>Waiting up to 5 minutes. "
        f"If you don't select, all files will be downloaded.</i>",
    )

    LOGGER.info(f"Mega: waiting for file selection, gid={gid}, files={len(file_list)}")

    for _ in range(300):
        await asleep(1)
        if listener.is_cancelled:
            mega_cleanup(gid)
            return None, None
        result = mega_get_selection(gid)
        if result is not None:
            mega_cleanup(gid)
            LOGGER.info(
                f"Mega: selection received — selected={len(result['selected'])}, "
                f"unselected={len(result['unselected'])}"
            )
            return result["selected"], result["unselected"]

    # Timed out — proceed with everything
    mega_cleanup(gid)
    LOGGER.info(f"Mega: selection timed out for gid={gid}, downloading all files")
    return None, None


async def add_mega_download(listener, path):
    if Config.DISABLE_MEGA:
        await listener.on_download_error("Mega Link downloads are currently disabled by the Bot Owner.")
        return

    user_dict = user_data.get(listener.user_id, {})
    mega_email = user_dict.get("MEGA_EMAIL") or Config.MEGA_EMAIL
    mega_password = user_dict.get("MEGA_PASSWORD") or Config.MEGA_PASSWORD

    if not await _reserve_link(listener.link):
        await listener.on_download_error("This Mega link is already being downloaded! Wait for it to finish.")
        return

    async_api = None
    mega_base = ""
    try:
        sdk_gid = token_hex(5)
        await makedirs(path, exist_ok=True)
        mega_base = os.path.join(os.path.dirname(path.rstrip("/")), ".mega_sdk", sdk_gid)
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
            folder_listener = MegaFolderListener(async_api, listener)
            async_api._folder_listener = folder_listener
            folder_api.addListener(folder_listener)
            folder_api._listener_ref = folder_listener
            dl_listener = folder_listener

            # --- PREMIUM AUTHENTICATION FIX ---
            if mega_email and mega_password:
                LOGGER.info("Mega: authenticating premium account for folder download")
                
                # 1. Login to the main API first
                await async_api.login(mega_email, mega_password)
                if listener.is_cancelled or async_api._mega_listener.is_cancelled:
                    return
                if async_api._mega_listener.error:
                    await listener.on_download_error(_mega_error_format(async_api._mega_listener.error))
                    return

                # 2. Extract local auth and apply it instantly to avoid IP bans
                account_auth = api.getAccountAuth()
                if not account_auth:
                    await listener.on_download_error("Failed to obtain MEGA account authentication.")
                    return

                folder_api.setAccountAuth(account_auth)
                LOGGER.info("Mega: premium account auth applied to folder API")
                del account_auth
            # ----------------------------------

            await async_api.loginToFolder(listener.link)
            if listener.is_cancelled or dl_listener.is_cancelled:
                return
            if dl_listener.error:
                await listener.on_download_error(_mega_error_format(dl_listener.error))
                return
            await async_api.fetchNodes(api=folder_api)
            await asleep(0)
            if listener.is_cancelled or dl_listener.is_cancelled:
                return
            if dl_listener.error:
                await listener.on_download_error(_mega_error_format(dl_listener.error))
                return
            if not dl_listener.node:
                await listener.on_download_error("Failed to get root node for MEGA folder")
                return

            if subfolder_handle:
                node = _find_child_in_list(dl_listener._children, subfolder_handle)
                if not node:
                    await listener.on_download_error("Subfolder not found in the MEGA link")
                    return
                dl_listener.node = node
                dl_listener._cache_node_data(node)
                try:
                    dl_listener._size = await sync_to_async(folder_api.getSize, node)
                except Exception:
                    pass
            else:
                node = dl_listener.node
        else:
            dl_listener = mega_listener
            if mega_email and mega_password:
                await async_api.login(mega_email, mega_password)
                if listener.is_cancelled or mega_listener.is_cancelled:
                    return
                if mega_listener.error:
                    await listener.on_download_error(_mega_error_format(mega_listener.error))
                    return
                await async_api.fetchNodes()
                if listener.is_cancelled or mega_listener.is_cancelled:
                    return
                if mega_listener.error:
                    await listener.on_download_error(_mega_error_format(mega_listener.error))
                    return
            await async_api.getPublicNode(listener.link)
            if listener.is_cancelled or mega_listener.is_cancelled:
                return
            node = mega_listener.public_node
            if not node:
                await listener.on_download_error("Failed to resolve MEGA link")
                return

        listener.name = listener.name or dl_listener._name or f"MEGA_Download_{token_hex(5)}"
        listener.size = dl_listener._size
        if not listener.size and node:
            try:
                correct_api = folder_api if node == dl_listener.node and is_folder else api
                listener.size = await sync_to_async(correct_api.getSize, node)
            except Exception as e:
                LOGGER.info("Mega: correct_api getSize exception: %s", e)
        gid = token_hex(5)

        # ── MEGA file selection ───────────────────────────────────────────────
        # Only runs for folder links with >1 file and when BASE_URL is set.
        if is_folder:
            correct_api = folder_api if is_folder else api
            selected_handles, unselected_handles = await _maybe_await_mega_selection(
                listener, node, correct_api, gid
            )
            if listener.is_cancelled:
                return
            # If user deselected everything, abort
            if selected_handles is not None and len(selected_handles) == 0:
                await listener.on_download_error("No files selected. Download cancelled.")
                return
        else:
            selected_handles, unselected_handles = None, None
        # ─────────────────────────────────────────────────────────────────────

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

        # Build a set of selected handles for fast lookup during download
        selected_handle_set = set(selected_handles) if selected_handles else None

        for attempt in range(5):
            cancel_token = _make_cancel_token()
            dl_listener._cancel_token = cancel_token
            dl_listener.error = None
            dl_listener.retryable_error = None
            dl_listener._bytes_transferred = 0
            dl_listener._total_downloaded_bytes = 0
            dl_listener._caller_manages_completion = False

            # If the user made a selection, filter the node to only selected files.
            # For a folder link we resolve individual child nodes and download each;
            # for a single file the handle set will be None so we download as normal.
            if selected_handle_set and is_folder:
                download_errors = []
                children_flat = _collect_mega_files(folder_api, node)
                for file_info in children_flat:
                    if file_info["handle"] not in selected_handle_set:
                        continue
                    if listener.is_cancelled or dl_listener.is_cancelled:
                        break
                    try:
                        child_handle_int = folder_api.base64ToHandle(file_info["handle"])
                        child_node = folder_api.getNodeByHandle(child_handle_int)
                    except Exception:
                        child_node = None
                    if not child_node:
                        LOGGER.warning(f"Mega: could not resolve node for handle {file_info['handle']}, skipping")
                        continue
                    # Preserve sub-folder structure inside download_path
                    parts = file_info["name"].replace("\\", "/").split("/")
                    sub_dir = os.path.join(download_path, *parts[:-1]) if len(parts) > 1 else download_path
                    await makedirs(sub_dir, exist_ok=True)
                    child_cancel = _make_cancel_token()
                    dl_listener._cancel_token = child_cancel
                    dl_listener.error = None
                    dl_listener.retryable_error = None
                    dl_listener._bytes_transferred = 0
                    dl_listener._total_downloaded_bytes = 0
                    dl_listener._caller_manages_completion = False
                    await async_api.startDownload(
                        child_node,
                        sub_dir,
                        parts[-1],
                        None,
                        False,
                        child_cancel,
                        3,
                        2,
                        False,
                    )
                    await async_api.wait_for_transfer()
                    if dl_listener.retryable_error:
                        download_errors.append(file_info["name"])
                # After all selected files, treat any retryable errors as the loop error
                if download_errors:
                    dl_listener.retryable_error = f"Failed: {', '.join(download_errors[:3])}"
                else:
                    dl_listener.retryable_error = None
            else:
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
            if attempt >= 4:
                await listener.on_download_error(_mega_error_format(dl_listener.retryable_error))
                return
            await clean_download(download_path)
            await asleep(2 ** attempt)

    except Exception as e:
        LOGGER.error(f"Unexpected error in add_mega_download: {e}", exc_info=True)
        if not listener.is_cancelled:
            await listener.on_download_error(f"Internal error: {e}")
    finally:
        if async_api is not None:
            if not is_folder:
                with suppress(Exception):
                    await async_api.logout()
                if async_api.api is not None and async_api._mega_listener is not None:
                    with suppress(Exception):
                        async_api.api.removeListener(async_api._mega_listener)
                if async_api.folder_api is not None and async_api._folder_listener is not None:
                    with suppress(Exception):
                        async_api.folder_api.removeListener(async_api._folder_listener)
        await _release_link(listener.link)
        await clean_download(mega_base)
