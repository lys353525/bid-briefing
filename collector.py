# -*- coding: utf-8 -*-
"""수집기 — 조달청 나라장터 입찰공고정보서비스(data.go.kr).

두 가지 모드를 지원한다.

1) live : DATA_GO_KR_KEY 가 있으면 공공데이터포털 OpenAPI를 실제 호출한다.
          (공사/용역/물품 목록 오퍼레이션, 페이지네이션, 3회 지수 백오프 재시도)
2) sim  : 키가 없을 때. 실제 API 응답과 **동일한 필드명·형식**의 표본 데이터를
          결정적으로 생성한다. 코드 경로(정규화·저장·계산·검증)는 live와 완전히 같다.

시뮬레이션 데이터는 payload에 값을 지어내지 않고, 생성 규칙을 README와 페이지
하단 배너에 명시한다. 발행 글에도 '시뮬레이션' 표시가 붙는다.
"""
from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from config import (
    API_OPERATIONS, CATEGORIES, COLLECT, KST, REGIONS, REGION_KEYWORDS,
    SOURCE, Settings, now_kst, log,
)

# --------------------------------------------------------------------------
# 표본 생성용 사전 (시뮬레이션 모드 전용)
# --------------------------------------------------------------------------
INSTITUTIONS: dict[str, list[tuple[str, str]]] = {
    "서울": [("서울특별시교육청", "서울특별시교육청"), ("서울특별시 도봉구청", "서울특별시 도봉구청"),
             ("서울교통공사", "서울교통공사"), ("서울특별시립보라매병원", "서울특별시립보라매병원"),
             ("한국전력공사 서울본부", "한국전력공사")],
    "부산": [("부산광역시교육청", "부산광역시교육청"), ("부산광역시 해운대구청", "부산광역시 해운대구청"),
             ("부산교통공사", "부산교통공사"), ("부산대학교병원", "부산대학교병원"),
             ("부산광역시 시설관리공단", "부산광역시 시설관리공단")],
    "대구": [("대구광역시교육청", "대구광역시교육청"), ("대구광역시 수성구청", "대구광역시 수성구청"),
             ("대구도시개발공사", "대구도시개발공사"), ("경북대학교병원", "경북대학교병원")],
    "인천": [("인천광역시교육청", "인천광역시교육청"), ("인천광역시 미추홀구청", "인천광역시 미추홀구청"),
             ("인천국제공항공사", "인천국제공항공사"), ("인천광역시 시설관리공단", "인천광역시 시설관리공단")],
    "광주": [("광주광역시교육청", "광주광역시교육청"), ("광주광역시 북구청", "광주광역시 북구청"),
             ("광주도시공사", "광주도시공사")],
    "대전": [("대전광역시교육청", "대전광역시교육청"), ("대전광역시 유성구청", "대전광역시 유성구청"),
             ("대전광역시시설관리공단", "대전광역시시설관리공단")],
    "울산": [("울산광역시교육청", "울산광역시교육청"), ("울산광역시 남구청", "울산광역시 남구청"),
             ("울산시설공단", "울산시설공단")],
    "세종": [("세종특별자치시교육청", "세종특별자치시교육청"), ("세종특별자치시청", "세종특별자치시청")],
    "경기": [("경기도교육청", "경기도교육청"), ("경기도 수원시청", "경기도 수원시청"),
             ("경기도 성남시청", "경기도 성남시청"), ("경기주택도시공사", "경기주택도시공사"),
             ("경기도의료원", "경기도의료원")],
    "강원": [("강원특별자치도교육청", "강원특별자치도교육청"), ("춘천시청", "춘천시청"), ("강원랜드", "강원랜드")],
    "충북": [("충청북도교육청", "충청북도교육청"), ("청주시청", "청주시청"), ("충북개발공사", "충북개발공사")],
    "충남": [("충청남도교육청", "충청남도교육청"), ("천안시청", "천안시청"), ("충남개발공사", "충남개발공사")],
    "전북": [("전북특별자치도교육청", "전북특별자치도교육청"), ("전주시청", "전주시청"), ("전북개발공사", "전북개발공사")],
    "전남": [("전라남도교육청", "전라남도교육청"), ("순천시청", "순천시청"), ("여수시청", "여수시청")],
    "경북": [("경상북도교육청", "경상북도교육청"), ("포항시청", "포항시청"), ("경주시청", "경주시청")],
    "경남": [("경상남도교육청", "경상남도교육청"), ("창원시청", "창원시청"), ("양산시청", "양산시청"),
             ("김해시청", "김해시청"), ("경남개발공사", "경남개발공사")],
    "제주": [("제주특별자치도교육청", "제주특별자치도교육청"), ("제주시청", "제주시청"),
             ("제주국제자유도시개발센터", "제주국제자유도시개발센터")],
}

REGION_WEIGHTS = {
    "서울": 1.7, "경기": 1.6, "부산": 1.1, "인천": 1.0, "대구": 0.8, "경남": 0.9,
    "경북": 0.8, "충남": 0.7, "전남": 0.7, "전북": 0.7, "충북": 0.65, "강원": 0.6,
    "대전": 0.65, "광주": 0.6, "울산": 0.6, "제주": 0.55, "세종": 0.45,
}

# 분야별 프로젝트명 템플릿 (연도·차수 없이도 자연스럽게 읽히도록)
PROJECT_TEMPLATES: dict[str, list[str]] = {
    "시설공사": ["{org} 본관 외벽 도장공사", "{org} 노후 화장실 개선공사", "{org} 옥상 방수공사",
                 "{org} 체육관 바닥 교체공사", "{org} 사무실 환경개선공사", "{org} 창호 교체공사",
                 "{org} 급식실 바닥 보수공사", "{org} 강당 음향반사판 설치공사"],
    "토목·조경": ["{org} 도로 포장 보수공사", "{org} 보도 정비공사", "{org} 하천 정비공사",
                  "{org} 가로수 전정공사", "{org} 근린공원 조경공사", "{org} 배수로 정비공사",
                  "{org} 옹벽 보강공사"],
    "전기·통신": ["{org} 전기설비 안전진단 및 보수공사", "{org} LED 조명 교체공사",
                  "{org} 수배전반 교체공사", "{org} 통신 케이블 정비공사", "{org} CCTV 교체 설치공사"],
    "소방·설비": ["{org} 소방시설 종합정밀점검 용역", "{org} 기계설비 유지관리 용역",
                  "{org} 승강기 유지보수 용역", "{org} 냉난방기 세척 및 점검 용역",
                  "{org} 급배수설비 보수공사"],
    "청소·시설관리": ["{org} 청사 청소 용역", "{org} 시설물 유지관리 용역", "{org} 경비 용역",
                      "{org} 방역 및 소독 용역", "{org} 주차장 관리 용역"],
    "정보통신·SW": ["{org} 통합정보시스템 유지보수 용역", "{org} 홈페이지 개편 사업",
                    "{org} 데이터 분석 플랫폼 구축 용역", "{org} 클라우드 전환 사업",
                    "{org} 정보보안 컨설팅 용역", "{org} 전자문서 시스템 고도화 용역"],
    "교육·행사": ["{org} 교직원 역량강화 연수 운영 용역", "{org} 진로체험 프로그램 운영 용역",
                  "{org} 지역축제 행사 대행 용역", "{org} 학부모 교육 운영 용역",
                  "{org} 독서문화 프로그램 운영 용역"],
    "물품·구매": ["{org} 사무용 컴퓨터 구매", "{org} 교실 냉난방기 구매 설치", "{org} 도서관 도서 구매",
                  "{org} 사무용 가구 구매", "{org} 실험기구 구매", "{org} 전기버스 구매"],
    "연구·컨설팅": ["{org} 정책연구 용역", "{org} 사업 타당성 조사 용역", "{org} 성과평가 연구 용역",
                    "{org} 중장기 기본계획 수립 용역", "{org} 만족도 조사 용역"],
    "의료·보건": ["{org} 의료장비 구매", "{org} 방사선 장비 유지보수 용역", "{org} 의약품 구매",
                  "{org} 건강검진 위탁 용역", "{org} 감염관리 컨설팅 용역"],
}

CATEGORY_BUDGET_RANGE: dict[str, tuple[int, int]] = {
    "시설공사": (80_000_000, 3_500_000_000),
    "토목·조경": (100_000_000, 5_000_000_000),
    "전기·통신": (50_000_000, 800_000_000),
    "소방·설비": (20_000_000, 300_000_000),
    "청소·시설관리": (80_000_000, 1_200_000_000),
    "정보통신·SW": (60_000_000, 1_500_000_000),
    "교육·행사": (15_000_000, 250_000_000),
    "물품·구매": (20_000_000, 900_000_000),
    "연구·컨설팅": (30_000_000, 400_000_000),
    "의료·보건": (40_000_000, 1_200_000_000),
}

INDUSTRY_NAME = {
    "시설공사": "건축공사업", "토목·조경": "토목공사업", "전기·통신": "전기공사업",
    "소방·설비": "기계설비공사업", "청소·시설관리": "시설물유지관리업",
    "정보통신·SW": "소프트웨어사업자", "교육·행사": "학술·교육서비스업",
    "물품·구매": "물품공급업", "연구·컨설팅": "기술용역업", "의료·보건": "의료기기공급업",
}

BIZ_TYPE_BY_CATEGORY = {
    "시설공사": "공사", "토목·조경": "공사", "전기·통신": "공사", "소방·설비": "공사",
    "청소·시설관리": "용역", "정보통신·SW": "용역", "교육·행사": "용역",
    "물품·구매": "물품", "연구·컨설팅": "용역", "의료·보건": "물품",
}


# --------------------------------------------------------------------------
# 정규화
# --------------------------------------------------------------------------
def natural_key(item: dict) -> str:
    return f"{item.get('bidNtceNo', '')}-{item.get('bidNtceOrd', '')}"


def normalize_item(raw: dict, biz_type: str) -> Optional[dict]:
    """API 원본 dict → 엔진 표준 dict. 필수 키가 없으면 None."""
    no = str(raw.get("bidNtceNo") or "").strip()
    if not no:
        return None
    payload = {k: ("" if v is None else str(v).strip()) for k, v in raw.items()}
    payload["bidNtceNo"] = no
    payload["bidNtceOrd"] = str(raw.get("bidNtceOrd") or "0").strip()
    payload["biz_type"] = biz_type
    return {
        "natural_key": natural_key(payload),
        "biz_type": biz_type,
        "payload": payload,
    }


def _pick_region(text: str) -> str:
    """기관명 문자열 → 시도. 시뮬레이션과 live가 같은 함수를 쓴다."""
    for region, keywords in REGION_KEYWORDS.items():
        for kw in keywords:
            if kw and kw in text:
                return region
    return "전국"


def _sim_money(rng: random.Random, lo: int, hi: int) -> int:
    value = math.exp(rng.uniform(math.log(lo), math.log(hi)))
    return int(round(value, -3))


def simulate(settings: Settings, days: Optional[int] = None) -> dict:
    """API 응답과 동일한 필드명으로 표본 데이터를 결정적으로 생성한다."""
    days = days or settings.days
    as_of = settings.as_of
    rng = random.Random(f"pseo-sim-{as_of.isoformat()}-{days}")
    regions = list(INSTITUTIONS.keys())
    weights = [REGION_WEIGHTS.get(r, 0.5) for r in regions]
    items: list[dict] = []
    seq = 1
    stamps = []

    for offset in range(days - 1, -1, -1):
        day = as_of - timedelta(days=offset)
        weekend = day.weekday() >= 5
        count = rng.randint(6, 10) if weekend else rng.randint(26, 34)
        for _ in range(count):
            region = rng.choices(regions, weights=weights, k=1)[0]
            org_inst, demand_inst = rng.choice(INSTITUTIONS[region])
            category = rng.choice(CATEGORIES)["label"]
            template = rng.choice(PROJECT_TEMPLATES[category])
            org_short = org_inst.replace("특별시", "").replace("광역시", "").replace("특별자치도", "").replace("특별자치시", "")
            title = template.format(org=org_short)
            lo, hi = CATEGORY_BUDGET_RANGE[category]
            budget = _sim_money(rng, lo, hi)
            hid = rng.random() < 0.09
            posted = datetime(day.year, day.month, day.day,
                              rng.randint(9, 17), rng.randint(0, 59), tzinfo=KST)
            close = posted + timedelta(days=rng.randint(4, 14), hours=rng.randint(0, 6))
            biz = BIZ_TYPE_BY_CATEGORY[category]
            prefix = {"공사": "BK", "용역": "BD", "물품": "BE"}[biz]
            raw = {
                "bidNtceNo": f"R{day:%y}{prefix}{seq:08d}",
                "bidNtceOrd": rng.choice(["0", "1"]),
                "bidNtceNm": title,
                "ntceInsttNm": org_inst if rng.random() < 0.45 else "조달청",
                "dminsttNm": demand_inst,
                "bidNtceDt": posted.strftime("%Y-%m-%d %H:%M:%S"),
                "bidClseDt": close.strftime("%Y-%m-%d %H:%M:%S"),
                "presmptPrce": "" if hid else str(int(budget * 0.9)),
                "asignBdgtAmt": "" if hid else str(budget),
                "cntrctCnclsMthdNm": rng.choices(
                    ["일반경쟁입찰", "제한경쟁입찰", "지명경쟁입찰", "수의계약"],
                    weights=[55, 30, 8, 7], k=1)[0],
                "bidMethdNm": rng.choices(
                    ["전자입찰", "전자입찰(가격/기술)", "전자입찰(기술/가격)"],
                    weights=[80, 13, 7], k=1)[0],
                "indstrytyNm": INDUSTRY_NAME[category],
                "prtcptLmtRgnNm": region if region != "전국" else "",
                "rgstDt": posted.strftime("%Y-%m-%d %H:%M:%S"),
                # 시뮬레이션에서는 존재하지 않는 상세 URL을 만들지 않는다(깨진 링크 방지).
                "bidNtceDtlUrl": "",
                "bidNtceUrl": "",
                "biz_type": biz,
                "_simulated": "true",
            }
            seq += 1
            norm = normalize_item(raw, biz)
            if norm:
                items.append(norm)
        stamps.append(day.strftime("%Y-%m-%d"))

    return {
        "mode": "sim",
        "items": items,
        "operations": {name: {"calls": 0, "received": len(items)} for name in API_OPERATIONS},
        "calls": 0,
        "errors": [],
        "days": days,
        "window": {"start": stamps[0], "end": stamps[-1]},
    }


# --------------------------------------------------------------------------
# live 수집
# --------------------------------------------------------------------------
def _http_get_json(url: str, timeout: int, retries: int, backoff: float) -> dict:
    last_error = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "pseo-engine/1.0 (+https://data.go.kr)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                snippet = body.strip().replace("\n", " ")[:300]
                raise RuntimeError(f"JSON 아님: {snippet}")
        except Exception as exc:  # noqa: BLE001 - 재시도 후 마지막 오류를 올린다
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(last_error)


def _extract_items(payload: dict) -> tuple[list[dict], int, str]:
    """나라장터 응답 구조 방어적 파싱. (items, totalCount, resultCode)"""
    response = payload.get("response") or payload.get("nkoneps.com.response.ResponseError") or {}
    header = response.get("header") or {}
    code = str(header.get("resultCode") or "")
    message = str(header.get("resultMsg") or "")
    body = response.get("body") or {}
    total = int(body.get("totalCount") or 0)
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    items = [i for i in items if isinstance(i, dict)]
    if code and code not in ("00", "0", "INFO-00", "NORMAL_CODE"):
        raise RuntimeError(f"API 오류 {code}: {message}")
    return items, total, code


def collect_live(settings: Settings, days: Optional[int] = None) -> dict:
    days = days or settings.days
    if not settings.api_key:
        raise RuntimeError("DATA_GO_KR_KEY 환경변수가 비어 있습니다. "
                           "공공데이터포털에서 인증키를 발급해 설정하세요.")
    end = datetime.combine(settings.as_of, datetime.min.time(), tzinfo=KST) + timedelta(hours=23, minutes=59)
    start = datetime.combine(settings.as_of - timedelta(days=days - 1), datetime.min.time(), tzinfo=KST)

    # 나라장터 OpenAPI(BidPublicInfoService02)는 1회 조회 시 최대 7일(168시간) 간격만 허용한다.
    # 초과 시 '입력범위값 초과 에러(resultCode: 07)'가 발생하므로 7일 이하 슬라이스로 분할한다.
    slices: list[tuple[str, str]] = []
    cur_start = start
    while cur_start <= end:
        cur_end = min(cur_start + timedelta(days=6, hours=23, minutes=59), end)
        slices.append((cur_start.strftime("%Y%m%d%H%M"), cur_end.strftime("%Y%m%d%H%M")))
        cur_start = datetime.combine(cur_end.date() + timedelta(days=1), datetime.min.time(), tzinfo=KST)

    all_items: list[dict] = []
    ops_report: dict[str, dict] = {}
    errors: list[str] = []
    calls = 0
    seen: set[str] = set()
    target_count_per_op = settings.max_pages * settings.rows_per_page

    for op_name, op in API_OPERATIONS.items():
        received = 0
        pages = 0
        try:
            # 최신 공고부터 역순으로 수집 (필요시 이전 슬라이스로 확장)
            for bgn, fin in reversed(slices):
                for page_no in range(1, settings.max_pages + 1):
                    params = {
                        "serviceKey": settings.api_key,
                        "pageNo": str(page_no),
                        "numOfRows": str(settings.rows_per_page),
                        "type": "json",
                        "inqryDiv": "1",
                        "inqryBgnDt": bgn,
                        "inqryEndDt": fin,
                    }
                    url = f"{SOURCE['api_base']}{op['path']}?{urllib.parse.urlencode(params)}"
                    payload = _http_get_json(url, COLLECT["timeout_sec"],
                                             COLLECT["retries"], COLLECT["backoff_base"])
                    calls += 1
                    pages += 1
                    rows, total, _code = _extract_items(payload)
                    received += len(rows)
                    for row in rows:
                        norm = normalize_item(row, op["biz_type"])
                        if norm and norm["natural_key"] not in seen:
                            seen.add(norm["natural_key"])
                            all_items.append(norm)
                    if not rows or received >= total or received >= target_count_per_op:
                        break
                    time.sleep(COLLECT["request_pause_sec"])
                if received >= target_count_per_op:
                    break
        except Exception as exc:  # noqa: BLE001 - 한 업무구분 실패가 전체를 죽이지 않게
            errors.append(f"{op_name}: {exc}")
            log(f"{op_name} 수집 실패 — {exc}", "WARN")
        ops_report[op_name] = {"calls": pages, "received": received}
        time.sleep(COLLECT["request_pause_sec"])

    if not all_items and errors:
        raise RuntimeError("모든 오퍼레이션 수집 실패: " + " | ".join(errors))

    return {
        "mode": "live",
        "items": all_items,
        "operations": ops_report,
        "calls": calls,
        "errors": errors,
        "days": days,
        "window": {"start": start.strftime("%Y-%m-%d"), "end": settings.as_of.isoformat()},
    }


# --------------------------------------------------------------------------
# 진입점
# --------------------------------------------------------------------------
def write_raw_snapshot(raw_dir: Path, result: dict, settings: Settings) -> Path:
    raw_dir = Path(raw_dir) / "g2b"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{settings.as_of:%Y%m%d}-{result['mode']}.json"
    doc = {
        "collected_at": now_kst().isoformat(timespec="seconds"),
        "mode": result["mode"],
        "as_of": settings.as_of.isoformat(),
        "source": SOURCE["name"],
        "source_url": SOURCE["url"],
        "operations": result.get("operations", {}),
        "calls": result.get("calls", 0),
        "errors": result.get("errors", []),
        "count": len(result["items"]),
        "items": [item["payload"] for item in result["items"]],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def collect(settings: Settings) -> dict:
    mode = settings.resolved_mode()
    if mode == "live":
        log(f"실데이터 수집 시작 — 최근 {settings.days}일, 오퍼레이션 {len(API_OPERATIONS)}종", "STEP")
        result = collect_live(settings)
        log(f"실데이터 {len(result['items']):,}건 수집 (API 호출 {result['calls']}회)", "OK")
    else:
        log(f"시뮬레이션 모드 — API 키 없음, 최근 {settings.days}일 표본 생성", "STEP")
        result = simulate(settings)
        log(f"표본 {len(result['items']):,}건 생성 (실제 API와 동일 필드)", "OK")
    result["raw_path"] = str(write_raw_snapshot(settings.raw_dir, result, settings))
    return result


if __name__ == "__main__":  # 단독 실행 지원: python3 collector.py
    from config import default_settings
    s = default_settings()
    r = collect(s)
    print(json.dumps({"mode": r["mode"], "items": len(r["items"]),
                      "raw": r["raw_path"], "errors": r["errors"]},
                     ensure_ascii=False, indent=2))
