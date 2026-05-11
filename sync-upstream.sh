#!/bin/bash
# shellcheck disable=SC2317

# Script สำหรับ sync upstream อย่างปลอดภัย
# ทำการ update upstream-tracking จาก upstream แล้ว merge เข้า feature branch
#
# Workflow:
#   1. Fetch upstream
#   2. Update 'upstream-tracking' branch from upstream/main
#   3. Merge upstream-tracking into current feature branch
#   4. โค้ดของคุณจะไม่ถูกแทน (ใช้ merge commit แทน rebase)
#
# Usage:
#   ./sync-upstream.sh
#
# รองรับ:
#   - stash working tree อัตโนมัติถ้ามี uncommitted changes
#   - cleanup ถ้า script ถูก interrupt (Ctrl+C)
#   - merge ทุก feature branch (ไม่ใช่แค่ "my-changes")

set -euo pipefail

# Auto-detect repo path จากตำแหน่งไฟล์นี้
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_PATH="${SCRIPT_DIR}"
LOG_FILE="/tmp/notebooklm-sync.log"

# Terminal colors
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

# Helper: log to both terminal and file
log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

# Helper: log bold header
header() {
    echo -e "\n${BOLD}$1${RESET}" | tee -a "$LOG_FILE"
}

# --- Trap: cleanup on exit/interrupt ---
STASHED=false
MERGE_IN_PROGRESS=false

abort_merge() {
    if $MERGE_IN_PROGRESS; then
        log "${YELLOW}🧹 Cleaning up unfinished merge...${RESET}"
        git merge --abort 2>/dev/null || true
        MERGE_IN_PROGRESS=false
    fi
}
restore_stash() {
    if $STASHED; then
        log "${YELLOW}📦 Restoring stashed changes...${RESET}"
        git stash pop 2>/dev/null || true
        STASHED=false
    fi
}
cleanup() {
    local exit_code=$?
    abort_merge
    # Return to original branch if we're not there
    local current
    current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -n "${CURRENT_BRANCH:-}" ] && [ "$current" != "$CURRENT_BRANCH" ]; then
        log "${YELLOW}↩️  Returning to ${CURRENT_BRANCH}...${RESET}"
        git checkout "$CURRENT_BRANCH" 2>/dev/null || true
    fi
    restore_stash
    if [ $exit_code -ne 0 ]; then
        log "${RED}❌ Sync failed (exit ${exit_code}). See ${LOG_FILE}${RESET}"
    else
        log "${GREEN}✅ Sync completed.${RESET}"
    fi
    log "=== Sync finished at $(date) ===\n" >> "$LOG_FILE"
}
trap cleanup EXIT

# --- Start ---
log "=== Sync started at $(date) ==="
cd "$REPO_PATH"

# Validate git repo and upstream remote
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    log "${RED}❌ Not a git repository: ${REPO_PATH}${RESET}"
    exit 1
fi
if ! git remote get-url upstream >/dev/null 2>&1; then
    log "${RED}❌ 'upstream' remote not found.${RESET}"
    log "   Run: git remote add upstream <upstream-url>"
    exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
header "📍 Current branch: ${CURRENT_BRANCH}"

# --- Stash if dirty working tree ---
if ! git diff --quiet HEAD || ! git diff --cached --quiet HEAD; then
    log "${YELLOW}⚠️  Working tree is dirty — stashing...${RESET}"
    git stash push -m "sync-upstream auto-stash $(date +%s)"
    STASHED=true
fi

# --- Step 1: Fetch upstream ---
header "🌐 Fetching upstream..."
git fetch upstream 2>&1 | tee -a "$LOG_FILE"

# --- Step 2: Create/update upstream-tracking branch from upstream/main ---
header "🔄 Updating upstream-tracking branch..."
if git show-ref --verify --quiet refs/heads/upstream-tracking; then
    git checkout upstream-tracking 2>&1 | tee -a "$LOG_FILE"
    git reset --hard upstream/main 2>&1 | tee -a "$LOG_FILE"
    log "${GREEN}✅ upstream-tracking updated to upstream/main${RESET}"
else
    git checkout -b upstream-tracking upstream/main 2>&1 | tee -a "$LOG_FILE"
    log "${GREEN}✅ Created upstream-tracking from upstream/main${RESET}"
fi

# --- Step 3: Merge upstream-tracking into feature branch (if not upstream-tracking) ---
if [ "$CURRENT_BRANCH" != "upstream-tracking" ]; then
    header "🔄 Merging upstream-tracking into ${CURRENT_BRANCH}..."
    git checkout "$CURRENT_BRANCH" 2>&1 | tee -a "$LOG_FILE"

    if git merge upstream-tracking --no-ff 2>&1 | tee -a "$LOG_FILE"; then
        log "${GREEN}✅ Successfully merged upstream-tracking into ${CURRENT_BRANCH}${RESET}"
    else
        MERGE_IN_PROGRESS=true
        log "${RED}❌ Merge conflict on ${CURRENT_BRANCH}.${RESET}"
        log "   Fix conflicts, then run: git merge --continue"
        log "   Or cancel with:          git merge --abort"
        exit 1
    fi
else
    log "${GREEN}ℹ️  Staying on upstream-tracking — no merge needed${RESET}"
fi

# --- Step 4: Optional push ---
header "📤 Push options"
if [ "$CURRENT_BRANCH" != "upstream-tracking" ]; then
    log "   To push merged branch: ${BOLD}git push origin ${CURRENT_BRANCH}${RESET}"
else
    log "   To push upstream-tracking: ${BOLD}git push origin upstream-tracking${RESET}"
fi

exit 0
