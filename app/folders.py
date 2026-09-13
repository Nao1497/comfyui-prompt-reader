"""Build the folder tree for GET /folders from (dir_path, count) pairs (design.md §6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FolderNode:
    name: str
    path: str
    direct_count: int = 0
    children: dict[str, "FolderNode"] = field(default_factory=dict)

    def total_count(self) -> int:
        return self.direct_count + sum(c.total_count() for c in self.children.values())

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "directCount": self.direct_count,
            "totalCount": self.total_count(),
            # 確認事項 #17: siblings sorted by name.
            "children": [c.to_json() for _, c in sorted(self.children.items())],
        }


def is_excluded(dir_path: str, thumbnail_dir_name: str) -> bool:
    """FR-35: thumbnail directory and dot-directories never appear in the tree."""
    return any(seg == thumbnail_dir_name or seg.startswith(".") for seg in dir_path.split("/") if seg)


def build_tree(counts: list[tuple[str, int]], thumbnail_dir_name: str) -> tuple[list[dict], int]:
    """Return (folders, root_total_count) from GROUP BY dir_path results."""
    root = FolderNode(name="", path="")
    for dir_path, count in counts:
        if is_excluded(dir_path, thumbnail_dir_name):
            continue
        if dir_path == "":
            root.direct_count += count
            continue
        node = root
        parts = dir_path.split("/")
        for depth, seg in enumerate(parts):
            if seg not in node.children:
                node.children[seg] = FolderNode(name=seg, path="/".join(parts[: depth + 1]))
            node = node.children[seg]
        node.direct_count += count
    folders = [c.to_json() for _, c in sorted(root.children.items())]
    return folders, root.total_count()
