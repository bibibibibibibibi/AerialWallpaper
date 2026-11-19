# -*- coding: utf-8 -*-
"""Pure configuration, library-search and layout helpers for the preferences UI.

This module deliberately imports neither the application nor any Win32 APIs.
Configuration read failures are never treated as an empty configuration: callers
must report ConfigError rather than replacing an unreadable file with defaults.
"""

import errno
import json
import math
import ntpath
import numbers
import os
import re
import tempfile
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation


DEFAULT_BURST = 8
MIN_BURST = 3
MAX_BURST = 30
BURST_PRESETS = (5, 8, 15, 30)
BOARD_WIDTH = 1064
BOARD_HEIGHT = 720

_BURST_ERROR = "请输入 3 到 30 之间的整数秒数。"
_OUTER_MARGIN = 16.0
_CONFIG_LOCK_TIMEOUT = 2.0
_CONFIG_LOCK_POLL = 0.025


class ConfigError(Exception):
    """A configuration could not be read or safely written."""


def _reject_json_constant(value):
    # The standard decoder otherwise accepts NaN/Infinity, which are not JSON.
    raise ValueError("Non-standard JSON numeric constant: {}".format(value))


def read_config(path):
    """Read a UTF-8 JSON object; only a missing file yields a new empty dict.

    Malformed JSON, invalid UTF-8, a non-object root and all other I/O errors
    raise ConfigError. This function never creates, repairs or rewrites a file.
    """
    try:
        with open(path, "r", encoding="utf-8") as stream:
            cfg = json.load(stream, parse_constant=_reject_json_constant)
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        raise ConfigError(
            "Cannot read configuration {!r}: {}".format(path, exc)
        ) from exc
    if not isinstance(cfg, dict):
        raise ConfigError(
            "Configuration root must be a JSON object (dictionary): {!r}".format(path)
        )
    return cfg


def write_config(path, cfg):
    """Atomically write an entire dict without modifying or dropping its keys.

    The unique temporary file is created beside the destination, flushed,
    fsynced and closed before os.replace. A failed write leaves the previous
    destination untouched and removes the temporary file where permitted.
    The parent directory must already exist. All ordinary failures raise
    ConfigError; there is no non-atomic overwrite fallback.
    """
    if not isinstance(cfg, dict):
        raise ConfigError("Configuration must be a dictionary.")

    temporary_path = None
    try:
        raw_path = os.fsdecode(path)
        if not raw_path:
            raise ValueError("Configuration path must not be empty")
        destination = os.path.abspath(raw_path)
        directory = os.path.dirname(destination)
        prefix = ".{}.".format(os.path.basename(destination))
        payload = json.dumps(cfg, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=directory,
            prefix=prefix, suffix=".tmp", delete=False
        ) as stream:
            temporary_path = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except (OSError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError(
            "Cannot write configuration {!r}: {}".format(path, exc)
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                # Preserve the original error if permissions also deny cleanup.
                pass


@contextmanager
def _config_lock(lock_path):
    # Keep platform-specific standard-library imports lazy: importing these
    # preferences helpers must work without either platform's locking module.
    if os.name == "nt":
        import msvcrt

        def lock(stream):
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

        def unlock(stream):
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def lock(stream):
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def unlock(stream):
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    # Append mode never truncates a sidecar already used by another process.
    # Windows permits locking byte zero even before that byte exists.
    with open(lock_path, "a+b", buffering=0) as stream:
        deadline = time.monotonic() + _CONFIG_LOCK_TIMEOUT
        while True:
            try:
                lock(stream)
                break
            except OSError as exc:
                if exc.errno not in (
                    errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK,
                    errno.EDEADLK, errno.EINTR
                ):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise ConfigError(
                        "Timed out waiting for configuration lock {!r} "
                        "({:.2f} seconds)".format(lock_path, _CONFIG_LOCK_TIMEOUT)
                    ) from exc
                time.sleep(min(_CONFIG_LOCK_POLL, remaining))
        try:
            # Initialize only after acquiring the lock: simultaneous first-time
            # writers must not race a write against another handle's byte lock.
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"\0")
            yield
        finally:
            try:
                unlock(stream)
            except OSError:
                # Closing the handle releases its locks too. In particular,
                # an unlock error must not obscure the original write error.
                pass
    # Never unlink: a waiting process may already hold this same inode/handle.


def update_config(path, changes, missing_only=False):
    """Merge top-level keys into freshly read configuration under a shared lock.

    Return the merged dict after atomic write_config succeeds. ``changes`` must
    be a dict and is never modified. With missing_only=True, a change is applied
    only when the current value is missing or falsy, matching ``not cfg.get(key)``.

    Explicitly participating writers share the stable adjacent ``path + '.lock'``
    sidecar. Lock acquisition retries nonblockingly for at most two seconds,
    then raises ConfigError. The sidecar deliberately remains after success or
    failure; removing it could let another process lock a different file.
    read_config and write_config remain unlocked. This helper cannot coordinate
    legacy writers that bypass it, nor distinct symlink/hardlink path aliases.
    Corrupt/unreadable input aborts the update without overwriting its data.
    """
    if not isinstance(changes, dict):
        raise ConfigError("Configuration changes must be a dictionary.")
    try:
        raw_path = os.fsdecode(path)
        if not raw_path:
            raise ValueError("Configuration path must not be empty")
        destination = os.path.abspath(raw_path)
        with _config_lock(destination + ".lock"):
            cfg = read_config(destination)
            for key, value in changes.items():
                if not missing_only or not cfg.get(key):
                    cfg[key] = value
            write_config(destination, cfg)
            return cfg
    except ConfigError:
        raise
    except (OSError, TypeError, ValueError, ImportError) as exc:
        raise ConfigError(
            "Cannot update configuration {!r}: {}".format(path, exc)
        ) from exc


def burst_seconds(value):
    """Read legacy numeric values, truncating finite values then clamping 3..30.

    Numeric strings may contain fractions or exponents. Booleans, None,
    non-numbers and non-finite values use DEFAULT_BURST instead. Rational and
    Decimal handling also supports large finite values without float overflow.
    """
    if isinstance(value, bool) or value is None:
        return DEFAULT_BURST
    try:
        number = Decimal(value.strip()) if isinstance(value, str) else value
        if not isinstance(number, numbers.Number):
            return DEFAULT_BURST
        if isinstance(number, Decimal):
            if not number.is_finite():
                return DEFAULT_BURST
        elif not isinstance(number, numbers.Rational) and not math.isfinite(number):
            return DEFAULT_BURST
        # Clamping before int is equivalent here, and avoids constructing a
        # gigantic integer for a finite value such as Decimal("1e999999").
        if number < MIN_BURST:
            return MIN_BURST
        if number > MAX_BURST:
            return MAX_BURST
        return int(number)
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return DEFAULT_BURST


def validate_burst(text):
    """Validate UI text as ASCII decimal digits representing an integer 3..30.

    Surrounding whitespace and leading zeroes are accepted. Signs, decimal
    points, exponents and non-ASCII digits are not. Invalid input is never
    silently rounded or clamped; it raises ValueError with a Chinese message.
    """
    if not isinstance(text, str):
        raise ValueError(_BURST_ERROR)
    digits = text.strip()
    if re.fullmatch(r"[0-9]+", digits) is None:
        raise ValueError(_BURST_ERROR)
    digits = digits.lstrip("0") or "0"
    # Bound input length before int(), including Python's integer-string limit.
    if len(digits) > 2:
        raise ValueError(_BURST_ERROR)
    value = int(digits, 10)
    if not MIN_BURST <= value <= MAX_BURST:
        raise ValueError(_BURST_ERROR)
    return value


def same_path(a, b):
    """Compare nonempty lexical absolute paths using native case normalization.

    Windows comparisons are case-insensitive and normalize both separators.
    Files need not exist; symlinks are intentionally not resolved. Invalid or
    blank paths compare false. PathLike and filesystem-encoded bytes work too.
    """
    try:
        first, second = os.fsdecode(a), os.fsdecode(b)
        if not first.strip() or not second.strip():
            return False
        if "\x00" in first or "\x00" in second:
            return False
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )
    except (OSError, TypeError, ValueError):
        return False


def _basename(path):
    # ntpath handles Windows and forward-slash paths even on non-Windows hosts.
    return ntpath.basename(os.fsdecode(path))


def friendly_name(path):
    """Return the basename without its last extension, replacing '_' with ' '."""
    return ntpath.splitext(_basename(path))[0].replace("_", " ")


def filter_library(items, query):
    """Return original indexes whose name/basename contain every query token.

    Matching uses Unicode casefold, ignores directory and thumbnail names,
    and permits tokens to match across the two searchable fields. Empty or
    whitespace-only queries return every original index in its original order.
    The supplied items are never reordered or modified.
    """
    tokens = query.casefold().split()
    matches = []
    for index, (name, path, _thumbnail) in enumerate(items):
        if not tokens:
            matches.append(index)
            continue
        searchable = "{} {}".format(name, _basename(path)).casefold()
        if all(token in searchable for token in tokens):
            matches.append(index)
    return matches


def _finite_float(value, default):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def fit_board(work_width, work_height, dpi_scale=1.0):
    """Return the largest fitting scale, never exceeding a valid requested DPI.

    Work-area dimensions and margins are physical pixels; the board is
    BOARD_WIDTH x BOARD_HEIGHT logical units. Each edge reserves 16 pixels.
    Invalid/nonpositive dimensions, or dimensions leaving no room inside the
    margins (<=32), return 0.0. Invalid/nonpositive DPI falls back to 1.0.
    Numeric strings are accepted. There is deliberately no minimum scale that
    could make a small work area overflow.
    """
    width = _finite_float(work_width, 0.0)
    height = _finite_float(work_height, 0.0)
    dpi = _finite_float(dpi_scale, 1.0)
    if dpi <= 0.0:
        dpi = 1.0
    available_width = max(0.0, width - 2.0 * _OUTER_MARGIN)
    available_height = max(0.0, height - 2.0 * _OUTER_MARGIN)
    return float(min(dpi, available_width / BOARD_WIDTH, available_height / BOARD_HEIGHT))
