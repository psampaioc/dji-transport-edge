#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${EDGE_RUNTIME_DIR:-$repo_root/.runtime/edge-receiver}"
pid_file="$runtime_dir/pid"
command_file="$runtime_dir/command"
log_file="$runtime_dir/receiver.log"

usage() {
    cat <<'EOF'
Usage:
  scripts/edge_receiver.sh start -- COMMAND [ARG ...]
  scripts/edge_receiver.sh status [--telemetry-port PORT] [--video-port PORT]
  scripts/edge_receiver.sh stop

The script manages only the exact process recorded in .runtime/edge-receiver.
Set EDGE_RUNTIME_DIR to place runtime state elsewhere.
EOF
}

read_pid() {
    [[ -f "$pid_file" ]] || return 1
    local pid
    read -r pid < "$pid_file"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    printf '%s' "$pid"
}

is_running() {
    local pid
    pid="$(read_pid)" || return 1
    kill -0 "$pid" 2>/dev/null
}

start_receiver() {
    shift
    [[ "${1:-}" == "--" ]] || { usage >&2; exit 2; }
    shift
    (( $# > 0 )) || { usage >&2; exit 2; }
    if is_running; then
        printf 'edge receiver already running (pid %s)\n' "$(read_pid)"
        return 0
    fi
    mkdir -p "$runtime_dir"
    printf '%q ' "$@" > "$command_file"
    printf '\n' >> "$command_file"
    nohup "$@" >> "$log_file" 2>&1 &
    local pid=$!
    printf '%s\n' "$pid" > "$pid_file"
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        printf 'edge receiver exited during startup; see %s\n' "$log_file" >&2
        return 1
    fi
    printf 'edge receiver started (pid %s, log %s)\n' "$pid" "$log_file"
}

status_receiver() {
    shift
    local telemetry_port="" video_port=""
    while (( $# )); do
        case "$1" in
            --telemetry-port) telemetry_port="${2:-}"; shift 2 ;;
            --video-port) video_port="${2:-}"; shift 2 ;;
            *) usage >&2; exit 2 ;;
        esac
    done
    if ! is_running; then
        printf 'edge receiver is not running\n' >&2
        return 1
    fi
    local pid
    pid="$(read_pid)"
    printf 'edge receiver process is running (pid %s)\n' "$pid"
    if [[ -n "$telemetry_port" || -n "$video_port" ]]; then
        command -v ss >/dev/null || { printf 'error: ss is required for port checks\n' >&2; return 1; }
        local sockets
        sockets="$(ss -H -lunp 2>/dev/null || ss -H -lun)"
        for port in "$telemetry_port" "$video_port"; do
            [[ -n "$port" ]] || continue
            if grep -Eq "[:.]${port}([[:space:]]|$)" <<< "$sockets"; then
                printf 'UDP port %s is bound\n' "$port"
            else
                printf 'error: UDP port %s is not bound\n' "$port" >&2
                return 1
            fi
        done
    fi
}

stop_receiver() {
    local pid
    if ! pid="$(read_pid)" || ! kill -0 "$pid" 2>/dev/null; then
        printf 'edge receiver is not running\n'
        return 0
    fi
    kill "$pid"
    for _ in {1..20}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
        printf 'error: edge receiver pid %s did not stop after SIGTERM\n' "$pid" >&2
        return 1
    fi
    rm -f "$pid_file"
    printf 'edge receiver stopped (pid %s)\n' "$pid"
}

case "${1:-}" in
    start) start_receiver "$@" ;;
    status) status_receiver "$@" ;;
    stop) stop_receiver ;;
    *) usage >&2; exit 2 ;;
esac
