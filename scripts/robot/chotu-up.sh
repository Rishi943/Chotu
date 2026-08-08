#!/usr/bin/env bash
# chotu-up — bring Chotu's bridge online and leave him standing. Idempotent.
#
#   warm (bridge already running)  ~1 s   health + stand + face + battery
#   cold (bridge down)             ~8 s   ssh-start, then poll /health
#
# Cold starts pass --no-camera: vilib's camera init is ~40 s of a cold boot and
# nothing on a shoot needs it. Any endpoint that needs a frame (/capture,
# /perception, /stream) starts it on demand in ~3 s, or POST /camera {"on":true}.
# Use --camera to have it up from the start.
#
# Speaks HTTP straight to the bridge: no Python spawn, no chotu_tool import,
# which is where the per-call 3-5 s used to go. Needs the `chotu` ssh alias
# from ~/.ssh/config.
#
#   chotu-up.sh              bring up + stand
#   chotu-up.sh --no-stand   bring up, leave the pose alone (safe on a table)
#   chotu-up.sh --status     report only, never move, never start anything
#   chotu-up.sh --camera     cold-start with the camera up (slow: adds ~40 s)
#
# The cold path ALWAYS ends standing — the bridge's own startup does
# do_step("stand", 40) before it serves, and that is not suppressible from here.
# --no-stand only governs the warm path.

set -uo pipefail

SSH_HOST="${CHOTU_SSH_HOST:-chotu}"
BASE="${CHOTU_BASE:-http://chotu.local:7000}"

STAND=1
STATUS_ONLY=0
CAMERA=0
for arg in "$@"; do
  case "$arg" in
    --no-stand) STAND=0 ;;
    --status)   STATUS_ONLY=1 ;;
    --camera)   CAMERA=1 ;;
    *) echo "usage: $(basename "$0") [--no-stand] [--status] [--camera]" >&2; exit 2 ;;
  esac
done

CAM_FLAG="--no-camera"
[ "$CAMERA" -eq 1 ] && CAM_FLAG=""

up() { curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1; }

# Pull one number out of a bridge envelope without spawning a JSON parser.
field() { sed -n "s/.*\"$1\":\([0-9.]*\).*/\1/p"; }

report() {
  local bat
  bat=$(curl -fsS --max-time 5 "$BASE/battery" 2>/dev/null)
  if [ -n "$bat" ]; then
    echo "battery: $(printf '%s' "$bat" | field percent)% ($(printf '%s' "$bat" | field voltage) V)"
  fi
  echo "stream:  $BASE/stream"
}

# --- Console TLS cert: keep the leaf's SANs in step with the LAN IP ----------
# The console's cert (:8888) lists this laptop's LAN IPv4 in its SANs. DHCP can
# hand out a new address, the cert stops matching, and the phone breaks. Reissue
# the LEAF only (signed by the existing CA) when the IP changed. The CA is the
# trust anchor installed on the phone and is NEVER regenerated here.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CERT_DIR="$REPO_ROOT/certs"
CA_CERT="$CERT_DIR/chotu-ca.pem"
CA_KEY="$CERT_DIR/chotu-ca-key.pem"
LEAF_CERT="$CERT_DIR/chotu.pem"

# Convert a bash path to a form the chosen python can open (the Windows
# python.exe cannot open a cygwin /e/... path; cygpath -w fixes it; on Linux
# cygpath is absent and this is a no-op).
winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

# A python that can run scripts/make_cert.py (needs the `cryptography` package).
find_py() {
  if [ -n "${CHOTU_PY:-}" ] && command -v "$CHOTU_PY" >/dev/null 2>&1 \
     && "$CHOTU_PY" -c 'import cryptography' >/dev/null 2>&1; then
    printf '%s' "$CHOTU_PY"; return 0
  fi
  for cand in "$REPO_ROOT/.venv/bin/python" \
              "/c/Users/rushi/paliv-win-venv/Scripts/python.exe" \
              "python3" "python"; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import cryptography' >/dev/null 2>&1; then
      printf '%s' "$cand"; return 0
    fi
  done
  return 1
}

# Current LAN IPv4, discovered exactly as scripts/make_cert.py does: open a UDP
# socket toward 8.8.8.8 and read getsockname(). No packet is actually sent.
lan_ip_via_py() {
  "$1" -c 'import socket as _s; _x=_s.socket(_s.AF_INET,_s.SOCK_DGRAM); _x.connect(("8.8.8.8",80)); print(_x.getsockname()[0]); _x.close()'
}

ensure_console_cert() {
  local py current_ip sans repo_root_win
  if [ ! -f "$LEAF_CERT" ]; then
    echo "cert: $LEAF_CERT missing - run scripts/make_cert.py --force, then reinstall the CA on the phone" >&2
    return 0
  fi
  py=$(find_py) || {
    echo "cert: no python with 'cryptography' available (set CHOTU_PY) - cannot reissue leaf" >&2
    return 0
  }
  current_ip=$(lan_ip_via_py "$py") || return 0
  [ -n "$current_ip" ] || { echo "cert: no LAN IPv4 discovered - skipping leaf check" >&2; return 0; }

  sans=$(openssl x509 -in "$LEAF_CERT" -noout -ext subjectAltName 2>/dev/null)
  if printf '%s' "$sans" | grep -Eq "IP Address:${current_ip}([^0-9]|$)"; then
    echo "cert: LAN IP $current_ip already in SANs - nothing to do"
    return 0
  fi

  if [ ! -f "$CA_CERT" ] || [ ! -f "$CA_KEY" ]; then
    echo "cert: REFUSING to reissue - CA files missing ($CA_CERT / $CA_KEY)." >&2
    echo "cert: the CA is the phone's trust anchor and must not be regenerated in place." >&2
    echo "cert: run scripts/make_cert.py --force, then reinstall chotu-ca.pem on the phone." >&2
    return 0
  fi

  repo_root_win=$(winpath "$REPO_ROOT")
  echo "cert: LAN IP changed to $current_ip - reissuing leaf (existing CA reused)"
  "$py" "$repo_root_win/scripts/make_cert.py" --reissue-leaf --host "$current_ip"
}
if [ "$STATUS_ONLY" -eq 1 ]; then
  if up; then echo "bridge:  up"; report; else echo "bridge:  down"; exit 1; fi
  exit 0
fi

# --- Console TLS certificate upkeep: run before the console is used ----------
# ensure_console_cert discovers the current LAN IPv4, compares it against the
# leaf's SANs, and reissues the LEAF only (reusing the existing CA) if the IP
# changed. --status above already exited, so it can never run in report-only
# mode and can never reissue anything.
ensure_console_cert

cold=0
if up; then
  echo "bridge:  already up"
else
  echo "bridge:  down - starting over ssh ($SSH_HOST)"
  # setsid + nohup + closed stdin so the bridge outlives this ssh session.
  # Runs unprivileged: sudo on the Pi is password-gated, and the only thing
  # that needs root is a vilib pinctrl call that fails harmlessly.
  # A half-dead server from a previous attempt holds the camera and the servo
  # bus, and every retry then fails for a reason that isn't the real one.
  ssh -o BatchMode=yes "$SSH_HOST" 'pkill -9 -f "python3 server.py"; sleep 1' \
    >/dev/null 2>&1
  ssh -o BatchMode=yes "$SSH_HOST" \
    "cd ~/chotu-bridge && setsid nohup ./.venv/bin/python3 server.py $CAM_FLAG >> ~/bridge.log 2>&1 < /dev/null &" \
    >/dev/null 2>&1
  cold=1
  for i in $(seq 1 90); do
    up && break
    sleep 1
  done
  if ! up; then
    echo "bridge:  FAILED to come up after 90 s - tail of ~/bridge.log:" >&2
    ssh -o BatchMode=yes "$SSH_HOST" 'tail -20 ~/bridge.log' >&2
    exit 1
  fi
  echo "bridge:  up after ${i}s (stood during startup)"
fi

# Warm start means the pose is whatever the last session left behind, so stand
# unless told not to. Cold start already stood.
if [ "$cold" -eq 0 ] && [ "$STAND" -eq 1 ]; then
  if curl -fsS --max-time 25 -X POST "$BASE/pose" \
       -H 'Content-Type: application/json' -d '{"name":"stand"}' >/dev/null; then
    echo "pose:    stand"
  else
    echo "pose:    stand FAILED" >&2
  fi
fi

curl -fsS --max-time 5 -X POST "$BASE/face" \
     -H 'Content-Type: application/json' -d '{"name":"idle"}' >/dev/null \
  && echo "face:    idle"

report
