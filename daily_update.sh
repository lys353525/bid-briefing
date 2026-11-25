#!/usr/bin/env bash
set -euo pipefail

cd /workspace/pseo-engine

# pseo.db 는 로컬 누적 DB라 Git에 올리지 않는다.
# (GitHub 파일 50MB 경고 / 100MB 차단 방지 — DB는 매일 커지므로 절대 add 금지, 로컬 파일은 유지)
DB_EXCLUDES=(':(exclude)pseo.db' ':(exclude)*.db' ':(exclude)*.db-wal' ':(exclude)*.db-shm' ':(exclude)*.db-journal')

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
git add -A -- . "${DB_EXCLUDES[@]}"
git commit -m "deploy(auto): daily live site update $(date +%Y-%m-%d)" || true
git push origin gh-pages || true
git checkout main
