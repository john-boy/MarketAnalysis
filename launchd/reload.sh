#!/usr/bin/env bash
# Reload the com.marketanalysis.daily_update launchd agent after editing the
# plist. launchd reads the plist at bootstrap time; subsequent edits are NOT
# picked up automatically. This script does the bootout + bootstrap cycle and
# then prints the schedule so you can confirm the new fire time was loaded.
#
# First-time install: this same script works — it skips the bootout when the
# agent isn't loaded yet, then symlinks + bootstraps.

set -euo pipefail

LABEL="com.marketanalysis.daily_update"
PLIST_LINK="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"

# Ensure the LaunchAgents symlink exists (idempotent).
mkdir -p "${HOME}/Library/LaunchAgents"
ln -sfn "${PLIST_SRC}" "${PLIST_LINK}"

# Unload if currently loaded; ignore the error if it isn't.
if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  echo "Unloading ${LABEL}..."
  launchctl bootout "gui/$(id -u)/${LABEL}"
else
  echo "${LABEL} not currently loaded (skipping bootout)."
fi

echo "Loading from ${PLIST_LINK}..."
launchctl bootstrap "gui/$(id -u)" "${PLIST_LINK}"

echo
echo "Active schedule for ${LABEL}:"
launchctl print "gui/$(id -u)/${LABEL}" \
  | awk '/start calendar interval/,/^}/' \
  | sed 's/^/  /'

echo
echo "Source plist: ${PLIST_SRC}"
echo "Symlink:      ${PLIST_LINK}"
