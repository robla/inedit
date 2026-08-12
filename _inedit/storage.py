"""Document loading, explicit saving, and recovery-file transactions."""

from __future__ import annotations

import codecs
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path

from .model import (
    AutoSaveError,
    AutoSaveSnapshot,
    ConflictError,
    Document,
    Fingerprint,
    LoadError,
    NewlineStyle,
    SaveError,
)

WriteAll = Callable[[int, bytes], None]


def _fingerprint(st: os.stat_result) -> Fingerprint:
    return Fingerprint(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _stat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _resolved_path(path: Path) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def decode_document(data: bytes) -> tuple[str, NewlineStyle, bool]:
    """Decode and normalize a supported on-disk document."""

    if b"\0" in data:
        raise LoadError("file contains a NUL byte")

    has_bom = data.startswith(codecs.BOM_UTF8)
    encoded_text = data[len(codecs.BOM_UTF8) :] if has_bom else data
    try:
        text = encoded_text.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise LoadError(f"file is not valid UTF-8 at byte {exc.start}") from exc

    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf:
        raise LoadError("file contains a bare carriage return")
    has_crlf = "\r\n" in text
    if has_crlf and "\n" in without_crlf:
        raise LoadError("file contains mixed LF and CRLF line endings")

    newline_style = NewlineStyle.CRLF if has_crlf else NewlineStyle.LF
    return text.replace("\r\n", "\n"), newline_style, has_bom


def _load_snapshot_is_current(
    requested_path: Path,
    target_path: Path,
    entry_fingerprint: Fingerprint | None,
    target_fingerprint: Fingerprint | None,
) -> bool:
    if _resolved_path(requested_path) != target_path:
        return False
    current_entry = _lstat_optional(requested_path)
    current_target = _stat_optional(target_path)
    return (
        _fingerprint(current_entry) if current_entry else None
    ) == entry_fingerprint and (
        _fingerprint(current_target) if current_target else None
    ) == target_fingerprint


def load_document(path: Path) -> Document:
    """Load one regular UTF-8 file, or describe a new empty target."""

    display_path = os.fspath(path)
    requested_path = Path(os.path.abspath(display_path))

    try:
        entry_stat = _lstat_optional(requested_path)
        entry_fingerprint = _fingerprint(entry_stat) if entry_stat else None
        target_path = _resolved_path(requested_path)
        target_stat = _stat_optional(target_path)

        if target_stat is None:
            if entry_stat is not None and not stat.S_ISLNK(entry_stat.st_mode):
                raise LoadError(f"{display_path}: file changed while opening")
            data = b""
            mode = None
            target_fingerprint = None
        else:
            if not stat.S_ISREG(target_stat.st_mode):
                raise LoadError(f"{display_path}: not a regular file")

            with target_path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise LoadError(f"{display_path}: not a regular file")
                if _fingerprint(before) != _fingerprint(target_stat):
                    raise LoadError(f"{display_path}: file changed while opening")
                data = stream.read()
                after = os.fstat(stream.fileno())
                if _fingerprint(after) != _fingerprint(before):
                    raise LoadError(f"{display_path}: file changed while reading")
                target_fingerprint = _fingerprint(after)
                mode = stat.S_IMODE(after.st_mode)

        if not _load_snapshot_is_current(
            requested_path,
            target_path,
            entry_fingerprint,
            target_fingerprint,
        ):
            raise LoadError(f"{display_path}: file changed while loading")
    except LoadError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise LoadError(f"{display_path}: {detail}") from exc

    try:
        text, newline_style, has_bom = decode_document(data)
    except LoadError as exc:
        raise LoadError(f"{display_path}: {exc}") from exc

    return Document(
        display_path=display_path,
        requested_path=requested_path,
        target_path=target_path,
        text=text,
        newline_style=newline_style,
        has_bom=has_bom,
        mode=mode,
        fingerprint=target_fingerprint,
        entry_fingerprint=entry_fingerprint,
    )


def encode_document(document: Document, text: str) -> bytes:
    """Encode normalized editor text in the document's original format."""

    if "\0" in text:
        raise SaveError("buffer contains a NUL character")
    if "\r" in text:
        raise SaveError("buffer contains a carriage return")

    disk_text = (
        text.replace("\n", "\r\n")
        if document.newline_style is NewlineStyle.CRLF
        else text
    )
    try:
        encoded = disk_text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SaveError("buffer cannot be encoded as UTF-8") from exc
    return codecs.BOM_UTF8 + encoded if document.has_bom else encoded


def _check_conflict(document: Document) -> None:
    try:
        if _resolved_path(document.requested_path) != document.target_path:
            raise ConflictError("file path changed; refusing to overwrite")

        entry_stat = _lstat_optional(document.requested_path)
        entry_fingerprint = _fingerprint(entry_stat) if entry_stat else None
        if entry_fingerprint != document.entry_fingerprint:
            raise ConflictError("file path changed; refusing to overwrite")

        target_stat = _stat_optional(document.target_path)
        target_fingerprint = _fingerprint(target_stat) if target_stat else None
        if target_fingerprint != document.fingerprint:
            raise ConflictError("file changed on disk; refusing to overwrite")
    except ConflictError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConflictError(f"could not check file for conflicts: {detail}") from exc


def _create_temporary_sibling(target_path: Path) -> tuple[int, Path]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        name = f".inedit-{os.getpid()}-{secrets.token_hex(8)}.tmp"
        temporary_path = target_path.parent / name
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
        except FileExistsError:
            continue
        return descriptor, temporary_path
    raise SaveError("could not allocate a temporary sibling file")


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while saving")
        remaining = remaining[written:]


def _new_file_mode() -> int:
    """Return the mode that creating a regular 0666 file would produce."""

    # Python has no read-only umask operation. Temporarily making it maximally
    # restrictive is safer than setting it to zero if another thread happens
    # to create a file during this very small window.
    previous_umask = os.umask(0o777)
    try:
        return 0o666 & ~previous_umask
    finally:
        os.umask(previous_umask)


def save_document(
    document: Document,
    text: str,
    *,
    write_all: WriteAll = _write_all,
) -> Document:
    """Atomically save text and return the new on-disk snapshot."""

    encoded = encode_document(document, text)
    _check_conflict(document)
    try:
        requested_entry = _lstat_optional(document.requested_path)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConflictError(
            f"could not inspect file path before saving: {detail}"
        ) from exc
    requested_entry_is_symlink = (
        requested_entry is not None and stat.S_ISLNK(requested_entry.st_mode)
    )

    descriptor: int | None = None
    temporary_path: Path | None = None
    saved_fingerprint: Fingerprint | None = None
    saved_mode: int | None = None
    final_mode = document.mode if document.mode is not None else _new_file_mode()
    try:
        descriptor, temporary_path = _create_temporary_sibling(document.target_path)
        write_all(descriptor, encoded)
        os.fchmod(descriptor, final_mode)
        os.fsync(descriptor)
        saved_stat = os.fstat(descriptor)
        saved_fingerprint = _fingerprint(saved_stat)
        saved_mode = stat.S_IMODE(saved_stat.st_mode)
        os.close(descriptor)
        descriptor = None

        _check_conflict(document)
        os.replace(temporary_path, document.target_path)
        temporary_path = None
    except (SaveError, ConflictError):
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise SaveError(f"could not save {document.display_path}: {detail}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # The original save error is more useful, and the target was not
                # replaced. Never risk deleting anything except this exact name.
                pass

    assert saved_fingerprint is not None
    entry_fingerprint = (
        document.entry_fingerprint
        if requested_entry_is_symlink
        else saved_fingerprint
    )
    return Document(
        display_path=document.display_path,
        requested_path=document.requested_path,
        target_path=document.target_path,
        text=text,
        newline_style=document.newline_style,
        has_bom=document.has_bom,
        mode=saved_mode,
        fingerprint=saved_fingerprint,
        entry_fingerprint=entry_fingerprint,
    )


def auto_save_path(document: Document) -> Path:
    """Return the Emacs-style recovery filename for a document."""

    name = document.requested_path.name
    return document.requested_path.with_name(f"#{name}#")


def write_auto_save(
    document: Document,
    text: str,
    previous: AutoSaveSnapshot | None = None,
    *,
    write_all: WriteAll = _write_all,
) -> AutoSaveSnapshot:
    """Atomically write one private recovery snapshot without touching FILE."""

    encoded = encode_document(document, text)
    path = auto_save_path(document)
    if previous is not None and previous.path != path:
        raise AutoSaveError("auto-save path changed; refusing to overwrite")

    descriptor: int | None = None
    temporary_path: Path | None = None
    saved_fingerprint: Fingerprint | None = None
    try:
        descriptor, temporary_path = _create_temporary_sibling(path)
        write_all(descriptor, encoded)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        saved_fingerprint = _fingerprint(os.fstat(descriptor))
        os.close(descriptor)
        descriptor = None

        current = _lstat_optional(path)
        if previous is None:
            if current is not None:
                raise AutoSaveError(
                    f"auto-save file already exists; preserving {path}"
                )
            try:
                # Linking installs a complete first snapshot without ever
                # replacing a recovery file from an earlier session.
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise AutoSaveError(
                    f"auto-save file already exists; preserving {path}"
                ) from exc
            try:
                temporary_path.unlink()
            except OSError:
                # The complete #name# snapshot is already installed. The
                # finally block gets one more chance to remove this private
                # hard-link sibling without misreporting auto-save failure.
                pass
            else:
                temporary_path = None
        else:
            current_fingerprint = _fingerprint(current) if current else None
            if current_fingerprint != previous.fingerprint:
                raise AutoSaveError(
                    f"auto-save file changed; preserving {path}"
                )
            os.replace(temporary_path, path)
            temporary_path = None
    except (SaveError, AutoSaveError):
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise AutoSaveError(f"could not auto-save {path}: {detail}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    assert saved_fingerprint is not None
    return AutoSaveSnapshot(path, saved_fingerprint)


def remove_auto_save(snapshot: AutoSaveSnapshot) -> None:
    """Remove only the recovery file represented by this session's snapshot."""

    try:
        current = _lstat_optional(snapshot.path)
        if current is None:
            return
        if _fingerprint(current) != snapshot.fingerprint:
            raise AutoSaveError(
                f"auto-save file changed; preserving {snapshot.path}"
            )
        snapshot.path.unlink()
    except AutoSaveError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise AutoSaveError(
            f"could not remove auto-save {snapshot.path}: {detail}"
        ) from exc
