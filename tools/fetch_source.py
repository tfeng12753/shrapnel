"""Fetch only the rules-permitted sqlglot repository at its exact revision."""
import io
from pathlib import Path
import tarfile
import urllib.request

REVISION = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
url = f"https://codeload.github.com/tobymao/sqlglot/tar.gz/{REVISION}"
root = Path("/opt/sqlglot")
root.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(url, timeout=90) as response:
    archive = response.read()
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
    for member in source:
        relative = Path(member.name).parts[1:]
        if not relative:
            continue
        member.name = str(Path(*relative))
        source.extract(member, root, filter="data")
(root / ".pinned-revision").write_text(REVISION + "\n")
