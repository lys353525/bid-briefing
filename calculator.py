# -*- coding: utf-8 -*-
"""룰 기반 계산기.

원칙: **숫자는 전부 여기서 계산한다.** formatter는 이 함수가 만든 표시 문자열을
배치만 하고, 새로운 숫자를 만들지 않는다. 품질 게이트는 본문의 모든 수치를
facts와 대조하고, 핵심 수치는 원본 공고에서 다시 계산해 교차 검증한다.

산출물: 페이지 1장당 facts dict (표시 문자열 포함).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from config import (
    BUDGET_BUCKETS, CATEGORIES, REGIONS, SOURCE, THRESHOLDS,
    Settings, WEEKDAYS_KO, now_kst,
)
from collector import _pick_region  # 기관명 → 지역 (live/sim 공통 규칙)

REGION_SLUG = {
    "서울": "seoul", "부산": "busan", "대구": "daegu", "인천": "incheon",
    "광주": "gwangju", "대전": "daejeon", "울산": "ulsan", "세종": "sejong",
    "경기": "gyeonggi", "강원": "gangwon", "충북": "chungbuk", "충남": "chungnam",
    "전북": "jeonbuk", "전남": "jeonnam", "경북": "gyeongbuk", "경남": "gyeongnam",
    "제주": "jeju", "전국": "nationwide",
}

CATEGORY_BY_LABEL = {c["label"]: c for c in CATEGORIES}


# --------------------------------------------------------------------------
# 기본 파싱·분류
# --------------------------------------------------------------------------
def parse_money(value) -> Optional[int]:
    """'1,234,000' / '1234000' / '' → int | None"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    text = str(value).strip().replace(",", "").replace("원", "").replace(" ", "")
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number > 0 else None


def parse_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def classify_category(industry: str, title: str) -> str:
    """업종명 + 공고명 키워드 점수로 분야를 정한다. 판별 실패 시 '기타'."""
    industry = industry or ""
    title = title or ""
    best_label, best_score = "기타", 0
    for cat in CATEGORIES:
        score = 0
        for kw in cat["keywords"]:
            if kw and kw in industry:
                score += 3
            if kw and kw in title:
                score += 1
        if score > best_score:
            best_label, best_score = cat["label"], score
    return best_label


def build_notice(item: dict, as_of: date) -> Optional[dict]:
    """원본 payload → 계산용 표준 레코드. 게시일이 없으면 None."""
    payload = item.get("payload") or item
    posted = parse_datetime(payload.get("bidNtceDt") or payload.get("rgstDt"))
    if posted is None:
        return None
    close = parse_datetime(payload.get("bidClseDt"))
    budget = parse_money(payload.get("asignBdgtAmt"))
    budget_src = "배정예산"
    if budget is None:
        budget = parse_money(payload.get("presmptPrce"))
        budget_src = "추정가격" if budget else "미공개"
    demand = str(payload.get("dminsttNm") or "").strip()
    inst = str(payload.get("ntceInsttNm") or "").strip()
    org = demand or inst or "기관 미상"
    region_text = " ".join([demand, inst, str(payload.get("prtcptLmtRgnNm") or "")])
    region = _pick_region(region_text)
    title = str(payload.get("bidNtceNm") or "").strip()
    industry = str(payload.get("indstrytyNm") or "").strip()
    close_date = close.date() if close else None
    days_left = (close_date - as_of).days if close_date else None
    return {
        "no": str(payload.get("bidNtceNo") or ""),
        "ord": str(payload.get("bidNtceOrd") or ""),
        "name": title or "(공고명 없음)",
        "org": org,
        "inst": inst,
        "posted": posted,
        "posted_date": posted.date(),
        "close": close,
        "close_date": close_date,
        "days_left": days_left,
        "budget": budget,
        "budget_src": budget_src,
        "method": str(payload.get("cntrctCnclsMthdNm") or "미기재"),
        "bid_method": str(payload.get("bidMethdNm") or ""),
        "industry": industry,
        "category": classify_category(industry, title),
        "region": region,
        "url": str(payload.get("bidNtceDtlUrl") or payload.get("bidNtceUrl") or ""),
        "biz_type": str(payload.get("biz_type") or item.get("biz_type") or ""),
        "revision": item.get("_revision", 1),
    }


def build_notices(items: Iterable[dict], as_of: date) -> list[dict]:
    out, seen = [], set()
    for item in items:
        rec = build_notice(item, as_of)
        if rec is None:
            continue
        key = f"{rec['no']}-{rec['ord']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    out.sort(key=lambda r: (r["posted"], r["no"]))
    return out


# --------------------------------------------------------------------------
# 표시 형식 (모든 숫자는 이 함수들을 통과해 문자열이 된다)
# --------------------------------------------------------------------------
def fmt_money(value: Optional[int]) -> str:
    """금액을 읽기 쉬운 억/만 단위로. 만 원 미만은 반올림(최대 1만 원 차이)."""
    if value is None:
        return "미공개"
    value = int(value)
    if value < 10_000:
        return f"{value:,}원"
    eok, rem = divmod(value, 100_000_000)
    man = rem // 10_000
    if rem % 10_000 >= 5_000:      # 반올림(버림으로 생기는 하향 편향 방지)
        man += 1
    if man >= 10_000:
        eok, man = eok + 1, man - 10_000
    if eok and man:
        return f"{eok:,}억 {man:,}만 원"
    if eok:
        return f"{eok:,}억 원"
    if man:
        return f"{man:,}만 원"
    return f"{value:,}원"


def fmt_int(value: Optional[int]) -> str:
    return "—" if value is None else f"{int(value):,}"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "비교 불가"
    if abs(value) < 0.05:
        return "0.0%"
    return f"{value:+.1f}%"


def _pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / prev * 100


def fmt_date_ko(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def fmt_short_date(d: date) -> str:
    return f"{d.month}월 {d.day}일"


def fmt_range(start: date, end: date) -> str:
    return f"{fmt_short_date(start)}~{fmt_short_date(end)}"


# --------------------------------------------------------------------------
# 집계
# --------------------------------------------------------------------------
def slice_window(notices: list[dict], start: date, end: date) -> list[dict]:
    return [n for n in notices if start <= n["posted_date"] <= end]


def aggregate(notices: list[dict]) -> dict:
    n = len(notices)
    budgets = [x["budget"] for x in notices if x["budget"]]
    budget_n = len(budgets)
    total = sum(budgets)
    avg = total // budget_n if budget_n else None
    median = int(statistics.median(budgets)) if budgets else None
    bmax = max(budgets) if budgets else None
    bmin = min(budgets) if budgets else None
    open_left = [x for x in notices if x["days_left"] is not None and x["days_left"] >= 0]
    soon = [x for x in open_left if x["days_left"] <= 3]
    avg_left = (sum(x["days_left"] for x in open_left) / len(open_left)) if open_left else None

    by_org: dict[str, dict] = defaultdict(lambda: {"n": 0, "total": 0, "max": 0})
    by_field: dict[str, dict] = defaultdict(lambda: {"n": 0, "total": 0, "max": 0})
    by_region: dict[str, dict] = defaultdict(lambda: {"n": 0, "total": 0, "max": 0})
    by_method: Counter = Counter()
    buckets = {label: 0 for label, _lo, _hi in BUDGET_BUCKETS}

    for x in notices:
        for bucket, key in ((by_org[x["org"]], "org"), (by_field[x["category"]], "field"),
                            (by_region[x["region"]], "region")):
            bucket["n"] += 1
            if x["budget"]:
                bucket["total"] += x["budget"]
                bucket["max"] = max(bucket["max"], x["budget"])
        by_method[x["method"]] += 1
        if x["budget"]:
            for label, lo, hi in BUDGET_BUCKETS:
                if x["budget"] >= lo and (hi is None or x["budget"] < hi):
                    buckets[label] += 1
                    break

    return {
        "n": n,
        "budget_n": budget_n,
        "budget_total": total,
        "budget_avg": avg,
        "budget_median": median,
        "budget_max": bmax,
        "budget_min": bmin,
        "open_n": len(open_left),
        "soon_n": len(soon),
        "avg_left": avg_left,
        "by_org": dict(by_org),
        "by_field": dict(by_field),
        "by_region": dict(by_region),
        "by_method": dict(by_method),
        "buckets": buckets,
    }


def _delta_rows(cur: dict, prev: Optional[dict]) -> dict:
    if not prev or not prev.get("n"):
        return {"notices": "비교 불가", "budget": "비교 불가", "avg": "비교 불가",
                "median": "비교 불가", "available": False}
    return {
        "notices": fmt_pct(_pct(cur["n"], prev["n"])),
        "budget": fmt_pct(_pct(cur["budget_total"], prev["budget_total"])),
        "avg": fmt_pct(_pct(cur["budget_avg"], prev["budget_avg"])),
        "median": fmt_pct(_pct(cur["budget_median"], prev["budget_median"])),
        "available": True,
    }


def _org_rows(stats: dict, limit: int = 6) -> list[dict]:
    rows = sorted(stats["by_org"].items(), key=lambda kv: (-kv[1]["total"], -kv[1]["n"]))
    out = []
    for rank, (name, v) in enumerate(rows[:limit], start=1):
        out.append({"rank": f"{rank}", "name": name, "n": f"{v['n']}건",
                    "total": fmt_money(v["total"] or None), "max": fmt_money(v["max"] or None)})
    return out


def _field_rows(stats: dict, total_n: int) -> list[dict]:
    rows = sorted(stats["by_field"].items(), key=lambda kv: (-kv[1]["total"], -kv[1]["n"]))
    out = []
    for name, v in rows:
        share = (v["n"] / total_n * 100) if total_n else 0
        out.append({"name": name, "n": f"{v['n']}건", "share": f"{share:.1f}%",
                    "total": fmt_money(v["total"] or None)})
    return out


def _region_rows(stats: dict, total_n: int, total_budget: int) -> list[dict]:
    rows = sorted(stats["by_region"].items(), key=lambda kv: (-kv[1]["total"], -kv[1]["n"]))
    out = []
    rank = 0
    for name, v in rows:
        share = (v["n"] / total_n * 100) if total_n else 0
        bshare = (v["total"] / total_budget * 100) if total_budget else 0
        if name in REGIONS:
            rank += 1
            rank_label = f"{rank}"
            display = name
        else:
            rank_label = "—"
            display = "지역 미분류"
        out.append({"rank": rank_label, "name": display, "n": f"{v['n']}건",
                    "share": f"{share:.1f}%", "total": fmt_money(v["total"] or None),
                    "bshare": f"{bshare:.1f}%"})
    return out


def _bucket_rows(stats: dict) -> list[dict]:
    base = stats["budget_n"] or 0
    out = []
    for label, _lo, _hi in BUDGET_BUCKETS:
        count = stats["buckets"].get(label, 0)
        share = (count / base * 100) if base else 0
        out.append({"label": label, "n": f"{count}건", "share": f"{share:.1f}%"})
    return out


def _urgent_rows(notices: list[dict], as_of: date, limit: int = 6) -> list[dict]:
    pool = [n for n in notices if n["days_left"] is not None and 0 <= n["days_left"] <= 3]
    pool.sort(key=lambda n: (n["days_left"], -(n["budget"] or 0)))
    out = []
    for n in pool[:limit]:
        out.append({
            "name": n["name"], "org": n["org"], "category": n["category"],
            "close": f"{fmt_short_date(n['close_date'])} ({WEEKDAYS_KO[n['close_date'].weekday()]})",
            "left": f"{n['days_left']}일" if n["days_left"] > 0 else "오늘 마감",
            "budget": fmt_money(n["budget"]),
        })
    return out


def _top_rows(notices: list[dict], limit: int = 10) -> list[dict]:
    pool = [n for n in notices if n["budget"]]
    pool.sort(key=lambda n: (-n["budget"], n["posted"]))
    out = []
    for rank, n in enumerate(pool[:limit], start=1):
        out.append({
            "rank": f"{rank}", "name": n["name"], "org": n["org"], "category": n["category"],
            "region": n["region"], "method": n["method"], "budget": fmt_money(n["budget"]),
            "posted": fmt_short_date(n["posted_date"]),
            "close": fmt_short_date(n["close_date"]) if n["close_date"] else "—",
            "url": n["url"],
        })
    return out


# --------------------------------------------------------------------------
# 페이지 facts 생성
# --------------------------------------------------------------------------
def _window_meta(start: date, end: date) -> dict:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_ko": fmt_date_ko(start),
        "end_ko": fmt_date_ko(end),
        "short": fmt_range(start, end),
        "range_ko": f"{fmt_date_ko(start)}~{fmt_date_ko(end)}",
        "days": (end - start).days + 1,
    }


def _summary_rows(cur: dict, prev: Optional[dict], delta: dict) -> list[dict]:
    def prev_cell(key, fmt):
        if not prev or not prev.get("n"):
            return "—"
        return fmt(prev.get(key))

    return [
        {"metric": "공고 건수", "value": f"{cur['n']:,}건", "prev": prev_cell("n", lambda v: f"{v:,}건"),
         "delta": delta["notices"]},
        {"metric": "총 배정예산", "value": fmt_money(cur["budget_total"] or None),
         "prev": prev_cell("budget_total", lambda v: fmt_money(v or None)), "delta": delta["budget"]},
        {"metric": "평균 배정예산", "value": fmt_money(cur["budget_avg"]),
         "prev": prev_cell("budget_avg", fmt_money), "delta": delta["avg"]},
        {"metric": "중위 배정예산", "value": fmt_money(cur["budget_median"]),
         "prev": prev_cell("budget_median", fmt_money), "delta": delta["median"]},
        {"metric": "최대 단일 공고", "value": fmt_money(cur["budget_max"]),
         "prev": prev_cell("budget_max", fmt_money), "delta": "—"},
        {"metric": "진행 중 공고", "value": f"{cur['open_n']:,}건", "prev": "—", "delta": "—"},
        {"metric": "마감 3일 이내", "value": f"{cur['soon_n']:,}건", "prev": "—", "delta": "—"},
        {"metric": "예산 공개율", "value": (f"{(cur['budget_n'] / cur['n'] * 100):.1f}%" if cur["n"] else "0.0%"),
         "prev": (f"{(prev['budget_n'] / prev['n'] * 100):.1f}%" if prev and prev.get("n") else "—"),
         "delta": "—"},
    ]


def _method_notes(settings: Settings, window: dict, scope_label: str) -> list[str]:
    notes = [
        f"집계 대상은 공고게시일시(bidNtceDt)가 {window['range_ko']}에 속하는 나라장터 입찰공고입니다.",
        "예산은 배정예산(asignBdgtAmt)을 우선 사용하고, 값이 없으면 추정가격(presmptPrce)으로 대체했습니다. 둘 다 없으면 '미공개'로 두고 평균·중위 계산에서 제외했습니다.",
        "증감률은 (이번 기간 값 − 지난 기간 값) ÷ 지난 기간 값 × 100으로 계산해 소수 첫째 자리에서 반올림했습니다.",
        "금액은 읽기 쉽도록 만 원 단위로 반올림해 표시하며, 표시된 금액을 그대로 더하면 원본 합계와 최대 1만 원 미만 차이가 날 수 있습니다. 증감률과 순위는 원본 정수 금액으로 계산했습니다.",
        "중위값은 예산이 공개된 공고들의 중앙값이며, 평균이 큰 공고 몇 건에 끌려가는 왜곡을 보완하는 지표입니다.",
        f"지역 분류는 수요기관·공고기관명의 시도 키워드로 판별하며, 판별되지 않는 건은 '전국'으로 둡니다. 따라서 {scope_label} 집계는 기관 소재지 기준의 근사치입니다.",
        "분야 분류는 업종명(indstrytyNm)과 공고명 키워드에 가중치를 준 규칙 기반 분류로, 공식 통계 분류가 아닌 참고 값입니다.",
        "모든 수치는 원본 공고 필드에서 코드로 직접 계산했습니다. 문장을 다듬는 과정에서 새로운 숫자를 만들어 넣지 않습니다.",
    ]
    if settings.resolved_mode() == "sim":
        notes.append("이 페이지는 공공데이터포털 API 키 없이 생성한 표본 데이터를 사용했습니다. 실제 입찰공고가 아니므로 참고용으로만 읽어야 합니다.")
    return notes


def _faq(scope_label: str, cur: dict, prev: Optional[dict], delta: dict,
         notices: list[dict], top: list[dict], window: dict) -> list[dict]:
    faq = []
    if top:
        faq.append({
            "q": f"{scope_label}에서 이번 기간 배정예산이 가장 큰 공고는 무엇인가요?",
            "a": f"{top[0]['name']}({top[0]['org']})의 배정예산은 {top[0]['budget']}입니다. 공고일은 {top[0]['posted']}, 마감일은 {top[0]['close']}입니다.",
        })
    if delta["available"]:
        change = (cur["n"] - prev["n"]) / prev["n"] * 100 if prev["n"] else 0
        if change > 0:
            word = f"{abs(change):.1f}% 늘었습니다"
        elif change < 0:
            word = f"{abs(change):.1f}% 줄었습니다"
        else:
            word = "변화가 없었습니다"
        faq.append({
            "q": "공고 건수는 지난 기간과 비교해 어떻게 달라졌나요?",
            "a": f"이번 기간 {cur['n']:,}건으로 지난 기간 {prev['n']:,}건 대비 {word}. 총 배정예산은 {delta['budget']} 변동했습니다.",
        })
    faq.append({
        "q": "평균 배정예산은 얼마인가요?",
        "a": f"예산이 공개된 {cur['budget_n']:,}건의 평균은 {fmt_money(cur['budget_avg'])}, 중위값은 {fmt_money(cur['budget_median'])}입니다. 예산이 공개되지 않은 공고는 {cur['n'] - cur['budget_n']:,}건입니다.",
    })
    faq.append({
        "q": "마감이 임박한 공고는 몇 건인가요?",
        "a": f"기준일 현재 마감까지 3일 이내로 남은 공고가 {cur['soon_n']:,}건입니다. 진행 중인 공고는 모두 {cur['open_n']:,}건이며, 평균 잔여 기간은 {cur['avg_left']:.1f}일입니다." if cur["avg_left"] is not None else
             f"기준일 현재 마감까지 3일 이내로 남은 공고가 {cur['soon_n']:,}건입니다.",
    })
    if cur["by_field"]:
        top_field = max(cur["by_field"].items(), key=lambda kv: kv[1]["total"])
        faq.append({
            "q": "어떤 분야에 예산이 가장 많이 배정되었나요?",
            "a": f"{top_field[0]} 분야가 {top_field[1]['n']}건, 총 {fmt_money(top_field[1]['total'] or None)}으로 가장 큽니다. 이는 {scope_label} 전체 배정예산의 {(top_field[1]['total'] / cur['budget_total'] * 100 if cur['budget_total'] else 0):.1f}%입니다.",
        })
    return faq


def _top_faq(scope_label: str, top_pool: list[dict], top_sum: int, cur: dict) -> list[dict]:
    if not top_pool:
        return []
    share = (top_sum / cur["budget_total"] * 100) if cur["budget_total"] else 0
    avg = top_sum // len(top_pool)
    fields = Counter(n["category"] for n in top_pool)
    top_field, top_field_n = fields.most_common(1)[0]
    return [
        {"q": "이번 기간 배정예산이 가장 큰 공고는 무엇인가요?",
         "a": f"{top_pool[0]['name']}({top_pool[0]['org']})으로 배정예산은 {fmt_money(top_pool[0]['budget'])}입니다. 공고일은 {fmt_short_date(top_pool[0]['posted_date'])}, 마감일은 {fmt_short_date(top_pool[0]['close_date']) if top_pool[0]['close_date'] else '미상'}입니다."},
        {"q": f"상위 {len(top_pool)}건의 배정예산 합계는 얼마인가요?",
         "a": f"합계 {fmt_money(top_sum)}, 건당 평균 {fmt_money(avg)}입니다. 이는 이번 기간 전체 배정예산 {fmt_money(cur['budget_total'] or None)}의 {share:.1f}%에 해당합니다."},
        {"q": "상위 공고는 어떤 분야에 몰려 있나요?",
         "a": f"{len(top_pool)}건 가운데 {top_field} 분야가 {top_field_n}건으로 가장 많습니다. 나머지는 분야별 분포 표에서 확인할 수 있습니다."},
        {"q": "이 목록은 어떤 기준으로 골랐나요?",
         "a": "공고게시일시가 집계 기간에 속하는 공고 중 배정예산(없으면 추정가격)이 큰 순서로 정렬했습니다. 마감이 지난 공고도 게시일이 기간에 들어오면 포함됩니다."},
    ]


def build_facts(*, scope_type: str, scope_value: str, label: str, cur: dict,
                prev: Optional[dict], monthly: Optional[dict], notices: list[dict],
                settings: Settings, window: dict, prev_window: dict,
                region_stats: Optional[dict] = None, region_rank: Optional[int] = None,
                region_total: int = 0) -> dict:
    delta = _delta_rows(cur, prev)
    top = _top_rows(notices, 10)
    top_pool = sorted([x for x in notices if x["budget"]], key=lambda n: -n["budget"])[: len(top)]
    top_sum = sum(x["budget"] for x in top_pool)
    top_stats = {
        "count": f"{len(top_pool)}",
        "sum": fmt_money(top_sum),
        "avg": fmt_money(top_sum // len(top_pool)) if top_pool else "—",
        "max": fmt_money(max((x["budget"] for x in top_pool), default=None)),
        "min": fmt_money(min((x["budget"] for x in top_pool), default=None)),
        "share": (f"{(top_sum / cur['budget_total'] * 100):.1f}%" if cur["budget_total"] else "0.0%"),
    }
    top_field_counter = Counter(x["category"] for x in top_pool)
    top_field_rows = [
        {"name": name, "n": f"{n}건",
         "share": f"{(n / len(top_pool) * 100):.1f}%"}
        for name, n in top_field_counter.most_common()
    ]
    summary = _summary_rows(cur, prev, delta)
    field_rows = _field_rows(cur, cur["n"])
    org_rows = _org_rows(cur)
    bucket_rows = _bucket_rows(cur)
    urgent = _urgent_rows(notices, settings.as_of)

    monthly_delta = "비교 불가"
    if monthly and monthly.get("prev") and monthly["prev"].get("n"):
        monthly_delta = fmt_pct(_pct(monthly["cur"]["n"], monthly["prev"]["n"]))

    slug_prefix = f"{window['end'][:4]}-w{date.fromisoformat(window['end']).isocalendar().week:02d}"
    if scope_type == "national":
        slug = f"{slug_prefix}-nationwide"
        title = (f"나라장터 입찰공고 주간 동향 ({window['short']}) — "
                 f"{cur['n']:,}건 {fmt_money(cur['budget_total'] or None)}")
        description = (f"{window['short']} 나라장터 입찰공고 {cur['n']:,}건을 집계했습니다. "
                       f"총 배정예산 {fmt_money(cur['budget_total'] or None)}, 평균 {fmt_money(cur['budget_avg'])}, "
                       f"전주 대비 건수 {delta['notices']}. 지역·분야별 분포와 마감 임박 공고를 정리했습니다.")
        category = "주간 요약"
        tags = ["나라장터", "입찰공고", "전국", "주간 동향", "조달청", "배정예산"]
    elif scope_type == "region":
        slug = f"{slug_prefix}-region-{REGION_SLUG.get(scope_value, 'etc')}"
        title = (f"{label} 나라장터 입찰공고 동향 ({window['short']}) — "
                 f"{cur['n']:,}건 {fmt_money(cur['budget_total'] or None)}")
        description = (f"{window['short']} {label} 지역 나라장터 입찰공고 {cur['n']:,}건. "
                       f"총 배정예산 {fmt_money(cur['budget_total'] or None)}, 평균 {fmt_money(cur['budget_avg'])}, "
                       f"전주 대비 {delta['notices']}. 발주기관·분야·마감 임박 공고를 정리했습니다.")
        category = "지역별 동향"
        tags = ["나라장터", "입찰공고", label, "지역별 동향", "조달청", "배정예산"]
    elif scope_type == "field":
        slug = f"{slug_prefix}-field-{CATEGORY_BY_LABEL.get(scope_value, {}).get('key', 'etc')}"
        title = (f"{label} 입찰공고 동향 ({window['short']}) — "
                 f"{cur['n']:,}건 {fmt_money(cur['budget_total'] or None)}")
        description = (f"{window['short']} {label} 분야 나라장터 입찰공고 {cur['n']:,}건. "
                       f"총 배정예산 {fmt_money(cur['budget_total'] or None)}, 평균 {fmt_money(cur['budget_avg'])}, "
                       f"전주 대비 {delta['notices']}. 지역 분포와 예산 상위 공고를 정리했습니다.")
        category = "분야별 동향"
        tags = ["나라장터", "입찰공고", label, "분야별 동향", "조달청", "배정예산"]
    else:  # top
        slug = f"{slug_prefix}-top-budget"
        title = f"이번 주 배정예산 상위 입찰공고 {len(top)}선 ({window['short']})"
        description = (f"{window['short']} 나라장터 입찰공고 가운데 배정예산이 큰 {len(top)}건을 추렸습니다. "
                       f"최대 {fmt_money(cur['budget_max'])}, 상위 {len(top)}건 합계 {top_stats['sum']}입니다.")
        category = "예산 순위"
        tags = ["나라장터", "입찰공고", "예산 순위", "대형 공고", "조달청", "주간 동향"]

    facts = {
        "page_key": f"{scope_type}|{scope_value}|{window['end']}",
        "scope_type": scope_type,
        "scope_value": scope_value,
        "label": label,
        "slug": slug,
        "title": title,
        "description": description,
        "category": category,
        "tags": tags,
        "window": window,
        "prev_window": prev_window,
        "summary_rows": summary,
        "delta": delta,
        "headline": {
            "notices": f"{cur['n']:,}건",
            "budget": fmt_money(cur["budget_total"] or None),
            "avg": fmt_money(cur["budget_avg"]),
            "soon": f"{cur['soon_n']:,}건",
        },
        "org_rows": org_rows,
        "field_rows": field_rows,
        "buckets": bucket_rows,
        "urgent_rows": urgent,
        "top_rows": top,
        "top_count": f"{len(top)}",
        "top_sum": top_sum,
        "top_stats": top_stats,
        "top_field_rows": top_field_rows,
        "faq": _top_faq(label, top_pool, top_sum, cur) if scope_type == "top"
        else _faq(label, cur, prev, delta, notices, top, window),
        "method_notes": _method_notes(settings, window, label),
        "source_links": [
            {"label": f"{SOURCE['name']} (공공데이터포털)", "url": SOURCE["url"]},
            {"label": "공공데이터포털", "url": "https://www.data.go.kr"},
        ],
        "monthly": {
            "available": bool(monthly and monthly.get("prev") and monthly["prev"].get("n")),
            "delta": monthly_delta,
            "cur_n": f"{monthly['cur']['n']:,}건" if monthly else "—",
            "prev_n": f"{monthly['prev']['n']:,}건" if monthly and monthly.get("prev") else "—",
        },
        "data_mode": settings.resolved_mode(),
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "source_name": SOURCE["name"],
        "source_url": SOURCE["url"],
        "verify": {
            "notices": cur["n"],
            "budget_total": cur["budget_total"],
            "budget_n": cur["budget_n"],
            "budget_median": cur["budget_median"],
            "window_start": window["start"],
            "window_end": window["end"],
        },
        "_cur": cur,
        "_prev": prev,
    }
    if scope_type in ("region", "field"):
        facts["region_rows"] = _region_rows(cur, cur["n"], cur["budget_total"])
    else:
        facts["region_rows"] = _region_rows(cur, cur["n"], cur["budget_total"])
    if region_stats is not None and region_rank:
        facts["region_rank"] = {
            "rank": f"{region_rank}",
            "of": f"{len(REGIONS)}",
        }
    return facts


# --------------------------------------------------------------------------
# 전체 페이지 세트
# --------------------------------------------------------------------------
def compute_pages(notices: list[dict], settings: Settings) -> tuple[list[dict], dict]:
    as_of = settings.as_of
    cur_start = as_of - timedelta(days=6)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    window = _window_meta(cur_start, as_of)
    prev_window = _window_meta(prev_start, prev_end)

    cur_all = slice_window(notices, cur_start, as_of)
    prev_all = slice_window(notices, prev_start, prev_end)

    month_start = as_of - timedelta(days=29)
    month_prev_end = month_start - timedelta(days=1)
    month_prev_start = month_prev_end - timedelta(days=29)
    month_cur = aggregate(slice_window(notices, month_start, as_of))
    month_prev = aggregate(slice_window(notices, month_prev_start, month_prev_end))

    cur_stats = aggregate(cur_all)
    prev_stats = aggregate(prev_all) if prev_all else None

    # 지역/분야 순위 계산용
    region_stats = cur_stats["by_region"]
    rankable_regions = {k: v for k, v in region_stats.items() if k in REGIONS}
    region_rank_by_name = {
        name: rank for rank, (name, _v) in enumerate(
            sorted(rankable_regions.items(), key=lambda kv: (-kv[1]["total"], -kv[1]["n"])), start=1)
    }

    facts_list: list[dict] = []
    skipped: list[dict] = []

    # 1) 전국 요약
    if cur_stats["n"] >= THRESHOLDS["min_national_notices"]:
        facts_list.append(build_facts(
            scope_type="national", scope_value="전국", label="전국",
            cur=cur_stats, prev=prev_stats,
            monthly={"cur": month_cur, "prev": month_prev},
            notices=cur_all, settings=settings, window=window, prev_window=prev_window,
        ))
    else:
        skipped.append({"scope": "national", "reason": f"표본 부족({cur_stats['n']}건)"})

    # 2) 예산 상위
    if len([n for n in cur_all if n["budget"]]) >= 10:
        facts_list.append(build_facts(
            scope_type="top", scope_value="top-budget", label="전국",
            cur=cur_stats, prev=prev_stats,
            monthly={"cur": month_cur, "prev": month_prev},
            notices=cur_all, settings=settings, window=window, prev_window=prev_window,
        ))

    # 3) 지역별
    for region in REGIONS:
        subset = [n for n in cur_all if n["region"] == region]
        if len(subset) < THRESHOLDS["min_region_notices"]:
            skipped.append({"scope": f"region:{region}", "reason": f"표본 부족({len(subset)}건)"})
            continue
        subset_prev = [n for n in prev_all if n["region"] == region]
        facts_list.append(build_facts(
            scope_type="region", scope_value=region, label=region,
            cur=aggregate(subset), prev=aggregate(subset_prev) if subset_prev else None,
            monthly=None,
            notices=subset, settings=settings, window=window, prev_window=prev_window,
            region_stats=region_stats, region_rank=region_rank_by_name.get(region),
        ))

    # 4) 분야별
    for cat in CATEGORIES:
        label = cat["label"]
        subset = [n for n in cur_all if n["category"] == label]
        if len(subset) < THRESHOLDS["min_field_notices"]:
            skipped.append({"scope": f"field:{label}", "reason": f"표본 부족({len(subset)}건)"})
            continue
        subset_prev = [n for n in prev_all if n["category"] == label]
        facts_list.append(build_facts(
            scope_type="field", scope_value=label, label=label,
            cur=aggregate(subset), prev=aggregate(subset_prev) if subset_prev else None,
            monthly=None,
            notices=subset, settings=settings, window=window, prev_window=prev_window,
        ))

    if settings.limit_pages:
        facts_list = facts_list[: settings.limit_pages]

    meta = {
        "window": window,
        "prev_window": prev_window,
        "cur_stats": cur_stats,
        "prev_stats": prev_stats,
        "monthly": {"cur": month_cur, "prev": month_prev},
        "skipped": skipped,
        "notice_count": len(notices),
    }
    return facts_list, meta
