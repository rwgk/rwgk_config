#!/usr/bin/env python3
"""Read-only Git/metadata checkpoint verification for gh-stack v0.1.1, schema 1.

GitHub stack membership, PR bases/status, and merge queues require separate API
checks. No gh-stack commands, fetches (including lazy fetches), or repairs run.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

OPERATIONS = (
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "gh-stack-rebase-state",
    "gh-stack-modify-state",
)
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class Incomplete(Exception):
    """A required observation could not be made."""


def endpoint_identity(url, worktree):
    """Compare ordinary HTTPS/SSH repository spellings; don't resolve host aliases."""
    if "://" in url:
        parsed = urlsplit(url)
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                return ("opaque", url)
            return ("local", str(Path(unquote(parsed.path)).resolve()))
        if parsed.scheme not in ("http", "https", "ssh", "git"):
            return ("opaque", url)
        defaults = {"http": 80, "https": 443, "ssh": 22, "git": 9418}
        port = parsed.port
        port = None if port == defaults[parsed.scheme] else port
        return (
            "network",
            parsed.hostname,
            port,
            parsed.path.strip("/").removesuffix(".git"),
        )
    scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", url)
    if scp:
        return ("network", scp[1].lower(), None, scp[2].strip("/").removesuffix(".git"))
    path = Path(url).expanduser()
    return ("local", str((path if path.is_absolute() else worktree / path).resolve()))


def display_endpoint(url):
    """Never display URL credentials, query strings, or fragments."""
    if "://" in url:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host += f":{parsed.port}"
        credentials = "[redacted]@" if parsed.username is not None else ""
        return f"{parsed.scheme}://{credentials}{host}{parsed.path}"
    return re.sub(r"^[^/@:]+@", "[redacted]@", url)


def file_stamp(path):
    try:
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return None


def load_metadata(raw, git):
    def require(condition, message):
        if not condition:
            raise Incomplete(f"malformed metadata: {message}")

    def optional_types(value, fields):
        for key, kind in fields.items():
            require(key not in value or type(value[key]) is kind, f"invalid {key}")

    def branch(value):
        require(type(value) is dict, "branch reference must be an object")
        require(type(value.get("branch")) is str, "branch name required")
        name = value["branch"]
        require(
            git("check-ref-format", f"refs/heads/{name}", ok=(0, 1)).returncode == 0,
            "invalid branch name",
        )
        optional_types(value, {"head": str, "base": str})
        if "pullRequest" in value:
            pr = value["pullRequest"]
            require(type(pr) is dict, "pullRequest must be an object")
            require(
                type(pr.get("number")) is int and pr["number"] > 0, "PR number required"
            )
            optional_types(pr, {"id": str, "url": str, "merged": bool})

    try:

        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, "duplicate JSON key")
                result[key] = value
            return result

        data = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise Incomplete("malformed metadata: invalid JSON") from exc
    require(type(data) is dict, "top level must be an object")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise Incomplete(
            "unsupported metadata schema; only schemaVersion 1 (gh-stack v0.1.1) is audited"
        )
    optional_types(data, {"repository": str})
    require(type(data.get("stacks")) is list, "stacks array required")
    for stack in data["stacks"]:
        require(type(stack) is dict, "stack must be an object")
        optional_types(stack, {"id": str, "number": int})
        branch(stack.get("trunk"))
        require(type(stack.get("branches")) is list, "branches array required")
        names = {stack["trunk"]["branch"]}
        for layer in stack["branches"]:
            branch(layer)
            require(layer["branch"] not in names, "duplicate trunk/layer branch")
            names.add(layer["branch"])
    return data


class Checker:
    def __init__(self, args):
        self.args = args
        self.worktree = Path(args.worktree).resolve()
        self.env = dict(
            os.environ,
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
        )
        # --worktree must select the repository even inside a Git hook/shell.
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
        ):
            self.env.pop(key, None)
        self.report = {
            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "worktree": str(self.worktree),
            "remote": args.remote,
            "mode": "allow-local-ahead" if args.allow_local_ahead else "expect-synced",
            "supported_metadata": "gh-stack v0.1.1 / schema 1",
            "github_verified": False,
            "layers": [],
            "issues": [],
        }
        self.no_lazy_fetch = False

    def issue(self, level, scope, message):
        self.report["issues"].append(
            {"level": level, "scope": scope, "message": message}
        )

    def git(self, *args, ok=(0,)):
        try:
            result = subprocess.run(
                [
                    "git",
                    *(["--no-lazy-fetch"] if self.no_lazy_fetch else []),
                    "-c",
                    "core.fsmonitor=false",
                    "-C",
                    str(self.worktree),
                    *args,
                ],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=self.args.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Incomplete(f"git {args[0]} unavailable or timed out") from exc
        if result.returncode not in ok:
            # Git errors can include a credential-bearing URL, so don't echo stderr.
            suffix = (
                "; network/authentication or publication endpoint unavailable"
                if args[0] == "ls-remote"
                else ""
            )
            raise Incomplete(f"git {args[0]} failed (exit {result.returncode}){suffix}")
        return result

    def path(self, name):
        return Path(
            self.git(
                "rev-parse", "--path-format=absolute", "--git-path", name
            ).stdout.strip()
        )

    def snapshot(self):
        refs = self.git(
            "for-each-ref",
            "--format=%(refname) %(objectname) %(symref)",
            "refs/heads/",
            "refs/remotes/",
        ).stdout
        head = self.git("rev-parse", "--verify", "HEAD").stdout
        branch = self.git("symbolic-ref", "--quiet", "HEAD", ok=(0, 1)).stdout.strip()
        config = self.git(
            "config",
            "--null",
            "--get-regexp",
            r"^(branch|remote|url|extensions)\.",
            ok=(0, 1),
        ).stdout
        raw = self.metadata_path.read_bytes() if self.metadata_path.exists() else None
        stamps = tuple(file_stamp(path) for path in self.watch_paths)
        status = self.git("status", "--porcelain=v1", "--untracked-files=normal").stdout
        return (refs, head, branch, config, raw, stamps, status)

    def tip(self, branch):
        result = self.git(
            "show-ref", "--verify", "--hash", f"refs/heads/{branch}", ok=(0, 1, 128)
        )
        if result.returncode:
            self.issue("violation", branch, "missing local branch under refs/heads/")
            return None
        return result.stdout.strip()

    def ancestry(self, older, newer, scope, purpose):
        for sha in (older, newer):
            if not SHA.fullmatch(sha):
                self.issue("violation", scope, f"{purpose}: invalid commit SHA")
                return None
            if self.git(
                "cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128)
            ).returncode:
                self.issue(
                    "incomplete",
                    scope,
                    f"{purpose}: commit {sha[:12]} unavailable locally; ancestry unknown (no fetch)",
                )
                return None
        # Shallow histories cannot prove a negative ancestry result.
        result = self.git("merge-base", "--is-ancestor", older, newer, ok=(0, 1))
        if (
            result.returncode == 1
            and self.git("rev-parse", "--is-shallow-repository").stdout.strip()
            == "true"
        ):
            self.issue(
                "incomplete",
                scope,
                f"{purpose}: ancestry unknown in shallow history (no fetch)",
            )
            return None
        return result.returncode == 0

    def urls(self, remote, push=False):
        args = ["remote", "get-url", "--all"]
        if push:
            args.append("--push")
        return self.git(*args, remote).stdout.splitlines()

    def tracking(self, name, publication):
        remotes = self.git(
            "config", "--get-all", f"branch.{name}.remote", ok=(0, 1)
        ).stdout.splitlines()
        merges = self.git(
            "config", "--get-all", f"branch.{name}.merge", ok=(0, 1)
        ).stdout.splitlines()
        if len(remotes) != 1 or len(merges) != 1:
            self.issue(
                "violation",
                name,
                "missing or ambiguous branch tracking; upstream preserved",
            )
            return None
        tracking = {"remote": remotes[0], "ref": merges[0]}
        if merges[0] != f"refs/heads/{name}":
            self.issue(
                "violation",
                name,
                f"tracking ref {merges[0]} differs from same-named publication branch",
            )
        if remotes[0] == ".":
            self.issue(
                "violation",
                name,
                "upstream tracks a local branch rather than publication repository",
            )
        else:
            try:
                urls = self.urls(remotes[0])
                if len(urls) != 1:
                    self.issue(
                        "incomplete", name, "upstream fetch endpoint is ambiguous"
                    )
                elif endpoint_identity(urls[0], self.worktree) != publication:
                    self.issue(
                        "violation",
                        name,
                        "upstream fetch repository differs from publication repository; tracking preserved",
                    )
                ref = self.git(
                    "for-each-ref", "--format=%(upstream)", f"refs/heads/{name}"
                ).stdout.strip()
                tracking["resolved_ref"] = ref
                if not ref:
                    self.issue(
                        "violation",
                        name,
                        "tracking configuration does not resolve an upstream ref (check fetch refspec)",
                    )
            except Incomplete as exc:
                self.issue("incomplete", name, str(exc))
        return tracking

    def inspect(self, snapshot):
        raw = snapshot[4]
        if raw is None:
            raise Incomplete("missing worktree gh-stack metadata")
        data = load_metadata(raw, self.git)
        current = snapshot[2].removeprefix("refs/heads/")
        self.report["current_branch"] = current or None
        candidates = [
            stack
            for stack in data["stacks"]
            if (
                stack.get("id") == self.args.stack
                if self.args.stack is not None
                else current
                in [stack["trunk"]["branch"], *(b["branch"] for b in stack["branches"])]
            )
        ]
        if len(candidates) != 1:
            raise Incomplete(
                "stack selection missing or ambiguous; provide --stack with a unique metadata id"
            )
        stack = candidates[0]
        self.report["stack_id"] = stack.get("id")
        if not current:
            self.issue(
                "violation",
                "worktree",
                "detached HEAD; expected resumable branch not checked out",
            )
        elif current not in [
            stack["trunk"]["branch"],
            *(b["branch"] for b in stack["branches"]),
        ]:
            self.issue(
                "violation", "worktree", "checked-out branch is outside selected stack"
            )
        for name, path in self.operation_paths.items():
            if path.exists():
                self.issue("violation", "worktree", f"unfinished operation: {name}")
        if snapshot[6]:
            self.issue(
                "info",
                "worktree",
                "pending local changes (tracked or untracked); preserved",
            )

        fetch_urls = self.urls(self.args.remote)
        push_urls = self.urls(self.args.remote, push=True)
        self.report["fetch_endpoints"] = [display_endpoint(url) for url in fetch_urls]
        self.report["push_endpoints"] = [display_endpoint(url) for url in push_urls]
        publication = None
        remote_heads = None
        if len(push_urls) != 1:
            self.issue(
                "incomplete",
                "remote",
                "ambiguous publication endpoint: selected remote must have exactly one push URL",
            )
        else:
            publication = endpoint_identity(push_urls[0], self.worktree)
            self.report["publication_endpoint"] = display_endpoint(push_urls[0])
            if (
                len(fetch_urls) != 1
                or endpoint_identity(fetch_urls[0], self.worktree) != publication
            ):
                self.issue(
                    "info",
                    "remote",
                    "fetch and push endpoints differ; publication comparisons use the push endpoint",
                )
            try:
                remote_heads = {}
                output = self.git("ls-remote", "--heads", "--", push_urls[0]).stdout
                for line in output.splitlines():
                    fields = line.split("\t")
                    if (
                        len(fields) != 2
                        or not SHA.fullmatch(fields[0])
                        or not fields[1].startswith("refs/heads/")
                    ):
                        raise Incomplete("malformed ls-remote output")
                    remote_heads[fields[1]] = fields[0]
            except Incomplete as exc:
                remote_heads = None
                self.issue("incomplete", "remote", str(exc))

        trunk = stack["trunk"]["branch"]
        parent_name, parent_tip = trunk, self.tip(trunk)
        remote_trunk = (
            remote_heads.get(f"refs/heads/{trunk}")
            if remote_heads is not None
            else None
        )
        self.report["trunk"] = {
            "branch": trunk,
            "local_tip": parent_tip,
            "remote_tip": remote_trunk,
        }
        if remote_heads is None:
            freshness = "unknown"
        elif not remote_trunk:
            freshness = "missing publication trunk"
            self.issue(
                "incomplete", "trunk", "remote trunk missing; freshness unavailable"
            )
        elif parent_tip == remote_trunk:
            freshness = "equal"
        else:
            freshness = "different"
            self.issue(
                "info",
                "trunk",
                "local trunk differs from publication trunk; PR synchronization does not imply latest trunk",
            )
        self.report["trunk"]["freshness"] = freshness

        for saved in stack["branches"]:
            name = saved["branch"]
            if saved.get("pullRequest", {}).get("merged", False):
                self.report["layers"].append(
                    {"branch": name, "status": "skipped-merged"}
                )
                continue
            local = self.tip(name)
            remote = (
                remote_heads.get(f"refs/heads/{name}")
                if remote_heads is not None
                else None
            )
            layer = {
                "branch": name,
                "parent": parent_name,
                "parent_tip": parent_tip,
                "local_tip": local,
                "remote_tip": remote,
                "saved_head": saved.get("head"),
                "saved_base": saved.get("base"),
                "publication_checked": publication is not None
                and remote_heads is not None,
            }
            self.report["layers"].append(layer)
            if publication is not None:
                layer["tracking"] = self.tracking(name, publication)
            if remote_heads is not None and remote is None:
                self.issue("violation", name, "missing remote publication branch")
            elif local and remote and local != remote:
                if not self.args.allow_local_ahead:
                    self.issue(
                        "violation",
                        name,
                        "local and publication tips differ (strict synchronization required)",
                    )
                else:
                    ahead = self.ancestry(
                        remote, local, name, "remote/local comparison"
                    )
                    if ahead is False:
                        self.issue(
                            "violation",
                            name,
                            "local branch is behind or diverged; remote tip is not an ancestor",
                        )
                    elif ahead:
                        self.issue("info", name, "local-ahead work verified")
            saved_head = saved.get("head")
            if not saved_head:
                self.issue("incomplete", name, "missing saved metadata head")
            elif not SHA.fullmatch(saved_head):
                self.issue("incomplete", name, "malformed saved metadata head SHA")
            elif local and saved_head != local:
                if not self.args.allow_local_ahead:
                    self.issue(
                        "violation", name, "saved metadata head differs from local tip"
                    )
                else:
                    ancestral = self.ancestry(
                        saved_head, local, name, "saved metadata head"
                    )
                    if ancestral is False:
                        self.issue(
                            "violation",
                            name,
                            "saved metadata head is unrelated to local tip",
                        )
                    elif ancestral:
                        self.issue(
                            "info",
                            name,
                            "saved metadata head pending refresh (verified ancestor)",
                        )
            if not saved.get("base"):
                self.issue("incomplete", name, "missing saved metadata base")
            elif not SHA.fullmatch(saved["base"]):
                self.issue("incomplete", name, "malformed saved metadata base SHA")
            elif parent_tip and saved["base"] != parent_tip:
                self.issue(
                    "violation",
                    name,
                    "saved base differs from effective parent tip; aligned replay boundary required",
                )
            if local and parent_tip:
                contained = self.ancestry(parent_tip, local, name, "effective parent")
                if contained is False:
                    self.issue(
                        "violation",
                        name,
                        "child does not contain effective parent; needs restacking",
                    )
            parent_name, parent_tip = name, local

    def run(self):
        before = None
        try:
            self.git("rev-parse", "--show-toplevel")
            # Old Git silently ignores GIT_NO_LAZY_FETCH. Before any object
            # reads/status, prove support or refuse a configured partial clone.
            self.no_lazy_fetch = (
                self.git("--no-lazy-fetch", "--version", ok=(0, 129)).returncode == 0
            )
            self.report["git_no_lazy_fetch_supported"] = self.no_lazy_fetch
            partial = self.git(
                "config",
                "--get-regexp",
                r"^(extensions\.partialclone|remote\..*\.(promisor|partialclonefilter))$",
                ok=(0, 1),
            )
            if partial.stdout and not self.no_lazy_fetch:
                raise Incomplete(
                    "partial clone requires Git with --no-lazy-fetch support; refusing object reads/status to avoid fetching"
                )
            self.metadata_path = self.path("gh-stack")
            self.report["metadata_path"] = str(self.metadata_path)
            self.operation_paths = {name: self.path(name) for name in OPERATIONS}
            self.watch_paths = [
                self.metadata_path,
                self.path("HEAD"),
                self.path("config"),
                self.path("packed-refs"),
                *self.operation_paths.values(),
            ]
            before = self.snapshot()
            self.inspect(before)
        except (Incomplete, OSError, ValueError) as exc:
            self.issue(
                "incomplete",
                "inspection",
                str(exc)
                if isinstance(exc, Incomplete)
                else "local files or endpoint format could not be inspected",
            )
        finally:
            if before is not None:
                try:
                    if before != self.snapshot():
                        self.issue(
                            "incomplete",
                            "snapshot",
                            "concurrent inspection: local refs, metadata, configuration or worktree changed; observation inconclusive",
                        )
                except (Incomplete, OSError):
                    self.issue(
                        "incomplete",
                        "snapshot",
                        "could not recheck local snapshot; observation inconclusive",
                    )
        for layer in self.report["layers"]:
            if layer.get("status") == "skipped-merged":
                continue
            levels = [
                issue["level"]
                for issue in self.report["issues"]
                if issue["scope"] == layer["branch"]
            ]
            layer["status"] = (
                "incomplete"
                if "incomplete" in levels or not layer["publication_checked"]
                else "violation"
                if "violation" in levels
                else "pass"
            )
        levels = [issue["level"] for issue in self.report["issues"]]
        code = 2 if "incomplete" in levels else 1 if "violation" in levels else 0
        self.report["exit_code"] = code
        self.report["result"] = {0: "pass", 1: "violation", 2: "incomplete"}[code]
        return self.report


def print_report(report):
    print(
        f"gh_stack_check.py: {report['result'].upper()} ({report['mode']}) at {report['observed_at']}"
    )
    print(f"Worktree: {report['worktree']}")
    if "publication_endpoint" in report:
        print(
            f"Publication: {report['publication_endpoint']} (same-named refs/heads/*)"
        )
    if "trunk" in report:
        trunk = report["trunk"]
        print(
            f"Trunk {trunk['branch']}: local={str(trunk['local_tip'])[:12]} remote={str(trunk['remote_tip'])[:12]} freshness={trunk['freshness']}"
        )
    for layer in report["layers"]:
        if layer["status"] == "skipped-merged":
            print(f"  {layer['branch']}: skipped merged layer")
        else:
            short = lambda key, values=layer: str(values.get(key))[:12]
            print(
                f"  {layer['branch']}: {layer['status']} local={short('local_tip')} remote={short('remote_tip')} head={short('saved_head')}"
            )
            print(
                f"    parent={layer['parent']}@{short('parent_tip')} saved-base={short('saved_base')}"
            )
    for issue in report["issues"]:
        print(f"  {issue['level'].upper()} [{issue['scope']}]: {issue['message']}")
    print(
        "Checks: Git + local schema-1 metadata. GitHub membership, PR bases/status and queues NOT verified."
    )
    print(
        "Remote tips are a timestamped observation; other writers may change them afterward."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Exit codes: 0 passed requested Git/metadata checks; 1 invariant violation; 2 incomplete (also reports known violations). No fetch or repair.",
    )
    parser.add_argument("--worktree", required=True, help="selected worktree to verify")
    parser.add_argument(
        "--remote",
        required=True,
        help="remote whose single push endpoint is publication destination",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--expect-synced",
        action="store_true",
        help="require equal publication, local and saved layer tips (default)",
    )
    mode.add_argument(
        "--allow-local-ahead",
        action="store_true",
        help="also allow proven local-ahead tips and ancestral saved heads",
    )
    parser.add_argument(
        "--stack", help="local metadata stack id; required for ambiguous current branch"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit structured observation"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="seconds allowed per Git command (default: 30)",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    report = Checker(args).run()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
