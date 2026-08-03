#!/bin/sh
# Grove: one short line describing THIS CONTAINER's own CPU and memory use.
#
# NEVER read /proc/loadavg here. That file is not namespaced: inside a container
# it reports the HOST's run queue, so a workspace capped at 1.5 vCPU would show
# a number that looks like a measurement of itself while measuring a machine it
# cannot see. A wrong number is worse than no number — do not "helpfully" add it
# back. The cgroup files below are the only per-container truth available.
#
# Contract: prints at most ONE line and always exits 0. A status bar must never
# render an error, and an unreadable cgroup is a legitimate answer (print
# nothing). Pure POSIX sh — no bash, no awk, no jq: this runs inside whatever
# minimal image the project chose.

set -u

# Overridable so the arithmetic below can be exercised by a real `sh` against a
# synthetic cgroup tree. On a HOST the root cgroup carries no `cpu.max` or
# `memory.current` at all, so there is no way to test the interesting paths
# in place — and a shell script verified only as a string is a failure mode this
# repo has already paid for.
CG="${GROVE_CGROUP_ROOT:-/sys/fs/cgroup}"
STATE="${TMPDIR:-/tmp}/grove-resources.$(id -u 2>/dev/null || echo 0)"

cpu_quota=""   # microseconds of CPU allowed per period; "" when uncapped
cpu_period=""
cpu_usage=""   # cumulative microseconds of CPU consumed
mem_used=""
mem_limit=""   # "" when uncapped

# ── read the cgroup, v2 first ────────────────────────────────────────────────
if [ -r "$CG/cpu.max" ] && read -r q p < "$CG/cpu.max" 2>/dev/null; then
    # "<quota> <period>", where quota is the literal "max" when uncapped.
    if [ "$q" != "max" ]; then
        cpu_quota=$q
        cpu_period=$p
    fi
    if [ -r "$CG/cpu.stat" ]; then
        while read -r key value rest; do
            if [ "$key" = "usage_usec" ]; then
                cpu_usage=$value
                break
            fi
        done < "$CG/cpu.stat"
    fi
elif [ -r "$CG/cpu/cpu.cfs_quota_us" ]; then
    # cgroup v1: a quota of -1 means uncapped.
    read -r q < "$CG/cpu/cpu.cfs_quota_us" 2>/dev/null || q=-1
    read -r p < "$CG/cpu/cpu.cfs_period_us" 2>/dev/null || p=""
    if [ "$q" -gt 0 ] 2>/dev/null && [ -n "$p" ]; then
        cpu_quota=$q
        cpu_period=$p
    fi
    if read -r ns < "$CG/cpuacct/cpuacct.usage" 2>/dev/null; then
        cpu_usage=$((ns / 1000))   # cpuacct reports nanoseconds
    fi
fi

if [ -r "$CG/memory.current" ]; then
    read -r mem_used < "$CG/memory.current" 2>/dev/null || mem_used=""
    if read -r limit < "$CG/memory.max" 2>/dev/null && [ "$limit" != "max" ]; then
        mem_limit=$limit
    fi
elif [ -r "$CG/memory/memory.usage_in_bytes" ]; then
    read -r mem_used < "$CG/memory/memory.usage_in_bytes" 2>/dev/null || mem_used=""
    if read -r limit < "$CG/memory/memory.limit_in_bytes" 2>/dev/null; then
        # v1 spells "uncapped" as a sentinel near 2^63 rather than a keyword.
        if [ "$limit" -lt 4611686018427387904 ] 2>/dev/null; then
            mem_limit=$limit
        fi
    fi
fi

# ── CPU percent needs a DELTA, so keep the previous sample ───────────────────
# The first call in a container has no previous sample and therefore no honest
# CPU figure; it prints memory only rather than inventing one. A read-only
# TMPDIR costs the CPU segment permanently and nothing else.
now=$(date +%s 2>/dev/null || echo "")
prev_usage=""
prev_at=""
if [ -r "$STATE" ]; then
    read -r prev_usage prev_at < "$STATE" 2>/dev/null || true
fi
if [ -n "$cpu_usage" ] && [ -n "$now" ]; then
    printf '%s %s\n' "$cpu_usage" "$now" > "$STATE" 2>/dev/null || true
fi

cpu_pct=""
if [ -n "$cpu_usage" ] && [ -n "$now" ] && [ -n "$prev_usage" ] && [ -n "$prev_at" ]; then
    if [ "$cpu_usage" -ge "$prev_usage" ] 2>/dev/null && [ "$now" -gt "$prev_at" ] 2>/dev/null; then
        used_ms=$(( (cpu_usage - prev_usage) / 1000 ))
        window_ms=$(( (now - prev_at) * 1000 ))
        capacity_ms=""
        if [ -n "$cpu_quota" ] && [ -n "$cpu_period" ] && [ "$cpu_period" -gt 0 ] 2>/dev/null; then
            capacity_ms=$(( window_ms * cpu_quota / cpu_period ))
        elif command -v nproc > /dev/null 2>&1 && ncpu=$(nproc 2>/dev/null); then
            # Uncapped: there is no cap to report a fraction of, so the honest
            # denominator is what this container may actually use. `nproc` reads
            # the process's CPU affinity mask, which a cpuset DOES restrict — so
            # unlike /proc/loadavg it is a fact about this container, and where
            # no cpuset exists the container genuinely may use every listed CPU.
            # No nproc means no denominator, so the segment is omitted entirely
            # rather than computed against a guess.
            capacity_ms=$((window_ms * ncpu))
        fi
        if [ -n "$capacity_ms" ] && [ "$capacity_ms" -gt 0 ] 2>/dev/null; then
            cpu_pct=$((100 * used_ms / capacity_ms))
            [ "$cpu_pct" -gt 100 ] && cpu_pct=100
        fi
    fi
fi

# ── render ───────────────────────────────────────────────────────────────────
human() {
    bytes=$1
    if [ "$bytes" -ge 1073741824 ] 2>/dev/null; then
        whole=$((bytes / 1073741824))
        tenth=$(((bytes % 1073741824) * 10 / 1073741824))
        printf '%s.%sG' "$whole" "$tenth"
    else
        printf '%sM' "$((bytes / 1048576))"
    fi
}

line=""
[ -n "$cpu_pct" ] && line="cpu ${cpu_pct}%"

if [ -n "$mem_used" ] && [ "$mem_used" -ge 0 ] 2>/dev/null; then
    if [ -n "$mem_limit" ]; then
        mem="mem $(human "$mem_used")/$(human "$mem_limit")"
    else
        mem="mem $(human "$mem_used")"
    fi
    if [ -n "$line" ]; then
        line="$line $mem"
    else
        line=$mem
    fi
fi

[ -n "$line" ] && printf '%s\n' "$line"
exit 0
