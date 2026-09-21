#!/usr/bin/env bash
# Compile every catalog listed in locale/LINGUAS.
set -euo pipefail

# CDPATH= is an environment prefix for cd, not an assignment: it keeps a user's
# CDPATH from making cd jump elsewhere and print the target. ShellCheck reads it
# as a mistyped assignment.
# shellcheck disable=SC1007
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
DESTINATION=${1:-"$ROOT/usr/share/locale"}
DOMAIN=big-remote-play

if ! command -v msgfmt >/dev/null 2>&1; then
	printf 'error: GNU gettext msgfmt is required\n' >&2
	exit 127
fi

while IFS= read -r raw || [[ -n $raw ]]; do
	line=${raw%%#*}
	read -r -a languages <<<"$line"
	for language in "${languages[@]}"; do
		[[ -n $language ]] || continue
		source_file="$ROOT/locale/$language.po"
		target_dir="$DESTINATION/$language/LC_MESSAGES"
		[[ -f $source_file ]] || {
			printf 'error: missing catalog: %s\n' "$source_file" >&2
			exit 1
		}
		install -d -m 0755 "$target_dir"
		msgfmt --check --check-format --check-header \
			-o "$target_dir/$DOMAIN.mo" \
			"$source_file"
		rm -f "$target_dir/big-remote-play-together.mo"
	done
done <"$ROOT/locale/LINGUAS"

printf 'Compiled catalogs into %s\n' "$DESTINATION"
