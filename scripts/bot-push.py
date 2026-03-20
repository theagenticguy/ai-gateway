# /// script
# requires-python = ">=3.12"
# dependencies = ["PyJWT", "cryptography", "requests"]
# ///
"""Push local changes to GitHub as verified bot commits.

Uses the GitHub Git Data API so commits are automatically signed by
GitHub, satisfying signed-commit rulesets without bypass.

Usage:
    uv run scripts/bot-push.py -b feat/my-feature -m "feat: add thing"
    uv run scripts/bot-push.py -b feat/my-feature -m "feat: add thing" --pr
    uv run scripts/bot-push.py -b feat/my-feature -m "feat: add thing" --pr --pr-body "Fixes #42"
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jwt
import requests

APP_ID = "3142436"
PEM_PATH = Path.home() / ".bonk" / "github" / "bonk-ai.pem"
API = "https://api.github.com"
GIT = shutil.which("git") or "/usr/bin/git"
TIMEOUT = 30


def get_repo() -> str:
    """Derive owner/repo from the git remote."""
    result = subprocess.run(  # noqa: S603
        [GIT, "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    )
    url = result.stdout.strip().rstrip(".git")
    # Handle both HTTPS and SSH URLs
    if url.startswith("https://"):
        return "/".join(url.split("/")[-2:])
    return url.split(":")[-1]


def get_token() -> str:
    """Generate a short-lived GitHub App installation token."""
    pem = PEM_PATH.read_text()
    now = int(time.time())
    tok = jwt.encode({"iat": now - 60, "exp": now + 600, "iss": APP_ID}, pem, algorithm="RS256")
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}

    r = requests.get(f"{API}/app/installations", headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    install_id = r.json()[0]["id"]

    r2 = requests.post(f"{API}/app/installations/{install_id}/access_tokens", headers=headers, timeout=TIMEOUT)
    r2.raise_for_status()
    return r2.json()["token"]


def gh(method: str, repo: str, endpoint: str, token: str, **kwargs) -> dict | None:
    """Make a GitHub API request."""
    url = f"{API}/repos/{repo}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    kwargs.setdefault("timeout", TIMEOUT)
    r = getattr(requests, method)(url, headers=headers, **kwargs)
    r.raise_for_status()
    return r.json() if r.content else None


def changed_files(base: str) -> list[tuple[str, str]]:
    """Return [(status, path), ...] for files changed vs base."""
    result = subprocess.run(  # noqa: S603
        [GIT, "diff", "--name-status", base, "HEAD"],
        capture_output=True, text=True, check=True,
    )
    files = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            files.append(("D", parts[1]))
            files.append(("A", parts[2]))
        else:
            files.append((status, parts[-1]))
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="Push local changes as verified bot commits")
    parser.add_argument("-b", "--branch", required=True, help="Remote branch name")
    parser.add_argument("-m", "--message", required=True, help="Commit message")
    parser.add_argument("--base", default="main", help="Base branch (default: main)")
    parser.add_argument("--pr", action="store_true", help="Also create a PR")
    parser.add_argument("--pr-body", default="", help="PR body text")
    args = parser.parse_args()

    repo = get_repo()
    print(f"Repo: {repo}")

    token = get_token()
    print("Token acquired")

    # Support both "main" and "origin/main" as base
    local_base = args.base  # Used for local git diff
    api_base = args.base.removeprefix("origin/")  # Used for GitHub API

    # Resolve base branch
    base_ref = gh("get", repo, f"git/ref/heads/{api_base}", token)
    base_sha = base_ref["object"]["sha"]
    base_commit = gh("get", repo, f"git/commits/{base_sha}", token)
    base_tree_sha = base_commit["tree"]["sha"]
    print(f"Base: {api_base} @ {base_sha[:8]}")

    # Diff against local ref (supports origin/main for when local main is ahead)
    changes = changed_files(local_base)
    if not changes:
        print("No changes to push.")
        sys.exit(0)

    print(f"Files changed: {len(changes)}")

    # Build tree entries
    tree_entries = []
    for status, path in changes:
        if status == "D":
            # Omitting sha signals deletion to the Trees API
            tree_entries.append({"path": path, "mode": "100644", "type": "blob"})
            print(f"  D {path}")
        else:
            content = Path(path).read_bytes()
            blob = gh("post", repo, "git/blobs", token, json={
                "content": base64.b64encode(content).decode(),
                "encoding": "base64",
            })
            mode = "100755" if os.access(path, os.X_OK) else "100644"
            tree_entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
            print(f"  {status} {path}")

    # Create tree
    tree = gh("post", repo, "git/trees", token, json={
        "base_tree": base_tree_sha,
        "tree": tree_entries,
    })
    print(f"Tree: {tree['sha'][:8]}")

    # Create commit — omit author/committer so GitHub signs it as the app
    commit = gh("post", repo, "git/commits", token, json={
        "message": args.message,
        "tree": tree["sha"],
        "parents": [base_sha],
    })
    verified = commit.get("verification", {}).get("verified", False)
    print(f"Commit: {commit['sha'][:8]} (verified: {verified})")

    # Create or update branch (matching-refs handles slashes in branch names)
    refs = gh("get", repo, f"git/matching-refs/heads/{args.branch}", token) or []
    if refs:
        gh("patch", repo, f"git/refs/heads/{args.branch}", token, json={
            "sha": commit["sha"], "force": True,
        })
        print(f"Updated branch: {args.branch}")
    else:
        gh("post", repo, "git/refs", token, json={
            "ref": f"refs/heads/{args.branch}", "sha": commit["sha"],
        })
        print(f"Created branch: {args.branch}")

    # Create PR
    if args.pr:
        pr = gh("post", repo, "pulls", token, json={
            "title": args.message.splitlines()[0],
            "body": args.pr_body,
            "head": args.branch,
            "base": api_base,
        })
        print(f"PR: {pr['html_url']}")

    print("Done!")


if __name__ == "__main__":
    main()
