"""Shared repository identity, paths and worktree ownership for git-wt."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class WTError(Exception):
    """An operation stopped without silently discarding work."""


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def branch_name(slug):
    name = slug if slug.startswith("rwgk/") else "rwgk/" + slug
    if not re.fullmatch(r"rwgk/[a-z0-9][a-z0-9_-]{0,99}", name):
        raise WTError("Use rwgk/<slug>: 1–100 lowercase letters, digits, '-' or '_'.")
    if name == "rwgk/archive":
        raise WTError("The archive namespace is reserved.")
    return name


def directory_name(branch):
    """One deterministic mapping; legacy names never collide with new names."""
    if re.fullmatch(r"rwgk/[a-z0-9][a-z0-9_-]{0,99}", branch):
        return branch.replace("/", "-")
    if not branch or "\x00" in branch or "\n" in branch:
        raise WTError("Invalid branch name.")
    slug = re.sub(r"[^a-z0-9_-]+", "-", branch.lower()).strip("-")[:70]
    digest = hashlib.sha256(branch.encode()).hexdigest()[:16]
    return f"legacy-{slug or 'branch'}--{digest}"


def github_repository(url):
    if "://" in url:
        parsed = urlsplit(url)
        if parsed.hostname != "github.com":
            return ""
        name = parsed.path.strip("/").removesuffix(".git")
    else:
        match = re.fullmatch(r"(?:git@)?github\.com:(.+)", url)
        if not match:
            return ""
        name = match[1].removesuffix(".git")
    return name if re.fullmatch(r"[^/\s]+/[^/\s]+", name) else ""


def run_command(argv, *, cwd=None, check=True, input=None):
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("GIT_") and key not in ("GIT_SSH_COMMAND", "GIT_SSH"):
            env.pop(key)
    env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GH_PAGER="cat")
    try:
        result = subprocess.run(
            list(map(str, argv)),
            cwd=cwd,
            env=env,
            input=input,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise WTError(f"Cannot run {argv[0]}: {error}") from error
    if check and result.returncode:
        message = (result.stderr or result.stdout or f"{argv[0]} failed").strip()
        message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", message)
        raise WTError(message)
    return result


class Repo:
    def __init__(self, cwd=None, *, initialized=True):
        cwd = Path(cwd or os.getcwd()).resolve()
        self.root = Path(
            run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd).stdout.strip()
        ).resolve()
        self.common = Path(
            self.git("rev-parse", "--path-format=absolute", "--git-common-dir")
        ).resolve()
        self.state_path = self.common / "rwgk-wt.json"
        self.state = {"version": 1, "config": {}, "branches": {}}
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
                if (
                    self.state.get("version") != 1
                    or not isinstance(self.state.get("config"), dict)
                    or not isinstance(self.state.get("branches"), dict)
                ):
                    raise ValueError("unsupported or malformed catalog")
            except (OSError, ValueError) as error:
                raise WTError(f"Cannot read {self.state_path}: {error}") from error
        elif initialized:
            raise WTError("Repository is not registered. Run 'wt init' first.")
        self.config = self.state["config"]
        self.branches = self.state["branches"]

    @property
    def primary(self):
        return Path(self.config["primary"])

    def run(self, argv, *, cwd=None, check=True, input=None):
        return run_command(
            argv,
            cwd=cwd or (self.root if self.root.is_dir() else self.primary),
            check=check,
            input=input,
        )

    def git(self, *args, cwd=None, check=True, input=None):
        return self.run(
            ["git", *args], cwd=cwd, check=check, input=input
        ).stdout.strip()

    def save(self):
        atomic_json(self.state_path, self.state)

    @contextlib.contextmanager
    def locked(self):
        with (self.common / "rwgk-wt.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WTError(
                    "Another wt command is changing this repository."
                ) from error
            # Reload after locking: constructors may have raced with a writer.
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text())
                self.config = self.state["config"]
                self.branches = self.state["branches"]
            yield

    def oid(self, ref):
        return self.git("rev-parse", "--verify", f"{ref}^{{commit}}")

    def exists(self, ref):
        return (
            self.run(
                ["git", "show-ref", "--verify", "--quiet", ref], check=False
            ).returncode
            == 0
        )

    def current_branch(self):
        return (
            self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None
        )

    def worktrees(self):
        raw = self.run(["git", "worktree", "list", "--porcelain", "-z"]).stdout
        records = []
        for block in raw.split("\x00\x00"):
            if not block.strip("\x00"):
                continue
            fields = {}
            for line in block.strip("\x00").split("\x00"):
                key, _, value = line.partition(" ")
                fields[key] = value
            if "worktree" in fields:
                records.append(
                    {
                        "path": fields["worktree"],
                        "branch": fields.get("branch", "").removeprefix("refs/heads/")
                        or None,
                        "head": fields.get("HEAD", ""),
                        "locked": "locked" in fields,
                        "prunable": "prunable" in fields,
                    }
                )
        return records

    def owner(self, branch):
        owners = [
            Path(row["path"]).resolve()
            for row in self.worktrees()
            if row["branch"] == branch
        ]
        if len(owners) > 1:
            raise WTError(f"Branch {branch} has multiple owning worktrees.")
        return owners[0] if owners else None

    def ensure_idle(self, path=None):
        if self.state.get("submodule_rebase") and not getattr(
            self, "_wt_submodule_rebase_active", False
        ):
            raise WTError(
                "A restack has pending submodule work; finish or abort it with "
                "'wt stack rebase' before another lifecycle operation."
            )
        for name in (
            "gh-stack-rebase-state",
            "gh-stack-modify-state",
            "gh-stack-adopt-state",
            "gh-stack-migration",
        ):
            if (self.common / name).exists():
                raise WTError(f"Finish or abort the pending operation: {name}.")
        path = path or self.root
        for name in (
            "rebase-merge",
            "rebase-apply",
            "sequencer",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "BISECT_LOG",
        ):
            location = Path(
                self.git(
                    "rev-parse", "--path-format=absolute", "--git-path", name, cwd=path
                )
            )
            if location.exists():
                raise WTError(f"Unfinished Git operation in {path}: {name}.")

    def ensure_clean(self, path=None):
        path = path or self.root
        self.ensure_idle(path)
        status = self.git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
            cwd=path,
        )
        if status:
            raise WTError(
                f"Worktree has uncommitted or untracked files: {path}\n{status}"
            )
        flags = self.git("ls-files", "-v", cwd=path)
        if any(
            line and (line[0].islower() or line[0] == "S")
            for line in flags.splitlines()
        ):
            raise WTError(
                f"Clear assume-unchanged/skip-worktree flags in {path} first."
            )
        from . import submodules

        submodules.inspect(self, path)

    def check_primary(self):
        if not self.primary.is_dir():
            raise WTError(f"Primary worktree is missing: {self.primary}")
        actual = self.git(
            "symbolic-ref", "--quiet", "--short", "HEAD", cwd=self.primary, check=False
        )
        if (
            actual != self.config["trunk"]
            or self.owner(actual) != self.primary.resolve()
        ):
            raise WTError(
                f"Primary worktree must own {self.config['trunk']}: {self.primary}"
            )

    def resolve(self, selector=None):
        selector = selector or self.current_branch()
        if selector in self.branches:
            return selector
        if selector and "rwgk/" + selector in self.branches:
            return "rwgk/" + selector
        if selector and re.fullmatch(r"pr[1-9][0-9]*", selector):
            candidates = [
                name
                for name, row in self.branches.items()
                if row.get("pr", {}).get("number") == int(selector[2:])
                and row["pr"].get("base_repo", "").lower()
                == self.config["upstream_repo"].lower()
            ]
            if len(candidates) == 1:
                return candidates[0]
        raise WTError(f"No managed branch matches {selector!r}. Use 'wt list'.")

    def canonical_path(self, branch):
        return (
            Path(self.config["trees_root"])
            / self.config["key"]
            / directory_name(branch)
        )

    def create(self, branch, base, role="owned", extra=None):
        self.check_primary()
        self.ensure_idle()
        if self.run(
            ["git", "check-ref-format", "refs/heads/" + branch], check=False
        ).returncode:
            raise WTError(f"Invalid branch: {branch}")
        path = self.canonical_path(branch)
        owner = self.owner(branch)
        if owner:
            if (
                branch in self.branches
                and owner == Path(self.branches[branch]["path"]).resolve()
            ):
                if self.branches[branch].get("creating"):
                    from . import submodules

                    submodules.recover(self, owner)
                    self.ensure_clean(owner)
                    submodules.initialize(self, owner)
                    self.branches[branch].pop("creating", None)
                    self.save()
                return owner
            raise WTError(
                f"Branch already has a worktree at {owner}; adopt it deliberately."
            )
        if (
            path.exists()
            or path.is_symlink()
            or any(
                Path(row["path"]).absolute() == path.absolute()
                for row in self.worktrees()
            )
        ):
            raise WTError(
                f"Worktree destination already exists or is registered: {path}"
            )
        if branch in self.branches:
            raise WTError(
                f"Managed branch has a missing owner; inspect 'wt doctor': {branch}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"path": str(path), "role": role, **(extra or {})}
        self.branches[branch] = {**record, "creating": True}
        self.save()
        args = ["worktree", "add"]
        if not self.exists("refs/heads/" + branch):
            args += ["--no-track", "-b", branch, str(path), self.oid(base)]
        else:
            args += [str(path), branch]
        try:
            self.git("-c", "submodule.recurse=false", *args)
            if self.owner(branch) != path.resolve():
                raise WTError(f"Creation incomplete; inspect {path} and 'wt doctor'.")
            from . import submodules

            submodules.initialize(self, path)
        except Exception:
            if not self.owner(branch) and not path.exists():
                self.branches.pop(branch, None)
                self.save()
            raise
        self.branches[branch] = record
        self.save()
        return path

    def alias(self, branch, pr):
        if pr["base_repo"].lower() != self.config["upstream_repo"].lower():
            raise WTError("PR belongs to a different base repository.")
        target = self.owner(branch)
        if target is None:
            raise WTError(f"Branch has no owning worktree: {branch}")
        alias = self.canonical_path(branch).parent / f"pr{pr['number']}"
        if alias.is_symlink():
            if alias.resolve() != target:
                raise WTError(f"PR alias points elsewhere: {alias}")
        elif alias.exists():
            raise WTError(f"PR alias collides with an existing directory: {alias}")
        else:
            alias.symlink_to(
                os.path.relpath(target, alias.parent), target_is_directory=True
            )
        self.branches[branch]["pr"] = pr
        self.save()
        return alias

    def initialize(self, **options):
        if self.config:
            if any(value is not None for value in options.values()):
                raise WTError(
                    "Repository is already registered; edit its explicit configuration deliberately."
                )
            return self.config
        trees = self.worktrees()
        primary = Path(trees[0]["path"]).resolve()
        remotes = self.git("remote").splitlines()
        upstream = options.get("upstream") or (
            "upstream" if "upstream" in remotes else "origin"
        )
        url = (
            self.git("remote", "get-url", upstream, check=False)
            if upstream in remotes
            else ""
        )
        repository = options.get("repo") or github_repository(url)
        defaults_path = Path(
            os.environ.get(
                "RWGK_WT_DEFAULTS",
                str(Path.home() / "rwgk_config_nvidia/git_stuff/wt-defaults.json"),
            )
        )
        defaults = {}
        if defaults_path.exists():
            defaults = (
                json.loads(defaults_path.read_text())
                .get("repositories", {})
                .get(repository, {})
            )
        trunk = options.get("trunk") or defaults.get("trunk")
        if not trunk:
            for candidate in ("main", "master", "ctk-next"):
                if self.exists("refs/heads/" + candidate):
                    trunk = candidate
                    break
        if not trunk:
            raise WTError("Cannot infer trunk. Supply 'wt init --trunk BRANCH'.")
        actual = self.git(
            "symbolic-ref", "--quiet", "--short", "HEAD", cwd=primary, check=False
        )
        if actual != trunk:
            raise WTError(
                f"Primary is on {actual or 'detached HEAD'}, expected {trunk}. No checkout was changed."
            )
        key = (
            options.get("key")
            or defaults.get("key")
            or (repository.rsplit("/", 1)[-1] if repository else primary.name)
        )
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", key):
            raise WTError(
                "Repository key must contain only letters, digits, '-' and '_'."
            )
        self.config.update(
            key=key,
            primary=str(primary),
            trunk=trunk,
            upstream_remote=upstream,
            upstream_repo=repository,
            publish_remote=options.get("publish")
            or defaults.get("publish_remote", "origin"),
            archive_remote=options.get("archive") or "origin",
            trees_root=str(
                Path(
                    options.get("trees")
                    or str(
                        Path(os.environ.get("W", str(Path.home() / "wrk"))) / "trees"
                    )
                )
                .expanduser()
                .absolute()
            ),
            backtracking=str(
                Path(
                    options.get("backtracking")
                    or str(Path.home() / "Sve/git_backtracking_info")
                )
                .expanduser()
                .absolute()
            ),
        )
        self.save()
        return self.config

    def trunk_update(self):
        from . import submodules

        self.check_primary()
        submodules.recover(self, self.primary)
        self.ensure_clean(self.primary)
        remote = self.config["upstream_remote"]
        if self.config["upstream_repo"]:
            from .github import remote_identity

            if (
                remote_identity(self, remote).lower()
                != self.config["upstream_repo"].lower()
            ):
                raise WTError(
                    "Upstream endpoint no longer matches the registered repository."
                )
        trunk = self.config["trunk"]
        self.git(
            "fetch",
            "--recurse-submodules=no",
            "--no-tags",
            remote,
            f"+refs/heads/{trunk}:refs/remotes/{remote}/{trunk}",
            cwd=self.primary,
        )
        target = f"refs/remotes/{remote}/{trunk}"
        submodules.validate_update(self, self.primary, target)
        self.git(
            "-c",
            "submodule.recurse=false",
            "merge",
            "--ff-only",
            target,
            cwd=self.primary,
        )
        submodules.initialize(self, self.primary)
        return {
            "trunk": trunk,
            "path": str(self.primary),
            "oid": self.oid("refs/heads/" + trunk),
        }

    def listing(self):
        rows = []
        for name, entry in self.branches.items():
            row = {"branch": name, **entry, "owner": str(self.owner(name) or "")}
            row["oid"] = (
                self.oid("refs/heads/" + name)
                if self.exists("refs/heads/" + name)
                else None
            )
            rows.append(row)
        return {"config": self.config, "branches": rows}

    def doctor(self):
        issues = []
        try:
            self.check_primary()
            self.ensure_idle()
        except WTError as error:
            issues.append(str(error))
        owners = {row["branch"]: row for row in self.worktrees() if row["branch"]}
        for name, entry in self.branches.items():
            owner = owners.get(name)
            if (
                not owner
                or Path(owner["path"]).resolve() != Path(entry["path"]).resolve()
                or not Path(entry["path"]).is_dir()
            ):
                issues.append(f"Missing or mismatched owner: {name} ({entry['path']})")
            if entry.get("creating"):
                issues.append(f"Incomplete creation: {name}")
            if (
                owner
                and Path(owner["path"]).resolve() != self.canonical_path(name).resolve()
            ):
                issues.append(f"Noncanonical path: {name} ({owner['path']})")
            if entry.get("pr"):
                alias = self.canonical_path(name).parent / f"pr{entry['pr']['number']}"
                if (
                    not alias.is_symlink()
                    or alias.resolve() != Path(entry["path"]).resolve()
                ):
                    issues.append(f"Missing or mismatched PR alias: {alias}")
        legacy = [
            name
            for name in self.git(
                "for-each-ref", "--format=%(refname:short)", "refs/heads/"
            ).splitlines()
            if name != self.config["trunk"] and name not in self.branches
        ]
        detached = [row["path"] for row in self.worktrees() if not row["branch"]]
        journals = list((self.common / "rwgk-wt/operations").glob("*.json"))
        for path in journals:
            try:
                state = json.loads(path.read_text())
                if state.get("phase") not in ("complete", "completed") and state.get(
                    "status"
                ) not in ("complete", "completed"):
                    issues.append(f"Review lifecycle journal: {path}")
            except (ValueError, OSError):
                issues.append(f"Unreadable lifecycle journal: {path}")
        return {
            "ok": not issues,
            "issues": issues,
            "legacy_branches": legacy,
            "detached_worktrees": detached,
            "config": self.config,
        }
