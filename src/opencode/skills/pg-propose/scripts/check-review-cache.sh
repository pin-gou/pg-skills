#!/bin/bash
#
# .opencode/skills/pg-propose/scripts/check-review-cache.sh
#
# Checks whether the AGENTS.md review cache (.pg/context/summary.yaml)
# is still valid by comparing stored fingerprints against current files.
# Called by pg-propose to decide whether to extract context+rules from
# AGENTS.md or use the cached version.
#
# Usage: bash check-review-cache.sh          (run from project root)
#
# Exit code: always 0
#
# Output (stdout):
#   HIT:  STATUS=HIT\n---\n<full YAML content>
#   MISS: STATUS=MISS\nREASON=<reason>\n[DETAIL=<detail>]\n---\nCURRENT_FINGERPRINTS:\n<entries>

set -euo pipefail

CACHE_FILE=".pg/context/summary.yaml"

# ── Step 1: Collect all AGENTS.md files ──────────────────────────

mapfile -t CURRENT_FILES < <(
    find . -name AGENTS.md \
        -not -path '*/node_modules/*' \
        -not -path '*/target/*' \
        -not -path '*/.git/*' \
        -not -path '*/dist/*' \
        -not -path '*/build/*' \
        | sort
)

if [ ${#CURRENT_FILES[@]} -eq 0 ]; then
    echo "STATUS=MISS"
    echo "REASON=no-agents-md-found"
    exit 0
fi

# ── Step 2: Compute current fingerprints ─────────────────────────

compute_fingerprints() {
    for f in "${CURRENT_FILES[@]}"; do
        local hash
        hash=$(sha256sum "$f" | cut -d' ' -f1)
        echo "$f sha256 $hash"
    done
}

current_fp=$(compute_fingerprints)

# ── Step 3: Check if cache file exists ───────────────────────────

if [ ! -f "$CACHE_FILE" ]; then
    echo "STATUS=MISS"
    echo "REASON=cache-not-found"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "$current_fp"
    exit 0
fi

# ── Step 4: Parse stored fingerprints from YAML ──────────────────

parse_stored_fingerprints() {
    local file="$1"
    local in_fp=0
    local cur_path=""
    local cur_algo=""

    while IFS= read -r line; do
        local trimmed
        trimmed=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$trimmed" ]] && continue

        if [[ "$trimmed" == "fingerprints:" ]]; then
            in_fp=1
            continue
        fi

        # Top-level key (no leading space) → exit fingerprints section
        if [[ $in_fp -eq 1 && ! "$line" =~ ^[[:space:]] && "$trimmed" != "fingerprints:" ]]; then
            break
        fi

        if [[ $in_fp -eq 1 ]]; then
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*path:[[:space:]]*(.*)$ ]]; then
                cur_path="${BASH_REMATCH[1]}"
            elif [[ -n "$cur_path" && "$line" =~ ^[[:space:]]*algorithm:[[:space:]]*(.*)$ ]]; then
                cur_algo="${BASH_REMATCH[1]}"
            elif [[ -n "$cur_path" && "$line" =~ ^[[:space:]]*value:[[:space:]]*(.*)$ ]]; then
                echo "$cur_path $cur_algo ${BASH_REMATCH[1]}"
                cur_path=""
                cur_algo=""
            fi
        fi
    done < "$file"
}

stored_fp=$(parse_stored_fingerprints "$CACHE_FILE")

# ── Step 5: Compare fingerprints ─────────────────────────────────

current_count=$(echo "$current_fp" | wc -l)
stored_count=$(echo "$stored_fp" | wc -l)

if [ "$current_count" -ne "$stored_count" ]; then
    echo "STATUS=MISS"
    echo "REASON=file-count-changed"
    echo "DETAIL=current=$current_count stored=$stored_count"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "$current_fp"
    exit 0
fi

sorted_current=$(echo "$current_fp" | sort)
sorted_stored=$(echo "$stored_fp" | sort)

if [ "$sorted_current" != "$sorted_stored" ]; then
    diff_out=$(diff <(echo "$sorted_stored") <(echo "$sorted_current") 2>/dev/null | head -8 || true)
    echo "STATUS=MISS"
    echo "REASON=fingerprint-mismatch"
    echo "DETAIL=$diff_out"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "$current_fp"
    exit 0
fi

# ── Step 6: Cache HIT — output full YAML content ─────────────────

echo "STATUS=HIT"
echo "---"
cat "$CACHE_FILE"
