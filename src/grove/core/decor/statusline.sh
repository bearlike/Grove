#!/bin/sh
# Grove: Claude Code's statusline, for an agent running inside a container.
#
# Claude Code pipes its statusline payload as JSON on stdin and renders whatever
# comes back on stdout. Contract here: print one or two lines and ALWAYS exit 0
# — a statusline that fails is a statusline that silently disappears, which is
# the exact failure this file exists to fix (the host's own statusline script
# lives beside a `settings.json` that Grove bind-mounts read-only without it).
#
# Two lines, and the split is the reading order rather than an overflow:
#
#   line 1 — WHERE AM I:  badge · user@host · directory · branch
#   line 2 — WHAT IS IT COSTING: model · effort · context · quota · resources
#
# So the eye lands in the same place every time: the badge is always column 0,
# the machine is always the second thing on line 1, and every number is on
# line 2. The badge leads and never shrinks — knowing you are inside a container
# is the single most important thing this line says, and hostname is the second
# (inside a container "which machine am I on" is genuinely ambiguous, and that is
# exactly when a person needs to be told).
#
# Every segment degrades on its own. No `git`, no `python3`, no `/etc/hostname`,
# a missing field or an unparseable payload each cost that one segment and
# nothing else.

set -u

ESC=$(printf '\033')
RESET="$ESC[0m"
C_BADGE="$ESC[1;48;5;141;38;5;235m"
C_MODEL="$ESC[38;5;117m"
C_DIR="$ESC[38;5;109m"
C_GIT="$ESC[38;5;176m"
C_HOST="$ESC[1;38;5;180m"
C_USER="$ESC[38;5;245m"
C_ICON="$ESC[38;5;146m"
C_DIM="$ESC[38;5;245m"
C_OK="$ESC[38;5;114m"
C_WARN="$ESC[38;5;179m"
C_HOT="$ESC[38;5;174m"

MODEL=""
CWD=""
EFFORT=""
CTX_PCT=""
CTX_USED=""
CTX_SIZE=""
FIVE_HOUR_PCT=""
FIVE_HOUR_RESETS=""
SEVEN_DAY_PCT=""
SEVEN_DAY_RESETS=""

# ── vocabulary ───────────────────────────────────────────────────────────────
# An icon REPLACES a word; it never decorates one. `ctx`, `effort` and `usage`
# are gone from the icon vocabulary entirely, which is where the horizontal room
# for identity came from.
#
# Whether those glyphs render is NOT a property of this container: a font is
# installed in the terminal emulator the human is attached FROM, and nothing
# reachable from inside the image can see it. There is therefore nothing honest
# to probe, so the defence is a switch rather than a detection — and the ASCII
# vocabulary is not a mangled version of the icon one, it puts the words back.
# The one thing both share is that neither ever renders a bare label with no
# value behind it.
#
# Each vocabulary carries two badges. The narrowest pane drops "DEV" and nothing
# else: the word CONTAINER, the accent and the leading position ARE the badge,
# and "DEV" is the only part of it a person is not missing information without.
if [ "${GROVE_STATUSLINE_GLYPHS:-nerd}" = "ascii" ]; then
    BADGE_TEXT="[ DEV CONTAINER ]"
    BADGE_NARROW="[ CONTAINER ]"
    I_ID=""
    I_DIR=""
    I_GIT="git"
    I_MODEL=""
    I_EFFORT="effort"
    I_CTX="ctx"
    I_USAGE="usage"
    I_RES=""
else
    # `⬢` is a plain geometric shape rather than a Nerd Font private-use glyph,
    # deliberately: the badge is the one segment that must survive a font that
    # renders nothing else, so it is the one segment that asks least of it.
    BADGE_TEXT="⬢ DEV CONTAINER"
    BADGE_NARROW="⬢ CONTAINER"
    I_ID="󰀄"
    I_DIR="󰉋"
    I_GIT="󰘬"
    I_MODEL="󰚩"
    I_EFFORT="󰓅"
    I_CTX="󰔂"
    I_USAGE="󰥔"
    I_RES="󰍛"
fi

# ── width ────────────────────────────────────────────────────────────────────
# A statusline competes with the agent's own output for terminal rows, so it
# ELIDES rather than wraps: a line that wraps costs a second row for two words
# and shifts every following row, which is worse than the fact it was carrying.
# `tput` needs a terminfo database this image may not have, so an unreadable
# width falls back to a conservative-but-not-tiny 100 rather than to nothing.
COLS=${GROVE_STATUSLINE_COLUMNS:-${COLUMNS:-}}
[ -n "$COLS" ] || COLS=$(tput cols 2>/dev/null) || COLS=""
case $COLS in
    '' | *[!0-9]*) COLS=100 ;;
esac

TIER=wide
[ "$COLS" -lt 100 ] && TIER=mid
[ "$COLS" -lt 72 ] && TIER=narrow

# ── payload ──────────────────────────────────────────────────────────────────
# One fork for the whole parse: python3 emits shell-quoted KEY=VALUE lines that
# are eval'd here, rather than being re-invoked per field. `jq` is never
# required — plenty of real images have python3 and no jq, and some have
# neither, which is what the degraded branch below is for.
payload=$(cat 2>/dev/null || true)
if [ -n "$payload" ] && command -v python3 > /dev/null 2>&1; then
    parsed=$(printf '%s' "$payload" | python3 -c '
import json, shlex, sys

def out(key, value):
    if value is None:
        return
    text = str(value).strip()
    if text:
        sys.stdout.write("%s=%s\n" % (key, shlex.quote(text)))

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)

def dig(*path):
    cur = data
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur

def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

out("MODEL", dig("model", "display_name"))
out("CWD", dig("workspace", "current_dir") or data.get("cwd"))
out("EFFORT", dig("effort", "level"))

size = num(dig("context_window", "context_window_size"))
total_in = num(dig("context_window", "total_input_tokens"))
total_out = num(dig("context_window", "total_output_tokens"))
used = None
if total_in is not None or total_out is not None:
    used = (total_in or 0) + (total_out or 0)
else:
    current = dig("context_window", "current_usage")
    if isinstance(current, dict):
        parts = [
            num(current.get(key))
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        ]
        parts = [part for part in parts if part is not None]
        if parts:
            used = sum(parts)

pct = num(dig("context_window", "used_percentage"))
if pct is None and used is not None and size:
    pct = 100.0 * used / size
if pct is not None:
    out("CTX_PCT", int(round(pct)))
if used is not None:
    out("CTX_USED", int(used))
if size is not None:
    out("CTX_SIZE", int(size))

# Subscription usage. Claude Code populates `rate_limits` for an OAuth
# subscription login, which is exactly what a container has: the credential
# store is shared into it, so the same pools the host reports are reported
# here. There is deliberately NO cache fallback — the host'"'"'s polled snapshot
# under ~/.cache is not shared into a container, and inventing a number for a
# pool we cannot read would be worse than omitting the segment.
for key, prefix in (("five_hour", "FIVE_HOUR"), ("seven_day", "SEVEN_DAY")):
    share = num(dig("rate_limits", key, "used_percentage"))
    if share is not None:
        out("%s_PCT" % prefix, int(round(share)))
    resets = num(dig("rate_limits", key, "resets_at"))
    if resets is not None:
        out("%s_RESETS" % prefix, int(resets))
' 2>/dev/null) || parsed=""
    [ -n "$parsed" ] && eval "$parsed"
fi

# With no python3 nothing has parsed `workspace.current_dir`, so the directory
# segment falls back to this process's own cwd. That is deliberate, and the
# alternative was weighed: pulling `current_dir` out of the JSON with sed/grep
# would be more faithful to the payload, at the cost of a hand-rolled JSON parse
# living on the one path that exists because the machine is already impoverished.
# It buys nothing in practice — Claude Code spawns the statusline IN the agent's
# cwd, so `$PWD` and `workspace.current_dir` are the same directory — and a regex
# JSON parse is exactly the clever-over-boring trade this repo argues against.
#
# `pwd` over `$PWD`: the shell builtin always reports where this process really
# is, while an inherited PWD is whatever the PARENT exported and can name a
# different directory entirely. Being a builtin it costs no fork and needs no
# PATH, which matters on precisely this degraded branch.
[ -z "$CWD" ] && CWD=$(pwd 2>/dev/null || printf '%s' "${PWD:-}")

# ── identity ─────────────────────────────────────────────────────────────────
# Neither name is guaranteed by anything: a slim image may have no `whoami`, no
# `hostname` binary and no populated `$USER`, and a devcontainer's exec env is
# whatever the CLI hands over rather than a login shell's. So each is a chain of
# cheap sources ending in NOTHING — an absent segment is honest, an empty one
# beside an `@` reads as a bug in Grove.
#
# Ordering within each chain is cheapest-first, and every fork is guarded by
# `command -v` so a missing binary costs a builtin test rather than an error
# line on the pane.
CLIP_TAIL="…"

# Truncate to *n* characters, in-shell. No `cut`, because this is the one file
# that has to keep working on an image which ships almost nothing.
clip() {
    text=$1
    limit=$2
    [ ${#text} -gt "$limit" ] || {
        printf '%s' "$text"
        return 0
    }
    while [ ${#text} -gt $((limit - 1)) ]; do
        text=${text%?}
    done
    printf '%s%s' "$text" "$CLIP_TAIL"
}

user_name=${USER:-}
[ -n "$user_name" ] || user_name=${LOGNAME:-}
[ -n "$user_name" ] || user_name=${USERNAME:-}
if [ -z "$user_name" ] && command -v id > /dev/null 2>&1; then
    # `id -un` over `whoami`: same answer, but `id` is in busybox's applet set
    # where `whoami` frequently is not.
    user_name=$(id -un 2>/dev/null) || user_name=""
fi
if [ -z "$user_name" ] && [ -n "${HOME:-}" ] && [ "${HOME:-}" != "/" ]; then
    user_name=${HOME##*/}
fi

host_name=${HOSTNAME:-}
if [ -z "$host_name" ] && [ -r /etc/hostname ]; then
    # A `read` builtin off the file the container runtime itself writes: no
    # fork, no PATH, and it is populated in every OCI container by construction.
    read -r host_name < /etc/hostname 2>/dev/null || host_name=""
fi
if [ -z "$host_name" ] && command -v hostname > /dev/null 2>&1; then
    host_name=$(hostname 2>/dev/null) || host_name=""
fi
if [ -z "$host_name" ] && command -v uname > /dev/null 2>&1; then
    host_name=$(uname -n 2>/dev/null) || host_name=""
fi
# Short form only: a status bar has no room for an FQDN, and the leading label
# is the part that identifies the machine.
host_name=${host_name%%.*}
if [ "$TIER" = narrow ]; then
    host_name=$(clip "$host_name" 12)
else
    host_name=$(clip "$host_name" 20)
fi
user_name=$(clip "$user_name" 16)

identity=""
if [ -n "$user_name" ] && [ -n "$host_name" ] && [ "$TIER" != narrow ]; then
    identity="$C_USER$user_name$RESET$C_DIM@$RESET$C_HOST$host_name$RESET"
elif [ -n "$host_name" ]; then
    # Narrow, or no username at all: the machine is the load-bearing half, so it
    # is the half that survives.
    identity="$C_HOST$host_name$RESET"
elif [ -n "$user_name" ]; then
    identity="$C_USER$user_name$RESET"
fi

# ── helpers ──────────────────────────────────────────────────────────────────
short_dir() {
    d=$1
    max=$2
    home=${HOME:-}
    if [ -n "$home" ]; then
        case $d in
            "$home") d="~" ;;
            "$home"/*) d="~${d#"$home"}" ;;
        esac
    fi
    # A status bar has no room for a deep path, and the leaf plus its parent is
    # what identifies a worktree. At the narrowest tier even the parent goes:
    # the leaf alone still tells you which workspace you are in, which is the
    # question the segment exists to answer.
    # `0` is the narrowest tier's request: the leaf alone, capped hard. It still
    # tells you which workspace you are in, which is the question the segment
    # exists to answer.
    if [ "$max" -eq 0 ]; then
        clip "${d##*/}" 16
        return 0
    fi
    if [ ${#d} -gt "$max" ]; then
        leaf=${d##*/}
        parent=${d%/*}
        parent=${parent##*/}
        d="…/$parent/$leaf"
        # The parent is a courtesy; the leaf is the answer. So when even the
        # two-component form does not fit, the parent goes rather than the leaf
        # losing its tail to a truncation.
        [ ${#d} -gt "$max" ] && d=$(clip "$leaf" "$max")
    fi
    printf '%s' "$d"
}

thousands() {
    if [ "$1" -ge 1000 ] 2>/dev/null; then
        printf '%sk' "$(($1 / 1000))"
    else
        printf '%s' "$1"
    fi
}

# Percentages here are all "how much is CONSUMED", so the colour ramps upward:
# calm until half, warning past half, hot past 80. One helper for the context
# gauge and both usage pools, because three copies of a threshold is how they
# come to disagree.
usage_color() {
    if [ "$1" -ge 80 ] 2>/dev/null; then
        printf '%s' "$C_HOT"
    elif [ "$1" -ge 50 ] 2>/dev/null; then
        printf '%s' "$C_WARN"
    else
        printf '%s' "$C_OK"
    fi
}

# Seconds until a pool resets, as the coarsest unit that is still useful. Past a
# day, minutes are noise — and a raw hour count hides the day rather than
# showing it. Empty output means the reset is unknown or already past, which the
# caller renders as no clause at all rather than as "0m".
reset_in() {
    [ -n "${1:-}" ] || return 0
    now=$(date +%s 2>/dev/null) || return 0
    left=$((${1} - now))
    [ "$left" -gt 0 ] 2>/dev/null || return 0
    mins=$((left / 60))
    hours=$((mins / 60))
    mins=$((mins % 60))
    days=$((hours / 24))
    hours=$((hours % 24))
    if [ "$days" -gt 0 ]; then
        printf '%dd %dh' "$days" "$hours"
    elif [ "$hours" -gt 0 ]; then
        printf '%dh %dm' "$hours" "$mins"
    else
        printf '%dm' "$mins"
    fi
}

# One pool as `5h 12% (2h 5m)`. Emits nothing when the pool is absent, which is
# the ordinary case for an API-key login: those carry no subscription pools at
# all, and a zero would read as "none used" rather than "not applicable". The
# countdown is the first thing a narrower pane gives up — the percentage is the
# number a decision turns on, the countdown only says when it stops mattering.
pool_segment() {
    [ -n "$2" ] || return 0
    seg="$(usage_color "$2")$1 $2%$RESET"
    if [ "$TIER" = wide ]; then
        left=$(reset_in "${3:-}")
        [ -n "$left" ] && seg="$seg $C_DIM($left)$RESET"
    fi
    printf '%s' "$seg"
}

# One segment onto the line being built. The icon is emitted only when the
# vocabulary supplies one AND there is a value behind it, so a missing fact
# never leaves a naked glyph or a doubled separator.
LINE=""
push() {
    [ -n "$2" ] || return 0
    piece=$2
    [ -n "$1" ] && piece="$C_ICON$1$RESET $2"
    if [ -z "$LINE" ]; then
        LINE=$piece
    else
        LINE="$LINE  $piece"
    fi
}

# ── git ──────────────────────────────────────────────────────────────────────
branch=""
staged=0
dirty=0
untracked=0
if [ -n "$CWD" ] && command -v git > /dev/null 2>&1 &&
    git -C "$CWD" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    branch=$(git -C "$CWD" rev-parse --abbrev-ref HEAD 2>/dev/null) || branch=""
    # Fed by a heredoc rather than a pipe on purpose: a pipeline would run the
    # loop in a subshell in most shells and every counter would be lost.
    while IFS= read -r entry; do
        tail_x=${entry#?}
        x=${entry%"$tail_x"}
        tail_y=${tail_x#?}
        y=${tail_x%"$tail_y"}
        if [ "$x" = "?" ] && [ "$y" = "?" ]; then
            untracked=$((untracked + 1))
        else
            [ -n "$x" ] && [ "$x" != " " ] && staged=$((staged + 1))
            [ -n "$y" ] && [ "$y" != " " ] && dirty=$((dirty + 1))
        fi
    done <<GIT_STATUS
$(git -C "$CWD" status --porcelain 2>/dev/null)
GIT_STATUS
fi

# ── resources ────────────────────────────────────────────────────────────────
# Where a host statusline would show a load average. Resolved as a sibling of
# this script first so an operator-supplied payload stays self-consistent, and
# invoked through `sh` so a lost execute bit cannot silently blank the segment.
# Skipped outright on the narrowest tier: it is the only segment whose absence
# costs nothing a person is mid-decision about, so it is the one to drop first.
here=${0%/*}
[ "$here" = "$0" ] && here="."
resources=""
if [ "$TIER" != narrow ]; then
    if [ -r "$here/resources.sh" ]; then
        resources=$(sh "$here/resources.sh" 2>/dev/null) || resources=""
    elif [ -r /grove/decor/resources.sh ]; then
        resources=$(sh /grove/decor/resources.sh 2>/dev/null) || resources=""
    fi
fi

# ── render: line 1, WHERE AM I ───────────────────────────────────────────────
# Every variable-length field is capped per tier, so the worst-case width of a
# line is a known number rather than whatever a repository happened to name its
# branch. That is what makes "elide, never wrap" a property instead of a hope.
case $TIER in
    wide)
        dir_max=34
        branch_max=24
        model_max=22
        ;;
    mid)
        dir_max=24
        branch_max=18
        model_max=18
        ;;
    *)
        dir_max=0
        branch_max=10
        model_max=14
        ;;
esac

[ "$TIER" = narrow ] && BADGE_TEXT=$BADGE_NARROW

LINE="$C_BADGE $BADGE_TEXT $RESET"
push "$I_ID" "$identity"
[ -n "$CWD" ] && push "$I_DIR" "$C_DIR$(short_dir "$CWD" "$dir_max")$RESET"
if [ -n "$branch" ]; then
    marks=""
    [ "$staged" -gt 0 ] && marks="$marks +$staged"
    [ "$dirty" -gt 0 ] && marks="$marks *$dirty"
    [ "$untracked" -gt 0 ] && marks="$marks ?$untracked"
    push "$I_GIT" "$C_GIT$(clip "$branch" "$branch_max")$marks$RESET"
fi
printf '%s\n' "$LINE"

# ── render: line 2, WHAT IS IT COSTING ───────────────────────────────────────
ctx=""
if [ -n "$CTX_PCT" ]; then
    # The percentage leads because it is the one number common to every tier;
    # the headroom clause follows because that is what a decision actually turns
    # on — whether there is room for the next step.
    ctx="${CTX_PCT}%"
    if [ "$TIER" != narrow ] && [ -n "$CTX_USED" ] && [ -n "$CTX_SIZE" ]; then
        left_tokens=$((CTX_SIZE - CTX_USED))
        [ "$left_tokens" -lt 0 ] && left_tokens=0
        if [ "$TIER" = wide ]; then
            ctx="$ctx · $(thousands "$left_tokens") left of $(thousands "$CTX_SIZE")"
        else
            ctx="$ctx · $(thousands "$left_tokens") left"
        fi
    fi
    ctx="$(usage_color "$CTX_PCT")$ctx$RESET"
fi

usage=""
for pool in "5h|$FIVE_HOUR_PCT|$FIVE_HOUR_RESETS" "7d|$SEVEN_DAY_PCT|$SEVEN_DAY_RESETS"; do
    label=${pool%%|*}
    rest=${pool#*|}
    seg=$(pool_segment "$label" "${rest%%|*}" "${rest#*|}")
    [ -n "$seg" ] || continue
    [ -n "$usage" ] && usage="$usage $C_DIM·$RESET "
    usage="$usage$seg"
done

LINE=""
[ -n "$MODEL" ] && push "$I_MODEL" "$C_MODEL$(clip "$MODEL" "$model_max")$RESET"
[ "$TIER" != narrow ] && [ -n "$EFFORT" ] && push "$I_EFFORT" "$C_DIM$EFFORT$RESET"
push "$I_CTX" "$ctx"
push "$I_USAGE" "$usage"
[ -n "$resources" ] && push "$I_RES" "$C_DIM$resources$RESET"
[ -n "$LINE" ] && printf '%s\n' "$LINE"

exit 0
