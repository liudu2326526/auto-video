"""Private cooperative store. Protects against mistakes, not hostile same-UID code."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile


class Error(Exception):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def no_links(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        # macOS /tmp and /var are system-owned aliases, not task-controlled links.
        if item in (Path("/tmp"), Path("/var"), Path("/var/folders")):
            continue
        if item.is_symlink():
            raise Error("symlink refused: " + item.name)
    return path


def read_regular(path, maximum=4 * 1024 * 1024, private=False):
    path = no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise Error("only single-link regular files are allowed")
        if private and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise Error("runtime file must be owned by this user and private")
        if st.st_size > maximum:
            raise Error("file size limit exceeded")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise Error("file grew past size limit")
        return data
    finally:
        os.close(fd)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_dir(path):
    path = no_links(path)
    path.mkdir(mode=0o700)
    fsync_dir(path.parent)
    return path


def write_new(path, data, mode=0o600):
    """Publish complete bytes without ever replacing a pre-existing destination."""
    path = no_links(path)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)  # EEXIST is deliberate.
        os.unlink(temporary)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def root_path(raw):
    if not Path(raw).is_absolute():
        raise Error("root must be absolute")
    path = no_links(raw)
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise Error("runtime root must be outside every Git working tree")
    if not path.exists():
        private_dir(path)
    st = path.stat()
    if not path.is_dir() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Error("existing root must be an owned private directory (0700)")
    # Refuse altered runtime permissions and links, including SQLite sidecars.
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(parent) / name
            st = item.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise Error("unsafe runtime entry: " + name)
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise Error("unsupported runtime entry")
            if stat.S_ISREG(st.st_mode) and st.st_nlink != 1:
                raise Error("hard-linked runtime file refused")
    return path


@contextmanager
def store(raw):
    root = root_path(raw)
    old_mask = os.umask(0o077)
    lock = os.open(root / "execution.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    db = None
    try:
        st = os.fstat(lock)
        if st.st_mode & 0o077 or st.st_uid != os.getuid() or st.st_nlink != 1:
            raise Error("unsafe lock file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Error("executor busy: an operation or inherited worker lease is active") from None
        db = sqlite3.connect(root / "state.sqlite3")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise Error("unsupported store schema_version")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, job TEXT NOT NULL, opkey TEXT NOT NULL, run TEXT NOT NULL, step TEXT NOT NULL, state TEXT NOT NULL)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS unresolved ON operations(job,opkey) WHERE state IN ('running','waiting_observation','unknown')")
        db.execute("PRAGMA user_version=1")
        db.commit()
        yield root, db, lock
    finally:
        if db is not None:
            db.close()
        # Do not LOCK_UN: an owned child may still hold the same open description.
        os.close(lock)
        os.umask(old_mask)
