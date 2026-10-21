#!/usr/bin/env python3
"""Adopt an existing native GitHub stack locally, without rebasing or pushing.

The positional number is a GitHub stack number, never a pull-request number.
Preview is the default; --apply updates existing local branches, their linked
worktrees, and schema-1 gh-stack metadata. Fetches update remote-tracking refs
in either mode. Merged branches are left alone; archive them separately.

Replaced local history must be demonstrably published: either contained in the
current GitHub head, or witnessed by GitHub's force-push timeline. Saved catalog
heads and equal file contents are not publication evidence. Unknown membership
changes, hidden edits and incomplete GitHub restacks cause an explanatory stop.

Uses the POSIX operation/catalog locks of gh-stack v0.2.1. An interrupted apply
leaves gh-stack-adopt-state in the common Git directory, with recovery details.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import select
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import gh_stack_check as checkpoint

JOURNAL = "gh-stack-adopt-state"
SHA = checkpoint.SHA
FORCE_PUSH_QUERY = """query($owner:String!,$name:String!,$number:Int!,$after:String) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      timelineItems(first:100,after:$after,itemTypes:[HEAD_REF_FORCE_PUSHED_EVENT]) {
        nodes { ... on HeadRefForcePushedEvent {
          beforeCommit { oid } afterCommit { oid }
        } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""


class Refusal(Exception):
    """A condition that must be resolved before adoption can proceed."""


def require(condition, message):
    if not condition:
        raise Refusal(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def lock(path):
    try:
        import fcntl
    except ImportError as error:
        raise Refusal(
            "This helper currently requires POSIX gh-stack file locks."
        ) from error
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Refusal(
                f"Another stack command holds {path.name}; try again after it finishes."
            ) from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, value, mode=0o600):
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}-", delete=False
    ) as handle:
        temporary = Path(handle.name)
        os.fchmod(handle.fileno(), mode)
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def repository_name(value):
    """Return (host, owner/repo) from explicit, catalog or PR-URL spelling."""
    if value.startswith("https://"):
        parsed = urlsplit(value)
        require(
            not parsed.username and not parsed.password,
            "Repository URLs must not contain credentials.",
        )
        parts = parsed.path.strip("/").split("/")
        host, name = parsed.hostname, "/".join(parts[:2])
    elif ":" in value:
        host, name = value.split(":", 1)
    else:
        parts = value.split("/")
        host, name = (
            ("github.com", value)
            if len(parts) == 2
            else (parts[0], "/".join(parts[1:]))
        )
    require(
        bool(host and re.fullmatch(r"[A-Za-z0-9.-]+", host)), "Invalid GitHub hostname."
    )
    require(
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)),
        "Use OWNER/REPO or HOST/OWNER/REPO.",
    )
    return host.lower(), name.removesuffix(".git")


class Adopter:
    def __init__(self, args):
        self.args = args
        self.worktree = Path(args.worktree).resolve()
        self.env = dict(
            os.environ,
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
            GIT_NO_LAZY_FETCH="1",
        )
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
        ):
            self.env.pop(key, None)
        self.common = Path(
            self.out("rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        self.catalog = self.common / "gh-stack"
        self.journal = self.common / JOURNAL

    def run(self, command, *, cwd=None, ok=(0,), input=None, env=None):
        try:
            result = subprocess.run(
                command,
                cwd=cwd or self.worktree,
                env=env or self.env,
                input=input,
                text=True,
                capture_output=True,
                timeout=self.args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise Refusal(
                f"{command[0]} timed out; local adoption was not completed."
            ) from error
        require(
            result.returncode in ok,
            f"{command[0]} {command[1]} failed (exit {result.returncode}); check access and Git operation state.",
        )
        return result

    def git(self, *args, index=None, **kwargs):
        if index is not None:
            kwargs["env"] = dict(self.env, GIT_INDEX_FILE=str(index))
        return self.run(["git", *args], **kwargs)

    def out(self, *args, **kwargs):
        return self.git(*args, **kwargs).stdout.strip()

    def tip(self, branch):
        return self.out("rev-parse", "--verify", f"refs/heads/{branch}")

    def ancestor(self, parent, child):
        return (
            self.git("merge-base", "--is-ancestor", parent, child, ok=(0, 1)).returncode
            == 0
        )

    def api(self, path, *fields):
        result = self.run(
            ["gh", "api", "--hostname", self.host, path, *fields], ok=(0, 1)
        )
        if result.returncode:
            if "404" in result.stderr or '"status": "404"' in result.stdout:
                raise Refusal(
                    f"No accessible native GitHub stack #{self.args.number} in {self.repo}. "
                    "Supply a stack number, not a PR number; private repositories also require access."
                )
            raise Refusal(
                "GitHub API access failed; check authentication, permissions and connectivity. No PR-number fallback was attempted."
            )
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise Refusal("GitHub returned invalid JSON; stopped.") from error
        require(
            isinstance(value, dict), "GitHub returned a non-object response; stopped."
        )
        require(
            not value.get("errors"),
            "GitHub GraphQL could not verify publication history; stopped.",
        )
        return value

    def remote_stack(self):
        value = self.api(f"repos/{self.repo}/stacks/{self.args.number}")
        require(
            type(value.get("number")) is int
            and value["number"] == self.args.number
            and type(value.get("id")) is int
            and value["id"] > 0
            and type(value.get("pull_requests")) is list
            and all(isinstance(pr, dict) for pr in value["pull_requests"])
            and isinstance(value.get("base"), dict),
            "The response is not the requested native GitHub stack object; no PR-number fallback is supported.",
        )
        require(
            value.get("open") is True,
            "This GitHub stack is closed; use your normal local archival/cleanup workflow.",
        )
        return value

    def choose_repository(self, data):
        selected = self.args.repo or os.environ.get("GH_REPO")
        if selected:
            return repository_name(selected)
        candidates = set()
        if data.get("repository"):
            candidates.add(repository_name(data["repository"]))
        for stack in data["stacks"]:
            for branch in stack["branches"]:
                url = branch.get("pullRequest", {}).get("url")
                if url:
                    candidates.add(repository_name(url))
        require(
            len(candidates) == 1,
            "Cannot infer one GitHub repository; specify --repo OWNER/REPO.",
        )
        return candidates.pop()

    def matching_remote(self, name):
        urls = self.git(
            "remote", "get-url", "--all", name, ok=(0, 2)
        ).stdout.splitlines()
        push = self.git(
            "remote", "get-url", "--push", "--all", name, ok=(0, 2)
        ).stdout.splitlines()

        def matches(url):
            identity = checkpoint.endpoint_identity(url, self.worktree)
            return (
                identity[0] == "network"
                and identity[1] == self.host
                and identity[2] is None
                and identity[3].casefold() == self.repo.casefold()
            )

        return len(urls) == len(push) == 1 and matches(urls[0]) and matches(push[0])

    def choose_remote(self, stack):
        if self.args.remote:
            require(
                self.matching_remote(self.args.remote),
                "The selected remote's fetch/push repository does not match the GitHub stack.",
            )
            return self.args.remote
        candidates = [
            name
            for name in self.out("remote").splitlines()
            if self.matching_remote(name)
        ]
        if len(candidates) != 1:
            tracked = {
                self.out("config", "--get", f"branch.{b['branch']}.remote", ok=(0, 1))
                for b in stack["branches"]
                if not b.get("pullRequest", {}).get("merged")
            }
            candidates = [name for name in candidates if tracked == {name}]
        require(
            len(candidates) == 1,
            "Cannot select one matching remote; specify --remote explicitly.",
        )
        return candidates[0]

    def published(self, local, target, number):
        if local == target or self.ancestor(local, target):
            return "current GitHub history"
        owner, name = self.repo.split("/")
        cursor = None
        seen = set()
        while True:
            fields = [
                "-f",
                f"query={FORCE_PUSH_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
            if cursor:
                fields += ["-f", f"after={cursor}"]
            value = self.api("graphql", *fields)
            try:
                timeline = value["data"]["repository"]["pullRequest"]["timelineItems"]
                nodes, page = timeline["nodes"], timeline["pageInfo"]
            except (KeyError, TypeError) as error:
                raise Refusal(
                    f"Could not verify past published heads of PR #{number}."
                ) from error
            for node in nodes:
                for field in ("beforeCommit", "afterCommit"):
                    witness = (node.get(field) or {}).get("oid")
                    if witness and SHA.fullmatch(witness):
                        if witness == local:
                            return "GitHub force-push history"
                        exists = (
                            self.git(
                                "cat-file",
                                "-e",
                                f"{witness}^{{commit}}",
                                ok=(0, 1, 128),
                            ).returncode
                            == 0
                        )
                        if exists and self.ancestor(local, witness):
                            return "published ancestor in GitHub force-push history"
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            require(
                cursor and cursor not in seen,
                "GitHub history pagination is incomplete; stopped.",
            )
            seen.add(cursor)
        raise Refusal(
            f"PR #{number}: local tip {local[:12]} has no publication evidence in GitHub history. "
            "It may contain unpublished work; preserve/reconcile it before adopting. Saved catalog heads are not proof of publication."
        )

    def worktrees(self, relevant):
        entries = {}
        for record in self.git("worktree", "list", "--porcelain", "-z").stdout.split(
            "\0\0"
        ):
            flags = set(record.split("\0"))
            fields = dict(
                part.split(" ", 1) for part in record.split("\0") if " " in part
            )
            if fields.get("branch", "").startswith("refs/heads/"):
                branch = fields["branch"].removeprefix("refs/heads/")
                if branch not in relevant:
                    continue
                require(
                    branch not in entries,
                    f"{branch} is checked out in multiple worktrees; stopped.",
                )
                require(
                    "locked" not in fields
                    and "prunable" not in fields
                    and "locked" not in flags
                    and "prunable" not in flags,
                    f"Worktree for {branch} is locked or prunable.",
                )
                entries[branch] = Path(fields["worktree"]).resolve()
        return entries

    def clean(self, path, branch):
        require(
            self.out("symbolic-ref", "--short", "HEAD", cwd=path) == branch,
            f"Unexpected checkout in {path}; stopped.",
        )
        require(
            not self.git(
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--ignore-submodules=none",
                cwd=path,
            ).stdout,
            f"Uncommitted changes in {path}; commit or stash them first.",
        )
        for operation in checkpoint.GIT_OPERATIONS:
            marker = self.out(
                "rev-parse", "--path-format=absolute", "--git-path", operation, cwd=path
            )
            require(
                not Path(marker).exists(), f"Unfinished {operation} in {path}; stopped."
            )
        flags = self.git("ls-files", "-v", "-z", cwd=path).stdout.split("\0")
        require(
            not any(item and (item[0].islower() or item[0] == "S") for item in flags),
            f"Hidden index flags (assume-unchanged or skip-worktree) in {path}; clear them before adopting.",
        )

    def collisions(self, path, old, new):
        ignored = self.git(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
            cwd=path,
        ).stdout
        changed = self.git(
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--name-only",
            "--diff-filter=ACMT",
            "-r",
            "-z",
            old,
            new,
        ).stdout
        for local in ignored.split("\0"):
            if not local:
                continue
            local = local.rstrip("/")
            for tracked in changed.split("\0"):
                if tracked and (
                    local == tracked
                    or local.startswith(tracked + "/")
                    or tracked.startswith(local + "/")
                ):
                    raise Refusal(
                        f"Ignored path {local!r} in {path} collides with the target tree; move it aside first."
                    )
        raw = self.git(
            "diff-tree", "--no-commit-id", "--raw", "--no-renames", "-r", old, new
        ).stdout
        require(
            not any(
                line.startswith(":160000 ")
                or line.startswith(":100")
                and " 160000 " in line
                for line in raw.splitlines()
            ),
            f"The target changes submodule entries in {path}; update that checkout and its submodules explicitly first.",
        )

    def validate_remote(self, native, stack):
        trunk = native.get("base", {}).get("ref")
        require(
            trunk == stack["trunk"]["branch"],
            "GitHub and local stack trunks differ; stopped.",
        )
        entries = native["pull_requests"]
        require(entries, "The native stack contains no pull requests.")
        names, numbers, seen_active = set(), set(), False
        old_active = []
        for branch in stack["branches"]:
            if not branch.get("pullRequest", {}).get("merged"):
                old_active.append(
                    (branch["branch"], branch.get("pullRequest", {}).get("number"))
                )
        normalized = []
        for pr in entries:
            try:
                name, sha, number = pr["head"]["ref"], pr["head"]["sha"], pr["number"]
                repo_url = pr["head"]["repo"]["url"]
                merged = pr.get("merged_at") is not None
            except (KeyError, TypeError) as error:
                raise Refusal(
                    "Incomplete native stack PR metadata; stopped."
                ) from error
            require(
                isinstance(name, str)
                and isinstance(sha, str)
                and isinstance(repo_url, str)
                and isinstance(pr.get("base"), dict),
                "Malformed native stack head/base; stopped.",
            )
            require(
                repo_url.rstrip("/").casefold()
                == f"https://api.{self.host}/repos/{self.repo}".casefold()
                or repo_url.rstrip("/").casefold()
                == f"https://{self.host}/api/v3/repos/{self.repo}".casefold(),
                "Cross-repository stack heads are not supported; stopped.",
            )
            require(
                type(number) is int
                and number > 0
                and SHA.fullmatch(sha or "")
                and self.git(
                    "check-ref-format", f"refs/heads/{name}", ok=(0, 1)
                ).returncode
                == 0,
                "Malformed native stack head; stopped.",
            )
            require(
                name not in names and number not in numbers,
                "Duplicate native stack entries; stopped.",
            )
            names.add(name)
            numbers.add(number)
            if merged:
                require(
                    not seen_active,
                    "Merged layers are not a prefix; inspect the remote stack before adopting.",
                )
            else:
                seen_active = True
                require(
                    pr.get("state") == "open",
                    f"PR #{number} is closed but unmerged; stopped.",
                )
            normalized.append(
                {
                    "branch": name,
                    "head": sha,
                    "number": number,
                    "merged": merged,
                    "base": pr.get("base", {}).get("ref"),
                    "id": pr.get("node_id"),
                    "url": pr.get("html_url"),
                }
            )
        require(
            seen_active,
            "All PRs in this stack are merged; use your normal local archival/cleanup workflow.",
        )
        remote_pairs = {(p["branch"], p["number"]) for p in normalized}
        require(
            all(pair in remote_pairs for pair in old_active),
            "Local and remote stack membership changed; inspect before adopting.",
        )
        remote_active = [
            (p["branch"], p["number"]) for p in normalized if not p["merged"]
        ]
        old_remaining = [
            pair
            for pair in old_active
            if not next(
                p["merged"] for p in normalized if (p["branch"], p["number"]) == pair
            )
        ]
        require(
            old_remaining == remote_active,
            "Active stack membership/order changed; importing or restructuring stacks is outside this command's scope.",
        )
        parent = trunk
        for pr in normalized:
            if not pr["merged"]:
                require(
                    pr["base"] == parent,
                    "GitHub's stack base chain is still changing or inconsistent; try again after restacking finishes.",
                )
                parent = pr["branch"]
        return normalized

    def build_plan(self, data, stack, native):
        remote_prs = self.validate_remote(native, stack)
        active = [pr for pr in remote_prs if not pr["merged"]]
        trunk = stack["trunk"]["branch"]
        names = [trunk, *[pr["branch"] for pr in active]]
        for other in data["stacks"]:
            if other is stack:
                continue
            other_layers = {b["branch"] for b in other["branches"]}
            require(
                not set(names[1:]).intersection(
                    other_layers | {other["trunk"]["branch"]}
                )
                and trunk not in other_layers,
                "An active branch belongs to another local stack; inspect before adopting.",
            )
        locations = self.worktrees(set(names))
        for branch in names:
            require(
                self.git(
                    "symbolic-ref", "--quiet", f"refs/heads/{branch}", ok=(0, 1)
                ).returncode
                != 0,
                f"Symbolic branch ref {branch} is unsupported.",
            )
            self.tip(branch)
            if branch in locations:
                self.clean(locations[branch], branch)
        remote_names = self.out(
            "ls-remote",
            "--heads",
            "--",
            self.remote,
            *[f"refs/heads/{name}" for name in names],
        )
        advertised = dict(
            line.split("\t", 1)[::-1] for line in remote_names.splitlines()
        )
        require(
            set(advertised) == {f"refs/heads/{name}" for name in names},
            "A required remote branch is missing; stopped.",
        )
        for pr in active:
            require(
                advertised[f"refs/heads/{pr['branch']}"] == pr["head"],
                "GitHub API and remote refs disagree; try again after restacking finishes.",
            )
        self.git(
            "fetch",
            "--no-tags",
            "--recurse-submodules=no",
            "--",
            self.remote,
            *[
                f"+refs/heads/{name}:refs/remotes/{self.remote}/{name}"
                for name in names
            ],
        )
        targets = {
            name: self.out(
                "rev-parse", "--verify", f"refs/remotes/{self.remote}/{name}"
            )
            for name in names
        }
        require(
            all(targets[name] == advertised[f"refs/heads/{name}"] for name in names),
            "Remote refs changed during fetch; try again.",
        )
        main = self.tip(trunk)
        require(
            self.ancestor(main, targets[trunk]),
            f"Local trunk {trunk} cannot fast-forward; preserve/reconcile its local commits first.",
        )
        rows = [
            {
                "branch": trunk,
                "old": main,
                "target": targets[trunk],
                "path": str(locations[trunk]) if trunk in locations else None,
            }
        ]
        bases = {}
        parent = targets[trunk]
        for index, pr in enumerate(active):
            branch, target = pr["branch"], targets[pr["branch"]]
            local = self.tip(branch)
            proof = self.published(local, target, pr["number"])
            if index == 0:
                merge_bases = self.out(
                    "merge-base", "--all", parent, target
                ).splitlines()
                require(
                    len(merge_bases) == 1,
                    "Cannot establish a unique trunk replay boundary; stopped.",
                )
                base = merge_bases[0]
            else:
                require(
                    self.ancestor(parent, target),
                    "Remote stack ancestry is incomplete; try again after restacking finishes.",
                )
                base = parent
            bases[branch] = base
            rows.append(
                {
                    "branch": branch,
                    "old": local,
                    "target": target,
                    "path": str(locations[branch]) if branch in locations else None,
                    "publication_evidence": proof,
                }
            )
            parent = target
        for row in rows:
            if row["path"] and row["old"] != row["target"]:
                self.collisions(Path(row["path"]), row["old"], row["target"])
        updated = copy.deepcopy(data)
        replacement = updated["stacks"][data["stacks"].index(stack)]
        replacement["id"], replacement["number"] = str(native["id"]), native["number"]
        replacement["trunk"]["head"] = targets[trunk]
        by_name = {pr["branch"]: pr for pr in remote_prs}
        for entry in replacement["branches"]:
            pr = by_name.get(entry["branch"])
            require(
                pr is not None or entry.get("pullRequest", {}).get("merged"),
                "An unmatched local layer is not known merged; stopped.",
            )
            if pr is None:
                continue
            ref = entry.setdefault("pullRequest", {})
            ref["number"], ref["merged"] = pr["number"], pr["merged"]
            if pr["id"]:
                ref["id"] = pr["id"]
            if pr["url"]:
                ref["url"] = pr["url"]
            if not pr["merged"]:
                entry["head"], entry["base"] = (
                    targets[entry["branch"]],
                    bases[entry["branch"]],
                )
        return {
            "repository": f"{self.host}:{self.repo}",
            "stack": self.args.number,
            "remote": self.remote,
            "mode": "apply" if self.args.apply else "preview",
            "rows": rows,
            "replay_bases": bases,
            "trunk_needs_rebase": bases[active[0]["branch"]] != targets[trunk],
        }, updated

    def unchanged(self, raw, rows):
        require(
            self.catalog.read_bytes() == raw,
            "Local stack metadata changed concurrently; stopped.",
        )
        locations = self.worktrees({row["branch"] for row in rows})
        for row in rows:
            require(
                self.tip(row["branch"]) == row["old"],
                f"Local branch {row['branch']} changed concurrently; stopped.",
            )
            path = locations.get(row["branch"])
            require(
                (str(path) if path else None) == row["path"],
                "Worktree ownership changed concurrently; stopped.",
            )
            if path:
                self.clean(path, row["branch"])
                if row["old"] != row["target"]:
                    self.collisions(path, row["old"], row["target"])

    def read_tree(self, row, old, target, index):
        self.git(
            "-c",
            "submodule.recurse=false",
            "read-tree",
            "-m",
            "-u",
            old,
            target,
            cwd=Path(row["path"]),
            index=index,
        )

    def verify_index(self, row, index):
        path = Path(row["path"])
        self.git(
            "diff-index",
            "--cached",
            "--quiet",
            row["target"],
            "--",
            cwd=path,
            index=index,
        )
        self.git(
            "diff-files",
            "--quiet",
            "--ignore-submodules=none",
            "--",
            cwd=path,
            index=index,
        )
        require(
            self.out("symbolic-ref", "--short", "HEAD", cwd=path) == row["branch"],
            "An affected worktree changed branches during adoption; stopped.",
        )

    def apply(self, raw, plan, updated, native):
        require(
            self.remote_stack() == native,
            "GitHub stack changed while planning; run the preview again.",
        )
        rows = plan["rows"]
        with lock(self.common / "gh-stack.lock"):
            self.unchanged(raw, rows)
            if plan["already_synchronized"]:
                return
            journal = {
                "stack": self.args.number,
                "repository": plan["repository"],
                "phase": "prepared",
                "catalog_before_sha256": digest(raw),
                "catalog_after": updated,
                "rows": rows,
            }
            require(
                not self.journal.exists(),
                f"An interrupted adoption is recorded in {self.journal}; inspect it first.",
            )
            atomic_json(self.journal, journal)
            changed = []
            committed = False
            try:
                with WorktreeGuards(self, rows) as guards:
                    journal["worktree_locks"] = guards.describe()
                    atomic_json(self.journal, journal)
                    with RefTransaction(self, rows) as transaction:
                        try:
                            for row in rows:
                                if row["path"] and row["old"] != row["target"]:
                                    self.collisions(
                                        Path(row["path"]), row["old"], row["target"]
                                    )
                                    changed.append(row)
                                    self.read_tree(
                                        row,
                                        row["old"],
                                        row["target"],
                                        guards.index(row),
                                    )
                            for row in changed:
                                self.verify_index(row, guards.index(row))
                            journal["phase"] = "worktrees-updated"
                            atomic_json(self.journal, journal)
                            transaction.commit()
                            committed = True
                        except (Refusal, OSError):
                            if all(
                                self.tip(row["branch"]) == row["old"] for row in rows
                            ):
                                for row in reversed(changed):
                                    self.read_tree(
                                        row,
                                        row["target"],
                                        row["old"],
                                        guards.index(row),
                                    )
                            raise
                    guards.publish()
                journal["phase"] = "refs-committed"
                atomic_json(self.journal, journal)
                require(
                    all(self.tip(row["branch"]) == row["target"] for row in rows),
                    "A local branch changed after adoption; inspect the recovery journal.",
                )
                require(
                    self.catalog.read_bytes() == raw,
                    "Stack metadata changed concurrently; inspect the recovery journal.",
                )
                for row in rows:
                    if row["path"]:
                        self.clean(Path(row["path"]), row["branch"])
                atomic_json(
                    self.catalog, updated, stat.S_IMODE(self.catalog.stat().st_mode)
                )
                self.journal.unlink()
            except (Refusal, OSError):
                if not committed and all(
                    self.tip(row["branch"]) == row["old"] for row in rows
                ):
                    # Only remove recovery state after a complete, clean rollback.
                    for row in rows:
                        if row["path"]:
                            self.clean(Path(row["path"]), row["branch"])
                    self.journal.unlink()
                raise

    def execute(self):
        with lock(self.common / "gh-stack-operation.lock"):
            for name in {*checkpoint.STACK_OPERATIONS, JOURNAL}:
                require(
                    not (self.common / name).exists(),
                    f"Unfinished stack operation: {self.common / name}. Inspect/recover it before adopting.",
                )
            raw = self.catalog.read_bytes()
            data = checkpoint.load_metadata(raw, self.git)
            require(
                self.out("rev-parse", "--is-shallow-repository") == "false",
                "Shallow history cannot establish safe replay boundaries; obtain complete history before adopting.",
            )
            self.host, self.repo = self.choose_repository(data)
            native = self.remote_stack()
            numbers = {pr.get("number") for pr in native["pull_requests"]}
            matches = [
                s
                for s in data["stacks"]
                if any(
                    b.get("pullRequest", {}).get("number") in numbers
                    for b in s["branches"]
                )
            ]
            require(
                len(matches) == 1,
                "No unique matching existing local stack; importing unknown stacks is outside this command's scope.",
            )
            stack = matches[0]
            for entry in stack["branches"]:
                url = entry.get("pullRequest", {}).get("url")
                if url:
                    host, repo = repository_name(url)
                    require(
                        host == self.host and repo.casefold() == self.repo.casefold(),
                        "Local PR associations belong to a different GitHub repository; stopped.",
                    )
            require(
                not stack.get("number") or stack["number"] == self.args.number,
                "Local stack number differs from the requested native stack.",
            )
            require(
                not stack.get("id") or stack["id"] == str(native["id"]),
                "Local stack identity differs from the requested native stack.",
            )
            self.remote = self.choose_remote(stack)
            plan, updated = self.build_plan(data, stack, native)
            self.unchanged(raw, plan["rows"])
            plan["metadata_changed"] = updated != data
            plan["already_synchronized"] = not plan["metadata_changed"] and all(
                row["old"] == row["target"] for row in plan["rows"]
            )
            if self.args.apply:
                self.apply(raw, plan, updated, native)
            plan["result"] = (
                "already-synchronized"
                if plan["already_synchronized"]
                else "adopted"
                if self.args.apply
                else "ready"
            )
            return plan


class WorktreeGuards:
    """Stage indexes under their normal lock paths while holding checkout locks.

    Git protects HEAD in its own ref-transaction worktree. Linked worktrees need
    their own HEAD locks, and every affected checkout needs an index lock because
    checkout can write files before discovering a HEAD lock. read-tree uses a
    separate GIT_INDEX_FILE until all expected-old branch updates are committed.
    """

    def __init__(self, owner, rows):
        self.owner = owner
        self.rows = [row for row in rows if row["path"] and row["old"] != row["target"]]
        self.files = []
        self.indexes = {}

    def create(self, path, contents):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise Refusal(f"Another Git operation holds {path}; stopped.") from error
        self.files.append(path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(contents)

    def __enter__(self):
        try:
            current_head = self.owner.out(
                "rev-parse", "--path-format=absolute", "--git-path", "HEAD"
            )
            for row in self.rows:
                path = Path(row["path"])
                self.owner.clean(path, row["branch"])
                index = Path(
                    self.owner.out(
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-path",
                        "index",
                        cwd=path,
                    )
                )
                head = self.owner.out(
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "HEAD",
                    cwd=path,
                )
                locked_index = Path(str(index) + ".lock")
                self.create(locked_index, index.read_bytes())
                if head != current_head:
                    self.create(Path(head + ".lock"), b"")
                self.indexes[row["branch"]] = (locked_index, index)
                self.owner.clean(path, row["branch"])
        except (Refusal, OSError):
            self.close()
            raise
        return self

    def index(self, row):
        return self.indexes[row["branch"]][0]

    def describe(self):
        return [
            {
                "branch": branch,
                "staged_index": str(paths[0]),
                "original_index": str(paths[1]),
            }
            for branch, paths in self.indexes.items()
        ]

    def publish(self):
        for staged, original in self.indexes.values():
            os.replace(staged, original)
            # That lock path is available to other Git writers after rename.
            self.files.remove(staged)

    def close(self):
        for path in reversed(self.files):
            path.unlink(missing_ok=True)

    def __exit__(self, *_):
        self.close()


class RefTransaction:
    """Hold expected-old ref locks while updating the corresponding worktrees."""

    def __init__(self, owner, rows):
        self.owner, self.rows = owner, rows
        self.process = None

    def send(self, command, reply=None):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        if reply:
            ready, _, _ = select.select(
                [self.process.stdout], [], [], self.owner.args.timeout
            )
            require(
                ready and self.process.stdout.readline().strip() == reply,
                "Git could not prepare/commit the expected-old local ref transaction; another writer may have changed a branch.",
            )

    def __enter__(self):
        self.process = subprocess.Popen(
            [
                "git",
                "update-ref",
                "-m",
                f"adopt GitHub stack #{self.owner.args.number}",
                "--stdin",
            ],
            cwd=self.owner.worktree,
            env=self.owner.env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.send("start", "start: ok")
            self.send("option no-deref")
            for row in self.rows:
                ref = f"refs/heads/{row['branch']}"
                self.send(
                    f"update {ref} {row['target']} {row['old']}"
                    if row["old"] != row["target"]
                    else f"verify {ref} {row['old']}"
                )
            self.send("prepare", "prepare: ok")
        except (Refusal, OSError):
            self.close()
            raise
        return self

    def commit(self):
        self.send("commit", "commit: ok")

    def close(self):
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.process.stdout.close()
            self.process.stderr.close()

    def __exit__(self, *_):
        self.close()


def positive_number(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Supply a positive GitHub stack number."
        ) from error
    if number <= 0:
        raise argparse.ArgumentTypeError("Supply a positive GitHub stack number.")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "number",
        type=positive_number,
        help="native GitHub stack number (not PR number)",
    )
    parser.add_argument(
        "--worktree",
        default=os.getcwd(),
        help="repository/worktree to inspect (default: current directory)",
    )
    parser.add_argument(
        "--repo",
        help="GitHub OWNER/REPO or HOST/OWNER/REPO; otherwise infer from GH_REPO/catalog",
    )
    parser.add_argument(
        "--remote", help="matching publication remote; infer only when unambiguous"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the verified local adoption (default: preview)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a structured plan/result"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="seconds allowed per subprocess (default: 30)",
    )
    args = parser.parse_args(argv)
    adopter = None
    try:
        require(args.timeout > 0, "--timeout must be positive.")
        adopter = Adopter(args)
        result = adopter.execute()
    except (Refusal, checkpoint.Incomplete, OSError) as error:
        message = str(error)
        if adopter is not None and adopter.journal.exists():
            message += (
                f" Recovery journal retained at {adopter.journal}; inspect its old/target refs "
                "and worktree status before further stack operations."
            )
        if args.json:
            print(
                json.dumps(
                    {"result": "stopped", "stack": args.number, "message": message}
                )
            )
        else:
            print(f"gh_stack_adopt_from_github.py: stopped: {message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"GitHub stack #{args.number}: {result['result'].replace('-', ' ')} ({result['repository']}; remote {result['remote']})"
        )
        for row in result["rows"]:
            print(f"  {row['branch']}: {row['old'][:12]} -> {row['target'][:12]}")
        if result["trunk_needs_rebase"]:
            print(
                "Trunk has advanced beyond the stack: adopted replay bases are valid; a future rebase is still needed."
            )
            print(
                "Use gh_stack_check.py --allow-stale-trunk to verify this state without requiring that rebase."
            )
        if result["already_synchronized"]:
            print(
                "Local branches and stack metadata already match GitHub; nothing to adopt."
            )
        elif not args.apply:
            if all(row["old"] == row["target"] for row in result["rows"]):
                print("Branch tips already match; stack metadata needs updating.")
            print(
                "Preview only: fetched remote-tracking refs; local branches/worktrees and stack metadata are unchanged. Use --apply to adopt."
            )
        else:
            print(
                "Local branches, worktrees and stack metadata adopted. No remote refs were written; merged branches were left alone."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
