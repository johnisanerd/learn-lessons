#!/usr/bin/env bash
# The two lesson skills install as separate folders, so files they share are
# copied into both. This checks the copies match; run it before committing.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../skills"
shared=(
  scripts/review.py
  templates/lesson.css templates/quiz.js templates/index.html
  formats/MISSION-FORMAT.md formats/GLOSSARY-FORMAT.md formats/LEARNING-RECORD-FORMAT.md
  formats/COMMUNITIES-FORMAT.md formats/RESOURCES-FORMAT.md
)
status=0
for f in "${shared[@]}"; do
  if ! cmp -s "video-lessons/$f" "slide-lessons/$f"; then
    echo "differs: $f (edit one, then copy it to the other skill)"; status=1
  fi
done
((status)) || echo "shared files match (${#shared[@]})"
exit $status
