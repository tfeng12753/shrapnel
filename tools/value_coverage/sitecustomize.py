"""Record which baseline table values are actually fetched, including child interpreters."""

import os

if "BR_VALUE_LOG" in os.environ:
    import sys, json, importlib.machinery, builtins

    events = set()

    def write(tag, data):
        payload = (
            json.dumps(
                [os.environ.get("BR_ITEM", ""), tag, data], separators=(",", ":")
            )
            + "\n"
        ).encode()
        fd = os.open(
            os.environ["BR_VALUE_LOG"], os.O_APPEND | os.O_WRONLY | os.O_CREAT, 0o600
        )
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    def observe(table, key):
        # Match the running table by content, including a different dialect with an identical table.
        sig = tuple(sorted(table.items()))
        key_id = (os.environ.get("BR_ITEM", ""), sig, key)
        if key_id not in events:
            events.add(key_id)
            write("read", [sig, key])
        return table.get(key)

    builtins._baseline_option_read = observe
    orig = importlib.machinery.SourceFileLoader.get_code

    def code(loader, fullname):
        if fullname != "sqlglot.tokenizer_core":
            return orig(loader, fullname)
        text = loader.get_source(fullname)
        pattern = 'self.numeric_escapes.get("0" if is_octal else peek)'
        if text.count(pattern) != 2:
            raise RuntimeError("lookup layout changed")
        return compile(
            text.replace(
                pattern,
                '_baseline_option_read(self.numeric_escapes, "0" if is_octal else peek)',
            ),
            loader.path,
            "exec",
            dont_inherit=True,
        )

    importlib.machinery.SourceFileLoader.get_code = code
