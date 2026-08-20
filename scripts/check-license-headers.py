#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# SPDX-License-Identifier: Apache-2.0
"""Every source file carries the SPDX line; this proves it and can add it.

The repo is Apache-2.0 in `LICENSE` and said so nowhere else: zero of 72 Python
files, 6 shell scripts, and 87 Terraform files carried a license declaration
before this gate. A file copied out of the tree therefore arrived unlicensed, and
an SBOM consumer scanning file-level provenance found nothing to read.

One line rather than a header block. `SPDX-License-Identifier: Apache-2.0` is
machine-readable, survives reformatting, and does not push the actual code below a
screen of boilerplate. The check is a closed set over tracked `.py`, `.sh`, and
`.tf` files, so a new file cannot land unlicensed.

Insertion respects what has to stay first. A shebang must remain line 1 or the
script stops being executable, and a Python module docstring must remain the first
statement or it stops being `__doc__`, so for `.py` the line goes after the shebang
and after any PEP 723 metadata block but before the docstring, which is a comment
position in all three cases. Terraform takes `#` comments the same way.

Usage:
    scripts/check-license-headers.py         # the gate
    scripts/check-license-headers.py --fix   # insert the line where it is missing
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SPDX = "SPDX-License-Identifier: Apache-2.0"

#: Extensions carrying the line, and the comment syntax each one uses. Markdown,
#: JSON, and YAML are left out on purpose: JSON has no comment syntax at all, and a
#: license line in a rendered document or a config file is noise rather than
#: metadata. Terraform is in because `.tf` files get copied between repos most.
COMMENT = {".py": "#", ".sh": "#", ".tf": "#"}


def git() -> str:
    """An absolute path to git, so the gate names its own tool.

    `ruff`'s S607 is right that a bare `git` resolves through PATH; an absolute
    path is both the fix and a clearer failure when git is genuinely absent.
    """
    found = shutil.which("git")
    if not found:
        raise SystemExit("license headers: git not found on PATH")
    return found


def tracked_sources() -> list[Path]:
    out = subprocess.run(  # noqa: S603 - argv is this file's own literals plus COMMENT keys
        [git(), "ls-files", *(f"*{ext}" for ext in COMMENT)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(line) for line in out.stdout.splitlines() if line]


def has_spdx(path: Path) -> bool:
    # The line must appear near the top: a mention buried in a docstring later in
    # the file is documentation, not a license declaration.
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
    return SPDX in head


def insertion_point(lines: list[str], path: Path) -> int:
    """After a shebang and any PEP 723 block, before everything else.

    Both of those must keep their position to keep working, and everything after
    them (a docstring, an import, a Terraform block) is content this line may
    precede.
    """
    at = 0
    if lines and lines[0].startswith("#!"):
        at = 1
    if path.suffix == ".py" and at < len(lines) and lines[at].startswith("# /// script"):
        while at < len(lines) and lines[at] != "# ///":
            at += 1
        at += 1  # past the closing marker
    return at


def fix(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    at = insertion_point(lines, path)
    lines.insert(at, f"{COMMENT[path.suffix]} {SPDX}")
    # Preserve a missing trailing newline only where there was content to preserve
    # it from. An empty file (the seven `__init__.py` package markers here) has no
    # final newline to keep, and copying that absence onto the line we just added
    # writes a file whose last line is unterminated: ruff W292, in seven files,
    # caused by the gate meant to leave them alone. Measured.
    keep_bare = bool(text) and not text.endswith("\n")
    path.write_text("\n".join(lines) + ("" if keep_bare else "\n"), encoding="utf-8")


def main() -> int:
    apply = "--fix" in sys.argv[1:]
    sources = tracked_sources()
    if not sources:
        print(
            "license headers: git ls-files matched no source files. An empty set"
            " carries the line vacuously, so this is a failure rather than a pass.",
            file=sys.stderr,
        )
        return 1

    missing = [p for p in sources if not has_spdx(p)]
    if not missing:
        print(f"license headers: all {len(sources)} tracked source files carry the SPDX line")
        return 0
    if apply:
        for path in missing:
            fix(path)
        print(f"license headers: added the SPDX line to {len(missing)} files")
        return 0
    print(f"license headers: {len(missing)} of {len(sources)} files are missing '{SPDX}':", file=sys.stderr)
    for path in missing:
        print(f"  {path}", file=sys.stderr)
    print("  run scripts/check-license-headers.py --fix", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
