#!/bin/bash
# shellcheck disable=SC2317

# Script สำหรับ sync upstream อย่างปลอดภัย
# ทำการ update main จาก upstream แล้ว rebase feature branch เดิม
#
# Usage:
#   ./sync-upstream.sh
#
# รองรับ:
#   - stash working tree อัตโนมัติถ้ามี uncommitted changes
#   - cleanup ถ้า script ถูก interrupt (Ctrl+C)
#   - rebase feature branch ใดๆ (ไม่ใช่แค่ "my-changes")

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
REBASE_IN_PROGRESS=false

abort_merge() {
    if $MERGE_IN_PROGRESS; then
        log "${YELLOW}🧹 Cleaning up unfinished merge...${RESET}"
        git merge --abort 2>/dev/null || true
        MERGE_IN_PROGRESS=false
    fi
}
abort_rebase() {
    if $REBASE_IN_PROGRESS; then
        log "${YELLOW}🧹 Cleaning up unfinished rebase...${RESET}"
        git rebase --abort 2>/dev/null || true
        REBASE_IN_PROGRESS=false
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
    abort_rebase
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

# --- Step 2: Update main ---
header "🔄 Updating main..."
git checkout main 2>&1 | tee -a "$LOG_FILE"

if git merge-base --is-ancestor upstream/main main 2>/dev/null; then
    log "${GREEN}✅ main is already up-to-date with upstream/main${RESET}"
else
    # Fast-forward if possible, otherwise create a merge commit
    if git merge --ff-only upstream/main 2>&1 | tee -a "$LOG_FILE"; then
        log "${GREEN}✅ Fast-forwarded main to upstream/main${RESET}"
    else
        # Try a no-fast-forward merge with conflict detection
        if git merge --no-commit --no-ff upstream/main 2>&1 | tee -a "$LOG_FILE"; then
            MERGE_IN_PROGRESS=true
            git commit -m "chore: merge upstream/main" 2>&1 | tee -a "$LOG_FILE"
            MERGE_IN_PROGRESS=false
            log "${GREEN}✅ Successfully merged upstream/main into main${RESET}"
        else
            MERGE_IN_PROGRESS=true
            abort_merge
            log "${RED}❌ Merge conflict detected — aborted.${RESET}"
            log "   Manual fix: git checkout main && git merge upstream/main"
            exit 1
        fi
    fi
fi

# --- Step 3: Rebase feature branch (if not main) ---
if [ "$CURRENT_BRANCH" != "main" ]; then
    header "🔄 Rebasing ${CURRENT_BRANCH} on main..."
    git checkout "$CURRENT_BRANCH" 2>&1 | tee -a "$LOG_FILE"

    if git rebase main 2>&1 | tee -a "$LOG_FILE"; then
        log "${GREEN}✅ Successfully rebased ${CURRENT_BRANCH} on main${RESET}"
    else
        REBASE_IN_PROGRESS=true
        log "${RED}❌ Rebase conflict on ${CURRENT_BRANCH}.${RESET}"
        log "   Fix conflicts, then run: git rebase --continue"
        log "   Or cancel with:          git rebase --abort"
        exit 1
    fi
else
    # Already on main, nothing to rebase
    log "${GREEN}ℹ️  Staying on main — no rebase needed${RESET}"
fi

# --- Step 4: Optional push ---
header "📤 Push options"
if [ "$CURRENT_BRANCH" != "main" ]; then
    log "   To push rebased branch: ${BOLD}git push --force-with-lease origin ${CURRENT_BRANCH}${RESET}"
else
    log "   To push updated main:   ${BOLD}git push origin main${RESET}"
fi

exit 0
