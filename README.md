# rwgk_config
Ralf's personal configs like bashrc etc.

```
git clone https://github.com/rwgk/rwgk_config.git
```

## Adopt a GitHub stack locally

After merging some PRs on GitHub, adopt the automatically updated stack without
replaying or publishing its commits again:

```bash
gh_stack_adopt_from_github.py 3179          # Preview
gh_stack_adopt_from_github.py 3179 --apply  # Apply locally
```

The number must identify a native GitHub **stack**, not a PR. The helper runs
from any worktree of the repository; `--worktree PATH` selects another checkout.
Repository and remote selection are inferred only when unambiguous. Use
`--repo OWNER/REPO` and `--remote upstream` when needed. `--json` emits the plan
or result as JSON. Preview fetches remote-tracking refs but leaves local branches,
working files and stack metadata unchanged.
When both branch tips and saved stack metadata match GitHub, the helper reports
that there is nothing to adopt; `--apply` leaves that state untouched.

Adoption requires an existing matching local stack and clean affected worktrees.
Before replacing a local tip, the helper verifies publication through current
GitHub history or the PR's recorded force-push history. Unpublished or unverified
local work stops adoption. Existing branch tracking and unrelated stacks are
preserved; merged branches are left for the usual archival/deletion helpers.

Local trunk advances only by fast-forward. If GitHub's stack has not caught up
with the newest trunk commit, adoption records the actual replay boundary and
reports that a future rebase is needed. Verify that state with:

```bash
gh_stack_check.py --worktree "$PWD" --remote upstream --allow-stale-trunk
```

The checker's default remains strict. Adoption uses gh-stack's POSIX locks and
an expected-old Git ref transaction. Interrupted or incomplete updates retain
`gh-stack-adopt-state` in the common Git directory, including old/target refs and
the intended catalog. Inspect that journal and affected worktrees before further
stack operations. The helper never pushes, rebases, archives or deletes branches.
