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


"""
def print_tree(parent):
    for pre, _, node in RenderTree(parent):
        treestr = u"%s%s" % (pre, node.name)
        print(treestr.ljust(8), node.is_folder, node.is_file)
"""


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


def make_mega_tree(file_list):
    """
    Build a file-tree from a flat MEGA file list.

    Each entry in file_list must be a dict:
        {
            "name":     str,           # full relative path, e.g. "folderA/sub/file.mkv"
            "size":     int,           # bytes
            "handle":   str,           # MEGA node handle (used as unique file id)
            "selected": bool,          # True = included in download
        }

    Returns the same {"files": [...], "engine": "mega"} shape that make_tree() produces
    so the existing page.html / wserver logic works without changes.
    """
    parent = TorNode("MEGA")
    folder_id = 0

    for item in file_list:
        parts = [p for p in item["name"].replace("\\", "/").split("/") if p]
        if not parts:
            continue

        previous_node = parent
        # create / navigate folder nodes for every component except the last
        for folder_name in parts[:-1]:
            existing = next(
                (c for c in previous_node.children if c.name == folder_name and c.is_folder),
                None,
            )
            if existing is None:
                previous_node = TorNode(
                    folder_name,
                    is_folder=True,
                    parent=previous_node,
                    file_id=f"mega_folder_{folder_id}",
                )
                folder_id += 1
            else:
                previous_node = existing

        # leaf file node
        TorNode(
            parts[-1],
            is_file=True,
            parent=previous_node,
            size=item.get("size", 0),
            priority=1 if item.get("selected", True) else 0,
            file_id=item["handle"],
            progress=item.get("progress", 0),
        )

    result = create_list(parent)
    return {"files": result, "engine": "mega"}


def extract_mega_handles(data):
    """
    Walk the file-tree returned by make_mega_tree / create_list and split node
    handles into selected and unselected lists.

    Returns (selected_handles: list[str], unselected_handles: list[str])
    """
    selected = []
    unselected = []
    for item in data:
        if item.get("type") == "file":
            (selected if item.get("selected") else unselected).append(str(item["id"]))
        if item.get("children"):
            s, u = extract_mega_handles(item["children"])
            selected.extend(s)
            unselected.extend(u)
    return selected, unselected


def extract_file_ids(data):
    selected_files = []
    unselected_files = []
    for item in data:
        if item.get("type") == "file":
            if item.get("selected"):
                selected_files.append(str(item["id"]))
            else:
                unselected_files.append(str(item["id"]))
        if item.get("children"):
            child_selected, child_unselected = extract_file_ids(item["children"])
            selected_files.extend(child_selected)
            unselected_files.extend(child_unselected)
    return selected_files, unselected_files
