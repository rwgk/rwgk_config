"""Pinned submodule checkouts with retained local reference-object stores."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .core import WTError, atomic_json

_RETAINED = "refs/rwgk/wt/"
_OPERATIONS = (
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
)


def _run(repo, path, *args, check=True, input=None):
    return repo.run(
        ["git", "-c", "submodule.recurse=false", *map(str, args)],
        cwd=Path(path),
        check=check,
        input=input,
    )


def _git(repo, path, *args, check=True, input=None):
    return _run(repo, path, *args, check=check, input=input).stdout.strip()


def _head(repo, path):
    return _git(repo, path, "rev-parse", "--verify", "HEAD^{commit}")


def _gitdir(repo, path):
    return Path(
        _git(repo, path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )


def _path(root, value):
    relative = Path(value)
    if (
        not value
        or not relative.parts
        or relative.is_absolute()
        or any(c in value for c in "\0\r\n")
        or any(part in (".", "..") or part.lower() == ".git" for part in relative.parts)
    ):
        raise WTError(f"Unsafe submodule path: {value!r}")
    current = Path(root)
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise WTError(f"Submodule path is a symlink: {current}")
    return current


def _initialized(repo, path):
    if not path.is_dir() or not (path / ".git").exists():
        return False
    result = _run(repo, path, "rev-parse", "--show-toplevel", check=False)
    return (
        result.returncode == 0
        and Path(result.stdout.strip()).resolve() == path.resolve()
    )


def _links(repo, path, revision=None):
    modules = path / ".gitmodules"
    if modules.is_symlink():
        raise WTError(f"Refusing a symlinked .gitmodules: {modules}")
    if revision is None:
        raw = _git(repo, path, "ls-files", "--stage", "-z")
    else:
        raw = _git(repo, path, "ls-tree", "-r", "-z", revision)
    entries = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        fields, separator, name = entry.partition("\t")
        parts = fields.split()
        if not separator or not parts or parts[0] != "160000":
            continue
        _path(path, name)
        if revision is None:
            if len(parts) != 3 or parts[2] != "0":
                raise WTError(f"Unmerged submodule entry: {path / name}")
            oid = parts[1]
        else:
            oid = parts[2]
        entries.append((name, oid))
    return entries


def _clean(repo, path):
    for operation in _OPERATIONS:
        marker = Path(
            _git(
                repo,
                path,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                operation,
            )
        )
        if marker.exists():
            raise WTError(f"Unfinished Git operation in submodule {path}: {operation}")
    status = _git(
        repo,
        path,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=all",
    )
    if status:
        raise WTError(f"Submodule has uncommitted or untracked files: {path}\n{status}")
    flags = _git(repo, path, "ls-files", "-v", "-z")
    if any(row and (row[0].islower() or row[0] == "S") for row in flags.split("\0")):
        raise WTError(
            f"Clear assume-unchanged/skip-worktree flags in submodule {path} first"
        )


def _refs(repo, path, include_retained=False):
    raw = _git(repo, path, "for-each-ref", "--format=%(refname)%00%(objectname)")
    rows = []
    for line in raw.splitlines():
        name, separator, oid = line.partition("\0")
        if separator and (include_retained or not name.startswith(_RETAINED)):
            rows.append({"ref": name, "oid": oid})
    return rows


def _walk(repo, root, path, *, strict):
    for name, expected in _links(repo, path):
        child = _path(path, name)
        if not _initialized(repo, child):
            if child.exists() and (not child.is_dir() or any(child.iterdir())):
                raise WTError(f"Uninitialized submodule contains local files: {child}")
            continue
        _clean(repo, child)
        oid = _head(repo, child)
        if strict and oid != expected:
            raise WTError(
                f"Submodule HEAD differs from its recorded gitlink: {child}; preserve local commits first"
            )
        url = (
            _git(repo, child, "config", "--get", "remote.origin.url", check=False)
            or None
        )
        if url:
            url = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", url)
        yield {
            "path": child.relative_to(root).as_posix(),
            "oid": oid,
            "gitlink": expected,
            "gitdir": str(_gitdir(repo, child)),
            "refs": _refs(repo, child),
            "url": url,
        }
        yield from _walk(repo, root, child, strict=strict)


def inspect(repo, path):
    """Check every initialized module, including hidden state, without modifying it."""
    root = Path(path).resolve()
    return list(_walk(repo, root, root, strict=True))


def _cache(repo, relative):
    digest = hashlib.sha256(relative.encode()).hexdigest()
    return repo.common / "rwgk-wt" / "submodules" / f"{digest}.git"


def _alternates(repo, location):
    objects = _gitdir(repo, location) / "objects"
    file = objects / "info" / "alternates"
    if not file.exists():
        return []
    return [
        (objects / line).resolve() for line in file.read_text().splitlines() if line
    ]


def _independent(repo, location):
    if _alternates(repo, location):
        raise WTError(
            f"Reference submodule itself borrows objects: {location}; use an independent reference store"
        )


def _create_pins(repo, location, prefix, oids):
    existing = {row["ref"]: row["oid"] for row in _refs(repo, location, True)}
    commands = []
    for oid in sorted(set(oids)):
        ref = prefix + oid
        if ref in existing:
            if existing[ref] != oid:
                raise WTError(f"Submodule retention ref unexpectedly changed: {ref}")
        else:
            commands.append(f"create {ref} {oid}")
    if commands:
        _git(
            repo,
            location,
            "update-ref",
            "--stdin",
            input="start\n" + "\n".join(commands) + "\nprepare\ncommit\n",
        )


def _pin_reference(repo, location):
    _independent(repo, location)
    roots = [row["oid"] for row in _refs(repo, location, True)]
    head = _run(repo, location, "rev-parse", "--verify", "HEAD^{commit}", check=False)
    if head.returncode == 0:
        roots.append(head.stdout.strip())
    _create_pins(repo, location, _RETAINED + "reference/", roots)


def _has(repo, location, oid):
    return _run(repo, location, "cat-file", "-e", oid, check=False).returncode == 0


def _lender(repo, root, relative, expected):
    primary = _path(repo.primary, relative)
    cache = _cache(repo, relative)
    usable_primary = primary.resolve() != (root / relative).resolve() and _initialized(
        repo, primary
    )
    if (
        cache.is_dir()
        and (not usable_primary or not _has(repo, primary, expected))
        and _has(repo, cache, expected)
    ):
        _independent(repo, cache)
        return cache
    if usable_primary:
        _independent(repo, primary)
        return primary
    if cache.is_dir():
        _independent(repo, cache)
        return cache
    return None


def _protect_borrowed(repo, root, relative):
    module = _path(root, relative)
    primary = _path(repo.primary, relative)
    locations = [_cache(repo, relative)]
    if _initialized(repo, primary):
        locations.append(primary)
    approved = {_gitdir(repo, p).resolve(): p for p in locations if p.is_dir()}
    for objects in _alternates(repo, module):
        location = approved.get(objects.parent)
        if location is None:
            raise WTError(
                f"Submodule borrows from an unmanaged object store: {module}; inspect {objects}"
            )
        _pin_reference(repo, location)


def _destination(repo, relative, source):
    primary = _path(repo.primary, relative)
    if _initialized(repo, primary):
        _independent(repo, primary)
        return _gitdir(repo, primary)
    cache = _cache(repo, relative)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        object_format = _git(repo, source, "rev-parse", "--show-object-format")
        _git(
            repo,
            repo.primary,
            "init",
            "--bare",
            f"--object-format={object_format}",
            str(cache),
        )
    _independent(repo, cache)
    return cache


def preserve(repo, path, record_id, *, allow_mismatch=False):
    """Retain every module ref and HEAD outside the removable worktree gitdir."""
    root = Path(path).resolve()
    snapshots = list(_walk(repo, root, root, strict=not allow_mismatch))
    record = hashlib.sha256(str(record_id).encode()).hexdigest()[:24]
    for row in snapshots:
        source = _path(root, row["path"])
        destination = _destination(repo, row["path"], source)
        prefix = f"{_RETAINED}recovery/{record}/"
        oids = sorted({row["oid"], *(ref["oid"] for ref in row["refs"])})
        missing = [oid for oid in oids if not _has(repo, destination, oid)]
        if missing:
            for start in range(0, len(missing), 100):
                _git(
                    repo,
                    destination,
                    "fetch",
                    "--no-tags",
                    "--no-write-fetch-head",
                    "--",
                    row["gitdir"],
                    *(f"{oid}:{prefix}{oid}" for oid in missing[start : start + 100]),
                )
        _create_pins(repo, destination, prefix, oids)
        if _head(repo, source) != row["oid"] or _refs(repo, source) != row["refs"]:
            raise WTError(f"Submodule refs changed while preserving them: {source}")
        row["recovery_gitdir"] = str(destination)
        row["recovery_ref"] = prefix + row["oid"]
        row["recovery_refs"] = [
            dict(ref, recovery_ref=prefix + ref["oid"]) for ref in row["refs"]
        ]
    return snapshots


def validate_update(repo, path, target_oid):
    """Do not implicitly discard initialized modules removed by a parent update."""
    root = Path(path).resolve()
    target = dict(_links(repo, root, target_oid))
    for name, _ in _links(repo, root):
        if _initialized(repo, _path(root, name)) and name not in target:
            raise WTError(
                f"Parent update removes or replaces initialized submodule {root / name}; retire it deliberately first"
            )


def _pending(repo, root):
    return Path(
        _git(
            repo,
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "rwgk-wt-submodules-pending.json",
        )
    )


def _check_collisions(repo, path, old, target):
    if old == target:
        return
    changed = _git(
        repo,
        path,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMT",
        "-r",
        "-z",
        old,
        target,
    ).split("\0")
    ignored = _git(
        repo,
        path,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "-z",
    ).split("\0")
    for artifact in filter(None, ignored):
        candidate = Path(artifact)
        for name in filter(None, changed):
            changed_path = Path(name)
            if (
                candidate == changed_path
                or candidate in changed_path.parents
                or changed_path in candidate.parents
            ):
                raise WTError(
                    f"Submodule update would overwrite ignored files: {path / artifact}"
                )


def _initialize(repo, root, journal):
    pending = _pending(repo, root)
    if not journal:
        current = list(_walk(repo, root, root, strict=False))
        journal = {
            "superproject_oid": _head(repo, root),
            "modules": {
                row["path"]: {"old": row["oid"], "target": row["gitlink"]}
                for row in current
            },
        }
        atomic_json(pending, journal)
    preserve(
        repo, root, "initialize-" + journal["superproject_oid"], allow_mismatch=True
    )

    def visit(parent):
        for name, target in _links(repo, parent):
            child = _path(parent, name)
            relative = child.relative_to(root).as_posix()
            existing = _initialized(repo, child)
            old = _head(repo, child) if existing else None
            state = journal["modules"].setdefault(
                relative, {"old": old, "target": target}
            )
            state["target"] = target
            atomic_json(pending, journal)
            if existing:
                _clean(repo, child)
                _protect_borrowed(repo, root, relative)
            lender = _lender(repo, root, relative, target) if not existing else None
            if lender is not None:
                _pin_reference(repo, lender)
            # Fetch first so collision/removal checks inspect the actual target tree.
            if existing and not _has(repo, child, target):
                _git(repo, child, "fetch", "--no-tags", "origin", target)
            if existing:
                validate_update(repo, child, target)
                _check_collisions(repo, child, old, target)
            args = ["submodule", "update", "--init", "--checkout"]
            if lender is not None:
                args.extend(["--reference", str(lender)])
            try:
                _git(repo, parent, *args, "--", f":(literal){name}")
            except WTError:
                # A clone may succeed before checkout/fetch fails. Record its
                # actual initial head now, before any later user intervention.
                if not existing and _initialized(repo, child):
                    state["old"] = _head(repo, child)
                    atomic_json(pending, journal)
                raise
            if not _initialized(repo, child) or _head(repo, child) != target:
                raise WTError(f"Submodule did not reach its recorded commit: {child}")
            visit(child)

    visit(root)
    pending.unlink()


def initialize(repo, path):
    """Initialize/update recursive gitlinks; caller preflights before parent changes."""
    root = Path(path).resolve()
    if _pending(repo, root).exists():
        return recover(repo, root)
    if not _links(repo, root):
        return
    _initialize(repo, root, None)


def recover(repo, path):
    """Retry only an interrupted update whose recorded heads remain recognizable."""
    root = Path(path).resolve()
    pending = _pending(repo, root)
    if not pending.exists():
        return False
    try:
        journal = json.loads(pending.read_text())
        if not isinstance(journal, dict) or not isinstance(
            journal.get("modules"), dict
        ):
            raise TypeError("invalid journal schema")
        if _head(repo, root) != journal["superproject_oid"]:
            raise WTError(
                "Superproject HEAD changed after interrupted submodule initialization; inspect its pending journal"
            )
        current = list(_walk(repo, root, root, strict=False))
        for row in current:
            expected = journal["modules"].get(row["path"])
            if expected is None or row["oid"] not in (
                expected["old"],
                expected["target"],
            ):
                raise WTError(
                    f"Submodule HEAD changed after interrupted initialization: {root / row['path']}"
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise WTError(
            f"Cannot validate pending submodule initialization: {error}"
        ) from error
    _initialize(repo, root, journal)
    return True


def restore(repo, path, snapshots):
    """Recreate recovered local module refs after creating the parent pair."""
    root = Path(path).resolve()
    initialize(repo, root)
    if not snapshots:
        return
    baseline = {
        row["path"]: {ref["ref"]: ref["oid"] for ref in row["refs"]}
        for row in preserve(repo, root, "restore-before-" + _head(repo, root))
    }
    for row in snapshots:
        module = _path(root, row["path"])
        if not _initialized(repo, module) or _head(repo, module) != row["oid"]:
            raise WTError(
                f"Restored submodule does not match its recovery record: {module}"
            )
        local_refs = [
            ref
            for ref in row.get("recovery_refs", [])
            if ref["ref"].startswith(("refs/heads/", "refs/tags/"))
        ]
        if local_refs:
            _git(repo, module, "checkout", "--detach", "HEAD")
        for ref in local_refs:
            if not _has(repo, module, ref["oid"]):
                _git(
                    repo,
                    module,
                    "fetch",
                    "--no-tags",
                    "--no-write-fetch-head",
                    "--",
                    row["recovery_gitdir"],
                    ref["recovery_ref"],
                )
            current = _git(
                repo, module, "show-ref", "--verify", "--hash", ref["ref"], check=False
            )
            expected = baseline[row["path"]].get(ref["ref"], "")
            if current != expected:
                raise WTError(
                    f"Submodule ref changed during restoration: {module}: {ref['ref']}; preserving it"
                )
            _git(
                repo,
                module,
                "update-ref",
                ref["ref"],
                ref["oid"],
                expected or "0" * len(ref["oid"]),
            )
