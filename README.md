# rwgk_config

Ralf's personal configuration and commands.

```bash
git clone https://github.com/rwgk/rwgk_config.git
```

## Find and resume agent sessions

`agent-logbook` records which Codex sessions worked on PRs, issues, Slack threads,
NVBugs, and durable files. `codex-find` looks them up from any directory, and
`wt codex` uses the current worktree's saved PR association, then its workspace.
No network request or catalog write is needed for lookup.

```bash
wt pr 123
codex                        # start work; installed hooks register the session
# later, from that worktree:
wt codex
wt codex --list               # show matches without launching Codex
codex-find 'https://github.com/OWNER/REPO/pull/123'
codex-find 'https://WORKSPACE.slack.com/archives/CHANNEL/pTIMESTAMP'
codex-find path/to/artifact.txt
codex-find artifact.txt
codex-find 'topic words' --list
codex-find 'OWNER/REPO#123' --json
```

A single exact resource match resumes automatically when its saved Codex history
is available locally. Multiple matches list IDs, dates, hosts, relationship
labels, topic hints, and commands to copy. Topic substring matches are labeled
possible and never launch automatically. `--list` and `--json` always suppress
launching. No match reports an error instead of silently starting a new session.
`wt codex` resumes in the current worktree; standalone lookup uses the session's
recorded directory when it still exists. A missing directory suppresses automatic
resume; recreate the worktree or explicitly select `--cwd PATH`.

### Storage and setup

The logbook lives in `$H/agent-logbook` when `H` is set, otherwise
`$W/agent-logbook`, otherwise `~/.local/share/agent-logbook`.
`AGENT_LOGBOOK_DIR` selects an explicit location; `agent-logbook path` shows it.
An inaccessible configured location reports a failure instead of creating a
second logbook elsewhere.

The shared NetApp store contains private, immutable JSON event files published
with unique names by atomic rename. It needs no shared SQLite database, lock
service, daemon, or third-party Python packages. Readers fold records into the
session index. Incomplete temporary files are ignored; corrupt published records
stop lookup rather than permitting an automatic resume from incomplete results.
The first implementation scans these small metadata records on each query.

The index stores resource identifiers and short work hints, not session history.
Preserve the actual Codex history separately. Each session records its host and
observed history location; a session known only on another host is listed with
instructions to resume there. Deleting a worktree does not delete its logbook
links. Restored worktrees can find sessions through the canonical PR identity.

For Ralf's NVIDIA setup, `~/rwgk_config_nvidia/codex/01_create_codex_softlinks.sh`
installs the hook-file symlink when no existing hooks file is present. Keep both
configuration repositories up to date. Open `/hooks` in Codex to review and trust
the new hooks once; changed definitions require review again. Existing hook files
are preserved and can incorporate the handlers from
`~/rwgk_config_nvidia/codex/hooks.json`. Automatic capture requires trusted hooks;
manual recording and lookup work independently.

### Explicit associations and capture limits

Agents should register the main task and useful changes as they happen:

```bash
agent-logbook record 'https://github.com/OWNER/REPO/issues/123' \
    --relation discussed --note 'Investigate stream lifetime failure'
agent-logbook record path/to/report.txt --relation created \
    --note 'Reproducer results and remaining questions'
agent-logbook record --note 'Follow-up: validate the fix on another host'
```

The default ID comes from `CODEX_THREAD_ID` (`AGENT_LOGBOOK_SESSION_ID` overrides
it). Outside a session, supply `--session ID`. Registration includes current
workspace context, so work outside the starting directory remains discoverable.
The main agent should register meaningful work delegated to subagents under the
resumable main session.

Lifecycle hooks register workspace/PR context and a short prompt hint. Supported
successful tool writes add file, PR/issue, NVBug, Slack, and GitLab associations.
Read results do not imply creation or modification. Arbitrary shell commands,
unsupported tool shapes, and semantic review/discussion need explicit recording.
Hooks are best effort and never block or restart the agent; failures are reported
on stderr. Tool outputs and message bodies are not copied into the logbook.

No historical sessions or `agent-resume.txt` entries are imported automatically.

## Branches and worktrees with `wt`

Each active local branch owns one worktree. The primary checkout stays on trunk;
navigation changes directories. New development branches are `rwgk/<slug>` and
live under `$W/trees/<repo-key>/`, normally `/wrk/trees/<repo-key>/`.

**Origin-only exception:** when a repository's only remote is `origin`, edit and
commit in the supplied checkout on its current branch, including trunk. Create
a new branch or worktree only when explicitly requested. Requests to create a PR
or stack count as requesting the necessary branch/worktree, which follows the
normal `wt` conventions. This exception overrides defaults that create a task
branch/worktree for ordinary edits.

New worktrees use this location for both human and agent work, including PR
reviews and short-lived experiments. Reuse a worktree already supplied for the
task. For working material outside Git checkouts, see the
[persistent scratch policy](#persistent-scratch-and-temporary-files).

Tests that exercise Git in wholly disposable fixture repositories use
`$W/trees/.wt-tests/`; that exception does not cover real task repositories.

```text
/wrk/forked/cuda-python/                    main (primary)
/wrk/trees/cuda-python/rwgk-fix-stream/      rwgk/fix-stream
/wrk/trees/cuda-python/pr1234                alias to rwgk-fix-stream
```

PR aliases leave physical paths stable, including build and virtual-environment
paths. Branch slugs use lowercase letters, digits, hyphens, and underscores. The
single `wt name` implementation handles legacy names and collisions; directory
names are never parsed to recover branch names.

Requirements: Python 3.11 or later on POSIX (including WSL), Git 2.36 or later,
and the hosting service's CLI: `gh` for GitHub, `glab` for GitLab. GitLab
access uses the credentials configured in `glab`; compatibility was checked with
version 1.36.0. Extension-backed GitHub stack operations require
**gh-stack v0.3.0 or newer**, whose official release supports rebasing each layer
in its owning worktree. The helpers check the version before making changes and
stop with installation or upgrade instructions when necessary:

```bash
gh stack --version
gh extension upgrade stack              # installed but too old
gh extension install github/gh-stack     # not installed
```

The check is quiet on success and shared by the stack helpers. Local navigation,
help, previews, and the read-only checker remain available for diagnosis without
a compatible extension. Native Windows Python lacks the POSIX locking used by
this engine.

`wt` is a Bash function that runs the `git-wt` engine and changes directory after
successful navigation. Start a fresh shell after installing these changes. Both
`~/rwgk_config/bin` and `~/rwgk_config_nvidia/bin` are expected on `PATH`.
`git-wt` or `git wt` works in scripts and prints results without changing the
parent shell's directory. `--print-path` emits a navigation result; `--json`
emits structured output. No command generates shell code for evaluation.

### Register a repository

Run this from its primary checkout:

```bash
wt init
wt doctor
```

Registration saves configuration in the common Git directory and does not move
existing worktrees, switch branches, rename branches, or publish anything.
`doctor` reports existing inconsistencies for deliberate adoption later.

NVIDIA defaults come from `~/rwgk_config_nvidia/git_stuff/wt-defaults.json`:

| Repository | Key | Trunk | Independent PR publication |
|---|---|---|---|
| `NVIDIA/cuda-python` | `cuda-python` | `main` | `upstream` |
| `NVIDIA-dev/cuda-python-private` | `cuda-python-private` | `ctk-next` | `upstream` |
| `pybind/pybind11` | `pybind11` | `master` | `origin` |

**All stacks publish to upstream, unconditionally.** Upstream failure stops the
operation; publication never falls back to a fork. `origin` is the archive
repository. Remote aliases are checked against repository identities.

For another repository or a machine-specific layout:

```bash
wt init --key project --trunk main --upstream upstream \
    --publish origin --archive origin --trees /wrk/trees
```

Options also include `--backtracking PATH`. Configuration records `key`,
`primary`, `trunk`, `upstream_remote`, `upstream_repo`, `publish_remote`,
`archive_remote`, `trees_root`, and `backtracking`. Use `wt init --help` for the
current interface; inspect existing state before changing repository settings.

### Independent development

```bash
wt trunk update                 # fast-forward trunk in its primary worktree
wt new fix-stream               # create rwgk/fix-stream and enter its worktree
# edit, build, test, commit
mkdir -p "$W/codex_scratch_space/fix-stream"
# write the PR draft to "$W/codex_scratch_space/fix-stream/pr-body.md"
wt publish --title 'Fix stream lifetime' \
    --body-file "$W/codex_scratch_space/fix-stream/pr-body.md"
wt go pr1234                    # after GitHub assigns a PR number
```

New work starts from configured local trunk. Publication creates or updates the
branch and PR, then attaches the PR alias. New PRs default to draft; `--ready`
requests a ready PR. Existing PR readiness is preserved. Publication and merging
remain separate operations.

`wt` uses its PR associations and publication records rather than Git upstream
tracking. `wt push` and `wt publish`, including stack publication, leave existing
branch tracking unchanged and do not configure missing tracking. Use `wt pull`
and `wt push` for independent PR synchronization.

Once an independent PR is associated, `wt push` updates its branch without
changing its title, description, or draft status. Use `wt publish` to create a PR,
update owned PR metadata, publish an owned history rewrite, or publish a stack.

```bash
wt list                         # branches, worktrees, PR associations
wt go fix-stream                # slug, full branch, or pr1234
wt trunk                        # enter primary checkout without updating it
wt path rwgk/fix-stream          # print owning worktree path
wt name rwgk/fix-stream          # print centrally mapped directory name
```

### PR reviews

```bash
wt pr 1234                      # or the full PR URL
# review and test in the resulting worktree
wt pr 1234                      # resume the same pair
wt pull                         # update this worktree's PR; no number needed
# edit, test, commit a contribution
wt push                         # fast-forward the associated PR's source branch
# if local commits diverge from an updated PR:
wt pull --rebase                 # replay local work on the PR's current head
# test the combined result
wt push
```

Your own PR reuses its local branch when available. Another author's PR gets a
local `rwgk/review-pr1234` branch. PR head identity is recorded separately from
the local branch name, and the receiving repository's PR head ref permits
checkout after a contributor fork or branch disappears.

`wt push` works for your own independent PRs and authorized contributions to
another author's PR. It reads the current PR identity, verifies its actual source
repository and branch, and pushes the captured local commit only when that is a
fast-forward update. It needs neither Git upstream tracking nor a contributor
remote. An existing remote is used only after its destination is verified;
otherwise `wt` uses the PR's repository endpoint directly. A review contribution
stays classified as a review, so cleanup never claims ownership of the author's
remote branch. Permission failures, a changed or missing source, and concurrent
updates stop with guidance; there is no automatic force push or fallback to origin.
The result includes the destination, full commit hash, and PR URL. Ordinary
uncommitted files are not included in a push.

Use `wt pull` instead of `git pull` in an independent PR worktree. It reports
the PR and full resulting commit hash, follows rewritten PR history with recovery
records when no local work would be lost, and stops on divergence. Local commits
ahead of the PR are kept. `wt pull --rebase` explicitly replays local commits on
the fetched PR head, preserving the old tip first. It uses the recorded replay
boundary to distinguish local contributions from the author's old history after
a rewrite. Merely resuming `wt pr` does not advance that boundary.

Redundant local merges already represented on the incoming PR head can be
omitted during rebase; this includes independently merging the same main commit
with identical parents and contents. Other local merges stop for deliberate
reconciliation so merge-only resolutions are not silently discarded. Dirty
working files must be committed or preserved before pulling with rebase.
If conflicts stop a rebase:

```bash
# resolve conflicts, then stage the resolved files
git add <resolved-files>
wt pull --continue
# or restore the pre-rebase state
wt pull --abort
```

Continuation uses the saved operation and frozen target rather than fetching a
new PR head. Recovery state remains available if submodule alignment is
interrupted. Pull preserves your current directory, including subdirectories.
Trunk uses `wt trunk update`; stack layers use `wt stack pull` and its explicit
`--apply`, local `wt stack rebase`, and authorized `wt publish --stack`.

### GitLab merge requests

The same independent-worktree commands support GitLab.com and self-hosted GitLab.
The upstream remote selects the host and project, including nested namespaces and
custom SSH ports. HTTP(S) API access currently uses standard ports. `wt pr`
accepts a merge request IID or a full merge request URL;
the existing `prNUMBER` aliases and `rwgk/review-prNUMBER` branch names also apply
to GitLab so navigation stays consistent.

```bash
glab auth login --hostname gitlab.example.com
wt pr 42
# or: wt pr https://gitlab.example.com/group/subgroup/project/-/merge_requests/42
wt pull
wt pull --rebase                 # when local contributions diverged
wt push                         # publish an authorized contribution to the MR source
```

GitLab projects are recorded as `HOST/GROUP/PROJECT`, keeping host identity separate
from a GitHub `OWNER/REPO`. Existing registrations with an empty upstream identity
infer it from the configured upstream remote; a new checkout uses `wt init` as
usual. Cross-host and cross-project selectors stop rather than changing the
publication destination.

An MR is fetched through the receiving project's `refs/merge-requests/NUMBER/head`,
so review checkout does not require adding a contributor remote. `wt push` verifies
the current source project and branch, preserves custom SSH transport settings,
and keeps the worktree classified as a review. Git upstream tracking is unchanged.
`wt publish` supports owned MR creation and metadata updates, with new MRs draft
by default. `wt clean` and `wt housekeeping` use the same preservation and ownership
checks for GitLab as for GitHub.

If you already created the canonical review branch/worktree manually, `wt pr NUMBER`
can adopt that pair when its checkout is clean, idle, at the verified MR head, and
at the expected path. It refuses a different checkout, extra commits, or pending
work rather than overwriting them. No private catalog editing is needed.

Native stack commands require GitHub and `gh-stack`; they stop on GitLab before
creating branches or rewriting history. Independent GitLab MR workflows do not
require `gh` or the stack extension.

### Submodules

New pairs initialize submodules recursively at the commits recorded by their
superproject. When the corresponding primary submodule is initialized, `wt`
uses it as a Git `--reference` without `--dissociate`. Each worktree keeps its
own checkout and Git refs while reusing the primary's object storage. A module
without an available reference is initialized normally. Existing initialized
modules keep their current object-storage layout.

`wt pull`, `wt trunk update`, and stack updates align submodule checkouts after
updating the parent. Git's implicit recursive updates are suppressed until the
reference and recovery setup is ready. Initialized module removal, renaming,
or replacement with ordinary files requires deliberate handling; automatic
updates stop before discarding those checkouts.
Stack rebases also check replayed commits for module layout changes and stop
on non-build ignored material inside submodules.

Borrowed history is retained with immutable local refs under `refs/rwgk/wt/`
in the reference submodule repository, including histories behind branches and
tags. These refs protect objects from ordinary Git garbage collection and remain
for recovery. Keep the primary repository and its submodule object stores in
place while worktrees depend on them.

Cleanup checks nested edits, untracked files, hidden index flags, and unfinished
Git operations. Ignored submodule build outputs follow the same
`--discard-builds` policy as the parent. Before removal, submodule HEADs and refs
are preserved in a stable primary module or a bare cache under the common Git
directory's `rwgk-wt/submodules/`, and recorded in the backtracking record.
`wt restore` can recover local module branches and unpublished recorded pins.
This retention is local; cleanup does not push submodule refs.

Interrupted initialization leaves a pending journal. Retry the same creation or
update command; unexpected local changes stop recovery for inspection.
Other lifecycle operations wait for an interrupted PR rebase or restack to finish.

### Stacked PRs

```bash
wt new stream-core
# edit, test, commit the first layer
wt stack init
wt stack add stream-tests       # create and enter the next owning worktree
# edit, test, commit
wt publish --stack              # publish all layers to upstream
wt stack go down                # enter previous layer's worktree
# amend first layer
wt stack rebase                 # local restack in each layer's own worktree
wt publish --stack
```

`wt stack go up|down|top|bottom` navigates without switching any worktree's
branch. `wt stack rebase` supports `--upstack`, `--downstack`, `--no-trunk`,
`--accept-base-change`, `--continue`, and `--abort`. It retains recovery refs before
rewriting.

If GitHub retargets a stack after deleting its old base, publication stops until
the stack is rebased onto the new base. `wt stack rebase` supports a new base that
matches the repository's registered trunk, with its owning worktree and submodule
checks. Another target stops with guidance to inspect the repository configuration.
When the old base is not known to have merged, inspect the commits before explicitly
accepting their removal:

```bash
wt stack rebase --accept-base-change
```

This flag is never implied and cannot be combined with `--no-trunk`, `--continue`,
or `--abort`. Stack adoption remains a separate operation and refuses a changed
base rather than rewriting history.

After GitHub changes a stack, for example by merging its lower layers:

```bash
wt stack pull --stack 3179               # preview adoption
wt stack pull --stack 3179 --apply       # adopt locally
```

The number identifies a native GitHub **stack**, not a PR. Adoption preserves
previous local tips through recovery refs and backtracking records before
changing branches. Proven published tips displaced by adoption are queued for
immutable archival during the next authorized publication or terminal cleanup;
adoption itself performs no remote writes. Adoption, local rebase, and
publication are separate operations. Use the helper rather than
`gh stack sync`, `submit`, `push`, or `prune` as a lifecycle shortcut. Native
GitHub stacks require same-repository branches; upstream-only publication is
intentional, including pybind11.

Publication checks exact destination refs and uses fixed expected-SHA leases
with an atomic push. For an established native stack it checks the live stack
identity and base before preserving history, pushing, or updating PRs, including
when only one active PR remains. Before replacing published history it preserves
the old tip when necessary. Rewritten native stacks send `pull.stacked` to support
retaining approvals when the PR diff is unchanged; a remote that does not support
push options is retried with the same fixed leases and atomic push. Independent
PRs and fast-forward stack pushes do not send that option.

Concurrent remote changes stop publication for investigation. A failed lease is
not an invitation to refresh the expected SHA and overwrite new work. The
extension's atomic defaults do not replace these independently verified leases.

### Repository housekeeping

```bash
wt housekeeping                 # preview eligible cleanup and explain what remains
wt housekeeping --apply          # retire clearly obsolete pairs and report the rest
wt housekeeping --apply --discard-builds
wt housekeeping --json          # structured inventory and proposed cleanup
```

Housekeeping inventories the current repository's managed pairs, Git-registered
worktrees, and unpaired local branches. It keeps the primary checkout and reports
legacy, unmanaged, detached, or missing worktrees for inspection. PR aliases are
navigation links, not additional workspaces. It does not scan arbitrary filesystem
directories for things to delete.

Automatic retirement requires a freshly verified merged or closed PR, a clean
owning worktree and submodules, and local history contained in the final PR head
or verified upstream trunk. The PR-head comparison also covers squash merges,
whose original commits may not be ancestors of trunk. Additional local commits,
private submodule history (including stashes), unfinished operations, ownership
problems, and surviving stack dependencies keep a workspace visible for a decision.

The report includes each remaining workspace's reason, PR URL when available,
path, and useful next commands: for example, `wt push` for local commits on an
open PR, `wt pull --rebase` for divergence, or `wt go pr1234` to inspect modified
files. An inaccessible PR is reported as unchecked, never assumed obsolete.
One blocked workspace does not prevent independent eligible cleanup; stack
layers still retire from top to bottom while respecting retained dependencies.

Ignored files follow the same policy as `wt clean`: recognized generated builds
and caches require `--discard-builds`, while unknown ignored material stays
protected. Apply uses the existing cleanup engine and reports its backtracking
record paths, recovery refs, archives, and remote outcomes. Another author's
source branch is never deleted.

Housekeeping is deliberately stricter than an explicit `wt clean`: a terminal
PR with additional local commits remains in the report even when those commits
could be archived safely. After inspecting that work, use the deliberate
retirement commands below. Command availability does not authorize applying
cleanup or changing remotes; preview and apply within the requested scope.

### Retire work and recover it

```bash
wt clean                        # preview terminal PR candidates
wt clean pr1234                 # preview one PR
wt clean pr1234 --apply          # perform its lifecycle cleanup
```

Applied cleanup reports the backtracking record's full path and the outcome on
each remote: branches deleted or already absent, archive tags created or reused,
or no changes. This feedback is also visible through the `wt` shell function;
`--json` remains structured output, and `--print-path` writes the feedback to
stderr while keeping stdout reserved for the navigation path.

Merged and closed-unmerged PRs are eligible. Open, draft, or queued PRs stay live.
A batch previews every terminal candidate before deleting anything, then retires
stack layers from top to bottom. Surviving stack dependencies, unfinished Git
operations, dirty tracked files, or unexpected untracked files stop unsafe
cleanup. Known disposable build outputs and tool caches (including `.mypy_cache`
and `.lycheecache`) need the explicit `--discard-builds` option. Inspect the
preview before applying.

Git's ignore rules select the ignored paths. Cleanup recognizes conventional
Python extension binaries, package metadata, Cython debug output, benchmark
directories, and generated Cython/version files as disposable build outputs.
Generated source files require generator markers; Cython C/C++ also requires a
matching tracked `.pyx` source. Other ignored material stays protected, including
ignored credentials and handwritten source. An ignored path alone is not consent
to discard it.

For your own PR, cleanup records recovery information, preserves distinct local
and source histories, archives otherwise-uncovered tips with immutable tags on
origin, and deletes the verified live remote branch with an exact-SHA lease.
An exact tip already reachable from upstream trunk needs no extra remote tag.
Squash/rebase merges generally still need a tag to retain original PR commits.

Another author's PR defaults to local retirement only. Its remote branch is
never deleted. `--archive-local` also pins local review history on your origin
when you want to preserve additional commits remotely.

For unpublished work or deliberate retirement while leaving remotes alone:

```bash
wt drop fix-stream --local-only          # preview
wt drop fix-stream --local-only --apply
```

For a legacy local branch that has no worktree, use its exact existing name:

```bash
wt drop old-branch-name --local-only          # preview; no temporary worktree
wt drop old-branch-name --local-only --apply
```

This is the replacement for `git_branch_D_track_hash --local-only`: it records
the commit and branch configuration, creates a local recovery ref, and deletes
only that local branch. Neither upstream nor origin is changed. Legacy names do
not need an `rwgk/` prefix. Checked-out branches, trunk, missing managed worktrees,
and branches needed by surviving stack layers are protected. `wt restore` can
recreate the retired branch in its own managed worktree.

Backtracking records remain under `~/Sve/git_backtracking_info/` or the configured
location. The existing `MY_GIT_BACKTRACKING_INFO_LOCAL` and
`MY_GIT_BACKTRACKING_INFO_REMOTE=HOST:PATH` settings are honored. Local recovery
refs under `refs/rwgk/backtracking/` keep commit objects reachable without
creating extra local branches or worktrees. Remote archive tags are under
`refs/tags/rwgk/archive/`; they are immutable snapshots, not live PR branches.

```bash
wt restore <record-id-or-path>
```

Restore accepts **retired-pair records** and recreates the recorded
branch/worktree pair, obtaining missing objects from recorded recovery locations
when possible. Preservation records made before a rebase or publication rewrite
are not accepted by `wt restore`: inspect their `snapshots` and `recovery_ref`
entries, then use `git show <recovery-ref>` to inspect the preserved history before
choosing a recovery action. Text SHAs alone do not retain objects; the recovery
refs and remote tags provide retention. Earlier PR
iterations need preservation before rewriting because the PR's current head ref
only names its current history.

Cleanup journals record progress across archival, remote deletion, and local
retirement. If interrupted, inspect the journal and resume by its record ID:

```bash
wt clean --resume <record-id> --apply
# or, for a local-only retirement:
wt drop --resume <record-id> --local-only --apply
```

Completed preservation steps are repeatable. Cross-repository changes cannot be
one atomic transaction. Applied cleanup returns the shell to the primary
checkout.

### Diagnostics and internals

`wt doctor` reports ownership, path, primary/trunk, metadata, and unfinished
operation issues. Existing layouts are intentionally not migrated automatically.
Ask for a situation-specific adoption plan instead of mass-renaming live work.

The stack adoption/checking programs remain internal tools for detailed recovery:

```bash
gh_stack_check.py --worktree "$PWD" --remote upstream --expect-synced
gh_stack_check.py --worktree "$PWD" --remote upstream --allow-local-ahead
gh_stack_check.py --worktree "$PWD" --remote upstream --allow-stale-trunk
```

The checker is read-only and does not establish live GitHub state by itself.
`--allow-local-ahead` is for deliberately unpublished changes; it does not permit
arbitrary divergence. Stale-trunk adoption records the actual replay boundary
and reports when a future rebase is needed. An interrupted adoption retains
`gh-stack-adopt-state` in the common Git directory; inspect it before further
stack operations.

The previous creation, PR checkout, archival, deletion, and trunk-update helpers
have been removed. Use this command family for the complete lifecycle.

## Persistent scratch and temporary files

`$W/codex_scratch_space/` holds working material outside Git checkouts that should
survive an interruption or reboot. Keep material when its loss would cost authored
work, substantial preparation, or useful evidence or context for resuming,
explaining, reproducing, or verifying a task. This includes drafts, patches,
reproducers, investigation notes, diagnostic logs, downloaded inputs, and prepared
tooling. A short failed run can yield important evidence; runtime alone does not
decide what to retain.

Group material by task or reusable purpose:

| Location | Typical contents |
|---|---|
| `$W/codex_scratch_space/<task>/` | Drafts, patches, reproducers, investigation notes |
| `$W/codex_scratch_space/logs/<task>/` | Useful logs and results |
| `$W/codex_scratch_space/toolkits/<purpose>/` | Reusable tooling and prepared inputs |

Reuse established durable locations such as `$L` for QA logs, and keep build
outputs and environments in worktree-local ignored directories where the build
workflow specifies them. Maintained source and documentation belong in their
repositories; real Git checkouts use the `wt` layout above.

Use `/tmp` for cheap disposable plumbing: command inputs, intermediates, or
throwaway environments that can be recreated from durable inputs without losing
useful work or observations. Move useful evidence into durable storage before
relying on it for follow-up.

Use meaningful directory names, with a brief note when the purpose or reason to
retain the contents would otherwise be unclear. Agent handoffs should identify
useful retained artifacts and why they matter. Remove redundant scratch within
the task's scope once useful contents reach their intended destination or are no
longer needed. Keeping important work visible matters more than saving disk space.
