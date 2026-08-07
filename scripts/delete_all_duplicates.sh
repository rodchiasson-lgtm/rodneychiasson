#!/usr/bin/env bash
#
# Finds files in ~/Downloads (recursively) with identical content and
# deletes all but the newest copy of each duplicate set.
#
# Usage:
#   ./delete_all_duplicates.sh              # scans ~/Downloads
#   ./delete_all_duplicates.sh /some/dir    # scans a different directory

set -euo pipefail

TARGET_DIR="${1:-$HOME/Downloads}"

if [[ ! -d "$TARGET_DIR" ]]; then
    echo "Error: directory not found: $TARGET_DIR" >&2
    exit 1
fi

echo "Scanning for duplicate files in: $TARGET_DIR"

declare -A hash_to_newest_file
declare -A hash_to_newest_mtime
deleted_count=0
freed_bytes=0

# Pass 1: for each hash, find the newest file (the one to keep).
while IFS= read -r -d '' file; do
    hash=$(sha256sum "$file" | cut -d' ' -f1)
    mtime=$(stat -c '%Y' "$file")

    if [[ -z "${hash_to_newest_mtime[$hash]+x}" ]] || (( mtime > hash_to_newest_mtime[$hash] )); then
        hash_to_newest_mtime[$hash]=$mtime
        hash_to_newest_file[$hash]=$file
    fi
done < <(find "$TARGET_DIR" -type f -print0)

# Pass 2: delete every file that isn't the newest copy for its hash.
while IFS= read -r -d '' file; do
    hash=$(sha256sum "$file" | cut -d' ' -f1)
    keeper="${hash_to_newest_file[$hash]}"

    if [[ "$file" != "$keeper" ]]; then
        size=$(stat -c '%s' "$file")
        echo "Deleting duplicate: $file (keeping $keeper)"
        rm -- "$file"
        deleted_count=$((deleted_count + 1))
        freed_bytes=$((freed_bytes + size))
    fi
done < <(find "$TARGET_DIR" -type f -print0)

echo "Done. Deleted $deleted_count duplicate file(s), freed $freed_bytes bytes."
