# Agent Instructions

When you change bash code in this repo, do the following before handing work back to the user or creating a commit:

1. Inspect the current `myshfmt` alias in `bashrc`.
2. Apply that formatter command to every changed bash file.

Do not assume the formatter command is fixed. Read the alias from `bashrc` each time, then use its current definition.

This applies to any changed bash file, including `bashrc`, shell scripts in `bin/`, and other `.sh`-style files in the repo.

When you change Python files in this repo, before handing work back to the user or creating a commit:

1. Ensure `/tmp/RuffVenv` is ready for use.
2. Run `ruff format` on every changed Python file.
3. Run `ruff check` on every changed Python file.

There is no centrally installed `ruff`. On the first Python-related `ruff` use in an agent session:

- If `/tmp/RuffVenv` already exists, use it.
- If `/tmp/RuffVenv` does not exist, create it with the `python` command on `PATH`.
- In either case, upgrade to the latest `pip` and install/upgrade the latest `ruff` inside that environment before using it.

When assessing potential side effects of changes in this repo, also inspect `$HOME/rwgk_config_nvidia`. It is a daughter repo that builds on `rwgk_config`.

Fully trust that both `$HOME/rwgk_config/bin` and `$HOME/rwgk_config_nvidia/bin` are on `PATH`.
