"""Observe ordinary Python reads from tuples yielded by a fixture loader.

This diagnostic identifies candidates for testing. Native tuple access, aliases
imported before installation, reflective locals access and child processes can
escape observation. Its output alone cannot justify removing a scheduled item.
Run in a dedicated interpreter on the unchanged pinned checkout.
"""
import argparse
import dis
from functools import lru_cache, wraps
import importlib
import json
import os
from pathlib import Path
import sys


@lru_cache(maxsize=512)
def unpack_sites(code):
    """Map direct fixed-length unpacks to fields assigned to live local names."""
    if {"locals", "vars", "eval", "exec", "_getframe", "currentframe", "f_locals"}.intersection(code.co_names):
        return {}
    instructions = list(dis.get_instructions(code))
    loaded = set()
    for ins in instructions:
        # STORE_FAST_LOAD_FAST reads only its second operand. The compiler's
        # LOAD_FAST_AND_CLEAR saves an outer binding around a comprehension.
        if ins.opname == "STORE_FAST_LOAD_FAST":
            loaded.add(ins.argval[1])
        elif (ins.opname.startswith("LOAD_FAST") and ins.opname != "LOAD_FAST_AND_CLEAR"
              or "DEREF" in ins.opname):
            names = ins.argval if isinstance(ins.argval, tuple) else (ins.argval,)
            loaded.update(names)
    sites = {}
    for i, ins in enumerate(instructions):
        if ins.opname != "UNPACK_SEQUENCE":
            continue
        targets = []
        for store in instructions[i + 1:]:
            if store.opname == "STORE_FAST":
                targets.append(store.argval)
            elif store.opname == "STORE_FAST_STORE_FAST":
                targets.extend(store.argval)
            elif store.opname == "STORE_FAST_LOAD_FAST":
                targets.append(store.argval[0])
                break
            else:
                break
            if len(targets) >= ins.arg:
                break
        if len(targets) == ins.arg:
            sites[ins.offset] = [field for field, name in enumerate(targets)
                                 if name != "_" or name in loaded]
    return sites


def observe(source, loader, items):
    import pytest

    observations = {}
    current = "<shared>"

    def entry_for(filename):
        return observations.setdefault(current, {}).setdefault(filename, {
            "fields": set(), "loads": 0, "unpacks": 0, "other": 0,
        })

    def record(filename, fields, width):
        entry = entry_for(filename)
        if fields is None:
            entry["other"] += 1
            fields = range(width)
        else:
            entry["unpacks"] += 1
        entry["fields"].update(fields)

    class Row(tuple):
        def __iter__(self):
            caller = sys._getframe(1)
            fields = unpack_sites(caller.f_code).get(caller.f_lasti)
            record(self.filename, fields, len(self))
            return super().__iter__()

        def __getitem__(self, index):
            record(self.filename, None, len(self))
            return super().__getitem__(index)

    class Plugin:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            nonlocal current
            current = item.nodeid
            try:
                yield
            finally:
                current = "<shared>"

    os.chdir(source)
    sys.path.insert(0, str(source))
    module_name, attr = loader.split(":")
    module = importlib.import_module(module_name)
    if not Path(module.__file__).resolve().is_relative_to(source):
        raise ValueError("fixture loader must belong to the checkout")
    original = getattr(module, attr)

    @wraps(original)
    def traced(filename, *args, **kwargs):
        entry_for(str(filename))["loads"] += 1
        for value in original(filename, *args, **kwargs):
            if type(value) is tuple:
                row = Row(value)
                row.filename = str(filename)
                yield row
            else:
                yield value

    setattr(module, attr, traced)
    try:
        status = pytest.main(["-q", "-p", "no:cacheprovider", "--timeout=180", *items], plugins=[Plugin()])
    finally:
        setattr(module, attr, original)
    if status:
        raise RuntimeError(f"instrumented suite exited with status {status}")
    for files in observations.values():
        for entry in files.values():
            entry["fields"] = sorted(entry["fields"])
    return observations


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--loader", required=True, help="Generator as module:function")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("items", nargs="+")
    args = parser.parse_args()
    output = args.output.resolve()
    output.write_text(json.dumps(observe(args.source.resolve(), args.loader, args.items), sort_keys=True))
