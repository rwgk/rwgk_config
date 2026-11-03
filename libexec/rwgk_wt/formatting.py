"""Bounded human-readable filename lists; structured output keeps full details."""


def summarize_lines(lines):
    lines = list(lines)
    if len(lines) <= 10:
        return lines
    return lines[:3] + [f"... {len(lines) - 6} lines omitted ..."] + lines[-3:]


def format_file_list(heading, lines):
    return "\n".join((heading, *summarize_lines(lines)))
