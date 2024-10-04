#!/usr/bin/env python3

import argparse
import logging
import os.path
import re


class Source:

    re_system = re.compile(r"#include\s+<(.*)>")
    re_local = re.compile(r"#include\s+\"(.*)\"")

    def __init__(self, filename: str, system, local):
        self.filename = filename
        self.system = set(system)
        self.local = set(local)

    def __repr__(self):
        return f"""
{self.filename}:
  system: {self.system}
  locals: {self.local}
        """.strip()

    def __eq__(self, other):
        return self.filename == other.filename

    def __hash__(self):
        return hash(self.filename)

    @staticmethod
    def parse(filename: str, prefix: str = ""):
        system = set()
        local = set()
        with open(filename) as fh:
            for line in fh:
                if m := Source.re_system.search(line):
                    system.add(m.group(1))
                elif m := Source.re_local.search(line):
                    local.add(os.path.join(prefix, m.group(1)))

        return Source(filename, system, local)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", help="Source for to build")
    args = parser.parse_args()

    main = Source.parse(args.executable)

    sources = {args.executable: main}
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

    objects = {}
    for src in sources:
        root, _ = os.path.splitext(src)
        obj = f"{root}.o"
        objects[src] = obj

    objs = " ".join(objects.values())
    print(f"objects := {objs}\n")
    print(f"%.o: %.cpp\n\t$(CC) $(CFLAGS) -c -o $@ $^\n")
    print(f"%.o: %.cc\n\t$(CC) $(CFLAGS) -c -o $@ $^\n")
    print(f"main: $(objects)\n\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^")
