"""One branch, one worktree: creation, PRs, stacks, publication and recovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import github, lifecycle, stacks
from .core import Repo, WTError, branch_name, directory_name


def parser():
    result = argparse.ArgumentParser(
        prog="git wt",
        description=__doc__,
        epilog="Use --json for structured results; --print-path for shell navigation.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="register the primary checkout without migrating existing work"
    )
    for option in (
        "key",
        "trunk",
        "upstream",
        "publish",
        "archive",
        "trees",
        "backtracking",
        "repo",
    ):
        init.add_argument("--" + option)
    new = commands.add_parser(
        "new", help="create a branch and owning worktree from trunk"
    )
    new.add_argument("slug")
    pr = commands.add_parser(
        "pr", help="open or resume a PR worktree and attach its alias"
    )
    pr.add_argument("selector")
    pr.add_argument(
        "--refresh",
        action="store_true",
        help="update a clean review checkout from its observed PR head",
    )
    for name in ("go", "path"):
        command = commands.add_parser(name, help="resolve an existing owning worktree")
        command.add_argument("selector")
    name = commands.add_parser(
        "name", help="map a branch to its canonical directory name"
    )
    name.add_argument("branch")
    commands.add_parser("list", help="list managed branch/worktree pairs")
    commands.add_parser("doctor", help="check managed pairs and report legacy work")
    trunk = commands.add_parser(
        "trunk", help="navigate to trunk or update it by fast-forward"
    )
    trunk.add_argument("action", nargs="?", choices=("update",))
    stack = commands.add_parser(
        "stack", help="manage dependencies without switching owning worktrees"
    )
    actions = stack.add_subparsers(dest="action", required=True)
    actions.add_parser("init", help="adopt the current managed branch into a stack")
    add = actions.add_parser(
        "add", help="create and register the next layer in its own worktree"
    )
    add.add_argument("slug")
    go = actions.add_parser("go", help="navigate to a layer's existing worktree")
    go.add_argument("target", choices=("up", "down", "top", "bottom"))
    pull = actions.add_parser(
        "pull", help="adopt GitHub's current stack state without rewriting or pushing"
    )
    pull.add_argument("--stack", type=int)
    pull.add_argument("--apply", action="store_true")
    rebase = actions.add_parser(
        "rebase", help="rewrite local layers in their owning worktrees"
    )
    group = rebase.add_mutually_exclusive_group()
    group.add_argument("--upstack", action="store_true")
    group.add_argument("--downstack", action="store_true")
    rebase.add_argument("--no-trunk", action="store_true")
    recovery = rebase.add_mutually_exclusive_group()
    recovery.add_argument("--continue", dest="continue_rebase", action="store_true")
    recovery.add_argument("--abort", action="store_true")
    publish = commands.add_parser(
        "publish",
        help="push exact refs and create/update PRs; stacks always use upstream",
    )
    publish.add_argument("--stack", action="store_true")
    publish.add_argument("--title")
    publish.add_argument("--body-file")
    publish.add_argument("--ready", action="store_true")
    publish.add_argument(
        "--set-upstream",
        action="store_true",
        help="explicitly approve tracking a preexisting force-push destination",
    )
    for name in ("clean", "drop"):
        command = commands.add_parser(
            name,
            help="preview retirement; --apply records recovery before removing the pair",
        )
        command.add_argument("selector", nargs="?")
        command.add_argument("--apply", action="store_true")
        command.add_argument("--archive-local", action="store_true")
        command.add_argument("--discard-builds", action="store_true")
        command.add_argument(
            "--resume",
            metavar="RECORD",
            help="resume an interrupted retirement (requires --apply)",
        )
        command.add_argument(
            "--local-only", action="store_true", required=name == "drop"
        )
    restore = commands.add_parser(
        "restore", help="recreate a pair from a backtracking record"
    )
    restore.add_argument("record")
    return result


def _dispatch(repo, args):
    command = args.command
    if command == "init":
        config = repo.initialize(
            **{
                key: getattr(args, key)
                for key in (
                    "key",
                    "trunk",
                    "upstream",
                    "publish",
                    "archive",
                    "trees",
                    "backtracking",
                    "repo",
                )
            }
        )
        return {"result": "registered", "config": config}, None
    if command == "new":
        path = repo.create(branch_name(args.slug), "refs/heads/" + repo.config["trunk"])
        return {"branch": branch_name(args.slug), "path": str(path)}, path
    if command == "pr":
        path = github.open_pr(repo, args.selector, refresh=args.refresh)
        return {"path": str(path)}, path
    if command in ("go", "path"):
        branch = repo.resolve(args.selector)
        path = repo.owner(branch)
        if (
            path is None
            or path != Path(repo.branches[branch]["path"]).resolve()
            or not path.is_dir()
        ):
            raise WTError(f"Missing owning worktree for {branch}; run 'wt doctor'.")
        return {"branch": branch, "path": str(path)}, path
    if command == "list":
        return repo.listing(), None
    if command == "doctor":
        return repo.doctor(), None
    if command == "trunk":
        repo.check_primary()
        if args.action == "update":
            return repo.trunk_update(), None
        return {"path": str(repo.primary), "branch": repo.config["trunk"]}, repo.primary
    if command == "stack":
        if args.action == "init":
            return stacks.initialize(repo), None
        if args.action == "add":
            path = stacks.add(repo, args.slug)
            return {"path": str(path)}, path
        if args.action == "go":
            path = stacks.navigate(repo, args.target)
            return {"path": str(path)}, path
        if args.action == "pull":
            return stacks.pull(repo, args.stack, args.apply), None
        flags = []
        for attribute, option in (
            ("upstack", "--upstack"),
            ("downstack", "--downstack"),
            ("no_trunk", "--no-trunk"),
            ("continue_rebase", "--continue"),
            ("abort", "--abort"),
        ):
            if getattr(args, attribute):
                flags.append(option)
        if (args.continue_rebase or args.abort) and (
            args.upstack or args.downstack or args.no_trunk
        ):
            raise WTError("Use --continue or --abort on its own.")
        return stacks.rebase(repo, flags), None
    if command == "publish":
        return github.publish(
            repo,
            stack=args.stack,
            title=args.title,
            body_file=args.body_file,
            ready=args.ready,
            set_upstream=args.set_upstream,
        ), None
    if command in ("clean", "drop"):
        if args.resume:
            if not args.apply:
                raise WTError("Resuming retirement requires --apply.")
            result = lifecycle.resume(repo, args.resume)
            return result, repo.primary
        if command == "clean" and args.local_only:
            raise WTError("Use 'wt drop --local-only' for explicit local retirement.")
        if args.selector:
            names = [repo.resolve(args.selector)]
        elif command == "drop":
            names = [repo.resolve()]
        else:
            names = list(repo.branches)
        results, skipped = [], []
        selected = []
        for name in names:
            if command == "clean":
                saved = repo.branches[name].get("pr")
                if not saved:
                    if args.selector:
                        raise WTError("Branch has no PR. Use 'wt drop --local-only'.")
                    skipped.append({"branch": name, "reason": "no PR"})
                    continue
                live = github.get_pr(repo, saved["number"], saved["base_repo"])
                if live["state"] not in ("CLOSED", "MERGED"):
                    if args.selector:
                        raise WTError("PR is still open; cleanup leaves it intact.")
                    skipped.append({"branch": name, "reason": "PR is open"})
                    continue
            selected.append(name)
        if command == "clean":
            # Retire children before parents, including when local merged flags
            # predate GitHub's final merge/closure of the complete stack.
            positions = {
                row["branch"]: (stack_index, -layer_index)
                for stack_index, stack in enumerate(stacks.catalog(repo)["stacks"])
                for layer_index, row in enumerate(stack["branches"])
            }
            selected.sort(key=lambda name: positions.get(name, (len(positions), 0)))
            repo._wt_retiring = set(selected)
        try:
            # Every candidate is preflighted before any deletion. Only this
            # preview may disregard descendants in the same terminal batch.
            for name in selected:
                results.append(
                    lifecycle.retire(
                        repo,
                        name,
                        apply=False,
                        local_only=command == "drop",
                        archive_local=args.archive_local,
                        discard_builds=args.discard_builds,
                    )
                )
        finally:
            if command == "clean":
                del repo._wt_retiring
        if args.apply:
            results = [
                lifecycle.retire(
                    repo,
                    name,
                    apply=True,
                    local_only=command == "drop",
                    archive_local=args.archive_local,
                    discard_builds=args.discard_builds,
                )
                for name in selected
            ]
        return {
            "result": "retired" if args.apply else "preview",
            "operations": results,
            "skipped": skipped,
        }, repo.primary if args.apply else None
    if command == "restore":
        path = lifecycle.restore(repo, args.record)
        return {"path": str(path)}, path
    raise WTError("Unknown command")


def _display(args, value):
    if args.command == "list":
        print(
            f"{value['config']['key']}: {value['config']['trunk']} at {value['config']['primary']}"
        )
        for row in value["branches"]:
            pr = row.get("pr") or {}
            label = f"pr{pr['number']} {pr.get('state', '')}" if pr else "unpublished"
            print(f"{row['branch']}  {label}  {row['path']}")
    elif args.command == "doctor":
        print("Managed pairs: " + ("OK" if value["ok"] else "NEED ATTENTION"))
        for issue in value["issues"]:
            print("  " + issue)
        if value["legacy_branches"]:
            print("Legacy branches (unchanged): " + ", ".join(value["legacy_branches"]))
        if value["detached_worktrees"]:
            print(
                "Detached worktrees (unchanged): "
                + ", ".join(value["detached_worktrees"])
            )
    elif args.command in ("clean", "drop") and "operations" in value:
        print(f"{value['result']}: {len(value['operations'])} pair(s)")
        for row in value["operations"]:
            print(f"  {row['branch']}  {row['id']}")
            if row.get("source", {}).get("oid"):
                print(
                    f"    remove live ref: {row['source']['repo']} {row['source']['ref']}"
                )
            for snapshot in row.get("archive_snapshots", []):
                print(f"    pin on archive remote: {snapshot['oid']}")
        for row in value.get("skipped", []):
            print(f"  skip {row['branch']}: {row['reason']}")
        if value["result"] == "preview":
            print("Use --apply to execute after reviewing the plan.")
    elif args.command == "trunk" and args.action == "update":
        print(value["path"])
        print(f"{value['trunk']} {value['oid']}")
    elif "path" in value:
        print(value["path"])
    else:
        print(json.dumps(value, indent=2))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    print_path = "--print-path" in argv
    argv = [arg for arg in argv if arg not in ("--json", "--print-path")]
    args = parser().parse_args(argv)
    try:
        if as_json and print_path:
            raise WTError("Choose either --json or --print-path.")
        if args.command == "name":
            name = directory_name(args.branch)
            print(json.dumps({"directory": name}) if as_json else name)
            return 0
        repo = Repo(initialized=args.command != "init")
        readonly = (
            args.command in ("go", "path", "list", "doctor")
            or args.command == "trunk"
            and args.action is None
            or args.command == "stack"
            and args.action == "go"
        )
        import contextlib

        with contextlib.nullcontext() if readonly else repo.locked():
            value, path = _dispatch(repo, args)
        if print_path:
            if path is None:
                raise WTError("This invocation does not produce a navigation path.")
            print(Path(path).resolve())
        elif as_json:
            print(json.dumps(value, indent=2))
        else:
            _display(args, value)
        return 1 if args.command == "doctor" and not value["ok"] else 0
    except (WTError, OSError, ValueError, KeyError) as error:
        if as_json:
            print(json.dumps({"result": "stopped", "message": str(error)}))
        else:
            print(f"wt: {error}", file=sys.stderr)
        return 1
