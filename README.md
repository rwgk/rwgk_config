# rwgk_config

Ralf's personal configuration and commands.

```bash
git clone https://github.com/rwgk/rwgk_config.git
```

## Branches and worktrees with `wt`

Each active local branch owns one worktree. The primary checkout stays on trunk;
navigation changes directories. New development branches are `rwgk/<slug>` and
live under `$W/trees/<repo-key>/`, normally `/wrk/trees/<repo-key>/`.

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
and `gh` for GitHub operations. Stack commands need a **worktree-aware gh-stack
build** that rebases each layer in its owning worktree. Ralf's installed extension
contains local changes; its version tag alone does not establish that support.
Native Windows Python lacks the POSIX locking used by this engine.

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
wt publish --title 'Fix stream lifetime' --body-file /tmp/pr-body.txt
wt go pr1234                    # after GitHub assigns a PR number
```

New work starts from configured local trunk. Publication creates or updates the
branch and PR, then attaches the PR alias. New PRs default to draft; `--ready`
requests a ready PR. Existing PR readiness is preserved. Publication and merging
remain separate operations.

Tracking is established after a successful new or fast-forward publication when
missing. Existing tracking is preserved. A rewritten existing remote branch with
no local upstream needs an explicit tracking choice (`--set-upstream`); inspect
the reported destination before making that choice.

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
wt pr 1234 --refresh             # explicitly update after preserving local work
```

Your own PR reuses its local branch when available. Another author's PR gets a
local `rwgk/review-pr1234` branch. PR head identity is recorded separately from
the local branch name, and the receiving repository's PR head ref permits
checkout after a contributor fork or branch disappears. Refresh stops when local
work cannot be updated safely. Publishing changes to another author's branch
requires a separate explicit action; ordinary `wt publish` does not imply that.

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
`--continue`, and `--abort`. It retains recovery refs before rewriting.

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

Publication checks exact destination refs and uses fixed expected-SHA leases.
Before replacing published history it preserves the old tip when necessary.
Concurrent remote changes stop publication for investigation. A failed lease is
not an invitation to refresh the expected SHA and overwrite new work.

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
cleanup. Known disposable build outputs
need the explicit `--discard-builds` option. Inspect the preview before applying.

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
