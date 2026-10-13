#!/usr/bin/env bash
# Qualify real user/PID namespaces without relaxing the host-wide AppArmor policy.
set -euo pipefail

: "${RUNNER_TEMP:?GitHub runner temporary directory is required}"
namespace_dir="$RUNNER_TEMP/printstash-namespace"
namespace_profile="$RUNNER_TEMP/printstash-namespace.apparmor"

case "${1:-}" in
  prepare)
    : "${GITHUB_PATH:?GitHub path export is required}"
    # The profile attachment below must remain a literal, absolute AppArmor path.
    case "$RUNNER_TEMP" in
      /*) ;;
      *) printf '%s\n' 'RUNNER_TEMP must be absolute' >&2; exit 1 ;;
    esac
    if [[ "$RUNNER_TEMP" == *[!a-zA-Z0-9_./-]* ]]; then
      printf '%s\n' 'RUNNER_TEMP contains unsupported AppArmor path characters' >&2
      exit 1
    fi
    install -D -m 0755 /usr/bin/unshare "$namespace_dir/unshare"
    if [ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
      cat > "$namespace_profile" <<EOF
abi <abi/4.0>,
include <tunables/global>
profile printstash-ci-pid-namespace "$namespace_dir/unshare" flags=(unconfined) {
  userns,
}
EOF
      sudo apparmor_parser -r "$namespace_profile"
    fi
    # This is mandatory even on a host without the AppArmor userns restriction.
    "$namespace_dir/unshare" --user --map-root-user --pid --fork /usr/bin/true
    printf '%s\n' "$namespace_dir" >> "$GITHUB_PATH"
    ;;
  cleanup)
    if [ -f "$namespace_profile" ]; then
      sudo apparmor_parser -R "$namespace_profile"
      rm -- "$namespace_profile"
    fi
    if [ -d "$namespace_dir" ]; then
      rm -f -- "$namespace_dir/unshare"
      rmdir -- "$namespace_dir"
    fi
    ;;
  *) printf '%s\n' 'Usage: prepare-ci-pid-namespace.sh prepare|cleanup' >&2; exit 2 ;;
esac
