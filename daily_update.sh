#!/usr/bin/env bash
set -euo pipefail

cd /workspace/pseo-engine

# 1. 최신 나라장터 공고 수집 및 리포트/사이트 빌드
python3 pipeline.py --mode live

# 2. main 브랜치 docs/ 갱신 및 푸시
cp -r site/* docs/
git add docs output
git commit -m "chore(auto): daily bid briefing data update $(date +%Y-%m-%d)" || true
git push origin main || true

# 3. gh-pages 브랜치 정적 사이트 배포 및 푸시
rm -rf /tmp/pseo-site-backup
cp -r site /tmp/pseo-site-backup
git checkout gh-pages
cp -r /tmp/pseo-site-backup/* .
git add -A
git commit -m "deploy(auto): daily live site update $(date +%Y-%m-%d)" || true
git push origin gh-pages || true
git checkout main
