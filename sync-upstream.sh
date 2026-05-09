#!/bin/bash

# Script สำหรับ sync upstream อย่างปลอดภัย
# ทำการ update main จาก upstream แล้ว rebase my-changes

REPO_PATH="/home/tawan/Documents/notebooklm"
LOG_FILE="/tmp/notebooklm-sync.log"

# เพิ่ม timestamp
echo "=== Sync started at $(date) ===" >> "$LOG_FILE"

cd "$REPO_PATH" || exit 1

# บันทึกปัจจุบัน
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Current branch: $CURRENT_BRANCH" >> "$LOG_FILE"

# Step 1: ไป main
echo "Switching to main..." >> "$LOG_FILE"
git checkout main 2>&1 | tee -a "$LOG_FILE"

# Step 2: Fetch from upstream
echo "Fetching from upstream..." >> "$LOG_FILE"
git fetch upstream 2>&1 | tee -a "$LOG_FILE"

# Step 3: Check for conflicts ก่อน merge
echo "Checking for conflicts..." >> "$LOG_FILE"
if git merge-base --is-ancestor upstream/main main; then
    echo "main is already up-to-date with upstream/main" >> "$LOG_FILE"
else
    # Try merge with conflict detection
    if git merge --no-commit --no-ff upstream/main 2>&1 | tee -a "$LOG_FILE"; then
        git commit -m "chore: merge upstream/main" >> "$LOG_FILE" 2>&1
        echo "✅ Successfully merged upstream/main" >> "$LOG_FILE"
    else
        # Merge failed, abort
        git merge --abort
        echo "⚠️  Merge conflict detected - aborted merge. Manual intervention needed." >> "$LOG_FILE"
        echo "Run: cd $REPO_PATH && git checkout main && git merge upstream/main" >> "$LOG_FILE"
    fi
fi

# Step 4: กลับไปที่ branch เดิม
echo "Returning to original branch: $CURRENT_BRANCH..." >> "$LOG_FILE"
git checkout "$CURRENT_BRANCH" 2>&1 | tee -a "$LOG_FILE"

# Step 5: Rebase my-changes บน main (ถ้าเป็น my-changes)
if [ "$CURRENT_BRANCH" = "my-changes" ]; then
    echo "Rebasing my-changes on main..." >> "$LOG_FILE"
    if git rebase main 2>&1 | tee -a "$LOG_FILE"; then
        echo "✅ Successfully rebased my-changes on main" >> "$LOG_FILE"
    else
        # Rebase failed
        echo "⚠️  Rebase conflict detected - aborted. Manual intervention needed." >> "$LOG_FILE"
        echo "Run: cd $REPO_PATH && git rebase --abort (to cancel)" >> "$LOG_FILE"
        echo "Or: git rebase --continue (after fixing conflicts)" >> "$LOG_FILE"
    fi
fi

echo "=== Sync completed at $(date) ===" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
