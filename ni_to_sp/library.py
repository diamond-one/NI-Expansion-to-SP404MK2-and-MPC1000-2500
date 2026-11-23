"""Discover installed Maschine groups; never infer a kit from sample filenames."""
import hashlib
import os
from pathlib import Path


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def discover_kits(root="C:/Users/Public/Documents", cancel=None):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Choose the folder containing your installed NI libraries.")
    single = (root / "Groups").is_dir() or (root / "Kits").is_dir()
    packs = [root] if single else sorted(root.iterdir(), key=lambda p: p.name.casefold())
    kits = []
    for pack in packs:
        if cancel and cancel.is_set():
            raise InterruptedError("Discovery cancelled")
        if not pack.is_dir() or _is_link(pack):
            continue
        # Most expansions keep kits under Groups/; a few (Deep Matter, Molten Veil) use a top-level Kits/.
        for groups in (pack / "Groups", pack / "Kits"):
            kits.extend(_kits_in(pack, groups, cancel))
    return kits


def _kits_in(pack, groups, cancel):
    kits = []
    if not groups.is_dir() or _is_link(groups):
        return kits
    for folder, directories, names in os.walk(groups, followlinks=False):
        current = Path(folder)
        directories[:] = sorted(name for name in directories if not name.startswith(".")
            and not _is_link(current / name) and (current / name).resolve().is_relative_to(groups.resolve()))
        for name in sorted(names, key=str.casefold):
            if cancel and cancel.is_set():
                raise InterruptedError("Discovery cancelled")
            path = current / name
            if path.suffix.casefold() != ".mxgrp" or path.is_symlink() or not path.resolve().is_relative_to(groups.resolve()):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            preview = current / ".previews" / (name + ".ogg")
            kits.append({"id": hashlib.sha256(str(path).casefold().encode()).hexdigest()[:20],
                "name": path.stem, "pack": pack.name.removesuffix(" Library"), "path": str(path),
                "root": str(pack), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "preview": str(preview) if preview.is_file() else None})
    return kits
