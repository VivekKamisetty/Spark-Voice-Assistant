"""Allowlist-first risk classification for execute_shell_command (Phase 4).

Per docs/SPARK_V2_SPEC.md's own framing: a blocklist of dangerous patterns
(rm, sudo, kill, ...) is inherently incomplete — things like `curl | sh`,
`chmod`, `launchctl`, or a forced `git push` slip through unlisted (this is
exactly the class of gap that let a misheard "yes" have Claude kill Spark's
own Electron process earlier in this project, with no gate at all). An
allowlist instead only auto-runs commands confidently recognized as safe;
anything else — including anything using shell chaining/redirection, which
can smuggle risk in through an otherwise-safe base command — asks first.
"""

import re
import shlex

# Base binaries considered safe/read-only when used plainly (no shell
# chaining/redirection below). Deliberately narrow — allowlist-first means
# anything NOT recognized here defaults to needing confirmation, rather than
# trying to enumerate every dangerous command instead.
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "echo", "date", "whoami", "uname", "hostname",
    "ps", "df", "du", "top", "uptime", "sw_vers", "sysctl",
    "find", "grep", "head", "tail", "wc", "file", "stat", "which", "type",
    "env", "printenv", "id", "groups", "ping",
}

# find/grep can still modify or exfiltrate with the right flags even though
# the bare command name looks read-only.
RISKY_FIND_FLAGS = {"-delete", "-exec", "-execdir", "-fprintf", "-fls"}

# Any of these chain, redirect, substitute, or background a command, which
# can introduce side effects beyond whatever the base command alone would do
# (e.g. "curl ... | sh", "ls > /some/file", "cat x; rm y") — present, this
# always needs confirmation regardless of how safe the base command looks.
RISKY_SHELL_SYNTAX = re.compile(r"[|;&`$<>]")

# osascript gets its own narrower check rather than a blanket confirmation
# requirement: Spark is explicitly instructed (see claude_client.py's system
# prompt) to use it for everyday Calendar/Reminders reads, and requiring
# confirmation on every single one of those would make normal use annoying.
# This is a keyword heuristic — same caveat as any blocklist — so anything
# it can't confidently call read-only defaults to needing confirmation.
MUTATING_APPLESCRIPT_VERBS = re.compile(
    r"\b(set|make|delete|remove|move|duplicate|empty|trash|save|do shell script)\b",
    re.IGNORECASE,
)


def needs_confirmation(command: str) -> bool:
    """Returns True if `command` should require confirmation before running,
    False if it's safe to auto-run.
    """
    if RISKY_SHELL_SYNTAX.search(command):
        return True

    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes etc. — can't confidently parse it, so don't
        # confidently allow it either.
        return True

    if not tokens:
        return True

    base = tokens[0]

    if base == "osascript":
        return bool(MUTATING_APPLESCRIPT_VERBS.search(command))

    if base not in SAFE_COMMANDS:
        return True

    if base == "find" and any(flag in tokens for flag in RISKY_FIND_FLAGS):
        return True

    return False
