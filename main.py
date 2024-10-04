#!/usr/bin/env python3

import argparse
import configparser
import hashlib
import json
import logging
import os.path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from typing import *


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

    def __init__(self, cflags: List[str] = None, ldflags: List[str] = None):
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
    # TODO: This should become a tree; the .local need to become other Source objects

    re_system = re.compile(r"#include\s+<(.*)>")
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
    def parse(filename: str, prefix: str = "", target: str = None):
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
    lines.append(f"%.o: %.cpp\n\t$(CC) $(CFLAGS) -c -o $@ $^")
    lines.append(f"%.o: %.cc\n\t$(CC) $(CFLAGS) -c -o $@ $^")
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


def hash_source(source: Source, config, file_hashes: Dict[str, bytes]):
    m = hashlib.sha256()
    m.update(file_hashes[source.filename])
    m.update(config.hash())
    for fn in sorted(source.local):
        m.update(file_hashes[fn])

    return m.digest()


def make(db, build_dir, source, config, file_hashes):
    key = hash_source(source, config, file_hashes)
    obj = source.target()
    if obj is None:
        return

    filename = os.path.join(build_dir, obj)
    dirname = os.path.dirname(filename)
    os.makedirs(dirname, exist_ok=True)

    row = db.execute("SELECT id, value FROM builds WHERE key = ?", (key,)).fetchone()
    if row is not None:
        logging.info(f"{filename} in cache")
        _id = row[0]
        val = row[1]
        with open(filename, "wb") as fh:
            fh.write(val)
        db.execute(
            "UPDATE builds SET last_used = ? WHERE id = ?",
            (int(time.time()), _id),
        )
    else:
        logging.info(f"building {filename}")
        args = ["g++", "-c"] + config.cflags + ["-o", filename, source.filename]
        subprocess.run(args, stdout=subprocess.PIPE, encoding="utf-8", check=True)
        with open(filename, "rb") as fh:
            val = fh.read()
        db.execute("INSERT INTO builds(key, value) VALUES (?, ?)", (key, val))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=".cache.db", help="Build cache")
    parser.add_argument("--config", help="Build system config file")
    parser.add_argument("--out", default="main", help="Name of build output")
    parser.add_argument("source", help="Source to build")
    parser.add_argument("binary", help="Binary name")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    db = sqlite3.connect(args.cache)
    with db as db:
        db.execute(schema)

    config = Config()
    if args.config:
        with open(args.config) as fh:
            config = Config(**json.load(fh))

    sources = find_local_sources(args.source)
    # print(generate_makefile(sources, out=args.out))

    build = tempfile.TemporaryDirectory()
    file_hashes = hash_files(sources)
    with db as db:
        objects = []
        for source in sources.values():
            make(db, build.name, source, config, file_hashes)
            objects.append(os.path.join(build.name, source.target()))

        args = ["g++"] + config.cflags + config.ldflags + ["-o", args.binary] + objects
        subprocess.run(args, stdout=subprocess.PIPE, encoding="utf-8", check=True)
