#!/usr/bin/env bash
set -euo pipefail

cd /workspace/pseo-engine

# pseo.db 는 로컬에만 쌓이는 원본 DB다. Git에는 절대 올리지 않는다.
# (매일 커지는 파일이라 GitHub 50MB 경고·100MB 차단의 원인 — 로컬 파일은 그대로 유지)
DB_PATTERN='(^|/)[^/]*\.db(-wal|-shm|-journal)?$'

# 1. 최신 나라장터 공고 수집 및 리포트/사이트 빌드
python3 pipeline.py --mode live

# 2. main 브랜치 docs/ 갱신 및 푸시 (pseo.db 제외)
cp -r site/* docs/
git add docs output site raw *.py daily_update.sh .gitignore
git commit -m "chore(auto): daily bid briefing data update $(date +%Y-%m-%d)" || true
git push origin main || true

# 3. gh-pages 브랜치 정적 사이트 배포 및 푸시 (정적 파일만, pseo.db 제외)
rm -rf /tmp/pseo-site-backup
cp -r site /tmp/pseo-site-backup
git checkout gh-pages
cp -r /tmp/pseo-site-backup/* .
git add -A
if git diff --cached --name-only | grep -Eq "$DB_PATTERN"; then
  echo "ERROR: DB 파일이 gh-pages에 스테이징되어 배포를 중단합니다. (.gitignore 확인)" >&2
  git reset -q
  git checkout main
  exit 1
fi
git commit -m "deploy(auto): daily live site update $(date +%Y-%m-%d)" || true
git push origin gh-pages || true
git checkout main
