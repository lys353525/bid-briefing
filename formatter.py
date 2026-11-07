# -*- coding: utf-8 -*-
"""pSEO 마크다운 생성기.

facts(계산기 산출물)만 받아서:
  - Astro/Next Content Collections용 YAML frontmatter
  - 요약 표 · 통계 표 · 마감 임박 목록 · FAQ · 산출 방법 · 출처
  - schema.org JSON-LD (Article + Dataset + FAQPage)
를 만든다. 여기서는 **새로운 숫자를 계산하지 않는다**. 표시 문자열은 모두 facts에 있다.
"""
from __future__ import annotations

import json
from typing import Optional

from config import SITE_AUTHOR, SITE_LOCALE, SITE_NAME, SITE_URL, SOURCE


def _y(value) -> str:
    """YAML 스칼라로 안전하게 (JSON 문자열은 YAML에서도 유효)."""
    return json.dumps(value, ensure_ascii=False)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join([" --- "] * len(headers)) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "/").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _pub_iso(facts: dict) -> str:
    return facts["generated_at"]


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------
def render_frontmatter(facts: dict, pub_iso: str) -> str:
    tags = "\n".join(f"  - {_y(t)}" for t in facts["tags"])
    scope_region = facts["label"] if facts["scope_type"] == "region" else "전국"
    scope_field = facts["label"] if facts["scope_type"] == "field" else None
    lines = [
        "---",
        f"title: {_y(facts['title'])}",
        f"description: {_y(facts['description'])}",
        f"pubDate: {pub_iso}",
        f"updated: {pub_iso}",
        "tags:",
        tags,
        f"category: {_y(facts['category'])}",
        f"slug: {_y(facts['slug'])}",
        f"scope: {_y(facts['scope_type'])}",
        f"region: {_y(scope_region)}",
        f"field: {_y(scope_field) if scope_field else 'null'}",
        f"asOf: {_y(facts['window']['end'])}",
        f"windowStart: {_y(facts['window']['start'])}",
        f"windowEnd: {_y(facts['window']['end'])}",
        f"source: {_y(SOURCE['name'])}",
        f"sourceUrl: {_y(SOURCE['url'])}",
        f"dataMode: {_y(facts['data_mode'])}",
        "draft: false",
        "---",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 본문 조각
# --------------------------------------------------------------------------
def _lead(facts: dict) -> str:
    cur = facts["_cur"]
    w = facts["window"]
    d = facts["delta"]
    kind = facts["scope_type"]

    if kind == "national":
        text = (f"{w['range_ko']} 기준 전국에서 새로 게시된 나라장터 입찰공고는 "
                f"**{facts['headline']['notices']}**, 배정예산 합계는 **{facts['headline']['budget']}**입니다. "
                f"공고 1건당 평균 {facts['headline']['avg']}이며, 예산이 공개된 공고는 "
                f"{cur['budget_n']:,}건입니다. 마감까지 3일 이내로 남은 공고는 {facts['headline']['soon']}입니다.")
    elif kind == "region":
        rank = facts.get("region_rank")
        rank_text = (f"전국 {rank['of']}개 시도 가운데 배정예산 기준 {rank['rank']}위 규모입니다. "
                     if rank else "")
        text = (f"{w['range_ko']} 기준 {facts['label']} 지역에서 게시된 나라장터 입찰공고는 "
                f"**{facts['headline']['notices']}**, 배정예산 합계는 **{facts['headline']['budget']}**입니다. "
                f"{rank_text}평균 배정예산은 {facts['headline']['avg']}입니다.")
    elif kind == "field":
        text = (f"{w['range_ko']} 기준 {facts['label']} 분야로 분류된 나라장터 입찰공고는 "
                f"**{facts['headline']['notices']}**, 배정예산 합계는 **{facts['headline']['budget']}**입니다. "
                f"평균 {facts['headline']['avg']}이며, 마감 3일 이내 공고는 {facts['headline']['soon']}입니다.")
    else:
        text = (f"{w['range_ko']} 기준 전국 입찰공고 가운데 배정예산이 큰 순서로 "
                f"{facts['top_count']}건을 추렸습니다. 최대 공고는 {facts['summary_rows'][4]['value']}, "
                f"상위 {facts['top_count']}건의 배정예산 합계는 {facts['top_stats']['sum']}입니다.")

    tail = []
    if d["available"]:
        tail.append(f"지난 기간과 비교하면 공고 건수는 {d['notices']}, 총 배정예산은 {d['budget']} 변동했습니다.")
    if facts.get("monthly", {}).get("available"):
        tail.append(f"최근 30일 건수는 지난 30일 대비 {facts['monthly']['delta']}입니다.")
    if tail:
        text += " " + " ".join(tail)
    return text


def _summary_section(facts: dict) -> str:
    rows = [[r["metric"], r["value"], r["prev"], r["delta"]] for r in facts["summary_rows"]]
    return ("## 핵심 지표 요약\n\n"
            + _table(["지표", "이번 기간", "지난 기간", "증감"], rows)
            + "\n\n"
            + f"이번 기간은 {facts['window']['range_ko']}, 지난 기간은 {facts['prev_window']['range_ko']}입니다. "
            + "증감은 지난 기간 값을 기준으로 계산했으며, 지난 기간 데이터가 없으면 '비교 불가'로 표시합니다.")


def _compare_section(facts: dict) -> str:
    d = facts["delta"]
    if not d["available"]:
        body = ("지난 기간에 해당하는 공고 데이터가 없어 증감률을 계산하지 않았습니다. "
                "엔진을 처음 가동한 날에는 비교 기준이 비어 있는 것이 정상이며, 다음 수집부터 값이 채워집니다. "
                "같은 기간의 다른 지역·분야 페이지와 절대 규모를 비교해 읽는 편이 좋습니다.")
    else:
        lines = [
            f"- 공고 건수: {facts['summary_rows'][0]['prev']} → {facts['summary_rows'][0]['value']} ({d['notices']})",
            f"- 총 배정예산: {facts['summary_rows'][1]['prev']} → {facts['summary_rows'][1]['value']} ({d['budget']})",
            f"- 평균 배정예산: {facts['summary_rows'][2]['prev']} → {facts['summary_rows'][2]['value']} ({d['avg']})",
            f"- 중위 배정예산: {facts['summary_rows'][3]['prev']} → {facts['summary_rows'][3]['value']} ({d['median']})",
        ]
        body = ("건수와 금액을 함께 봐야 합니다. 건수가 늘어도 총액이 줄면 소규모 공고가 늘어난 것이고, "
                "건수가 줄어도 총액이 늘면 대형 공고가 몰린 것입니다. 평균과 중위값의 간격이 벌어질수록 "
                "특정 대형 공고의 영향이 커졌다는 뜻입니다.\n\n" + "\n".join(lines))
    return "## 지난 기간과의 비교\n\n" + body


def _org_section(facts: dict) -> str:
    rows = facts["org_rows"]
    if not rows:
        return "## 발주기관별 현황\n\n이번 기간에는 기관을 식별할 수 있는 공고가 없었습니다."
    body = _table(["순위", "수요기관", "공고 수", "총 배정예산", "최대 공고"],
                  [[r["rank"], r["name"], r["n"], r["total"], r["max"]] for r in rows])
    return ("## 발주기관별 발주 현황\n\n"
            "배정예산 합계가 큰 기관부터 정리했습니다. 같은 기관이 여러 건을 나눠 발주하는 경우 "
            "건수와 총액이 함께 커지므로, 특정 기관에 기회가 몰리는지 판단하는 데 쓸 수 있습니다.\n\n"
            + body)


def _field_section(facts: dict) -> str:
    rows = facts["field_rows"]
    if not rows:
        return ""
    body = _table(["분야", "공고 수", "건수 비중", "총 배정예산"],
                  [[r["name"], r["n"], r["share"], r["total"]] for r in rows])
    return ("## 분야별 분포\n\n"
            "업종명과 공고명 키워드로 분류한 결과입니다. 분류 기준은 아래 산출 방법에 적었습니다.\n\n"
            + body)


def _region_section(facts: dict) -> str:
    rows = facts["region_rows"]
    if not rows or facts["scope_type"] == "region":
        return ""
    body = _table(["순위", "시도", "공고 수", "건수 비중", "총 배정예산", "예산 비중"],
                  [[r["rank"], r["name"], r["n"], r["share"], r["total"], r["bshare"]] for r in rows])
    return ("## 지역별 분포\n\n"
            "기관 소재지를 기준으로 묶은 결과입니다. 인구와 사업 규모가 큰 지역에 공고가 몰리는 경향이 "
            "그대로 드러나므로, 지역 사업자에게는 자기 지역의 절대 규모와 전국 비중을 함께 보는 것이 유용합니다.\n\n"
            + body)


def _bucket_section(facts: dict) -> str:
    rows = facts["buckets"]
    body = _table(["배정예산 구간", "공고 수", "비중"],
                  [[r["label"], r["n"], r["share"]] for r in rows])
    return ("## 배정예산 구간 분포\n\n"
            "예산이 공개된 공고를 금액 구간별로 나눈 것입니다. 중소 사업자가 참여할 수 있는 구간이 "
            "어디에 몰려 있는지를 읽는 데 씁니다.\n\n"
            + body)


def _urgent_section(facts: dict) -> str:
    rows = facts["urgent_rows"]
    if not rows:
        return ("## 마감 임박 공고\n\n"
                "기준일로부터 3일 이내에 마감되는 공고가 이번 집계에 없습니다.")
    body = _table(["공고명", "수요기관", "분야", "마감", "잔여", "배정예산"],
                  [[r["name"], r["org"], r["category"], r["close"], r["left"], r["budget"]] for r in rows])
    return ("## 마감 임박 공고\n\n"
            "마감까지 3일 이내로 남은 공고를 잔여 기간이 짧은 순서로 정리했습니다. "
            "입찰 준비에 필요한 서류와 시간을 역산할 때 기준으로 삼을 수 있습니다.\n\n"
            + body)


def _top_section(facts: dict) -> str:
    rows = facts["top_rows"]
    if not rows:
        return ""
    limit = len(rows) if facts["scope_type"] == "top" else min(5, len(rows))
    rows = rows[:limit]
    body = _table(["순위", "공고명", "수요기관", "분야", "게시", "마감", "배정예산"],
                  [[r["rank"], r["name"], r["org"], r["category"], r["posted"], r["close"], r["budget"]]
                   for r in rows])
    return ("## 배정예산 상위 공고\n\n"
            "금액이 큰 공고는 경쟁도 치열하지만, 규모가 큰 사업자에게는 가장 현실적인 기회입니다. "
            "아래 목록은 이번 기간에 게시된 공고만 대상으로 합니다.\n\n"
            + body)


def _top_overview_section(facts: dict) -> str:
    s = facts["top_stats"]
    rows = [
        ["추린 공고 수", s["count"] + "건"],
        ["배정예산 합계", s["sum"]],
        ["건당 평균", s["avg"]],
        ["최대", s["max"]],
        ["최소", s["min"]],
        ["전체 배정예산 중 비중", s["share"]],
    ]
    return ("## 상위 공고 한눈에\n\n"
            + _table(["항목", "값"], rows)
            + "\n\n상위 공고는 전체 공고 수에 비해 수가 적지만 예산에서는 큰 비중을 차지합니다. "
              "아래 비중은 이번 기간 전체 배정예산 대비 상위 공고 합계의 비율입니다.")


def _top_field_section(facts: dict) -> str:
    rows = facts.get("top_field_rows") or []
    if not rows:
        return ""
    body = _table(["분야", "건수", "구성 비중"],
                  [[r["name"], r["n"], r["share"]] for r in rows])
    return "## 상위 공고의 분야 구성\n\n" + body


def _method_section(facts: dict) -> str:
    notes = "\n".join(f"- {n}" for n in facts["method_notes"])
    return ("## 산출 방법과 데이터 기준\n\n"
            "이 페이지의 모든 수치는 원본 공고 필드에서 코드로 계산했습니다. 계산식을 공개하는 이유는 "
            "숫자를 그대로 검증할 수 있게 하기 위해서입니다.\n\n"
            + notes)


def _faq_section(facts: dict) -> str:
    blocks = []
    for item in facts["faq"]:
        blocks.append(f"### {item['q']}\n\n{item['a']}")
    return "## 자주 묻는 질문\n\n" + "\n\n".join(blocks)


def _source_section(facts: dict) -> str:
    lines = [f"- [{s['label']}]({s['url']})" for s in facts["source_links"]]
    lines.append(f"- 데이터 갱신 주기: {SOURCE['update_cycle']} / 이용 조건: {SOURCE['license']}")
    lines.append(f"- 집계 기준 시각: {facts['generated_at']}")
    if facts["data_mode"] == "sim":
        lines.append("- 이 페이지는 API 키 없이 생성한 **시뮬레이션 표본**입니다. 실제 공고가 아닙니다.")
    return "## 출처와 이용 조건\n\n" + "\n".join(lines)


def _related_section(related: Optional[list[dict]]) -> str:
    if not related:
        return ""
    lines = [f"- [{r['label']}](/bids/{r['slug']}/)" for r in related]
    return "## 함께 보면 좋은 페이지\n\n" + "\n".join(lines)


def render_jsonld(facts: dict, pub_iso: str) -> str:
    article = {
        "@type": "Article",
        "headline": facts["title"][:110],
        "description": facts["description"],
        "datePublished": pub_iso,
        "dateModified": pub_iso,
        "inLanguage": SITE_LOCALE.replace("_", "-"),
        "author": {"@type": "Organization", "name": SITE_AUTHOR},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
        "isBasedOn": SOURCE["url"],
        "about": {"@type": "Thing", "name": "나라장터 입찰공고"},
        "keywords": ", ".join(facts["tags"]),
    }
    dataset = {
        "@type": "Dataset",
        "name": f"{facts['label']} 나라장터 입찰공고 집계 ({facts['window']['range_ko']})",
        "description": facts["description"],
        "creator": {"@type": "Organization", "name": SOURCE["provider"]},
        "isAccessibleForFree": True,
        "license": SOURCE["license"],
        "temporalCoverage": f"{facts['window']['start']}/{facts['window']['end']}",
        "spatialCoverage": facts["label"],
        "url": SOURCE["url"],
        "variableMeasured": ["공고 건수", "배정예산 합계", "평균 배정예산", "중위 배정예산"],
    }
    faq = {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q["q"],
             "acceptedAnswer": {"@type": "Answer", "text": q["a"]}}
            for q in facts["faq"]
        ],
    }
    doc = {"@context": "https://schema.org", "@graph": [article, dataset, faq]}
    payload = json.dumps(doc, ensure_ascii=False, indent=2)
    return f'<script type="application/ld+json">\n{payload}\n</script>'


# --------------------------------------------------------------------------
# 조립
# --------------------------------------------------------------------------
def render(facts: dict, related: Optional[list[dict]] = None, pub_iso: Optional[str] = None) -> str:
    pub_iso = pub_iso or facts["generated_at"]
    fm = render_frontmatter(facts, pub_iso)
    if facts["scope_type"] == "top":
        # 예산 순위 페이지는 전국 요약을 되풀이하지 않고 '상위 목록' 자체에 집중한다.
        parts = [
            fm, "", "> " + facts["description"], "", _lead(facts), "",
            _top_overview_section(facts), "", _top_section(facts), "",
            _top_field_section(facts), "", _method_section(facts), "",
            _faq_section(facts), "", _source_section(facts), "",
            _related_section(related), "", render_jsonld(facts, pub_iso), "",
        ]
    else:
        parts = [
            fm, "", "> " + facts["description"], "", _lead(facts), "",
            _summary_section(facts), "", _compare_section(facts), "",
            _org_section(facts), "", _field_section(facts), "", _region_section(facts), "",
            _bucket_section(facts), "", _urgent_section(facts), "", _top_section(facts), "",
            _method_section(facts), "", _faq_section(facts), "", _source_section(facts), "",
            _related_section(related), "", render_jsonld(facts, pub_iso), "",
        ]
    text = "\n".join(p for p in parts if p is not None)
    # 연속 빈 줄 정리
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip() + "\n"


def body_of(markdown: str) -> str:
    """frontmatter를 제외한 본문."""
    if markdown.startswith("---"):
        end = markdown.find("\n---", 3)
        if end != -1:
            return markdown[end + 4:]
    return markdown
