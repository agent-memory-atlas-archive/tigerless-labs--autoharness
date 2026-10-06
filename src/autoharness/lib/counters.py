"""Session counter (CAP trigger: +1 per Stop, reset at N) + per-layer request counter (MNG denominator: +1 per turn).

Single implementation, per-layer; O(1) read-modify-write, persisted immediately (each hook is a
separate short-lived process, nothing survives in memory). The numerator (call count) belongs to
sidecar (travels with the symbol across layers), not here. Session counts live in the repo-layer
state area (cap.md: session-scoped, written to this repo's .claude, not into live skills).

ponytail: read-modify-write is not atomic across processes; the lock for the global request counter
under concurrent multi-repo writes is deferred to mng.
"""
import errno
import re
import sys

from autoharness.lib import atomic, layer

if sys.platform == "win32":
    import msvcrt

    def _lock(f):
        # LK_LOCK gives up with EDEADLOCK after ~10s; retry to match flock's indefinite wait.
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError as e:
                if e.errno != errno.EDEADLOCK:
                    raise

    def _unlock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)

_SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_int(p):
    try:
        return int(p.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _bump(p, delta=1):
    """Read-modify-write under an exclusive lock so concurrent hook processes
    cannot both read the same value and lose an increment."""
    lock_path = p.with_suffix(p.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_fd:
        _lock(lock_fd)
        try:
            value = _read_int(p) + delta
            atomic.write_text(p, str(value))
        finally:
            _unlock(lock_fd)
    return value


def request_count(lyr, root=None):
    return _read_int(layer.state_dir(lyr, root) / "requests")


def bump_request(lyr, root=None):
    return _bump(layer.state_dir(lyr, root) / "requests")


def _session_path(session_id, root=None):
    if not isinstance(session_id, str) or not _SAFE_SESSION.match(session_id):
        raise ValueError(f"unsafe session id: {session_id!r}")
    return layer.state_dir(layer.PROJECT, root) / f"session-{session_id}"


def session_count(session_id, root=None):
    return _read_int(_session_path(session_id, root))


def bump_session(session_id, root=None):
    return _bump(_session_path(session_id, root))


def reset_session(session_id, root=None):
    atomic.write_text(_session_path(session_id, root), "0")


def clear_session(session_id, root=None):
    p = _session_path(session_id, root)
    if p.exists():
        p.unlink()


def _offset_path(session_id, root=None):
    if not isinstance(session_id, str) or not _SAFE_SESSION.match(session_id):
        raise ValueError(f"unsafe session id: {session_id!r}")
    return layer.state_dir(layer.PROJECT, root) / f"offset-{session_id}"


def session_offset(session_id, root=None):
    return _read_int(_offset_path(session_id, root))


def write_session_offset(session_id, offset, root=None):
    atomic.write_text(_offset_path(session_id, root), str(int(offset)))
