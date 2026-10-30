"""One branch, one worktree: creation, PRs, stacks, publication and recovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import github, lifecycle, stacks
from .core import Repo, WTError, branch_name, directory_name, request_prefix


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
    for command, service, noun in (("pr", "GitHub", "PR"), ("mr", "GitLab", "MR")):
        request = commands.add_parser(
            command,
            help=f"open or resume a {service} {noun} worktree and attach its alias",
        )
        request.add_argument("selector")
    pull = commands.add_parser(
        "pull",
        help="update the current independent PR/MR worktree safely",
        description="Pull the PR/MR's current head into this worktree, preserving local work.",
    )
    pull_mode = pull.add_mutually_exclusive_group()
    pull_mode.add_argument(
        "--rebase",
        action="store_true",
        help="replay local commits onto the current PR/MR head",
    )
    pull_mode.add_argument(
        "--continue",
        dest="continue_rebase",
        action="store_true",
        help="continue a paused pull rebase or finish submodule alignment",
    )
    pull_mode.add_argument(
        "--abort", action="store_true", help="abort a paused pull rebase"
    )
    commands.add_parser(
        "push",
        help="fast-forward the current PR/MR's source branch without setting Git tracking",
        description=(
            "Push committed HEAD to the associated PR/MR's verified source branch. "
            "Only fast-forward updates are allowed; Git upstream tracking is unchanged."
        ),
    )
    for name in ("go", "path"):
        command = commands.add_parser(name, help="resolve an existing owning worktree")
        command.add_argument("selector")
    name = commands.add_parser(
        "name", help="map a branch to its canonical directory name"
    )
    name.add_argument("branch")
    codex = commands.add_parser(
        "codex",
        help="find and resume Codex sessions for the current PR/MR or workspace",
    )
    codex.add_argument(
        "--list", action="store_true", help="list matches without resuming"
    )
    commands.add_parser("list", help="list managed branch/worktree pairs")
    commands.add_parser("doctor", help="check managed pairs and report legacy work")
    housekeeping = commands.add_parser(
        "housekeeping",
        help="preview obsolete pairs and explain retained workspaces",
        description=(
            "Inventory this repository's workspaces. Remove only clearly obsolete "
            "managed PR/MR pairs with --apply; report all remaining work with next steps."
        ),
    )
    housekeeping.add_argument("--apply", action="store_true")
    housekeeping.add_argument(
        "--discard-builds",
        action="store_true",
        help="allow recognized ignored build outputs and caches to be discarded",
    )
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
    rebase.add_argument(
        "--accept-base-change",
        action="store_true",
        help="accept dropping an unmerged old base after GitHub retargets the stack",
    )
    recovery = rebase.add_mutually_exclusive_group()
    recovery.add_argument("--continue", dest="continue_rebase", action="store_true")
    recovery.add_argument("--abort", action="store_true")
    publish = commands.add_parser(
        "publish",
        help="push exact refs and create/update PRs/MRs; stacks always use upstream",
    )
    publish.add_argument("--stack", action="store_true")
    publish.add_argument("--title")
    publish.add_argument("--body-file")
    publish.add_argument("--ready", action="store_true")
    for name in ("clean", "drop"):
        command = commands.add_parser(
            name,
            help="preview retirement; drop also accepts an unpaired local branch",
        )
        command.add_argument("selector", nargs="?")
        command.add_argument("--apply", action="store_true")
        command.add_argument("--archive-local", action="store_true")
        command.add_argument(
            "--discard-builds",
            action="store_true",
            help="discard recognized ignored build outputs and tool caches",
        )
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


def _readonly(args):
    return (
        args.command in ("go", "path", "list", "doctor")
        or args.command == "trunk"
        and args.action is None
        or args.command == "stack"
        and args.action == "go"
    )


def _dispatch(repo, args):
    command = args.command
    if repo.state.get("pr_rebase"):
        resuming = command == "pull" and (args.continue_rebase or args.abort)
        reporting = command == "housekeeping" and not args.apply
        if not resuming and not reporting and not _readonly(args):
            repo.ensure_idle()
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
    if command in ("pr", "mr"):
        github.require_request_command(repo, command, args.selector)
        path = github.open_pr(repo, args.selector)
        return {"path": str(path)}, path
    if command == "pull":
        from . import pr_rebase

        if args.continue_rebase or args.abort:
            result = pr_rebase.resume(repo, abort=args.abort)
        elif args.rebase:
            result = pr_rebase.start(repo)
        else:
            result = github.refresh_current(repo)
        return result, Path(result["path"])
    if command == "push":
        from . import pr_push

        return pr_push.push(repo), None
    if command in ("go", "path"):
        branch = repo.resolve(args.selector)
        path = repo.owner(branch)
        if path is None and repo.state.get("pr_rebase"):
            from .pr_rebase import navigation_path

            path = navigation_path(repo, branch)
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
    if command == "housekeeping":
        from . import housekeeping

        result = housekeeping.run(
            repo, apply=args.apply, discard_builds=args.discard_builds
        )
        return result, repo.primary if args.apply else None
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
            ("accept_base_change", "--accept-base-change"),
            ("continue_rebase", "--continue"),
            ("abort", "--abort"),
        ):
            if getattr(args, attribute):
                flags.append(option)
        if (args.continue_rebase or args.abort) and (
            args.upstack or args.downstack or args.no_trunk or args.accept_base_change
        ):
            raise WTError("Use --continue or --abort on its own.")
        if args.accept_base_change and args.no_trunk:
            raise WTError("Use --accept-base-change only when including trunk.")
        return stacks.rebase(repo, flags), None
    if command == "publish":
        return github.publish(
            repo,
            stack=args.stack,
            title=args.title,
            body_file=args.body_file,
            ready=args.ready,
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
            try:
                names = [repo.resolve(args.selector)]
            except WTError:
                if command != "drop" or not repo.exists("refs/heads/" + args.selector):
                    raise
                names = [args.selector]
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
                        raise WTError(
                            f"Branch has no {request_prefix(config=repo.config).upper()}. Use 'wt drop --local-only'."
                        )
                    skipped.append(
                        {
                            "branch": name,
                            "reason": "no "
                            + request_prefix(config=repo.config).upper(),
                        }
                    )
                    continue
                live = github.get_pr(repo, saved["number"], saved["base_repo"])
                if live["state"] not in ("CLOSED", "MERGED"):
                    if args.selector:
                        raise WTError(
                            f"{request_prefix(config=repo.config).upper()} is still open; cleanup leaves it intact."
                        )
                    skipped.append(
                        {
                            "branch": name,
                            "reason": request_prefix(config=repo.config).upper()
                            + " is open",
                        }
                    )
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


def _cleanup_lines(repo, value):
    operations = value.get("operations", [value])
    lines = []
    if not operations:
        lines.append("No branch/worktree pairs removed.")
    for record in operations:
        suffix = " and its worktree" if record.get("path") else " (no worktree)"
        lines.append(f"Removed local branch {record['branch']}{suffix}.")
        path = record.get("backtracking_path") or (
            lifecycle._backtracking_dir(repo) / f"{record['id']}.json"
        )
        count = sum(bool(row.get("recovery_ref")) for row in record["snapshots"])
        noun = "ref" if count == 1 else "refs"
        lines.append(
            f"Backtracking: {Path(path).expanduser().resolve()} "
            f"({count} recovery {noun})"
        )
        events = record.get("remote_actions", [])
        archives = record.get("archives", [])
        source = record.get("source") or {}
        remotes = dict.fromkeys(
            [
                repo.config.get("upstream_remote", "upstream"),
                repo.config.get("archive_remote", "origin"),
                *[row["remote"] for row in events],
                *[row["remote"] for row in archives],
                *([source["remote"]] if source else []),
            ]
        )
        for remote in remotes:
            # Resuming can verify an archive that this operation already created.
            # Report the recorded mutation once rather than claiming two changes.
            actions = {}
            for event in events:
                if event["remote"] != remote:
                    continue
                key = event["ref"]
                if key not in actions or event["action"] in (
                    "archive-created",
                    "branch-deleted",
                ):
                    actions[key] = event
            descriptions = []
            for event in actions.values():
                description = {
                    "archive-created": "created archive tag",
                    "archive-reused": "reused archive tag",
                    "branch-deleted": "deleted",
                    "branch-absent": "source branch already absent:",
                }[event["action"]]
                descriptions.append(f"{description} {event['ref']} ({event['repo']})")
            for archive in archives:
                if archive["remote"] == remote and archive["ref"] not in actions:
                    descriptions.append(
                        f"archive verified: {archive['ref']} ({archive['repo']}; "
                        "creation not recorded)"
                    )
            if (
                source.get("remote") == remote
                and source["ref"] not in actions
                and ("remote_branch" in record["completed"] or not source["oid"])
            ):
                descriptions.append(
                    f"source branch absent: {source['ref']} ({source['repo']}; "
                    "prior deletion not recorded)"
                )
            lines.append(f"{remote}: " + ("; ".join(descriptions) or "no changes"))
    for row in value.get("skipped", []):
        lines.append(f"Skipped {row['branch']}: {row['reason']}")
    return lines


def _housekeeping_lines(repo, value):
    operations = value.get("operations", [])
    preview = value["result"] == "preview"
    lines = []
    if preview:
        lines.append(f"Would clean {len(operations)} pair(s):")
        for record in operations:
            pr = record.get("pr") or {}
            label = (
                f"{request_prefix(pr, repo.config)}{pr['number']} {pr['state']} "
                if pr
                else ""
            )
            lines.append(f"  {label}{record['branch']}")
            lines.append(f"    {record['path']}")
            if record.get("source", {}).get("oid"):
                source = record["source"]
                lines.append(f"    delete source: {source['repo']}:{source['ref']}")
            for snapshot in record.get("archive_snapshots", []):
                lines.append(
                    f"    archive on {repo.config['archive_remote']}: {snapshot['oid']}"
                )
            if pr.get("url"):
                lines.append(f"    {pr['url']}")
    else:
        lines.extend(_cleanup_lines(repo, {"operations": operations}))
    for issue in value.get("issues", []):
        lines.append(f"Attention: {issue['message']}")
        if issue.get("hint"):
            lines.append(f"  Next: {issue['hint']}")
    remaining = value.get("remaining", [])
    lines.append(f"Remaining workspaces/branches: {len(remaining)}")
    for entry in remaining:
        pr = entry.get("pr") or {}
        label = ""
        if pr.get("number"):
            state = pr.get("state", "UNKNOWN")
            if not entry.get("pr_verified"):
                state += " (unchecked)"
            label = f"{request_prefix(pr, repo.config)}{pr['number']} {state} "
        name = entry.get("branch") or "detached worktree"
        lines.append(f"  {label}{name}")
        if entry.get("path"):
            lines.append(f"    {entry['path']}")
        for reason in entry.get("reasons", []):
            lines.append(f"    {reason['message']}")
        facts = entry.get("facts", {})
        if pr.get("state") == "OPEN" and {"ahead", "behind"} <= facts.keys():
            lines.append(
                f"    Commits relative to {request_prefix(pr, repo.config).upper()}: {facts['ahead']} local-only, "
                f"{facts['behind']} {request_prefix(pr, repo.config).upper()}-only"
            )
        for hint in dict.fromkeys(entry.get("hints", [])):
            lines.append(f"    Next: {hint}")
        if pr.get("url"):
            lines.append(f"    {pr['url']}")
        if entry.get("recovery_path"):
            lines.append(f"    Recovery: {entry['recovery_path']}")
    lines.append(
        f"{'Eligible' if preview else 'Cleaned'}: {len(operations)}; "
        f"retained: {len(remaining)}; errors: {len(value.get('errors', []))}"
    )
    if preview and operations:
        lines.append(
            "Use --apply to execute the eligible cleanup after reviewing this report."
        )
    return lines


def _display(args, value, repo=None):
    if args.command == "list":
        print(
            f"{value['config']['key']}: {value['config']['trunk']} at {value['config']['primary']}"
        )
        for row in value["branches"]:
            pr = row.get("pr") or {}
            label = (
                f"{request_prefix(pr, value['config'])}{pr['number']} {pr.get('state', '')}"
                if pr
                else "unpublished"
            )
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
    elif args.command == "housekeeping":
        print("\n".join(_housekeeping_lines(repo, value)))
    elif args.command in ("clean", "drop") and args.apply:
        print("\n".join(_cleanup_lines(repo, value)))
    elif args.command in ("clean", "drop") and "operations" in value:
        noun = (
            "branch(es)"
            if any(row.get("path") is None for row in value["operations"])
            else "pair(s)"
        )
        print(f"{value['result']}: {len(value['operations'])} {noun}")
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
    elif args.command == "pull":
        if value["result"] == "aborted":
            action = "Aborted pull rebase for"
        elif value["result"] == "rebased":
            action = "Rebased local commits onto"
        elif value["oid"] != value["pr"]["head_oid"]:
            action = "Kept local commits ahead of"
        elif value["oid"] == value["previous_oid"]:
            action = "Already up to date with"
        else:
            action = "Updated from"
        prefix = request_prefix(value["pr"], repo.config)
        marker = "!" if prefix == "mr" else "#"
        print(
            f"{action} {prefix.upper()} {marker}{value['pr']['number']}: {value['branch']}"
        )
        if value.get("recovery_record"):
            recovery = lifecycle._backtracking_dir(repo) / (
                value["recovery_record"] + ".json"
            )
            print(f"Recovery: {recovery.resolve()}")
        print(f"HEAD {value['oid']}")
        print(value["pr"]["url"])
    elif args.command == "push":
        action = "Already up to date" if value["result"] == "unchanged" else "Pushed"
        print(f"{action}: {value['repo']}:{value['ref'].removeprefix('refs/heads/')}")
        print(f"HEAD {value['oid']}")
        print(value["pr"]["url"])
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
        if args.command == "codex":
            if print_path:
                raise WTError("wt codex does not produce a navigation path.")
            from agent_logbook.cli import main as logbook_main

            options = ["here"]
            if as_json:
                options.append("--json")
            if args.list:
                options.append("--list")
            return logbook_main(options)
        repo = Repo(initialized=args.command != "init")
        if args.command in ("pr", "mr"):
            github.require_request_command(repo, args.command, args.selector)
        readonly = _readonly(args)
        import contextlib

        with contextlib.nullcontext() if readonly else repo.locked():
            value, path = _dispatch(repo, args)
        if print_path:
            if path is None:
                raise WTError("This invocation does not produce a navigation path.")
            if args.command in ("clean", "drop") and args.apply:
                print("\n".join(_cleanup_lines(repo, value)), file=sys.stderr)
            elif args.command == "housekeeping" and args.apply:
                print("\n".join(_housekeeping_lines(repo, value)), file=sys.stderr)
            print(Path(path).resolve())
        elif as_json:
            print(json.dumps(value, indent=2))
        else:
            _display(args, value, repo)
        if args.command == "housekeeping":
            return int(bool(value.get("errors")))
        return 1 if args.command == "doctor" and not value["ok"] else 0
    except (WTError, OSError, ValueError, KeyError) as error:
        if as_json:
            print(json.dumps({"result": "stopped", "message": str(error)}))
        else:
            print(f"wt: {error}", file=sys.stderr)
        return 1
