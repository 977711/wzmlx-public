# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)
# nodes.py — used by both the bot process and wserver (web/nodes.py symlink)

from anytree import NodeMixin


class TorNode(NodeMixin):
    def __init__(
        self,
        name,
        is_folder=False,
        is_file=False,
        parent=None,
        size=None,
        priority=None,
        file_id=None,
        progress=None,
    ):
        super().__init__()
        self.name = name
        self.is_folder = is_folder
        self.is_file = is_file

        if parent is not None:
            self.parent = parent
        if size is not None:
            self.fsize = size
        if priority is not None:
            self.priority = priority
        if file_id is not None:
            self.file_id = file_id
        if progress is not None:
            self.progress = progress


def qb_get_folders(path):
    return path.split("/")


def get_folders(path, root_path):
    fs = path.split(root_path)[-1]
    return fs.split("/")


def make_tree(res, tool, root_path=""):
    if tool == "qbittorrent":
        parent = TorNode("QBITTORRENT")
        folder_id = 0
        for i in res:
            folders = qb_get_folders(i.name)
            if len(folders) > 1:
                previous_node = parent
                for j in range(len(folders) - 1):
                    current_node = next(
                        (k for k in previous_node.children if k.name == folders[j]),
                        None,
                    )
                    if current_node is None:
                        previous_node = TorNode(
                            folders[j],
                            is_folder=True,
                            parent=previous_node,
                            file_id=folder_id,
                        )
                        folder_id += 1
                    else:
                        previous_node = current_node
                TorNode(
                    folders[-1],
                    is_file=True,
                    parent=previous_node,
                    size=i.size,
                    priority=i.priority,
                    file_id=i.index,
                    progress=round(i.progress * 100, 5),
                )
            else:
                TorNode(
                    folders[-1],
                    is_file=True,
                    parent=parent,
                    size=i.size,
                    priority=i.priority,
                    file_id=i.index,
                    progress=round(i.progress * 100, 5),
                )
    elif tool == "aria2":
        parent = TorNode("ARIA2")
        folder_id = 0
        for i in res:
            folders = get_folders(i["path"], root_path)
            priority = 1
            if i["selected"] == "false":
                priority = 0
            if len(folders) > 1:
                previous_node = parent
                for j in range(len(folders) - 1):
                    current_node = next(
                        (k for k in previous_node.children if k.name == folders[j]),
                        None,
                    )
                    if current_node is None:
                        previous_node = TorNode(
                            folders[j],
                            is_folder=True,
                            parent=previous_node,
                            file_id=folder_id,
                        )
                        folder_id += 1
                    else:
                        previous_node = current_node
                try:
                    progress = round(
                        (int(i["completedLength"]) / int(i["length"])) * 100, 5
                    )
                except ZeroDivisionError:
                    progress = 0
                TorNode(
                    folders[-1],
                    is_file=True,
                    parent=previous_node,
                    size=int(i["length"]),
                    priority=priority,
                    file_id=i["index"],
                    progress=progress,
                )
            else:
                try:
                    progress = round(
                        (int(i["completedLength"]) / int(i["length"])) * 100, 5
                    )
                except ZeroDivisionError:
                    progress = 0
                TorNode(
                    folders[-1],
                    is_file=True,
                    parent=parent,
                    size=int(i["length"]),
                    priority=priority,
                    file_id=i["index"],
                    progress=progress,
                )
    else:
        # sabnzbd
        parent = TorNode("SABNZBD+")
        priority = 1
        for i in res["files"]:
            TorNode(
                i["filename"],
                is_file=True,
                parent=parent,
                size=float(i["mb"]) * 1048576,
                priority=priority,
                file_id=i["nzf_id"],
                progress=round(
                    ((float(i["mb"]) - float(i["mbleft"])) / float(i["mb"])) * 100,
                    5,
                ),
            )

    result = create_list(parent)
    return {"files": result, "engine": tool}


# ── MEGA tree builder ────────────────────────────────────────────────────────
# file_list: list of dicts with keys: name, path, size, is_dir, id
# path is the parent path string (e.g. "FolderA/SubB/"), name is the node name.

def make_mega_tree(file_list: list) -> dict:
    """
    Build a nested file tree from the flat list produced by
    mega_download._walk_mega_node and return the standard
    {"files": [...], "engine": "mega"} dict that page.html understands.
    """
    parent = TorNode("MEGA")
    folder_id = 0
    path_to_node: dict[str, TorNode] = {"": parent}

    # Sort folders by depth so parents are created before children.
    folders = sorted(
        [f for f in file_list if f["is_dir"]],
        key=lambda x: x["path"].count("/"),
    )
    for f in folders:
        full_path = f"{f['path']}{f['name']}".rstrip("/")
        if full_path in path_to_node:
            continue
        parent_path = f["path"].rstrip("/")
        parent_node = path_to_node.get(parent_path, parent)
        path_to_node[full_path] = TorNode(
            f["name"],
            is_folder=True,
            parent=parent_node,
            file_id=folder_id,
        )
        folder_id += 1

    for f in file_list:
        if f["is_dir"]:
            continue
        parent_path = f["path"].rstrip("/")
        parent_node = path_to_node.get(parent_path, parent)
        TorNode(
            f["name"],
            is_file=True,
            parent=parent_node,
            size=f["size"],
            # honour previously-stored selection if present in the dict
            priority=1 if f.get("selected", True) else 0,
            file_id=f["id"],
            progress=0,
        )

    result = create_list(parent)
    return {"files": result, "engine": "mega"}


def make_terabox_tree(file_list):
    parent = TorNode("TERABOX")
    folder_id = 0
    path_to_node = {"": parent}

    folders = sorted(
        [f for f in file_list if f["is_dir"]],
        key=lambda x: x["path"].count("/"),
    )
    for f in folders:
        full_path = f"{f['path']}".rstrip("/")
        if full_path in path_to_node:
            continue
        parent_path = f["path"].rstrip("/").rsplit("/", 1)[0]
        parent_node = path_to_node.get(parent_path, parent)
        path_to_node[full_path] = TorNode(
            f["name"],
            is_folder=True,
            parent=parent_node,
            file_id=folder_id,
        )
        folder_id += 1

    for f in file_list:
        if f["is_dir"]:
            continue
        parent_path = f["path"].rstrip("/").rsplit("/", 1)[0]
        parent_node = path_to_node.get(parent_path, parent)
        TorNode(
            f["name"],
            is_file=True,
            parent=parent_node,
            size=f["size"],
            priority=1,
            file_id=f["id"],
            progress=0,
        )

    result = create_list(parent)
    return {"files": result, "engine": "terabox"}


def make_rclone_tree(file_list):
    parent = TorNode("RCLONE")
    folder_id = 0
    path_to_node = {"": parent}

    for f in sorted(file_list, key=lambda x: x.get("path", "")):
        full = (f.get("path") or "").strip("/")
        if not full:
            continue
        parts = full.split("/")
        cur = parent
        cur_path = ""
        for comp in parts[:-1]:
            cur_path = f"{cur_path}/{comp}" if cur_path else comp
            node = path_to_node.get(cur_path)
            if node is None:
                node = TorNode(
                    comp, is_folder=True, parent=cur, file_id=folder_id
                )
                folder_id += 1
                path_to_node[cur_path] = node
            cur = node
        TorNode(
            parts[-1],
            is_file=True,
            parent=cur,
            size=f.get("size", 0),
            priority=1,
            file_id=f.get("id", full),
            progress=0,
        )

    result = create_list(parent)
    return {"files": result, "engine": "rclone"}


# ── shared helpers ───────────────────────────────────────────────────────────

def create_list(parent, contents=None):
    if contents is None:
        contents = []
    for i in parent.children:
        if i.is_folder:
            children = []
            create_list(i, children)
            contents.append(
                {
                    "id": f"folderNode_{i.file_id}",
                    "name": i.name,
                    "type": "folder",
                    "children": children,
                }
            )
        else:
            contents.append(
                {
                    "id": i.file_id,
                    "name": i.name,
                    "size": i.fsize,
                    "type": "file",
                    "selected": bool(i.priority),
                    "progress": i.progress,
                }
            )
    return contents


def extract_file_ids(data):
    """
    Accept either:
      • the new compact form  {"selected_ids": [...], "unselected_ids": [...]}
      • the legacy nested list that page.html POSTs
    Returns (selected_files, unselected_files) as lists of strings.
    """
    if isinstance(data, dict) and (
        "selected_ids" in data or "unselected_ids" in data
    ):
        selected_files = [str(x) for x in data.get("selected_ids", []) or []]
        unselected_files = [str(x) for x in data.get("unselected_ids", []) or []]
        return selected_files, unselected_files

    selected_files = []
    unselected_files = []
    if not isinstance(data, list):
        return selected_files, unselected_files
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "file":
            file_id = item.get("id")
            if file_id is None:
                continue
            if item.get("selected"):
                selected_files.append(str(file_id))
            else:
                unselected_files.append(str(file_id))
        if item.get("children"):
            child_selected, child_unselected = extract_file_ids(item["children"])
            selected_files.extend(child_selected)
            unselected_files.extend(child_unselected)
    return selected_files, unselected_files
