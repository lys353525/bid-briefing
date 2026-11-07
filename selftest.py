# -*- coding: utf-8 -*-
"""자체 테스트 — `python3 selftest.py` (또는 `python3 -m unittest selftest -v`).

검증 범위
  1) 계산기: 금액 파싱·표시, 증감률, 중위값, 구간 분포, 분류 규칙
  2) 수집기: 중복 자연키, 재수집 시 변경 감지(revision)
  3) 생성기+게이트: 정상 페이지 통과, 그리고 일부러 망가뜨린 페이지는 반드시 차단
  4) End-to-End: 실제 파이프라인 서브프로세스 실행 → exit 0, 마크다운 10개 이상, HTML 사이트 생성
  5) 잘못된 설정(키 없이 live)에서 명확한 실패(exit 2)
  6) 빌더: frontmatter/표 파싱, 링크 재작성, 사이트 산출물·내부링크 무결성, --exclude-sim
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import builder
import calculator
import collector
import db
import formatter
import pipeline
import quality_gate
from config import KST, default_settings

ENGINE_DIR = Path(__file__).resolve().parent
AS_OF = date(2026, 11, 7)


def _settings(tmp: Path, days: int = 14):
    return default_settings().with_changes(
        as_of=AS_OF, days=days,
        db_path=tmp / "test.db", output_dir=tmp / "out", raw_dir=tmp / "raw",
    )


def _page(tmp: Path, scope: str = "national"):
    s = _settings(tmp)
    result = collector.simulate(s)
    notices = calculator.build_notices(result["items"], s.as_of)
    facts_list, _meta = calculator.compute_pages(notices, s)
    facts = next(f for f in facts_list if f["scope_type"] == scope)
    md = formatter.render(facts)
    subset = pipeline.page_notices(facts, notices)
    return s, notices, facts, md, subset


class TestCalculator(unittest.TestCase):
    def test_parse_money(self):
        self.assertEqual(calculator.parse_money("1,234,000"), 1234000)
        self.assertEqual(calculator.parse_money("1234000"), 1234000)
        self.assertIsNone(calculator.parse_money(""))
        self.assertIsNone(calculator.parse_money("미공개"))
        self.assertIsNone(calculator.parse_money(None))
        self.assertEqual(calculator.parse_money("1,234,000원"), 1234000)
        self.assertEqual(calculator.parse_money("1.234"), 1)

    def test_fmt_money(self):
        self.assertEqual(calculator.fmt_money(12_340_000), "1,234만 원")
        self.assertEqual(calculator.fmt_money(120_000_000), "1억 2,000만 원")
        self.assertEqual(calculator.fmt_money(300_000_000), "3억 원")
        self.assertEqual(calculator.fmt_money(9_000), "9,000원")
        self.assertEqual(calculator.fmt_money(63_098_335_000), "630억 9,834만 원")
        self.assertEqual(calculator.fmt_money(99_999_999), "1억 원")
        self.assertEqual(calculator.fmt_money(None), "미공개")

    def test_fmt_pct(self):
        self.assertEqual(calculator.fmt_pct(12.34), "+12.3%")
        self.assertEqual(calculator.fmt_pct(-4.24), "-4.2%")
        self.assertEqual(calculator.fmt_pct(None), "비교 불가")
        self.assertEqual(calculator.fmt_pct(0.0), "0.0%")

    def test_aggregate_math(self):
        base = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
        notices = [
            {"budget": 100_000_000, "days_left": 5, "org": "A", "category": "시설공사",
             "region": "부산", "method": "일반경쟁입찰", "posted_date": base.date()},
            {"budget": 200_000_000, "days_left": 2, "org": "A", "category": "물품·구매",
             "region": "부산", "method": "제한경쟁입찰", "posted_date": base.date()},
            {"budget": 900_000_000, "days_left": -1, "org": "B", "category": "시설공사",
             "region": "서울", "method": "일반경쟁입찰", "posted_date": base.date()},
            {"budget": None, "days_left": 1, "org": "B", "category": "물품·구매",
             "region": "서울", "method": "수의계약", "posted_date": base.date()},
        ]
        stats = calculator.aggregate(notices)
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["budget_n"], 3)
        self.assertEqual(stats["budget_total"], 1_200_000_000)
        self.assertEqual(stats["budget_avg"], 400_000_000)
        self.assertEqual(stats["budget_median"], 200_000_000)
        self.assertEqual(stats["budget_max"], 900_000_000)
        self.assertEqual(stats["soon_n"], 2)          # days_left 2, 1
        self.assertEqual(stats["open_n"], 3)          # 5, 2, 1
        self.assertEqual(stats["buckets"]["1억~5억 원"], 2)
        self.assertEqual(stats["buckets"]["5억~10억 원"], 1)
        self.assertEqual(stats["by_org"]["A"]["total"], 300_000_000)

    def test_region_classification(self):
        self.assertEqual(calculator._pick_region("부산광역시 해운대구청"), "부산")
        self.assertEqual(calculator._pick_region("경상남도교육청"), "경남")
        self.assertEqual(calculator._pick_region("양산시청"), "경남")
        self.assertEqual(calculator._pick_region("제주특별자치도교육청"), "제주")
        self.assertEqual(calculator._pick_region("조달청"), "전국")
        self.assertEqual(calculator._pick_region("서울특별시 도봉구청"), "서울")

    def test_category_classification(self):
        self.assertEqual(calculator.classify_category("건축공사업", "본관 외벽 도장공사"), "시설공사")
        self.assertEqual(calculator.classify_category("소프트웨어사업자", "통합정보시스템 유지보수"), "정보통신·SW")
        self.assertEqual(calculator.classify_category("", "도서관 도서 구매"), "물품·구매")
        self.assertEqual(calculator.classify_category("", "알 수 없는 사업"), "기타")

    def test_window_slices_on_boundary(self):
        d = datetime(2026, 11, 7, 23, 0, tzinfo=KST)
        notices = [{"posted_date": d.date()}]
        self.assertEqual(len(calculator.slice_window(notices, date(2026, 11, 1), date(2026, 11, 7))), 1)
        self.assertEqual(len(calculator.slice_window(notices, date(2026, 10, 1), date(2026, 10, 31))), 0)


class TestCollectorDb(unittest.TestCase):
    def test_simulated_fields_match_api_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _settings(Path(tmp))
            res = collector.simulate(s)
            self.assertGreater(len(res["items"]), 100)
            sample = res["items"][0]["payload"]
            for field in ["bidNtceNo", "bidNtceOrd", "bidNtceNm", "dminsttNm",
                          "bidNtceDt", "bidClseDt", "presmptPrce", "asignBdgtAmt",
                          "indstrytyNm", "cntrctCnclsMthdNm"]:
                self.assertIn(field, sample)
            keys = {i["natural_key"] for i in res["items"]}
            self.assertEqual(len(keys), len(res["items"]), "자연키 중복 없음")

    def test_revision_increment_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            db.init_db(conn)
            item = {"natural_key": "R26BK00000001-0", "biz_type": "공사",
                    "payload": {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "0",
                                "bidNtceNm": "원안", "bidNtceDt": "2026-11-01 10:00:00"}}
            db.upsert_raw_items(conn, [item], "run1", "test")
            stat = db.upsert_raw_items(conn, [item], "run2", "test")
            self.assertEqual(stat["unchanged"], 1)
            item2 = json.loads(json.dumps(item))
            item2["payload"]["bidNtceNm"] = "정정안"
            stat = db.upsert_raw_items(conn, [item2], "run3", "test")
            self.assertEqual(stat["updated"], 1)
            row = conn.execute("SELECT revision FROM raw_items").fetchone()
            self.assertEqual(row["revision"], 2)
            conn.close()


class TestQualityGate(unittest.TestCase):
    def test_generated_page_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            result = quality_gate.validate(md, facts, subset, {}, facts["page_key"])
            self.assertTrue(result["passed"], quality_gate.failing_summary(result))
            self.assertEqual(result["score"], 1.0)

    def test_rejects_unverified_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            broken = md.replace("## 핵심 지표 요약", "## 핵심 지표 요약\n\n총 987654건으로 집계되었습니다.", 1)
            result = quality_gate.validate(broken, facts, subset, {}, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertIn("number_consistency", names)

    def test_rejects_tampered_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            facts["verify"]["notices"] += 5
            result = quality_gate.validate(md, facts, subset, {}, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertIn("fact_integrity", names)

    def test_rejects_short_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            head, _sep, _body = md.partition("\n## ")
            result = quality_gate.validate(head + "\n## 끝\n\n짧은 글.", facts, subset, {}, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertTrue(
                {"body_length", "heading_structure"} & names)

    def test_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            broken = md.replace("data.go.kr", "example.org")
            result = quality_gate.validate(broken, facts, subset, {}, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertIn("source_link", names)

    def test_rejects_banned_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            broken = md.replace("## 핵심 지표 요약", "## 핵심 지표 요약\n\n수익 보장, 무조건 대박입니다.", 1)
            result = quality_gate.validate(broken, facts, subset, {}, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertIn("policy", names)

    def test_rejects_duplicate_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            _s, _n, facts, md, subset = _page(Path(tmp))
            _meta, body = quality_gate.split_frontmatter(md)
            published = {"other|page|2026-11-07": quality_gate.shingles(body)}
            result = quality_gate.validate(md, facts, subset, published, facts["page_key"])
            self.assertFalse(result["passed"])
            names = {c["name"] for c in result["checks"] if not c["passed"]}
            self.assertIn("duplicate", names)

    def test_region_pages_differ_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _settings(Path(tmp))
            result = collector.simulate(s)
            notices = calculator.build_notices(result["items"], s.as_of)
            facts_list, _ = calculator.compute_pages(notices, s)
            regions = [f for f in facts_list if f["scope_type"] == "region"][:5]
            published = {}
            for facts in regions:
                md = formatter.render(facts)
                subset = pipeline.page_notices(facts, notices)
                out = quality_gate.validate(md, facts, subset, published, facts["page_key"])
                self.assertTrue(out["passed"], f"{facts['label']}: {quality_gate.failing_summary(out)}")
                published[facts["page_key"]] = out["shingles"]


class TestEndToEnd(unittest.TestCase):
    def test_pipeline_runs_and_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            env = dict(os.environ)
            env.pop("DATA_GO_KR_KEY", None)
            proc = subprocess.run(
                [sys.executable, str(ENGINE_DIR / "pipeline.py"),
                 "--as-of", AS_OF.isoformat(), "--days", "14",
                 "--db", str(tmp_dir / "e2e.db"),
                 "--output", str(tmp_dir / "out"),
                 "--raw", str(tmp_dir / "raw"),
                 "--site", str(tmp_dir / "site")],
                capture_output=True, text=True, env=env, cwd=str(ENGINE_DIR), timeout=180,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout[-1500:] + proc.stderr[-1500:])
            files = sorted((tmp_dir / "out").glob("*.md"))
            self.assertGreaterEqual(len(files), 10, "마크다운 10개 이상 생성")
            for f in files:
                text = f.read_text(encoding="utf-8")
                meta, body = quality_gate.split_frontmatter(text)
                for key in ["title", "description", "pubDate", "tags", "category"]:
                    self.assertTrue(meta.get(key), f"{f.name}: {key} 누락")
                self.assertIn('<script type="application/ld+json">', body)
                self.assertIn("data.go.kr", body)
            self.assertTrue((tmp_dir / "out" / "sitemap.xml").exists())
            self.assertTrue((tmp_dir / "out" / "index.json").exists())
            # 파이프라인 마지막 단계가 정적 사이트까지 만든다
            site = tmp_dir / "site"
            self.assertTrue((site / "index.html").exists())
            self.assertTrue((site / "assets" / "style.css").exists())
            html_pages = sorted((site / "bids").glob("*.html"))
            self.assertEqual(len(html_pages), len(files), "마크다운 1장당 HTML 1장")
            report = json.loads((tmp_dir / "out" / "_run-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "sim")
            self.assertGreaterEqual(report["counts"]["published"], 10)
            self.assertEqual(report["counts"]["held"], 0)

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            args = [sys.executable, str(ENGINE_DIR / "pipeline.py"),
                    "--as-of", AS_OF.isoformat(), "--days", "14",
                    "--db", str(tmp_dir / "e2e.db"), "--output", str(tmp_dir / "out"),
                    "--raw", str(tmp_dir / "raw"), "--site", str(tmp_dir / "site")]
            first = subprocess.run(args, capture_output=True, text=True, cwd=str(ENGINE_DIR), timeout=180)
            self.assertEqual(first.returncode, 0)
            before = {f.name: f.stat().st_mtime for f in (tmp_dir / "out").glob("*.md")}
            second = subprocess.run(args, capture_output=True, text=True, cwd=str(ENGINE_DIR), timeout=180)
            self.assertEqual(second.returncode, 0)
            after = {f.name: f.stat().st_mtime for f in (tmp_dir / "out").glob("*.md")}
            self.assertEqual(before, after, "변경 없는 페이지는 다시 쓰지 않는다")

    def test_live_mode_without_key_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            env = dict(os.environ)
            env.pop("DATA_GO_KR_KEY", None)
            proc = subprocess.run(
                [sys.executable, str(ENGINE_DIR / "pipeline.py"), "--mode", "live",
                 "--db", str(tmp_dir / "x.db"), "--output", str(tmp_dir / "out"),
                 "--raw", str(tmp_dir / "raw"), "--site", str(tmp_dir / "site")],
                capture_output=True, text=True, env=env, cwd=str(ENGINE_DIR), timeout=60,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("DATA_GO_KR_KEY", proc.stdout + proc.stderr)




# --------------------------------------------------------------------------
# 5) 정적 사이트 빌더
# --------------------------------------------------------------------------
SAMPLE_MD = """---
title: "샘플 지역 입찰공고 동향 (11월 1일~11월 7일) — 12건 34억 5,000만 원"
description: "11월 1일~11월 7일 샘플 지역 나라장터 입찰공고 12건을 집계했습니다. 총 배정예산 34억 5,000만 원, 평균 2억 8,750만 원입니다."
pubDate: 2026-11-07T09:40:57+09:00
updated: 2026-11-07T09:40:57+09:00
tags:
  - "나라장터"
  - "샘플"
category: "지역별 동향"
slug: "{slug}"
scope: "{scope}"
region: "{region}"
field: null
asOf: "2026-11-07"
windowStart: "2026-11-01"
windowEnd: "2026-11-07"
source: "조달청 나라장터 입찰공고정보서비스"
sourceUrl: "https://www.data.go.kr/data/15129394/openapi.do"
dataMode: "{mode}"
draft: false
---

> 샘플 지역 11월 1일~11월 7일 입찰공고 12건을 정리했습니다.

2026년 11월 1일~2026년 11월 7일 기준 샘플 지역 공고는 **12건**입니다.

## 핵심 지표 요약

| 지표 | 이번 기간 | 지난 기간 | 증감 |
| --- | --- | --- | --- |
| 공고 건수 | 12건 | 10건 | +20.0% |
| 총 배정예산 | 34억 5,000만 원 | 30억 1,000만 원 | +14.6% |

## 발주기관별 발주 현황

기관별 표입니다.

| 순위 | 수요기관 | 공고 수 | 총 배정예산 |
| --- | --- | --- | --- |
| 1 | 샘플시청 | 3건 | 12억 원 |
| 2 | 샘플교육청 | 2건 | 8억 원 |

## 자주 묻는 질문

### 가장 큰 공고는 무엇인가요?

샘플시청 공고가 12억 원입니다.

## 출처와 이용 조건

- [공공데이터포털](https://www.data.go.kr)
- 이 페이지는 API 키 없이 생성한 **시뮬레이션 표본**입니다.

## 함께 보면 좋은 페이지

- [샘플 전국 동향](/bids/{other}/)

<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Article","headline":"샘플","inLanguage":"ko-KR"}}
</script>
"""


def _sample(tmp: Path, slug: str, scope: str = "region", region: str = "샘플",
            other: str = "sample-national", mode: str = "live") -> Path:
    out = tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{slug}.md").write_text(
        SAMPLE_MD.format(slug=slug, scope=scope, region=region, other=other, mode=mode),
        encoding="utf-8")
    return out


class TestBuilder(unittest.TestCase):
    def test_parses_frontmatter_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _sample(Path(tmp), "sample-a")
            pages, problems = builder.load_pages(out)
            self.assertEqual(problems, [])
            page = pages[0]
            self.assertEqual(page.slug, "sample-a")
            self.assertEqual(page.scope, "region")
            self.assertFalse(page.is_sim, "dataMode=live 는 시뮬레이션이 아니다")
            kinds = [k for k, _ in page.blocks]
            self.assertIn("table", kinds)
            self.assertIn("ul", kinds)
            self.assertIn("h3", kinds)
            self.assertEqual(kinds.count("h2"), 5)  # 지표·기관·FAQ·출처·관련
            table = page.table_in_section("발주기관")
            self.assertEqual(table["headers"], ["순위", "수요기관", "공고 수", "총 배정예산"])
            self.assertEqual(len(table["rows"]), 2)
            self.assertTrue(page.ld_json.startswith("{"), "JSON-LD는 head로 분리된다")
            self.assertEqual(page.metrics()["공고 건수"]["value"], "12건")

    def test_rewrites_bids_links_to_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _sample(Path(tmp), "sample-a")
            page = builder.load_pages(out)[0][0]
            self.assertEqual(builder.rewrite_url("/bids/sample-b/", page), "sample-b.html")
            self.assertEqual(builder.rewrite_url("/bids/sample-b/", page, for_index=True),
                             "bids/sample-b.html")
            self.assertEqual(builder.rewrite_url("https://data.go.kr/x", page),
                             "https://data.go.kr/x")
            html = builder.render_detail(page, "https://example.com", "2026-11-07T10:00:00+09:00", 2)
            self.assertIn('href="sample-national.html"', html)
            self.assertNotIn('href="/bids/', html)
            self.assertIn('rel="canonical" href="https://example.com/bids/sample-a.html"', html)
            self.assertIn('class="faq-item"', html)
            self.assertIn('data-copy', html)
            self.assertIn("BreadcrumbList", html)

    def test_build_writes_site_and_resolves_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out = _sample(tmp_dir, "sample-a")
            _sample(tmp_dir, "sample-national", scope="national", region="전국",
                    other="sample-a")
            info = builder.build(output_dir=out, site_dir=tmp_dir / "site",
                                 site_url="https://sample.test/", exclude_sim=False)
            site = tmp_dir / "site"
            self.assertEqual(info["pages"], 2)
            self.assertEqual(info["sitemap_urls"], 3, "메인 + 상세 2장")
            for name in ("index.html", "sitemap.xml", "robots.txt", "404.html",
                         "assets/style.css", "assets/favicon.svg", "build.json"):
                self.assertTrue((site / name).exists(), f"{name} 없음")
            self.assertEqual(len(list((site / "bids").glob("*.html"))), 2)

            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("bids/sample-a.html", index)
            self.assertIn("지역별 입찰 동향", index)
            self.assertIn('rel="canonical" href="https://sample.test/"', index)
            self.assertIn("https://sample.test/sitemap.xml",
                          (site / "robots.txt").read_text(encoding="utf-8"))

            # 모든 내부 링크가 실제 파일을 가리킨다
            for html_file in site.rglob("*.html"):
                for href in re.findall(r'href="([^"]+)"', html_file.read_text(encoding="utf-8")):
                    if href.startswith(("http", "mailto:", "tel:", "#")):
                        continue
                    path = href.split("#")[0]
                    if path:
                        self.assertTrue((html_file.parent / path).resolve().exists(),
                                        f"{html_file.name}: 깨진 링크 {href}")

    def test_exclude_sim_and_keep_foreign_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out = _sample(tmp_dir, "sample-live", mode="live")
            _sample(tmp_dir, "sample-sim", mode="sim")
            site = tmp_dir / "site"
            site.mkdir()
            (site / "keep.txt").write_text("사용자 파일", encoding="utf-8")

            info = builder.build(output_dir=out, site_dir=site,
                                 site_url="https://sample.test", exclude_sim=True)
            self.assertEqual(info["pages"], 1)
            self.assertEqual(info["excluded_sim"], 1)
            self.assertTrue((site / "bids" / "sample-live.html").exists())
            self.assertFalse((site / "bids" / "sample-sim.html").exists())
            self.assertTrue((site / "keep.txt").exists(), "생성물 외 파일은 지우지 않는다")
            self.assertNotIn("sample-sim", (site / "sitemap.xml").read_text(encoding="utf-8"))

            # 재빌드해도 동일한 결과(멱등)
            again = builder.build(output_dir=out, site_dir=site,
                                  site_url="https://sample.test", exclude_sim=True)
            self.assertEqual(again["pages"], 1)
            self.assertTrue((site / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
