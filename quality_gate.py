# -*- coding: utf-8 -*-
"""품질 검증기(발행 게이트).

통과 조건(전부 만족해야 발행):
  1. frontmatter 필수 필드(title·description·pubDate·tags·category)
  2. 제목·설명 길이, 태그 개수
  3. 본문 길이(공백 제외 600자 이상)와 구조(H2 3개, 표 2개 이상)
  4. 1차 데이터 출처 링크 1개 이상
  5. JSON-LD 파싱 가능 + Article/Dataset 포함
  6. 본문의 모든 3자리 이상 수치가 계산된 facts 안에 존재
  7. 핵심 수치를 원본 공고에서 다시 계산해 facts와 일치하는지 교차 검증
  8. 기존 발행 글과 5-gram 유사도 0.72 미만(중복 페이지 방지)
  9. 금칙어·미완성 자리표시자 없음

실패한 글은 output/_held/로 보내고 사유를 남긴다. 발행하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from typing import Optional

from config import BANNED_PHRASES, SOURCE, THRESHOLDS

FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.S)
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{2,}|\d+")
SCRIPT_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
TABLE_RE = re.compile(r"^\|.*\|$", re.M)
PLACEHOLDER_PATTERNS = ["TODO", "FIXME", "None", "undefined", "NaN", "{", "}"]


def split_frontmatter(md: str) -> tuple[dict, str]:
    m = FM_RE.match(md)
    if not m:
        return {}, md
    raw, body = m.group(1), md[m.end():]
    meta: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(_unquote(line[4:].strip()))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            if not v:
                meta[key] = []
            else:
                meta[key] = _unquote(v)
    return meta, body


def _unquote(value: str):
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value in ("null", "Null", "NULL"):
        return None
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _plain_text(body: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[|>#*`_\[\]()]", " ", text)
    return text


def _allowed_numbers(facts: dict, meta: dict) -> set[str]:
    blob = json.dumps(facts, ensure_ascii=False, default=str) + "\n" + json.dumps(
        meta, ensure_ascii=False, default=str)
    allowed: set[str] = set()
    for tok in NUM_RE.findall(blob):
        clean = tok.replace(",", "")
        allowed.add(clean)
        allowed.add(clean.split(".")[0])
        for part in clean.split("."):
            allowed.add(part)
    return allowed


def shingles(body: str, n: int = 5) -> set[str]:
    tokens = TOKEN_RE.findall(_plain_text(body).lower())
    out = set()
    for i in range(len(tokens) - n + 1):
        gram = " ".join(tokens[i:i + n])
        out.add(hashlib.sha1(gram.encode("utf-8")).hexdigest()[:16])
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _check(name: str, passed: bool, detail: str, weight: float = 1.0) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "weight": weight}


def validate(md: str, facts: dict, notices: Optional[list[dict]] = None,
             published: Optional[dict[str, set[str]]] = None,
             page_key: str = "") -> dict:
    meta, body = split_frontmatter(md)
    checks: list[dict] = []

    # 1) frontmatter 필수 필드
    required = ["title", "description", "pubDate", "tags", "category"]
    missing = [k for k in required if not meta.get(k)]
    checks.append(_check("frontmatter", not missing,
                         "필수 필드 확인" if not missing else f"누락: {', '.join(missing)}", 2.0))

    # 2) 제목·설명 길이
    title = str(meta.get("title") or "")
    desc = str(meta.get("description") or "")
    checks.append(_check("title_length", 0 < len(title) <= THRESHOLDS["max_title_chars"],
                         f"제목 {len(title)}자(상한 {THRESHOLDS['max_title_chars']}자)"))
    checks.append(_check(
        "description_length",
        THRESHOLDS["desc_min_chars"] <= len(desc) <= THRESHOLDS["desc_max_chars"],
        f"설명 {len(desc)}자({THRESHOLDS['desc_min_chars']}~{THRESHOLDS['desc_max_chars']}자)"))
    tags = meta.get("tags") or []
    checks.append(_check("tags", isinstance(tags, list) and len(tags) >= 3,
                         f"태그 {len(tags) if isinstance(tags, list) else 0}개"))

    # 3) 본문 길이·구조
    plain = _plain_text(body)
    char_count = len(re.sub(r"\s", "", plain))
    checks.append(_check("body_length", char_count >= THRESHOLDS["min_body_chars"],
                         f"공백 제외 {char_count:,}자(하한 {THRESHOLDS['min_body_chars']}자)"))
    h2_count = len(re.findall(r"^## ", body, re.M))
    checks.append(_check("heading_structure", h2_count >= THRESHOLDS["min_h2"],
                         f"H2 {h2_count}개"))
    table_blocks = _count_tables(body)
    checks.append(_check("tables", table_blocks >= THRESHOLDS["min_tables"],
                         f"표 {table_blocks}개"))

    # 4) 출처 링크
    link_count = len(re.findall(r"https?://[^\s)\]>]+", body))
    has_source = "data.go.kr" in body or "data.go.kr" in str(meta.get("sourceUrl") or "")
    checks.append(_check("source_link", has_source and link_count >= THRESHOLDS["min_source_links"],
                         f"출처 링크 {'있음' if has_source else '없음'} / 전체 링크 {link_count}개"))

    # 5) JSON-LD
    script_match = SCRIPT_RE.search(body)
    jsonld_ok, jsonld_detail = False, "JSON-LD 없음"
    if script_match:
        try:
            doc = json.loads(script_match.group(1))
            graph = doc.get("@graph") or [doc]
            types = {t for node in graph if isinstance(node, dict) for t in
                     ([node.get("@type")] if isinstance(node.get("@type"), str) else node.get("@type") or [])}
            jsonld_ok = {"Article", "Dataset"} <= types
            jsonld_detail = "Article+Dataset 포함" if jsonld_ok else f"@type 누락: {sorted(types)}"
        except json.JSONDecodeError as exc:
            jsonld_detail = f"JSON 파싱 실패: {exc}"
    checks.append(_check("jsonld", jsonld_ok, jsonld_detail, 1.5))

    # 6) 수치 일치
    allowed = _allowed_numbers(facts, meta)
    unverified = []
    for tok in NUM_RE.findall(body):
        clean = tok.replace(",", "").rstrip(".")
        if not clean:
            continue
        digits = re.sub(r"\D", "", clean)
        if len(digits) <= THRESHOLDS["allowed_short_number_len"]:
            continue
        if clean in allowed or clean.split(".")[0] in allowed or clean.replace(".", "") in allowed:
            continue
        unverified.append(tok)
    checks.append(_check("number_consistency", not unverified,
                         "모든 수치가 계산값과 일치" if not unverified
                         else f"검증되지 않은 수치 {len(unverified)}개: {unverified[:8]}", 2.0))

    # 7) facts 교차 검증 (원본에서 재계산)
    verify = facts.get("verify") or {}
    if notices is None:
        checks.append(_check("fact_integrity", False, "원본 공고 목록이 전달되지 않음", 2.0))
    else:
        budgets = [n["budget"] for n in notices if n.get("budget")]
        recomputed = {
            "notices": len(notices),
            "budget_total": sum(budgets),
            "budget_n": len(budgets),
            "budget_median": int(statistics.median(budgets)) if budgets else None,
        }
        diffs = []
        for key, value in recomputed.items():
            if verify.get(key) != value:
                diffs.append(f"{key}: facts={verify.get(key)} 재계산={value}")
        window_ok = (verify.get("window_start") == facts["window"]["start"]
                     and verify.get("window_end") == facts["window"]["end"])
        if not window_ok:
            diffs.append("집계 기간 불일치")
        checks.append(_check("fact_integrity", not diffs,
                             "원본 재계산 일치" if not diffs else "; ".join(diffs), 2.0))

    # 8) 중복 유사도
    current = shingles(body)
    worst_key, worst = "", 0.0
    for other_key, other in (published or {}).items():
        if other_key == page_key:
            continue
        score = jaccard(current, other)
        if score > worst:
            worst_key, worst = other_key, score
    checks.append(_check("duplicate", worst < THRESHOLDS["duplicate_jaccard"],
                         f"최대 유사도 {worst:.2f}" + (f" ({worst_key})" if worst_key else ""), 1.5))

    # 9) 금칙어·자리표시자
    banned = [p for p in BANNED_PHRASES if p in body]
    placeholders = [p for p in PLACEHOLDER_PATTERNS if p in body and p in ("TODO", "FIXME", "undefined", "NaN")]
    checks.append(_check("policy", not banned and not placeholders,
                         "금칙어 없음" if not banned and not placeholders
                         else f"금칙어 {banned[:5]} / 자리표시자 {placeholders}"))

    total_weight = sum(c["weight"] for c in checks)
    got = sum(c["weight"] for c in checks if c["passed"])
    score = round(got / total_weight, 4) if total_weight else 0.0
    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "score": score, "checks": checks, "shingles": current,
            "body_chars": char_count}


def _count_tables(body: str) -> int:
    """구분선(| --- |) 개수로 표 블록을 센다."""
    return len(re.findall(r"^\|\s*---", body, re.M))


def failing_summary(result: dict) -> str:
    return "; ".join(f"{c['name']}: {c['detail']}" for c in result["checks"] if not c["passed"])
