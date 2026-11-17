"""Durable local HTTP admission; zero claims is not remote inference cancellation."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import stat
from time import monotonic, sleep
from uuid import uuid4

from .lifecycle import SLOTS


MAX_CLAIMS = 32
SCHEMA = {
    "metadata": "CREATE TABLE metadata (version INTEGER NOT NULL)",
    "slots": "CREATE TABLE slots (language TEXT NOT NULL, task TEXT NOT NULL, closed INTEGER NOT NULL CHECK(closed IN (0,1)), PRIMARY KEY(language, task))",
    "claims": "CREATE TABLE claims (token TEXT PRIMARY KEY NOT NULL, language TEXT NOT NULL, task TEXT NOT NULL)",
}


class TrafficUnavailable(RuntimeError):
    pass


class TrafficBusy(TrafficUnavailable):
    pass


class _Claim:
    def __init__(self, gate, slot):
        self._gate, self._slot = gate, slot
        self.uncertain = False

    def mark_uncertain(self):
        """Retain the claim when remote inference completion cannot be proved."""
        self.uncertain = True
        with self._gate._transaction() as db:
            db.execute("UPDATE slots SET closed=1 WHERE language=? AND task=?", self._slot)


def _path(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Use a private absolute traffic database path.")
    return path.resolve()


def initialize(path):
    """Explicit offline initialization only; never replace an existing store."""
    _initialize(path, SCHEMA, lambda db: db.execute("INSERT INTO metadata VALUES (1)"))


def _initialize(path, schema, seed):
    """Shared exclusive creation for supported traffic-store versions."""
    path = _path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(descriptor)
    # Failed initialization deliberately leaves a closed/unusable store. Never
    # delete it automatically: another process may already be inspecting it.
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in schema.values():
                db.execute(statement)
            seed(db)
            db.executemany("INSERT INTO slots VALUES (?,?,0)", sorted(SLOTS))
            db.commit()
        except Exception:
            db.rollback()
            raise
    finally:
        db.close()


class TrafficGate:
    _schema = SCHEMA

    def __init__(self, path, *, timeout=5):
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError("Drain timeout must be positive and at most five seconds.")
        self._path, self._timeout = _path(path), timeout
        try:
            info = self._path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise OSError
            self._identity = (info.st_dev, info.st_ino)
            with self._transaction():
                pass
        except OSError:
            raise TrafficUnavailable("Traffic admission is unavailable.") from None

    def _check_file(self):
        info = self._path.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != self._identity:
            raise OSError

    def _validate(self, db):
        schema = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall())
        if schema != self._schema or db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
            raise ValueError
        self._validate_metadata(db)
        slots = db.execute("SELECT language,task,closed FROM slots LIMIT 5").fetchall()
        if len(slots) != 4 or {(language, task) for language, task, _ in slots} != SLOTS:
            raise ValueError

        if any(type(closed) is not int or closed not in (0, 1) for _, _, closed in slots):
            raise ValueError
        claims = db.execute("SELECT token,language,task FROM claims LIMIT 33").fetchall()
        if len(claims) > MAX_CLAIMS or any(
            not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token) or (language, task) not in SLOTS
            for token, language, task in claims
        ):
            raise ValueError

    def _validate_metadata(self, db):
        if db.execute("SELECT version FROM metadata LIMIT 2").fetchall() != [(1,)]:
            raise ValueError

    def _new_claim(self, db, slot):
        return _Claim(self, slot)

    @contextmanager
    def _transaction(self, deadline=None):
        db = None
        try:
            self._check_file()
            remaining = 0.1 if deadline is None else min(0.1, deadline - monotonic())
            if remaining <= 0:
                raise ValueError
            db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, timeout=remaining, isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise ValueError
            db.execute("BEGIN IMMEDIATE")
            self._check_file()
            self._validate(db)
            yield db
            db.commit()
        except (sqlite3.Error, OSError, ValueError, TypeError):
            if db is not None:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
            raise TrafficUnavailable("Traffic admission is unavailable.") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _slot(slot):
        if type(slot) is not tuple or len(slot) != 2 or any(type(value) is not str for value in slot) or slot not in SLOTS:
            raise ValueError("An English/Polish speech slot is required.")

    @contextmanager
    def claim(self, slot):
        self._slot(slot)
        token = uuid4().hex
        with self._transaction() as db:
            closed = db.execute("SELECT closed FROM slots WHERE language=? AND task=?", slot).fetchone()[0]
            if closed or db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] >= MAX_CLAIMS:
                raise TrafficBusy("Speech admission is paused or busy.")
            db.execute("INSERT INTO claims VALUES (?,?,?)", (token, *slot))
            claim = self._new_claim(db, slot)
        try:
            yield claim
        finally:
            if not claim.uncertain:
                with self._transaction() as db:
                    if db.execute("DELETE FROM claims WHERE token=? AND language=? AND task=?", (token, *slot)).rowcount != 1:
                        raise TrafficUnavailable("Traffic claim recovery is required.")

    def drain(self, slot):
        self._slot(slot)
        deadline = monotonic() + self._timeout
        try:
            with self._transaction(deadline) as db:
                db.execute("UPDATE slots SET closed=1 WHERE language=? AND task=?", slot)
            while True:
                with self._transaction(deadline) as db:
                    if not db.execute("SELECT 1 FROM claims WHERE language=? AND task=? LIMIT 1", slot).fetchone():
                        return True
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                sleep(min(0.025, remaining))
        except TrafficUnavailable:
            return False

    def resume(self, slot):
        self._slot(slot)
        try:
            with self._transaction() as db:
                if db.execute("SELECT 1 FROM claims WHERE language=? AND task=? LIMIT 1", slot).fetchone():
                    return False
                db.execute("UPDATE slots SET closed=0 WHERE language=? AND task=?", slot)
            return True
        except TrafficUnavailable:
            return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize a new private speech traffic store while services are offline.")
    parser.add_argument("action", choices=("init",))
    parser.add_argument("path")
    initialize(parser.parse_args().path)
