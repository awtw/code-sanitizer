"""Create a read-only working snapshot of the input (folder, ZIP or single file).

The input itself is never modified. ZIP archives are checked entry by entry
before anything is written: absolute paths, ``..`` traversal, symlinks,
special files, case-insensitive name collisions and size/ratio limits.
Folder input is copied without following symlinks.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class IngestError(Exception):
    pass


@dataclass
class Limits:
    max_zip_bytes: int = 200 * 1024**2
    max_total_bytes: int = 1024**3
    max_files: int = 20_000
    max_file_bytes: int = 10 * 1024**2
    max_ratio: int = 200  # uncompressed / compressed, per entry


@dataclass
class Snapshot:
    root: Path
    input_sha256: str
    skipped: list[str] = field(default_factory=list)  # (relative path: reason)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: Path) -> str:
    """Content hash of a folder (paths + bytes), independent of timestamps and OS."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(_sha256_file(p).encode() + b"\n")
    return h.hexdigest()


def _check_entry_name(name: str) -> PurePosixPath:
    name = name.replace("\\", "/")
    p = PurePosixPath(name)
    if p.is_absolute() or (len(name) > 1 and name[1] == ":"):
        raise IngestError(f"absolute path in archive: {name!r}")
    if any(part == ".." for part in p.parts):
        raise IngestError(f"path traversal in archive: {name!r}")
    return p


def _strip_single_top_folder(names: list[PurePosixPath]) -> int:
    """Return 1 if every entry (ignoring __MACOSX) lives under the same top folder."""
    tops = {n.parts[0] for n in names if n.parts and n.parts[0] != "__MACOSX"}
    deep = all(len(n.parts) > 1 for n in names if n.parts and n.parts[0] != "__MACOSX")
    return 1 if len(tops) == 1 and deep else 0


def _extract_zip(zip_path: Path, dest: Path, limits: Limits, skipped: list[str]) -> None:
    if zip_path.stat().st_size > limits.max_zip_bytes:
        raise IngestError(f"archive larger than {limits.max_zip_bytes} bytes")
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_files:
            raise IngestError(f"archive has more than {limits.max_files} files")
        names = [_check_entry_name(i.filename) for i in infos]
        strip = _strip_single_top_folder(names)
        seen_lower: dict[str, str] = {}
        total = 0
        plan = []
        for info, name in zip(infos, names):
            if info.flag_bits & 0x1:
                raise IngestError(f"encrypted entry not supported: {info.filename!r}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                skipped.append(f"{info.filename}: symlink in archive")
                continue
            if mode and not (stat.S_ISREG(mode) or mode & 0o170000 == 0):
                skipped.append(f"{info.filename}: special file in archive")
                continue
            if info.file_size > limits.max_file_bytes:
                raise IngestError(f"entry larger than {limits.max_file_bytes} bytes: {info.filename!r}")
            if info.compress_size and info.file_size / info.compress_size > limits.max_ratio:
                raise IngestError(f"suspicious compression ratio: {info.filename!r}")
            rel = PurePosixPath(*name.parts[strip:]) if name.parts[0] != "__MACOSX" else name
            key = rel.as_posix().lower()
            if key in seen_lower:
                raise IngestError(f"case-insensitive name collision: {seen_lower[key]!r} vs {rel.as_posix()!r}")
            seen_lower[key] = rel.as_posix()
            total += info.file_size
            if total > limits.max_total_bytes:
                raise IngestError(f"archive expands beyond {limits.max_total_bytes} bytes")
            plan.append((info, rel))
        for info, rel in plan:
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, target.open("wb") as out:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    written += len(chunk)
                    if written > limits.max_file_bytes:  # the header can lie
                        raise IngestError(f"entry larger than declared: {info.filename!r}")
                    out.write(chunk)


def _copy_tree(src: Path, dest: Path, limits: Limits, skipped: list[str]) -> None:
    count = total = 0
    seen_lower: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        d = Path(dirpath)
        for name in list(dirnames):
            if (d / name).is_symlink():
                skipped.append(f"{(d / name).relative_to(src).as_posix()}: symlinked folder not followed")
                dirnames.remove(name)
        for fn in filenames:
            p = d / fn
            rel = p.relative_to(src).as_posix()
            if p.is_symlink():
                skipped.append(f"{rel}: symlink not followed")
                continue
            if not p.is_file():
                skipped.append(f"{rel}: special file")
                continue
            key = rel.lower()
            if key in seen_lower:
                skipped.append(f"{rel}: case-insensitive collision with {seen_lower[key]}")
                continue
            seen_lower[key] = rel
            size = p.stat().st_size
            count += 1
            total += size
            if count > limits.max_files or total > limits.max_total_bytes:
                raise IngestError("input exceeds file count or size limit")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)


def ingest(source: Path, dest: Path, limits: Limits | None = None) -> Snapshot:
    """Copy or extract ``source`` into ``dest`` (which must not exist yet)."""
    limits = limits or Limits()
    source = source.resolve()
    dest = dest.resolve()
    if not source.exists():
        raise IngestError(f"input not found: {source}")
    if dest.exists():
        raise IngestError(f"snapshot folder already exists: {dest}")
    if source.is_dir() and (dest == source or source in dest.parents):
        raise IngestError("output must not be inside the input folder")
    skipped: list[str] = []
    dest.mkdir(parents=True)
    try:
        if source.is_dir():
            _copy_tree(source, dest, limits, skipped)
            digest = tree_sha256(source)
        elif zipfile.is_zipfile(source):
            _extract_zip(source, dest, limits, skipped)
            digest = _sha256_file(source)
        else:
            shutil.copyfile(source, dest / source.name)
            digest = _sha256_file(source)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return Snapshot(dest, digest, skipped)
