#!/usr/bin/env python3

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import os.path
import re
import sys
import sqlite3
import subprocess
import tempfile
import time
from typing import Dict, List, Optional, Tuple


schema = """
CREATE TABLE IF NOT EXISTS builds (
  id INTEGER PRIMARY KEY
  , key BLOB NOT NULL
  , value BLOB NOT NULL
  , last_used INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
  , UNIQUE(key)
);
"""


class Config:

    def __init__(self, cflags: Optional[List[str]] = None, ldflags: Optional[List[str]] = None):
        self.cflags = cflags or []
        self.ldflags = ldflags or []
        self._hash = None

    def hash(self):
        if self._hash is None:
            m = hashlib.sha256()
            for val in sorted(set(self.cflags)):
                m.update(val.encode("utf-8"))
            for val in sorted(set(self.ldflags)):
                m.update(val.encode("utf-8"))
            self._hash = m.digest()

        return self._hash


class Source:
    re_local = re.compile(r"#include\s+\"(.*)\"")

    def __init__(self, filename: str, local, target=None):
        self.filename = filename
        self.local = set(local)
        self._target = target

    def __repr__(self):
        return f"""
{self.filename}:
  locals: {self.local}
        """.strip()

    def target(self) -> Optional[str]:
        if self._target is not None:
            return self._target

        if self.filename.endswith(".cc") or self.filename.endswith(".cpp"):
            root, _ = os.path.splitext(self.filename)
            return f"{root}.o"

        return None

    @staticmethod
    def parse(filename: str, prefix: str = "", target: Optional[str] = None):
        local = set()
        with open(filename) as fh:
            for line in fh:
                m = Source.re_local.search(line)
                if m is not None:
                    local.add(os.path.join(prefix, m.group(1)))

        return Source(filename, local, target=target)


def find_local_sources(filename: str) -> Dict[str, Source]:
    main = Source.parse(filename)
    sources = {filename: main}
    local_sources = set(main.local)
    while local_sources:
        header = local_sources.pop()
        dirname = os.path.dirname(header)
        cpp = re.sub(r"\.h(pp)?$", ".cpp", header)
        cc = re.sub(r"\.h(pp)?$", ".cc", header)
        if cpp in sources or cc in sources:
            continue

        if os.path.exists(cpp):
            filename = cpp
        elif os.path.exists(cc):
            filename = cc
        else:
            continue

        hdr = Source.parse(header, prefix=dirname)
        src = Source.parse(filename, prefix=dirname)
        local_sources.update(hdr.local)
        local_sources.update(src.local)
        sources[filename] = src

    return sources


def generate_makefile(sources: Dict[str, Source], out):
    objects = {}
    for src in sources:
        root, _ = os.path.splitext(src)
        obj = f"{root}.o"
        objects[src] = obj

    lines = []

    objs = " ".join(objects.values())
    lines.append(f"objects := {objs}")
    lines.append("%.o: %.cpp\n\t$(CC) $(CFLAGS) -c -o $@ $^")
    lines.append("%.o: %.cc\n\t$(CC) $(CFLAGS) -c -o $@ $^")
    lines.append(f"{out}: $(objects)\n\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^")

    return "\n\n".join(lines)


def hash_files(sources: Dict[str, Source]) -> Dict[str, bytes]:
    digests = {}

    for filename, source in sources.items():
        if filename not in digests:
            with open(filename, "rb") as fh:
                digests[filename] = hashlib.sha256(fh.read()).digest()

        for fn in source.local:
            if fn not in digests:
                with open(fn, "rb") as fh:
                    digests[fn] = hashlib.sha256(fh.read()).digest()

    return digests


class Cache:

    def __init__(self, db, file_hashes: Dict[str, bytes]):
        self.db = db
        self.file_hashes = file_hashes

    def digest(self, source: Source, config: Config) -> bytes:
        m = hashlib.sha256()
        m.update(self.file_hashes[source.filename])
        m.update(config.hash())
        for fn in sorted(source.local):
            m.update(self.file_hashes[fn])

        return m.digest()

    def lookup(self, key) -> Optional[Tuple[int, bytes]]:
        row = self.db.execute("SELECT id, value FROM builds WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None

        _id, val = (row[0], row[1])
        now = int(time.time())
        with self.db as db:
            db.execute(
                "UPDATE builds SET last_used = ? WHERE id = ?",
                (now, _id),
            )

        return (_id, val)

    def insert(self, key: bytes, val: bytes):
        with self.db as db:
            db.execute("INSERT INTO builds(key, value) VALUES (?, ?)", (key, val))


def make(task):
    outfile, key, args = task
    logging.info(f"building {outfile}")
    proc = subprocess.run(args, stdout=subprocess.PIPE, encoding="utf-8", check=True)
    with open(outfile, "rb") as fh:
        val = fh.read()
    if proc.stderr:
        logging.warning(proc.stderr)

    return (outfile, key, val)


def get_filename(filename: Optional[str]) -> str:
    if filename is not None:
        return filename

    xdg_cache = os.getenv("XDG_CACHE_HOME", os.path.join(os.environ["HOME"], ".cache"))
    os.makedirs(xdg_cache, exist_ok=True)
    return os.path.join(xdg_cache, "cbs.db")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", help="Build cache")
    parser.add_argument("--config", help="Build system config file")
    parser.add_argument("--jobs", type=int, default=6, help="Number of CPUs to use")
    parser.add_argument("--makefile", help="Generate Makefile")
    parser.add_argument("source", help="Source to build")
    parser.add_argument("binary", help="Binary name")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    db = sqlite3.connect(get_filename(args.cache))
    with db as db:
        db.execute(schema)

    config = Config()
    if args.config:
        with open(args.config) as fh:
            config = Config(**json.load(fh))

    sources = find_local_sources(args.source)
    if args.makefile:
        print(generate_makefile(sources, out=args.binary))
        sys.exit(0)

    cache = Cache(db, hash_files(sources))

    build = tempfile.TemporaryDirectory()
    objects = []
    tasks = []
    for source in sources.values():
        obj = source.target()
        if obj is None:
            continue

        key = cache.digest(source, config)
        outfile = os.path.join(build.name, obj)
        dirname = os.path.dirname(outfile)
        os.makedirs(dirname, exist_ok=True)

        res = cache.lookup(key)
        if res is not None:
            logging.info(f"{outfile} in cache")
            _id, val = res
            with open(outfile, "wb") as f:
                f.write(val)
            objects.append(outfile)
        else:
            cmd = ["g++", "-c"] + config.cflags + ["-o", outfile, source.filename]
            tasks.append((outfile, key, cmd))

    with multiprocessing.Pool(processes=args.jobs) as pool:
        results = pool.map(make, tasks)

    for outfile, key, val in results:
        cache.insert(key, val)
        objects.append(outfile)

    cmd = ["g++"] + config.cflags + config.ldflags + ["-o", args.binary] + objects
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, encoding="utf-8", check=True)
    if proc.stderr:
        logging.warning(proc.stderr)
