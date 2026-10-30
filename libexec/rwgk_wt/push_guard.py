"""Protect managed worktrees from accidental raw Git pushes, preserving hooks.

This is a workflow guard, not a security boundary: deliberate recovery can set
RWGK_WT_ALLOW_RAW_PUSH=1 for one command while retaining the original hooks.
"""

from __future__ import annotations

import argparse
import contextvars
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import WTError

PERMIT_ENV = "RWGK_WT_PUSH_PERMIT"
RECOVERY_ENV = "RWGK_WT_ALLOW_RAW_PUSH"
MARKER = "# rwgk-wt-push-guard v1"
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PERMIT = contextvars.ContextVar("rwgk_wt_push_permit", default=None)


def _is_push(argv):
    values = list(map(str, argv))
    if values and Path(values[0]).name == "env":
        values.pop(0)
        while values and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", values[0]):
            values.pop(0)
    if not values or Path(values.pop(0)).name != "git":
        return False
    while values and values[0].startswith("-"):
        option = values.pop(0)
        if option in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            if not values:
                return False
            values.pop(0)
        elif option.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
            continue
        else:
            return False
    return bool(values and values[0] == "push")


def subprocess_env(argv, env):
    """Inject only into the exact push child, after fixture/transport adapters."""
    env = dict(env)
    env.pop(PERMIT_ENV, None)
    permit = _PERMIT.get()
    if permit is not None and _is_push(argv):
        env[PERMIT_ENV] = permit
    return env


def run(repo, argv, *, destination, updates, cwd=None, check=True):
    """Run one preflighted push with exact ref/old/new permissions and no cache."""
    argv = list(map(str, argv))
    if not _is_push(argv) or "--no-verify" in argv:
        raise WTError("The wt push permit may only wrap a Git push that runs its hooks")
    refs = {}
    for item in updates:
        ref, old, new = item.get("ref"), item.get("old"), item.get("new")
        if (
            not isinstance(ref, str)
            or not ref.startswith(("refs/heads/", "refs/tags/"))
            or any(character.isspace() for character in ref)
            or ref in refs
            or any(
                value is not None
                and (not isinstance(value, str) or not SHA.fullmatch(value))
                for value in (old, new)
            )
        ):
            raise WTError("Invalid exact-ref wt push permit")
        refs[ref] = {"old": old, "new": new}
    if not refs:
        raise WTError("A wt push permit requires at least one validated ref update")
    payload = {
        "version": 1,
        "common": str(Path(repo.common).resolve()),
        # Callers already verify the endpoint. This hash avoids storing URLs or
        # credentials and does not interfere with synthetic transport adapters.
        "destination_hash": hashlib.sha256(str(destination).encode()).hexdigest(),
        "refs": refs,
    }
    path = token = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="wt-push-permit-", delete=False
        ) as stream:
            path = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream)
        token = _PERMIT.set(str(path))
        return repo.run(argv, cwd=cwd, check=check)
    finally:
        if token is not None:
            _PERMIT.reset(token)
        if path is not None:
            path.unlink(missing_ok=True)


def _git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise WTError(
            "Cannot inspect this checkout for the wt push guard; run 'wt doctor'"
        )
    return result.stdout.strip()


def _context():
    common = Path(
        _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve()
    catalog = common / "rwgk-wt.json"
    if not catalog.exists():
        return common, None, None
    try:
        data = json.loads(catalog.read_text())
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("config"), dict)
            or not isinstance(data.get("branches"), dict)
        ):
            raise TypeError("invalid catalog")
    except (OSError, ValueError, TypeError) as error:
        raise WTError(
            "Cannot read the wt catalog; run 'wt doctor' before pushing"
        ) from error
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise WTError("Cannot inspect the current branch for the wt push guard")
    return common, data, result.stdout.strip().removeprefix("refs/heads/") or None


def _rows(raw):
    rows = []
    try:
        for line in raw.decode("utf-8", "surrogateescape").splitlines():
            parts = line.split()
            if (
                len(parts) != 4
                or not SHA.fullmatch(parts[1])
                or not SHA.fullmatch(parts[3])
            ):
                raise ValueError("invalid hook input")
            rows.append(parts)
    except (ValueError, UnicodeError) as error:
        raise WTError(
            "Git supplied malformed pre-push input; no push was allowed"
        ) from error
    return rows


def _managed(data, current, rows):
    if data is None:
        return False
    trunk = data["config"].get("trunk")

    def managed(branch):
        metadata = data["branches"].get(branch)
        return (
            branch != trunk
            and isinstance(metadata, dict)
            and metadata.get("role") in ("owned", "review")
        )

    return managed(current) or any(
        local.startswith("refs/heads/") and managed(local.removeprefix("refs/heads/"))
        for local, _, _, _ in rows
    )


def _matches(actual, expected):
    return (
        actual == expected
        if expected is not None
        else bool(actual and set(actual) == {"0"})
    )


def _permitted(common, rows):
    filename = os.environ.get(PERMIT_ENV)
    if not filename:
        return False
    try:
        path = Path(filename)
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            return False
        data = json.loads(path.read_text())
        if (
            data.get("version") != 1
            or Path(data["common"]).resolve() != common
            or not isinstance(data.get("refs"), dict)
        ):
            return False
        seen = set()
        for _, new, remote, old in rows:
            expected = data["refs"].get(remote)
            if (
                remote in seen
                or not isinstance(expected, dict)
                or not _matches(new, expected.get("new"))
                or not _matches(old, expected.get("old"))
            ):
                return False
            seen.add(remote)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def check(raw):
    common, data, current = _context()
    rows = _rows(raw)
    if not _managed(data, current, rows):
        return
    if os.environ.get(RECOVERY_ENV) == "1":
        return
    if os.environ.get(PERMIT_ENV):
        if _permitted(common, rows):
            return
        raise WTError(
            "The wt push was blocked because its exact-ref permit does not match "
            "Git's current updates. Inspect concurrent remote changes or hook inputs "
            "before retrying; no lease was refreshed."
        )
    raise WTError(
        "Raw git push is blocked for this wt-managed branch. Use 'wt push' to update "
        "the associated PR source, or 'wt publish' for owned PR creation/history changes "
        "and 'wt publish --stack' for a stack. For deliberate raw-Git recovery only, "
        "run one command with RWGK_WT_ALLOW_RAW_PUSH=1; the original hooks still run."
    )


def _tracked_hook(path):
    result = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return False
    root = Path(result.stdout.strip()).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", str(relative)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def install(repo):
    """Wrap the active hook without replacing core.hooksPath or maintained files."""
    # Resolve the directory only: rev-parse of hooks/pre-push dereferences an
    # existing final symlink, which would overwrite its maintained target.
    directory = Path(
        repo.git("rev-parse", "--path-format=absolute", "--git-path", "hooks")
    )
    hook = directory.resolve() / "pre-push"
    original = hook.with_name("pre-push.rwgk-original")
    launcher = Path(__file__).resolve().parents[2] / "bin" / "git-wt-push-guard"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise WTError(f"Missing executable wt push-guard launcher: {launcher}")
    if _tracked_hook(hook):
        raise WTError(
            f"Pre-push hook {hook} is tracked source; wt will not replace it. "
            "Use an untracked hook directory containing a pre-push symlink to this "
            "maintained file, explicitly point local core.hooksPath there, then retry "
            "'wt init'. The existing hooks configuration was unchanged."
        )
    hook.parent.mkdir(parents=True, exist_ok=True)
    with hook.with_name("pre-push.rwgk-guard.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        present = hook.exists() or hook.is_symlink()
        wrapped = False
        if present:
            if not hook.is_file():
                raise WTError(
                    f"Pre-push hook {hook} is not a regular file or executable script"
                )
            try:
                wrapped = MARKER in hook.read_text().splitlines()[:3]
            except (OSError, UnicodeError):
                wrapped = False
        if not wrapped and (original.exists() or original.is_symlink()):
            raise WTError(
                f"Existing hook backup {original} needs inspection; wt did not replace it"
            )
        if wrapped:
            try:
                command = shlex.split(
                    next(
                        line
                        for line in hook.read_text().splitlines()
                        if line.startswith("exec ")
                    )
                )
                backup = command[command.index("--original") + 1]
            except (ValueError, IndexError, StopIteration) as error:
                raise WTError(
                    "Installed wt push guard changed; inspect the hook before reinstalling"
                ) from error
            if backup and (backup != str(original) or not original.exists()):
                raise WTError(
                    f"Saved original pre-push hook {original} is missing or changed; inspect it before continuing"
                )
            if not backup and (original.exists() or original.is_symlink()):
                raise WTError(
                    f"Unexpected original-hook backup {original}; inspect it before continuing"
                )
        else:
            backup = str(original) if present else ""
        content = (
            "#!/bin/sh\n"
            + MARKER
            + "\n"
            + "exec "
            + shlex.quote(str(launcher))
            + " --original "
            + shlex.quote(backup)
            + ' -- "$@"\n'
        )
        if wrapped and hook.read_text() == content and os.access(hook, os.X_OK):
            return {"result": "already-installed", "hook": str(hook)}
        moved = False
        try:
            if present and not wrapped:
                os.replace(hook, original)
                moved = True
            with tempfile.NamedTemporaryFile(
                mode="w", dir=hook.parent, prefix=".pre-push-wt-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o755)
            try:
                os.replace(temporary, hook)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            if moved and not hook.exists():
                os.replace(original, hook)
            raise
        return {"result": "installed", "hook": str(hook), "original": backup or None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", default="")
    parser.add_argument("remote")
    parser.add_argument("location")
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read()
    try:
        check(raw)
    except WTError as error:
        print(f"wt push guard: {error}", file=sys.stderr)
        return 1
    if args.original and not Path(args.original).exists():
        print(
            "wt push guard: saved original pre-push hook is missing; inspect the hook backup before pushing",
            file=sys.stderr,
        )
        return 1
    if args.original and os.access(args.original, os.X_OK):
        try:
            result = subprocess.run(
                [args.original, args.remote, args.location], input=raw, check=False
            )
        except OSError as error:
            print(
                f"wt push guard: original pre-push hook could not run: {error}",
                file=sys.stderr,
            )
            return 1
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    return 0
