# CPython collection

Use this guide to refresh Ralf's local CPython collection or build it on a new
development machine. The maintained command is
[`bin/cpython_install.sh`](../bin/cpython_install.sh), available on `PATH`.
Run `cpython_install.sh --help` for its command interface.

## Collection policy

The default collection has ten interpreters, using the latest **final release**
in each requested minor series:

| Python minors | Variants |
| --- | --- |
| 3.9 through 3.12 | `withgil` |
| 3.13 through 3.15 | `withgil`, `freethreaded` |

Resolve releases again at each refresh. Alpha, beta, and release-candidate tags
are excluded. A new minor series requires an explicit request; refreshing the
default collection does not automatically add it.

These are shared-library release builds for development and extension testing,
without PGO, LTO, debug mode, or JIT. The free-threaded variant uses
`--disable-gil`; `withgil` uses the ordinary GIL-enabled build. Installed Python
executables find their shared library without activating an environment or
setting `LD_LIBRARY_PATH`.

Existing installation directories are assumed healthy and left untouched.
Refreshing skips existing `<version>_<variant>` prefixes and adds missing ones.
Legacy directory names remain available through `list` and full-name activation;
`path` selects the new naming scheme. Keep existing `TestVenv` environments and
their base installations in place; refreshing the collection does not select a
new interpreter for an existing project.
If the installer finds its marker for an interrupted new installation, it
stops instead of treating that prefix as complete. Inspect its persistent log
and report the interrupted build; do not automatically overwrite the prefix.

## Layout and provenance

| Path | Purpose |
| --- | --- |
| `$W/forked/cpython` | Persistent source repository, with its primary checkout on `main` |
| `$W/cpython_installs/<version>_<variant>` | New installations, such as `<version>_withgil` |
| `$W/disposable_worktrees/cpython/<version>` | Detached release-tag source worktree used for building |
| `$L`, when set | Persistent build and validation logs |
| `$W/codex_scratch_space/logs/cpython-collection` | Log location when `L` is unset |

The detached CPython build worktrees are an explicit exception to Ralf's normal
`wt` branch/worktree conventions. They are build inputs, with separate build
directories at `build/withgil` and `build/freethreaded` inside each source
worktree. They remain available for follow-up after a build. Keep development
and PR work in normal managed worktrees. Do not build in or switch branches in
the primary CPython checkout.

Each new installation records its release tag, source commit, compiler,
configuration, build time, and log path in `build-info.txt` inside its own
prefix. Build logs have names beginning `cpython_<version>_<variant>_`.
Keep the collection root for installation directories: the former shared
SHA summary is unnecessary because provenance travels with each installation.

## Refresh the collection

Start with the existing local configuration repositories and the `W` environment
variable for the machine's workspace. Inspect source status and available disk
space before building. Preserve any unrelated source changes.

```bash
cpython_install.sh list
cpython_install.sh refresh
```

`refresh` first updates the source repository's `main` with a fast-forward-only
pull and explicitly fetches CPython's upstream tags. It then selects the latest
final release in each default minor series and installs missing variants. This
source update is required on future refreshes, including when Ralf has already
pulled the repository himself.
An existing checkout must be clean, on `main`, with `upstream` pointing to the
official `python/cpython` repository; investigate a failed update while
preserving local changes.

To review selected releases or work on a subset:

```bash
cpython_install.sh update-source
cpython_install.sh releases 3.13 3.14 3.15
cpython_install.sh refresh 3.14 3.15
```

`releases` reads fetched source tags; it does not refresh them. To install an
explicit final version after updating source, use
`cpython_install.sh install VERSION withgil`, `freethreaded`, or `both`.
Omitting the variant selects `withgil` before 3.13 and both variants from 3.13.

The installer checks build prerequisites for new installations and reports
missing dependencies. It does not install or upgrade system packages. Follow
the package workflow below before making such changes.
Build parallelism defaults to at most 32 jobs; set `CPYTHON_BUILD_JOBS` when the
machine's load or memory calls for a different value.

## Build on a new machine

1. Make `~/rwgk_config/bin` available on `PATH`, set `W`, and inspect the machine's
   compiler, build tools, CPython development-library dependencies, and disk space.
2. Run `cpython_install.sh update-source`. It clones the official CPython
   repository into `$W/forked/cpython` when absent; otherwise it updates the
   existing primary `main` checkout. It also explicitly fetches upstream release
   tags. Preserve the machine's existing Git remote configuration when present.
3. Inspect required system packages and useful suggested package updates. Present
   the proposed changes to Ralf and wait for his feedback before changing any
   system package, even when the overall collection task is autonomous.
4. Run `cpython_install.sh refresh`. This updates `main`, fetches upstream tags,
   and builds the default collection with the same layout and validation used
   during refreshes.
5. Inspect the log paths and installation summary, and report installed versions,
   variants, skipped existing installations, and any failures.

### System package changes belong to Ralf

Before requesting package changes, inspect the proposed package-manager
transaction and useful suggested updates, then present the concrete proposal and
wait for Ralf's feedback. Do not run `sudo apt` or install, upgrade, or remove
system packages automatically.

If an agent cannot run the apt simulation, give Ralf a copy-paste command with
the actual proposed package names substituted:

```bash
apt-get --simulate install --no-install-recommends PACKAGE_NAMES |& myt "$L/apt-get_simulate_install_log_$(mlt).txt"
```

Read that persistent log, explain the proposed transaction and any useful
suggested updates, and wait for feedback. When Ralf chooses to install, give him
the corresponding logged command:

```bash
sudo apt-get install --no-install-recommends PACKAGE_NAMES |& myt "$L/apt-get_install_log_$(mlt).txt"
```

Use Ralf's resulting log to establish that prerequisites are ready, then resume
the collection work. Package names vary with the host and release; derive the
actual list from the installer's preflight and the needed standard-library
modules rather than copying an unrelated machine's transaction.

## Use an interpreter

Resolve the newest installed patch version in a minor series without changing
the shell environment:

```bash
python_executable="$(cpython_install.sh path 3.14 withgil)"
"$python_executable" -VV
"$python_executable" -m venv TestVenv
```

`path` also accepts an exact version; its variant defaults to `withgil`. It
selects installed interpreters, whereas `releases` selects source releases.
For scripts and build tools, prefer explicit executable paths and
`python -m pip` over a global shell activation.

For interactive activation, source the script so it can update the calling
shell:

```bash
source cpython_install.sh activate INSTALLATION_DIRECTORY_NAME
python3 -VV
source cpython_install.sh deactivate
```

Use a name printed by `cpython_install.sh list`. Legacy installations may only
provide `python3`; activation leaves their files untouched. Executing an ordinary
child process cannot activate its parent shell.

## Validation and choosing test versions

New installations are validated before the installer reports success. Validation
covers the interpreter version and GIL configuration, important standard-library
extension modules, headless Tk loading, `venv` and pip, and compiling/running a C
embedding program. Python 3.14 and later also require `compression.zstd`.
Use `cpython_install.sh check NAME` when explicitly checking an installation;
normal refreshes skip existing installations without probing them. An explicit
check can diagnose a marked interrupted installation, but preserves its marker
and does not finalize it.

Use the collection to match a project's requirements, not as an obligation to
run every test under all ten interpreters:

| Work | Usual selection |
| --- | --- |
| Routine Python tooling changes | A suitable existing environment |
| Python-version compatibility changes | Oldest and newest supported stable `withgil` interpreters |
| New paired pybind11 setup | Newest supported stable minor, with both variants |
| CUDA Python package work | Existing `TestVenv`, or a supported stable `withgil` interpreter for new setup |
| Changes involving threading or native Python APIs | Relevant supported free-threaded variants and affected Python versions |

Read the target revision's package metadata, CI matrix, repository guidance, and
task requirements before choosing versions. The collection's 3.9 lower bound
does not establish a project's minimum supported version. Preserve a valid
existing project environment unless the task requires changing it. The NVIDIA
`testvenv` and `pybind11-scons` skills describe their build and logging workflows.

## Extending the collection

When Ralf requests a new minor series, update the installer's default minor list
only if he wants it included in future default refreshes. Inspect that release's
configure options, free-threading support, compiler requirements, dependency
changes, and standard-library extension modules. Coordinate the installer and
this guide so the intended build flags and validation stay explicit. Resolve
only final release tags unless Ralf explicitly requests prerelease coverage.

After editing Bash in `rwgk_config`, read the current `myshfmt` alias in `bashrc`
and apply that command to every changed Bash file. Run the focused installer
tests and validate newly added interpreters. Report the exact releases and
variants installed and the persistent logs that establish the result.
