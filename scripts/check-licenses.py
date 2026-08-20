#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pip-licenses>=5.0.0"]
# ///
# SPDX-License-Identifier: Apache-2.0
"""Fail the build on a dependency license we have not accepted.

The gate this replaces reported and never failed. `.github/workflows/ci.yml`'s
`licenses` job ran `pip-licenses`, printed a table, uploaded it as an artifact,
and exited 0 whatever the table said, so a GPL dependency entering the tree
would have produced a green tick and a report nobody opens. Reporting is not
enforcement.

Two measured reasons the naive fail-closed version could not have been written:

  1. **`pip-licenses==4.4.0` cannot see most licenses here.** Pinned at that
     version it reports 28 of 65 packages as UNKNOWN, because it reads only the
     `License:` core-metadata field and a modern wheel declares
     `License-Expression` (PEP 639) or classifiers instead. Fail-closed on that
     output rejects `attrs`, `PyJWT`, and `Pygments`. This script takes
     pip-licenses >= 5 and `--from=mixed`, which consults the expression, the
     field, and the classifiers in turn: 1 UNKNOWN instead of 28.

  2. **The same license arrives under several spellings.** `MIT` and
     `MIT License`; `Apache-2.0`, `Apache Software License`, and
     `Apache-2.0 OR BSD-3-Clause`; `Mozilla Public License 2.0 (MPL 2.0)` and
     `MPL-2.0`. A literal allowlist passes one spelling and rejects its twin, so
     the string is normalized to an SPDX identifier before it is judged.

What is allowed is a closed set of permissive families, plus weak copyleft that
imposes nothing on a service that merely imports it. What is denied is strong and
network copyleft, by family, so a new GPL variant is denied by default rather than
by being listed. Anything the normalizer does not recognize is a failure and not a
pass: an unrecognized license is a license nobody has read.

`EXEMPT` is the escape hatch, and it takes a package name, the license we assert,
and the evidence for it. `atheris` is the one entry: its wheels carry no license
metadata of any kind (no expression, no field, no classifier), and
`gh api repos/google/atheris/license` answers Apache-2.0 (verified 2026-08-16).
An exemption without evidence is a finding someone silenced.

Usage:
    scripts/check-licenses.py             # the gate
    scripts/check-licenses.py --list      # every package and its normalized license
    scripts/check-licenses.py --deny MIT  # prove the gate fires: treat MIT as denied,
                                          # which must fail. A gate never observed
                                          # failing is a gate nobody has tested.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

#: Permissive and weak-copyleft licenses accepted for a dependency of a deployed
#: service. MPL-2.0 and MIT-0 sit here deliberately: MPL's reciprocity is per-file
#: and triggers on modifying MPL files, which importing a library does not do.
ALLOWED = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
}

#: Denied by family rather than by exact identifier, so `GPL-4.0` is denied the day
#: it exists. Strong copyleft would extend its terms to code that links this
#: service; network copyleft (AGPL, SSPL) would reach the deployed service itself.
DENIED_FAMILIES = ("GPL", "AGPL", "LGPL", "SSPL", "CDDL", "EPL", "CPL", "OSL", "EUPL")

#: A license string is not an identifier. These map every spelling this repo has
#: actually produced onto SPDX. Keys are matched after casefolding and collapsing
#: whitespace; an unmatched string fails the gate rather than passing it.
NORMALIZE = {
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "bsd license": "BSD-3-Clause",
    "bsd": "BSD-3-Clause",
    "mit license": "MIT",
    "isc license (iscl)": "ISC",
    "isc license": "ISC",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "python software foundation license": "PSF-2.0",
    "zope public license": "ZPL-2.1",
}

#: package -> (license we assert, why we may assert it). Requires evidence, not a
#: preference. See the module docstring.
EXEMPT = {
    "atheris": (
        "Apache-2.0",
        "wheels carry no license metadata at all; google/atheris LICENSE is"
        " Apache-2.0 via `gh api repos/google/atheris/license`, verified 2026-08-16",
    ),
}

#: `A OR B` (PEP 639) lets us take either side, so the package is compliant when any
#: disjunct is allowed. `A AND B` binds us to both. `;` is pip-licenses' own
#: separator for multiple classifiers and means the same as OR for our purposes.
_OR = re.compile(r"\s+OR\s+|;", re.IGNORECASE)
_AND = re.compile(r"\s+AND\s+", re.IGNORECASE)


def normalize(raw: str) -> str:
    """One license string to one SPDX identifier, or the input if unrecognized."""
    text = " ".join(raw.strip().split())
    if mapped := NORMALIZE.get(text.casefold()):
        return mapped
    # Already-SPDX identifiers differ from our keys only in case.
    for known in ALLOWED:
        if text.casefold() == known.casefold():
            return known
    return text


def verdict(raw: str, allowed: frozenset[str]) -> tuple[bool, str]:
    """Is this license string acceptable, and which identifier decided it?

    `allowed` is passed rather than read off the module so that `--deny` narrows it
    for every caller in one place; a gate whose policy depends on import-time global
    state is a gate whose test does not test the same thing the CI run does.
    """
    for conjunct in _AND.split(raw):
        alternatives = [normalize(part) for part in _OR.split(conjunct) if part.strip()]
        if not alternatives:
            return False, raw
        # Any allowed alternative satisfies the disjunction. A denied family
        # anywhere is decisive: `MIT OR GPL-3.0` still offers us MIT, but we
        # report the denial so the choice is a deliberate one.
        if any(alt in allowed for alt in alternatives):
            continue
        for alt in alternatives:
            if any(family in alt.upper() for family in DENIED_FAMILIES):
                return False, alt
        return False, alternatives[0]
    return True, normalize(_OR.split(_AND.split(raw)[0])[0])


def inventory() -> list[dict[str, str]]:
    """Every installed distribution and its license, resolved from all three sources.

    `--from=mixed` is what makes this usable: it prefers the PEP 639 expression,
    falls back to the legacy `License:` field, then to the classifiers. See reason
    1 in the module docstring for what happens without it.
    """
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("licenses: uv not found on PATH")
    out = subprocess.run(  # noqa: S603 - argv is entirely this file's own literals
        [uv, "run", "--frozen", "--with", "pip-licenses>=5.0.0", "pip-licenses", "--from=mixed", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print every package and its normalized license")
    parser.add_argument(
        "--deny",
        metavar="SPDX",
        action="append",
        default=[],
        help="treat this normally-allowed license as denied; the way to prove the"
        " gate can fail (`--deny MIT` must exit 1)",
    )
    args = parser.parse_args()

    allowed = frozenset(ALLOWED - set(args.deny))
    if args.deny:
        print(
            f"licenses: --deny in effect, {', '.join(args.deny)} treated as denied",
            file=sys.stderr,
        )

    packages = inventory()
    if not packages:
        print(
            "licenses: pip-licenses returned no packages. An empty inventory agrees"
            " with any policy, so this is a failure rather than a clean run.",
            file=sys.stderr,
        )
        return 1

    rows: list[tuple[str, str, str]] = []
    failures: list[str] = []
    exempted: list[str] = []

    for package in packages:
        name = package["Name"]
        raw = package["License"]
        if name in EXEMPT:
            asserted, why = EXEMPT[name]
            ok, decided = verdict(asserted, allowed)
            if not ok:
                failures.append(f"{name}: EXEMPT asserts {asserted}, which is itself not allowed")
                continue
            exempted.append(f"{name} ({asserted}: {why})")
            rows.append((name, decided, "exempt"))
            continue
        ok, decided = verdict(raw, allowed)
        rows.append((name, decided, "ok" if ok else "DENIED"))
        if not ok:
            failures.append(f"{name} {package['Version']}: {raw!r} normalizes to {decided!r}")

    if args.list:
        for name, decided, status in sorted(rows):
            print(f"{status:8} {decided:20} {name}")

    if failures:
        print(f"licenses: {len(failures)} package(s) carry a license we have not accepted:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\n  Accept it by adding the SPDX id to ALLOWED in this script (with a"
            " reason), or record a per-package entry in EXEMPT with the evidence for"
            " the license you assert. An unrecognized license fails here by design:"
            " it is a license nobody has read yet.",
            file=sys.stderr,
        )
        return 1

    print(f"licenses: all {len(packages)} Python dependencies carry an accepted license")
    for note in exempted:
        print(f"  exempt: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
