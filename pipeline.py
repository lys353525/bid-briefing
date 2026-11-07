# -*- coding: utf-8 -*-
"""End-to-End 파이프라인.

  수집 → 저장 → 계산 → 생성 → 품질 검증 → 발행(markdown) → 정적 HTML 빌드 → 보고서

실행 예:
  python3 pipeline.py                      # 키가 없으면 시뮬레이션, 있으면 실데이터
  DATA_GO_KR_KEY=... python3 pipeline.py   # 실데이터 수집
  python3 pipeline.py --as-of 2026-11-07 --days 62
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import calculator
import collector
import db
import formatter
import quality_gate
from config import (
    BASE_DIR, SITE_NAME, SITE_URL, SOURCE, Settings, default_settings, log, now_kst,
)

MIN_PAGES = 10


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: list[str]) -> Settings:
    p = argparse.ArgumentParser(description="pSEO 무인 수집·발행 파이프라인")
    p.add_argument("--mode", choices=["auto", "live", "sim"], default="auto")
    p.add_argument("--as-of", dest="as_of", help="기준일 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--days", type=int, help="수집 기간(일). 기본 62")
    p.add_argument("--db", help="SQLite 파일 경로")
    p.add_argument("--output", help="마크다운 출력 디렉터리")
    p.add_argument("--raw", help="원시 JSON 저장 디렉터리")
    p.add_argument("--site", help="정적 사이트 출력 디렉터리(기본: ./site)")
    p.add_argument("--limit", type=int, default=0, help="생성 페이지 수 제한(0=전체)")
    p.add_argument("--allow-few", action="store_true", help="페이지 10개 미만이어도 종료코드 0")
    p.add_argument("--verbose", action="store_true")
    ns = p.parse_args(argv)

    s = default_settings()
    changes: dict = {"mode": ns.mode, "limit_pages": ns.limit, "verbose": ns.verbose,
                     "allow_few": ns.allow_few}
    if ns.as_of:
        changes["as_of"] = date.fromisoformat(ns.as_of)
    if ns.days:
        changes["days"] = ns.days
    if ns.db:
        changes["db_path"] = Path(ns.db)
    if ns.output:
        changes["output_dir"] = Path(ns.output)
    if ns.raw:
        changes["raw_dir"] = Path(ns.raw)
    if ns.site:
        changes["site_dir"] = Path(ns.site)
    s = s.with_changes(**changes)
    return s


# --------------------------------------------------------------------------
# 유틸
# --------------------------------------------------------------------------
def new_run_id(settings: Settings) -> str:
    return f"run-{settings.as_of:%Y%m%d}-{now_kst():%H%M%S}"


def content_hash(text: str, generated_at: str = "") -> str:
    """생성 시각만 바뀐 경우를 '변경 없음'으로 보기 위해 타임스탬프를 고정 토큰으로 치환한다."""
    normalized = text.replace(generated_at, "<generated>") if generated_at else text
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def page_notices(facts: dict, notices: list[dict]) -> list[dict]:
    """facts가 가리키는 범위의 원본 레코드를 계산기와 같은 규칙으로 다시 고른다."""
    start = date.fromisoformat(facts["window"]["start"])
    end = date.fromisoformat(facts["window"]["end"])
    subset = calculator.slice_window(notices, start, end)
    if facts["scope_type"] == "region":
        subset = [n for n in subset if n["region"] == facts["scope_value"]]
    elif facts["scope_type"] == "field":
        subset = [n for n in subset if n["category"] == facts["scope_value"]]
    return subset


def related_links(facts: dict, all_facts: list[dict]) -> list[dict]:
    """내부링크 그래프. 숫자가 들어간 제목은 쓰지 않는다(페이지 간 수치 혼선 방지)."""
    def label(f: dict) -> str:
        if f["scope_type"] == "national":
            return "전국 나라장터 입찰공고 주간 동향"
        if f["scope_type"] == "top":
            return "배정예산 상위 입찰공고"
        if f["scope_type"] == "region":
            return f"{f['label']} 나라장터 입찰공고 동향"
        return f"{f['label']} 입찰공고 동향"

    def entry(f: dict) -> dict:
        return {"label": label(f), "slug": f["slug"], "scope": f["scope_type"]}

    if facts["scope_type"] == "national":
        picks = [f for f in all_facts if f["scope_type"] == "region"][:8]
        picks += [f for f in all_facts if f["scope_type"] == "top"][:1]
    elif facts["scope_type"] == "region":
        picks = [f for f in all_facts if f["scope_type"] == "national"][:1]
        picks += [f for f in all_facts if f["scope_type"] == "top"][:1]
        picks += [f for f in all_facts if f["scope_type"] == "field"][:3]
        picks += [f for f in all_facts if f["scope_type"] == "region" and f is not facts][:2]
    elif facts["scope_type"] == "field":
        picks = [f for f in all_facts if f["scope_type"] == "national"][:1]
        picks += [f for f in all_facts if f["scope_type"] == "top"][:1]
        picks += [f for f in all_facts if f["scope_type"] == "region"][:3]
    else:
        picks = [f for f in all_facts if f["scope_type"] == "national"][:1]
        picks += [f for f in all_facts if f["scope_type"] == "region"][:3]
        picks += [f for f in all_facts if f["scope_type"] == "field"][:2]
    seen = {facts["slug"]}
    out = []
    for f in picks:
        if f["slug"] in seen:
            continue
        seen.add(f["slug"])
        out.append(entry(f))
    return out[:6]


def write_sitemap(pages: list[dict], settings: Settings) -> Path:
    items = []
    for p in pages:
        loc = f"{SITE_URL}/bids/{p['slug']}/"
        items.append(f"  <url><loc>{loc}</loc><lastmod>{p['updated']}</lastmod>"
                     f"<changefreq>daily</changefreq><priority>0.8</priority></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(items) + "\n</urlset>\n")
    path = settings.output_dir / "sitemap.xml"
    path.write_text(xml, encoding="utf-8")
    (settings.output_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    return path


def write_manifest(pages: list[dict], settings: Settings, meta: dict) -> Path:
    doc = {
        "site": SITE_NAME,
        "site_url": SITE_URL,
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "data_mode": settings.resolved_mode(),
        "source": {"name": SOURCE["name"], "url": SOURCE["url"]},
        "window": meta["window"],
        "previous_window": meta["prev_window"],
        "count": len(pages),
        "pages": [
            {"slug": p["slug"], "title": p["title"], "description": p["description"],
             "category": p["category"], "scope": p["scope"], "value": p["value"],
             "path": f"/bids/{p['slug']}/", "updated": p["updated"],
             "tags": p["tags"]}
            for p in pages
        ],
    }
    path = settings.output_dir / "index.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 본체
# --------------------------------------------------------------------------
def run(settings: Settings) -> int:
    mode = settings.resolved_mode()
    run_id = new_run_id(settings)
    window_start = settings.as_of - timedelta(days=6)
    counts = {"collected": 0, "computed": 0, "published": 0, "held": 0, "skipped": 0}

    print("=" * 62)
    print(f" pSEO 파이프라인 · {SITE_NAME}")
    print(f" 기준일 {settings.as_of} · 모드 {mode} · 기간 {settings.days}일")
    print("=" * 62)

    if mode == "live" and not settings.api_key:
        log("모드를 live로 지정했지만 DATA_GO_KR_KEY가 없습니다. "
            "키를 설정하거나 --mode sim으로 실행하세요.", "FAIL")
        return 2

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    (settings.output_dir / "_held").mkdir(parents=True, exist_ok=True)

    conn = db.connect(settings.db_path)
    db.init_db(conn)
    db.start_run(conn, run_id, mode, settings.as_of.isoformat(),
                 window_start.isoformat(), settings.as_of.isoformat())

    try:
        # 1) 수집 ---------------------------------------------------------
        log("[1/7] 수집", "STEP")
        try:
            result = collector.collect(settings)
        except Exception as exc:  # noqa: BLE001
            log(f"수집 실패: {exc}", "FAIL")
            db.log_incident(conn, run_id=run_id, level="error", code="COLLECT_FAILED",
                            message=str(exc))
            db.finish_run(conn, run_id, status="failed", counts=counts, note=str(exc)[:300])
            return 2
        counts["collected"] = len(result["items"])
        for err in result.get("errors", []):
            db.log_incident(conn, run_id=run_id, level="warn", code="OP_PARTIAL", message=err)
        if counts["collected"] == 0:
            log("수집된 공고가 0건입니다. 기간이나 API 상태를 확인하세요.", "FAIL")
            db.finish_run(conn, run_id, status="failed", counts=counts, note="no items")
            return 2

        # 2) 저장 ---------------------------------------------------------
        log("[2/7] 저장(SQLite upsert)", "STEP")
        up = db.upsert_raw_items(conn, result["items"], run_id, SOURCE["name"])
        log(f"신규 {up['inserted']:,}건 · 변경 {up['updated']:,}건 · 그대로 {up['unchanged']:,}건 "
            f"(DB 누적 {db.raw_item_count(conn):,}건)", "OK")
        try:
            raw_label = Path(result["raw_path"]).relative_to(BASE_DIR)
        except ValueError:
            raw_label = Path(result["raw_path"])
        log(f"원시 스냅샷: {raw_label}")

        # 3) 계산 ---------------------------------------------------------
        log("[3/7] 룰 기반 계산", "STEP")
        raw_items = db.load_raw_items(conn)
        notices = calculator.build_notices(raw_items, settings.as_of)
        if not notices:
            log("분석 가능한 공고가 없습니다(게시일 필드 누락 또는 기간 불일치).", "FAIL")
            db.finish_run(conn, run_id, status="failed", counts=counts, note="no analyzable notices")
            return 2
        log(f"분석 대상 {len(notices):,}건 (게시일 {min(n['posted_date'] for n in notices)} ~ "
            f"{max(n['posted_date'] for n in notices)})")
        facts_list, meta = calculator.compute_pages(notices, settings)
        counts["computed"] = len(facts_list)
        counts["skipped"] = len(meta["skipped"])
        log(f"페이지 후보 {len(facts_list)}건 (표본 부족 제외 {len(meta['skipped'])}건)", "OK")

        # 4) 생성 + 5) 검증·발행 -----------------------------------------
        log("[4/7] 마크다운 생성 및 품질 검증", "STEP")
        published: list[dict] = []
        held: list[dict] = []
        published_shingles = db.published_shingles(conn)

        for facts in facts_list:
            subset = page_notices(facts, notices)
            md = formatter.render(facts, related=related_links(facts, facts_list))
            q = quality_gate.validate(md, facts, subset, published_shingles, facts["page_key"])
            db.record_quality(conn, page_key=facts["page_key"], run_id=run_id,
                              passed=q["passed"], score=q["score"], details=q["checks"])

            out_path = settings.output_dir / f"{facts['slug']}.md"
            db_facts = {k: v for k, v in facts.items() if not k.startswith("_")}
            db.save_metric_snapshot(
                conn, snapshot_id=facts["page_key"], run_id=run_id,
                scope_type=facts["scope_type"], scope_value=facts["scope_value"],
                window_start=facts["window"]["start"], window_end=facts["window"]["end"],
                facts={k: v for k, v in db_facts.items() if k != "top_rows"},
            )

            existing = db.get_page(conn, facts["page_key"])
            new_hash = content_hash(md, facts.get("generated_at", ""))
            unchanged = bool(existing and existing["content_hash"] == new_hash
                             and existing["status"] == "published" and out_path.exists())

            if q["passed"]:
                if not unchanged:
                    out_path.write_text(md, encoding="utf-8")
                    db.replace_shingles(conn, facts["page_key"], q["shingles"])
                    published_shingles[facts["page_key"]] = q["shingles"]
                db.upsert_page(conn, {
                    "page_key": facts["page_key"], "slug": facts["slug"],
                    "title": facts["title"], "description": facts["description"],
                    "category": facts["category"], "scope_type": facts["scope_type"],
                    "scope_value": facts["scope_value"],
                    "window_start": facts["window"]["start"],
                    "window_end": facts["window"]["end"],
                    "content_hash": new_hash, "body_chars": q["body_chars"],
                    "quality_score": q["score"], "status": "published",
                    "file_path": str(out_path), "run_id": run_id,
                })
                counts["published"] += 1
                published.append({
                    "slug": facts["slug"], "title": facts["title"],
                    "description": facts["description"], "category": facts["category"],
                    "scope": facts["scope_type"], "value": facts["scope_value"],
                    "tags": facts["tags"], "score": q["score"],
                    "body_chars": q["body_chars"],
                    "updated": (existing["updated_at"] if unchanged and existing else facts["generated_at"]),
                    "status": "unchanged" if unchanged else "published", "file": out_path.name,
                })
                mark = "변동 없음" if unchanged else "발행"
                log(f"  ✔ {mark} {facts['slug']} ({facts['title'][:34]}…) "
                    f"품질 {q['score']:.2f}", "OK")
            else:
                reason = quality_gate.failing_summary(q)
                held_path = settings.output_dir / "_held" / f"{facts['slug']}.md"
                held_path.write_text(
                    f"<!-- 품질 검증 실패: {reason} -->\n" + md, encoding="utf-8")
                db.upsert_page(conn, {
                    "page_key": facts["page_key"], "slug": facts["slug"],
                    "title": facts["title"], "description": facts["description"],
                    "category": facts["category"], "scope_type": facts["scope_type"],
                    "scope_value": facts["scope_value"],
                    "window_start": facts["window"]["start"],
                    "window_end": facts["window"]["end"],
                    "content_hash": new_hash, "body_chars": q["body_chars"],
                    "quality_score": q["score"], "status": "held",
                    "file_path": str(held_path), "run_id": run_id,
                })
                db.log_incident(conn, run_id=run_id, level="warn", code="QUALITY_GATE",
                                message=f"{facts['slug']}: {reason}")
                counts["held"] += 1
                held.append({"slug": facts["slug"], "reason": reason, "score": q["score"]})
                log(f"  ✘ 보류 {facts['slug']} — {reason}", "WARN")

        # 6) 보고서 -------------------------------------------------------
        log("[5/7] 사이트 산출물(sitemap·manifest)", "STEP")
        write_sitemap(published, settings)
        write_manifest(published, settings, meta)

        # 7) 정적 HTML 사이트 빌드 -----------------------------------------
        log("[6/7] 정적 HTML 사이트 생성", "STEP")
        site_info = None
        try:
            import builder
            site_info = builder.build(
                output_dir=settings.output_dir,
                site_dir=settings.site_dir,
                site_url=SITE_URL,
            )
            log(f"리포트 {site_info['detail_pages']}장 · 메인 index.html · "
                f"sitemap {site_info['sitemap_urls']}URL "
                f"({site_info['total_bytes']/1024:.1f} KB)", "OK")
            log(f"사이트: {settings.site_dir}")
            for problem in site_info.get("problems", []):
                log(problem, "WARN")
        except Exception as exc:  # noqa: BLE001 - 발행은 성공했으므로 사이트 빌드 실패만 따로 남긴다
            site_info = {"error": str(exc)}
            log(f"정적 사이트 빌드 실패(마크다운 발행은 완료): {exc}", "WARN")
            db.log_incident(conn, run_id=run_id, level="warn", code="SITE_BUILD_FAILED",
                            message=str(exc)[:300])

        log("[7/7] 완료", "STEP")
        db.finish_run(conn, run_id, status="ok", counts=counts)

        report = {
            "run_id": run_id,
            "generated_at": now_kst().isoformat(timespec="seconds"),
            "site": SITE_NAME,
            "mode": mode,
            "as_of": settings.as_of.isoformat(),
            "window": meta["window"],
            "previous_window": meta["prev_window"],
            "counts": counts,
            "quality": {
                "passed": counts["published"],
                "held": counts["held"],
                "average_score": round(sum(p["score"] for p in published) / len(published), 4)
                if published else 0,
            },
            "pages": published,
            "held_pages": held,
            "skipped_scopes": meta["skipped"][:20],
            "database": db.db_stats(conn),
            "output_dir": str(settings.output_dir),
            "site": site_info,
        }
        report_path = settings.output_dir / "_run-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print("-" * 62)
        print(f" 수집 {counts['collected']:,}건 → 페이지 후보 {counts['computed']}건 → "
              f"발행 {counts['published']}건 · 보류 {counts['held']}건")
        print(f" 출력: {settings.output_dir}")
        if site_info and "error" not in site_info:
            print(f" 사이트: {site_info['site_dir']} (index.html + {site_info['detail_pages']}장)")
        print(f" 보고서: {report_path}")
        print("-" * 62)

        if counts["published"] < MIN_PAGES and not settings.allow_few and not settings.limit_pages:
            log(f"발행 페이지가 {counts['published']}건으로 최소 기준({MIN_PAGES}건)에 못 미칩니다.", "FAIL")
            return 1
        if counts["published"] == 0:
            return 1
        return 0

    except Exception:  # noqa: BLE001 - 어떤 예외든 기록하고 실패 코드로 끝낸다
        traceback.print_exc()
        db.log_incident(conn, run_id=run_id, level="error", code="PIPELINE_CRASH",
                        message=traceback.format_exc()[-800:])
        db.finish_run(conn, run_id, status="failed", counts=counts, note="exception")
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    settings = parse_args(argv if argv is not None else sys.argv[1:])
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
