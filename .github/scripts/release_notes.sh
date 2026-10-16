#!/bin/sh
# Print the CHANGELOG.md section for a version, which becomes the GitHub
# Release notes. Fails when there is none, so a release cannot go out with
# notes that say nothing.
#
# usage: release_notes.sh 0.1.0
set -eu
version=$1
notes=$(awk -v v="$version" '
  /^## / { if (found) exit; if ($0 ~ "^## \\[?" v "\\]?([^0-9.]|$)") { found = 1; next } }
  found
' CHANGELOG.md)
if [ -z "$(printf '%s' "$notes" | tr -d '[:space:]')" ]; then
  echo "CHANGELOG.md has no section for $version" >&2
  exit 1
fi
printf '%s\n' "$notes"
