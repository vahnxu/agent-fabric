#!/usr/bin/env bash
# pre_tool_use_safety.sh — Physical interception of irreversible operations at the
# tool boundary. Part of the Agent Operations Fabric governance mesh.
#
# ── WHAT THIS ASSERTS (invariants, not a blacklist of past incidents) ──────────
#   I1. Deletion must be reversible.
#       => No deleter may run in COMMAND POSITION. Reversible deletion (OS trash,
#          timestamped rename) is the only permitted path.
#   I2. A write must never silently destroy an existing file.
#       => cp/mv must carry an explicit no-clobber guard (-n / -i); a truncating
#          redirect onto an existing regular file is refused.
#
# Asserting the invariant (rather than enumerating one past incident's syntax) is
# deliberate: a blacklist is bypassed the moment the same invariant is violated
# through a different carrier — an absolute path, a backslash escape, a glued
# separator, find's own -delete, or a one-line interpreter call.
# See docs/02_minimal_kernel.md § Meta-Rule 1.
#
# ── COMMAND POSITION ──────────────────────────────────────────────────────────
# A deleter is only dangerous when it is EXECUTED. It is executed when it sits at
# the start of the line, right after a shell separator (; & | && || ( ) { } $( ),
# after a runner keyword that is itself in command position (sudo/command/eval/…),
# or is handed to an executor in command position (bash -c, ssh host, xargs,
# find -exec). The same letters appearing as a quoted ARGUMENT, a search pattern,
# a comment, or a substring (rmdir, farm, rm_count) are NOT executed and must be
# allowed — a gate that cries wolf gets switched off, which is worse than no gate.
#
# Quote and backslash characters are stripped from the inspected copy before
# matching. This is safe precisely because command position is detected via
# SEPARATORS: a quoted argument is never preceded by a separator, so a mention
# inside a string stays out of command position, while an obfuscated command name
# collapses back onto a separator and is caught.
#
# ── KNOWN NON-COVERAGE (restate these whenever you restate this gate) ─────────
#   N1. Indirection through a variable or a script file is not resolved. Assigning
#       a deleter to a variable and expanding it, or invoking ./cleanup.sh, is let
#       through. This gate inspects one command string, not the program it reaches.
#   N2. Deletion performed by a compiled binary or a long-lived daemon never
#       crosses this boundary at all.
#   N3. It cannot tell a valuable target from a scratch one — it refuses the ACT,
#       not the target. Reversibility, not judgement, is what it buys you.
#
# ── I/O CONTRACT ──────────────────────────────────────────────────────────────
#   Input : argv[1] = the command string, OR a hook JSON payload on stdin
#           (.tool_input.command / .toolInput.command / .toolCall.args.command).
#   Output: stdout stays EMPTY. The reason goes to stderr.
#   Exit  : 0 = allow · 2 = block · 3 = malformed payload (fail closed)
# Runtime-agnostic by design: no CLI vendor name appears in this file.
set -u

CMD="${1:-}"

# stdin JSON payload (hook mode). Only consulted when argv is empty.
#
# The read is BOUNDED. A hook that blocks does not fail safe — it wedges the
# agent that called it, and from the outside that is indistinguishable from a
# hung task. A real payload is already buffered when the hook starts, so the
# timeout only ever fires when there was nothing coming.
STDIN_WAIT_S="${FABRIC_STDIN_WAIT_S:-1}"
if [[ -z "$CMD" ]] && [[ ! -t 0 ]]; then
    payload=""
    _line=""
    # `|| [[ -n "$_line" ]]` catches a final line with no trailing newline: read
    # returns non-zero at EOF but has already assigned it. Dropping it silently
    # would make a one-line JSON payload look like an empty payload — which is
    # allow, not fail-closed, and therefore the worst possible direction to fail.
    while IFS= read -r -t "$STDIN_WAIT_S" _line || [[ -n "$_line" ]]; do
        payload+="$_line"
        _line=""
    done
    if [[ -n "$payload" ]]; then
        if command -v jq >/dev/null 2>&1; then
            CMD="$(printf '%s' "$payload" | jq -r '
                .tool_input.command // .toolInput.command
                // .tool_input.command_line // .toolInput.command_line
                // .toolCall.args.command // .toolCall.args.CommandLine
                // empty' 2>/dev/null || true)"
        fi
        if [[ -z "$CMD" || "$CMD" == "null" ]]; then
            echo "FAIL_LOUD [PreToolUse]: payload received but no shell command could be extracted." >&2
            echo "Reason: failing closed — an uninspectable command is treated as unsafe." >&2
            exit 3
        fi
    fi
fi

[[ -z "$CMD" ]] && exit 0

# Inspected copy: quote and backslash characters removed (see header).
S="$(printf '%s' "$CMD" | tr -d "\\047\\042\\140\\134")"

# A deleter as a word, optionally given by absolute path.
DELETER='((/[^[:space:]]*/)?(rm|shred|srm))([^[:alnum:]_/]|$)'
# Command position: line start, or immediately after a shell separator.
CMDPOS='(^|[;&|(){}]|&&|\|\||\$\()[[:space:]]*'
# Runners that are transparent — whatever follows them is still command position.
RUNNERS='(sudo|command|eval|exec|nohup|env|time|timeout|nice|doas|then|else|do)'
# Executors that accept a command anywhere in their arguments.
EXECUTORS='(bash|sh|zsh|dash|ksh|fish|ssh|xargs|eval)'

block=""

# ── I1: deletion must be reversible ───────────────────────────────────────────
# (a) a deleter directly in command position
if printf '%s' "$S" | grep -qE "${CMDPOS}${DELETER}"; then
    block="a deleter runs in command position"
fi
# (b) a deleter behind a transparent runner
if [[ -z "$block" ]] && printf '%s' "$S" | grep -qE "${CMDPOS}${RUNNERS}[[:space:]]+${DELETER}"; then
    block="a deleter runs via a transparent runner (sudo/command/eval/...)"
fi
# (c) an executor in command position carrying a deleter anywhere after it
if [[ -z "$block" ]] \
   && printf '%s' "$S" | grep -qE "${CMDPOS}${EXECUTORS}([[:space:]]|$)" \
   && printf '%s' "$S" | grep -qE "(^|[^[:alnum:]_/])${DELETER}"; then
    block="a deleter is handed to an executor (bash -c / ssh / xargs)"
fi
# (d) find's -exec/-execdir running a deleter, and find's own -delete
if [[ -z "$block" ]] && printf '%s' "$S" | grep -qE "[^[:alnum:]_]exec(dir)?[[:space:]]+${DELETER}"; then
    block="a deleter runs through find -exec"
fi
if [[ -z "$block" ]] && printf '%s' "$S" | grep -qE "(^|[^[:alnum:]_-])find[[:space:]].*[[:space:]]-delete([^[:alnum:]_]|$)"; then
    block="find -delete removes files irreversibly"
fi
# (e) an interpreter one-liner reaching a deletion API
if [[ -z "$block" ]] \
   && printf '%s' "$S" | grep -qE "${CMDPOS}(python[0-9.]*|perl|ruby|node)([[:space:]]|$)" \
   && printf '%s' "$S" | grep -qE '(rmtree|os\.remove|os\.unlink|\.unlink\(|unlink[[:space:]]*\(|fs\.rm|File\.delete|FileUtils\.rm)'; then
    block="an interpreter one-liner calls a deletion API"
fi
# (f) in-place truncation of an existing file
if [[ -z "$block" ]] && printf '%s' "$S" | grep -qE "${CMDPOS}(sudo[[:space:]]+)?truncate([[:space:]]|$)"; then
    block="truncate destroys file contents in place"
fi

if [[ -n "$block" ]]; then
    echo "FAIL_LOUD [PreToolUse Safety Intercept]: $block." >&2
    echo "Invariant I1: deletion must be reversible. Move the target to the OS trash," >&2
    echo "or rename it with a timestamp suffix, then proceed." >&2
    echo "Command: $CMD" >&2
    exit 2
fi

# ── I2: a write must not silently destroy an existing file ────────────────────
# cp/mv without an explicit no-clobber guard. Asserting "must carry a guard" is
# decidable from the command string alone; asking "does the target already exist"
# is not (globs, variables, remote paths) — so the guard, not the target, is what
# this asserts.
if printf '%s' "$S" | grep -qE "${CMDPOS}(sudo[[:space:]]+)?(cp|mv)([[:space:]]|$)"; then
    if ! printf '%s' "$S" | grep -qE "(cp|mv)[[:space:]]+(-[[:alnum:]]*[ni][[:alnum:]]*)([[:space:]]|$)"; then
        echo "FAIL_LOUD [PreToolUse Safety Intercept]: cp/mv without a no-clobber guard." >&2
        echo "Invariant I2: a write must never silently destroy an existing file." >&2
        echo "Pass -n (no-clobber) or -i (interactive), or rename the existing target first." >&2
        echo "Command: $CMD" >&2
        exit 2
    fi
fi

# A truncating redirect onto an existing regular file. Here existence IS decidable:
# the literal path sits in the command string. Append (>>) and /dev/* are fine.
redirect_target="$(printf '%s' "$S" | sed -nE 's/.*[^>&]>[[:space:]]*([^[:space:]|;&<>]+).*/\1/p' | head -1)"
if [[ -n "$redirect_target" && "$redirect_target" != /dev/* ]]; then
    if [[ -f "$redirect_target" ]]; then
        echo "FAIL_LOUD [PreToolUse Safety Intercept]: redirect truncates the existing file '$redirect_target'." >&2
        echo "Invariant I2: a write must never silently destroy an existing file." >&2
        echo "Append with '>>', or rename the existing file first." >&2
        exit 2
    fi
fi

exit 0
