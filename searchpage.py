# -*- coding: utf-8 -*-
"""입찰동향 브리핑 — 실무자용 입찰공고 검색 페이지.

builder.py 가 매일 빌드할 때 이 모듈로 다음 두 파일을 만든다.

  site/tools/bid-search.html       검색 페이지(정적 HTML + 바닐라 JS, 외부 라이브러리 0)
  site/tools/bid-search-data.js    공고 데이터(경량 JSON, window.BID_SEARCH)

왜 별도 모듈인가
  주간 요약 리포트(/bids/*.html)는 "지역·분야별 통계"라서 실무자가 '양산시 공사 입찰'처럼
  특정 발주처·키워드로 찾아 들어오는 검색 의도와 어긋난다. 이 페이지는 pseo.db 에 쌓인
  개별 공고를 그대로 검색·필터하는 진입점이다. 서버가 없으므로 데이터를 JS 파일 하나로
  내려보내고 브라우저에서 즉시 필터링한다(설치·로그인·API 호출 없음).

수록 범위
  · 마감이 지나지 않은 공고(마감 2일 전까지) 전부
  · 최근 10일 안에 게시된 공고
  두 조건의 합집합을 최신순으로 정렬해 MAX_ITEMS 건까지 넣는다. 그래서 "마감임박"과
  "최신" 두 정렬이 모두 의미를 가진다.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from config import BASE_DIR, KST

TOOL_PATH = "tools/bid-search.html"
DATA_PATH = "tools/bid-search-data.js"

TOOL_TITLE = "나라장터 입찰공고 검색 — 지역·분야·마감임박 필터 | 입찰동향 브리핑"
TOOL_DESC = (
    "나라장터 입찰공고를 공고명·발주기관 키워드, 지역(17개 시·도), 분야(공사·용역·물품), "
    "마감임박·최신·기초금액 순으로 바로 검색합니다. 기초금액·추정가격·입찰마감일을 한눈에 보고 "
    "버튼 하나로 투찰금액 계산기까지 이어집니다. 설치·로그인 없이 무료."
)

# 화면에 노출할 분야 순서(설정의 CATEGORIES 라벨과 같다)
CATEGORY_ORDER = [
    "시설공사", "토목·조경", "전기·통신", "소방·설비", "청소·시설관리",
    "정보통신·SW", "교육·행사", "물품·구매", "연구·컨설팅", "의료·보건", "기타",
]

MAX_ITEMS = 2000          # 데이터 파일에 넣는 최대 건수
RECENT_DAYS = 7           # 최근 게시 공고 포함 범위
OPEN_GRACE_DAYS = 2       # 마감이 지난 공고도 이 정도까지는 남긴다(마감 직후 확인용)
STATIC_CARDS = 24         # 자바스크립트 없이도 보이는 기본 카드 수

G2B_LINK = "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={no}&bidPbancOrd={ord}"


# --------------------------------------------------------------------------
# 금액 표기 (calculator.fmt_money 와 화면·JS 표기를 일치시킨다)
# --------------------------------------------------------------------------
def fmt_money(value: Optional[int]) -> str:
    if not value:
        return "미공개"
    value = int(value)
    if value < 10_000:
        return f"{value:,}원"
    eok, rem = divmod(value, 100_000_000)
    man = rem // 10_000
    if rem % 10_000 >= 5_000:
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


def _epoch_ms(dt: Optional[datetime]) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return int(dt.timestamp() * 1000)


# --------------------------------------------------------------------------
# 데이터 수집 (pseo.db → 경량 레코드)
# --------------------------------------------------------------------------
def collect_notices(*, db_path: Path | str | None = None, as_of: date | None = None,
                    max_items: int = MAX_ITEMS) -> list[dict]:
    """pseo.db 의 원본 공고를 검색용 레코드로 정리한다. DB 가 없으면 빈 목록."""
    path = Path(db_path or (BASE_DIR / "pseo.db"))
    if not path.exists():
        return []
    today = as_of or datetime.now(KST).date()

    try:
        from db import connect, load_raw_items
        from calculator import build_notice
    except Exception:  # noqa: BLE001 - 빌드가 DB 사정으로 죽지 않게
        return []

    conn = connect(path)
    try:
        raw_items = load_raw_items(conn)
    finally:
        conn.close()

    # 공고번호+차수 기준 중복 제거 (정정 공고는 revision 이 큰 쪽을 남긴다)
    best: dict[tuple[str, str], dict] = {}
    for item in raw_items:
        if str(item.get("_simulated", "")).lower() in ("true", "1", "yes"):
            continue
        rec = build_notice(item, today)
        if not rec:
            continue
        key = (rec["no"], rec["ord"])
        prev = best.get(key)
        if prev is None or rec["revision"] >= prev["revision"]:
            best[key] = rec

    recent_from = today - timedelta(days=RECENT_DAYS)
    open_from = today - timedelta(days=OPEN_GRACE_DAYS)
    now_ms = _epoch_ms(datetime.now(KST))
    picked: list[dict] = []
    for rec in best.values():
        close_date = rec.get("close_date")
        still_listed = bool(close_date and close_date >= open_from)
        is_recent = rec["posted_date"] >= recent_from
        if not (still_listed or is_recent):
            continue
        est = int(rec.get("estimate") or 0)
        base = int(rec.get("budget") or 0)                 # 배정예산(부가세 포함) ≈ 기초금액
        if not base and est:
            base = int(round(est * 1.1))
        picked.append({
            "name": rec["name"],
            "org": rec["org"],
            "region": rec["region"],
            "cat": rec["category"],
            "biz": rec["biz_type"],
            "base": int(base or 0),
            "est": est,
            "close": _epoch_ms(rec.get("close")),
            "posted": _epoch_ms(rec.get("posted")),
            "no": rec["no"],
            "ord": rec["ord"],
            "floor": rec.get("floor_rate") or "",
            "method": rec.get("bid_method") or "",
            "_open": _epoch_ms(rec.get("close")) > now_ms,
        })

    # 마감 전 공고를 먼저, 그다음 최근 게시 순으로 정렬해 상한까지 자른다.
    picked.sort(key=lambda r: (not r["_open"], -r["posted"]))
    picked = picked[:max_items]
    for rec in picked:
        rec.pop("_open", None)
    return picked


# --------------------------------------------------------------------------
# 표시 도우미
# --------------------------------------------------------------------------
_REGION_ORDER = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
                 "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]


def _dday_label(close_ms: int, now_ms: int) -> tuple[str, str]:
    """마감까지 남은 날 → (표시, css 클래스)."""
    if not close_ms:
        return "마감 미정", "unknown"
    day_ms = 86_400_000
    kst_shift = 9 * 3_600_000
    close_day = (close_ms + kst_shift) // day_ms
    now_day = (now_ms + kst_shift) // day_ms
    diff = close_day - now_day
    if diff < 0:
        return "마감", "closed"
    if diff == 0:
        return "오늘 마감", "urgent"
    if diff <= 2:
        return f"D-{diff}", "urgent"
    if diff <= 6:
        return f"D-{diff}", "soon"
    return f"D-{diff}", ""


def _close_label(close_ms: int) -> str:
    if not close_ms:
        return "마감일 미공개"
    dt = datetime.fromtimestamp(close_ms / 1000, KST)
    dow = "월화수목금토일"[dt.weekday()]
    return f"{dt.month}월 {dt.day}일 ({dow}) {dt:%H:%M}"


def _calc_href(rec: dict) -> str:
    query = f"bid-calculator.html?base={rec['base']}&from=search"
    if rec.get("floor"):
        query += f"&floor={rec['floor']}"
    if rec["name"]:
        query += "&name=" + quote(rec["name"][:44], safe="")
    return query


def _card_html(rec: dict, now_ms: int) -> str:
    dday, tone = _dday_label(rec["close"], now_ms)
    g2b = G2B_LINK.format(no=rec["no"], ord=rec["ord"] or "000")
    cat = "" if rec["cat"] in ("기타", "") else (
        f'<span class="flag cat">{html.escape(rec["cat"])}</span>')
    return (
        '<article class="bid-card">'
        '<div class="bid-flags">'
        f'<span class="flag dday {tone}">{html.escape(dday)}</span>'
        f'<span class="flag biz">{html.escape(rec["biz"] or "구분없음")}</span>'
        f'{cat}'
        f'<span class="flag region">{html.escape(rec["region"])}</span>'
        '</div>'
        f'<h3 class="bid-title"><a href="{html.escape(g2b)}" target="_blank" '
        f'rel="noopener">{html.escape(rec["name"])}</a></h3>'
        f'<p class="bid-org">{html.escape(rec["org"])}</p>'
        '<div class="bid-figures">'
        f'<p><span>기초금액</span><b>{fmt_money(rec["base"])}</b></p>'
        f'<p><span>추정가격</span><b>{fmt_money(rec["est"])}</b></p>'
        '</div>'
        f'<p class="bid-close">입찰마감 <b>{html.escape(_close_label(rec["close"]))}</b></p>'
        '<div class="bid-actions">'
        f'<a class="btn btn-bid" href="{html.escape(_calc_href(rec), quote=True)}">투찰금액 계산</a>'
        f'<a class="btn btn-ghost" href="{html.escape(g2b)}" target="_blank" rel="noopener">공고 원문</a>'
        '</div>'
        '</article>'
    )


# --------------------------------------------------------------------------
# 데이터 파일
# --------------------------------------------------------------------------
def data_js(notices: list[dict], built_at: str) -> str:
    rows = [[n["name"], n["org"], n["region"], n["cat"], n["biz"], n["base"], n["est"],
             n["close"], n["posted"], n["no"], n["ord"], n["floor"]]
            for n in notices]
    doc = {
        "generated": built_at,
        "count": len(rows),
        "regions": _REGION_ORDER,
        "categories": CATEGORY_ORDER,
        "items": rows,
    }
    return ("/* 입찰동향 브리핑 — 입찰공고 검색 데이터 (builder.py 자동 생성, 직접 수정 금지) */\n"
            "window.BID_SEARCH=" + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + ";\n")


# --------------------------------------------------------------------------
# 구조화 데이터
# --------------------------------------------------------------------------
def ld_json(site_url: str, built_at: str, notices: list[dict], now_ms: int) -> str:
    url = f"{site_url.rstrip('/')}/{TOOL_PATH}"
    home = f"{site_url.rstrip('/')}/"
    top = sorted([n for n in notices if n["close"] > now_ms], key=lambda n: n["close"])[:10]
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebApplication",
                "@id": url + "#app",
                "name": "나라장터 입찰공고 검색",
                "url": url,
                "applicationCategory": "BusinessApplication",
                "applicationSubCategory": "입찰공고 검색",
                "operatingSystem": "Web",
                "browserRequirements": "JavaScript",
                "inLanguage": "ko-KR",
                "isAccessibleForFree": True,
                "description": TOOL_DESC,
                "featureList": [
                    "공고명·발주기관 키워드 검색",
                    "17개 시·도 지역 필터와 공사·용역·물품 구분 필터",
                    "마감임박·최신·기초금액 순 정렬",
                    "공고 기초금액을 투찰금액 계산기로 바로 전달",
                ],
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "KRW"},
                "dateModified": built_at,
                "publisher": {"@type": "Organization", "name": "입찰동향 브리핑", "url": home},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "홈", "item": home},
                    {"@type": "ListItem", "position": 2, "name": "입찰공고 검색", "item": url},
                ],
            },
        ],
    }
    if top:
        graph["@graph"].append({
            "@type": "ItemList",
            "name": "마감 임박 나라장터 입찰공고",
            "numberOfItems": len(top),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": n["name"]}
                for i, n in enumerate(top)
            ],
        })
    return ('<script type="application/ld+json">'
            + json.dumps(graph, ensure_ascii=False) + "</script>")


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------
def body_html(root: str = "../", notices: list[dict] | None = None,
              built_at: str = "") -> str:
    notices = notices or []
    now_ms = _epoch_ms(datetime.now(KST))
    open_now = [n for n in notices if n["close"] > now_ms]
    soon = [n for n in open_now if n["close"] <= now_ms + 3 * 86_400_000]
    open_now.sort(key=lambda n: n["close"])
    static = open_now[:STATIC_CARDS]

    region_opts = "".join(
        f'<option value="{r}">{r}</option>' for r in _REGION_ORDER)
    cat_chips = "".join(
        f'<button type="button" class="fcat" data-cat="{html.escape(c)}">{html.escape(c)}</button>'
        for c in CATEGORY_ORDER)
    static_cards = "".join(_card_html(n, now_ms) for n in static)

    generated = ""
    if built_at:
        try:
            generated = datetime.fromisoformat(built_at).strftime("%Y년 %-m월 %-d일 %H:%M")
        except ValueError:
            generated = built_at

    return f"""<main id="main">
  <div class="wrap">
    <nav class="crumbs" aria-label="위치">
      <a href="{root}index.html">홈</a><span aria-hidden="true">/</span>
      <span aria-current="page">입찰공고 검색</span>
    </nav>
    <header class="article-head tool-head search-head">
      <span class="kicker">실무자용 공고 검색 · 설치 없음</span>
      <h1>나라장터 입찰공고 검색</h1>
      <p class="tool-lead">공고명·발주기관 키워드, <b>지역 17개 시·도</b>, <b>분야(공사·용역·물품)</b>,
      <b>마감임박·최신·기초금액</b> 순으로 지금 열려 있는 나라장터 공고를 바로 찾습니다.
      카드에서 기초금액·추정가격·입찰마감일을 확인하고 <b>버튼 한 번으로 투찰금액 계산기</b>에
      금액을 넘겨 계산하세요. 검색은 방문자 브라우저에서만 실행됩니다.</p>
      <ul class="search-stats">
        <li><span>수록 공고</span><b>{len(notices):,}건</b></li>
        <li><span>마감 전</span><b>{len(open_now):,}건</b></li>
        <li><span>3일 내 마감</span><b>{len(soon):,}건</b></li>
        <li><span>데이터 갱신</span><b>{html.escape(generated) or "자동"}</b></li>
      </ul>
    </header>
  </div>

  <section class="section search-section" id="search">
    <div class="wrap">
      <form class="filter-card" id="bidFilter" role="search" onsubmit="return false">
        <div class="filter-row filter-main">
          <label class="field search-field" for="q">
            <span class="label">키워드</span>
            <input id="q" type="search" inputmode="search" autocomplete="off"
                   placeholder="예: 양산시, 창호 공사, 급식기자재, 도로포장">
          </label>
          <label class="field" for="region">
            <span class="label">지역</span>
            <select id="region"><option value="">전국</option>{region_opts}</select>
          </label>
          <label class="field" for="biz">
            <span class="label">업무구분</span>
            <select id="biz">
              <option value="">전체</option>
              <option value="공사">공사</option>
              <option value="용역">용역</option>
              <option value="물품">물품</option>
            </select>
          </label>
        </div>
        <div class="filter-row">
          <span class="label" id="catLabel">분야</span>
          <div class="fcats" role="group" aria-labelledby="catLabel">
            <button type="button" class="fcat on" data-cat="" aria-pressed="true">전체</button>
            {cat_chips}
          </div>
        </div>
        <div class="filter-row filter-foot">
          <div class="sorts" role="group" aria-label="정렬">
            <button type="button" class="sort on" data-sort="close" aria-pressed="true">마감임박순</button>
            <button type="button" class="sort" data-sort="latest" aria-pressed="false">최신순</button>
            <button type="button" class="sort" data-sort="budget" aria-pressed="false">기초금액순</button>
          </div>
          <label class="toggle"><input type="checkbox" id="hideClosed" checked>
            마감 지난 공고 숨기기</label>
          <button type="button" class="reset" id="resetFilter">조건 초기화</button>
        </div>
      </form>

      <div class="result-head">
        <p class="result-count" id="resultCount" aria-live="polite">공고를 불러오는 중…</p>
        <p class="result-sum" id="resultSum"></p>
      </div>

      <div class="bid-grid" id="resultList">{static_cards}</div>
      <p class="result-empty" id="resultEmpty" hidden>조건에 맞는 공고가 없습니다.
        지역이나 키워드를 줄여 다시 찾아보세요.</p>
      <div class="more-wrap"><button type="button" class="btn btn-more" id="moreBtn" hidden>더 보기</button></div>
      <p class="grid-note" id="staticNote">공고 데이터는 매일 아침 자동으로 갱신됩니다.
        금액은 공고문 표기 기준이며, 투찰 전 나라장터 원문을 다시 확인하세요.</p>
    </div>
  </section>

  <section class="section section-alt" id="guide">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>이렇게 찾으면 빠릅니다</h2>
          <p class="section-sub">주간 요약 리포트와 달리, 이 페이지는 공고 한 건 단위로 찾습니다.</p>
        </div>
      </div>
      <div class="guide-grid">
        <div class="guide-item">
          <h3>지역 + 업무구분으로 좁히기</h3>
          <p>예를 들어 <b>경남</b> + <b>공사</b>를 고르면 경남 소재 발주기관의 공사 공고만 남습니다.
          키워드에 <b>양산</b>을 더하면 양산시·양산교육지원청 등 특정 발주처 공고를 바로 볼 수 있습니다.</p>
        </div>
        <div class="guide-item">
          <h3>마감임박순으로 오늘 할 일 확인</h3>
          <p>기본 정렬은 마감이 가까운 순입니다. 카드 왼쪽 위 <b>D-day</b>가 2일 이내면 붉게,
          6일 이내면 파랗게 표시하니, 오늘 투찰서를 넣어야 할 공고부터 처리할 수 있습니다.</p>
        </div>
        <div class="guide-item">
          <h3>기초금액에서 투찰금액까지 한 번에</h3>
          <p>카드의 <b>투찰금액 계산</b>을 누르면 공고 기초금액이 계산기에 자동으로 들어갑니다.
          사정률과 낙찰하한율만 확인하면 예정가격·투찰금액·입찰보증금까지 바로 나옵니다.</p>
        </div>
      </div>
      <div class="source-box">
        <p class="source-title">데이터 기준</p>
        <ul class="source-list">
          <li>출처: 공공데이터포털 조달청 나라장터 입찰공고정보서비스(공사·용역·물품) — 매일 1회 자동 수집</li>
          <li>기초금액은 공고의 배정예산(부가세 포함) 표기값을 쓰고, 없으면 추정가격에 부가세를 더해 계산합니다.</li>
          <li>지역은 발주기관·수요기관명 기준 분류이며, 실제 참가자격의 지역 제한과 다를 수 있습니다.</li>
          <li class="warn">입찰 판단의 최종 근거는 나라장터 원문 공고문입니다. 이 페이지는 탐색을 돕는 도구입니다.</li>
        </ul>
      </div>
      <div class="rel-grid">
        <a class="rel-card" href="{root}index.html#highlight">이번 주 전국 입찰 동향 <i>→</i></a>
        <a class="rel-card" href="{root}index.html#regions">지역별 브리핑 <i>→</i></a>
        <a class="rel-card" href="{root}{TOOL_PATH.replace('bid-search.html', 'bid-calculator.html')}">투찰금액·보증금 계산기 <i>→</i></a>
      </div>
    </div>
  </section>
</main>
<noscript><div class="wrap"><p class="calc-noscript">검색과 필터는 브라우저에서 실행됩니다.
JavaScript를 켜면 전체 공고를 조건별로 찾을 수 있습니다. 위 목록은 마감이 가까운 공고 예시입니다.</p></div></noscript>
<script src="{DATA_PATH.rsplit('/', 1)[-1]}"></script>
<script>{SEARCH_JS}</script>
"""


# --------------------------------------------------------------------------
# 스타일
# --------------------------------------------------------------------------
SEARCH_CSS = r"""
/* ==========================================================================
   입찰공고 검색 페이지 (builder.py 가 생성)
   CSS 변수만 사용하므로 다크 모드도 그대로 대응된다.
   ========================================================================== */
.search-head .search-stats{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 0;padding:0;list-style:none}
.search-stats li{display:flex;align-items:baseline;gap:7px;background:var(--surface);
  border:1px solid var(--line);border-radius:999px;padding:7px 14px;box-shadow:var(--shadow)}
.search-stats span{font-size:12px;font-weight:650;color:var(--muted)}
.search-stats b{font-size:13.5px;font-weight:750;color:var(--ink);font-variant-numeric:tabular-nums}

body.tool .search-section{padding-top:22px}
.filter-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px;box-shadow:var(--shadow)}
.filter-row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px;margin-top:12px}
.filter-row:first-child{margin-top:0}
.filter-main .search-field{flex:1 1 260px}
.filter-main .field{flex:0 1 132px}
.filter-card .field{display:flex;flex-direction:column;gap:6px;margin:0}
.filter-card .label{font-size:12px;font-weight:750;color:var(--ink-2);letter-spacing:.01em}
.filter-card input[type=search],.filter-card select{width:100%;min-width:0;font:inherit;font-size:14.5px;
  color:var(--ink);background:var(--surface-2);border:1px solid var(--line);border-radius:11px;
  padding:10px 12px;outline:none;transition:border-color .15s,box-shadow .15s}
.filter-card input[type=search]:focus,.filter-card select:focus{border-color:var(--brand-2);
  box-shadow:0 0 0 3px rgba(47,98,201,.14)}
.filter-card input[type=search]::-webkit-search-cancel-button{cursor:pointer}
.fcats{display:flex;flex-wrap:wrap;gap:6px;flex:1 1 auto}
.fcat{background:var(--surface-2);border:1px solid var(--line);border-radius:999px;
  padding:6px 12px;font-size:12.5px;font-weight:650;color:var(--ink-2);cursor:pointer;
  transition:border-color .15s,background .15s,color .15s}
.fcat:hover{border-color:#b9cdf2;color:var(--brand);background:#f8fbff}
.fcat.on{border-color:var(--brand);background:var(--brand-weak);color:var(--brand)}
.filter-foot{align-items:center;justify-content:space-between;gap:10px;padding-top:12px;
  border-top:1px solid var(--line);margin-top:14px}
.sorts{display:flex;gap:4px;background:var(--surface-2);border:1px solid var(--line);
  border-radius:11px;padding:3px}
.sort{background:transparent;border:0;border-radius:8px;padding:7px 12px;font-size:12.5px;
  font-weight:700;color:var(--ink-2);cursor:pointer}
.sort.on{background:var(--surface);color:var(--brand);box-shadow:0 1px 3px rgba(16,24,40,.12)}
.toggle{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;font-weight:650;
  color:var(--ink-2);cursor:pointer}
.toggle input{width:16px;height:16px;accent-color:var(--brand)}
.reset{background:transparent;border:0;color:var(--brand-2);font-size:12.5px;font-weight:700;
  cursor:pointer;text-decoration:underline;text-underline-offset:3px}

.result-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;
  gap:6px;margin:20px 2px 12px}
.result-count{margin:0;font-size:14.5px;font-weight:750;color:var(--ink)}
.result-count b{color:var(--brand);font-variant-numeric:tabular-nums}
.result-sum{margin:0;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}

.bid-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.bid-card{display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);
  border-radius:14px;padding:15px 16px;box-shadow:var(--shadow);min-width:0}
.bid-flags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:9px}
.flag{font-size:11px;font-weight:750;border-radius:6px;padding:2px 7px;letter-spacing:-.01em;
  background:var(--surface-2);color:var(--ink-2);border:1px solid var(--line);white-space:nowrap}
.flag.dday{background:#eef1f6;color:var(--ink-2);border-color:transparent}
.flag.dday.soon{background:#e8f1ff;color:#1c56bd}
.flag.dday.urgent{background:#ffeceb;color:#c92a2a}
.flag.dday.closed{background:#f1f3f6;color:#8b93a1}
.flag.biz{background:var(--brand-weak);color:var(--brand);border-color:transparent}
.flag.region{background:#f4f6fa;color:#4b5563}
.bid-title{margin:0 0 5px;font-size:15.5px;font-weight:750;line-height:1.45;letter-spacing:-.02em;
  word-break:keep-all;overflow-wrap:anywhere}
.bid-title a{color:var(--ink)}
.bid-title a:hover{color:var(--brand);text-decoration:none}
.bid-org{margin:0 0 11px;font-size:12.5px;color:var(--muted);word-break:keep-all}
.bid-figures{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:var(--surface-2);
  border-radius:10px;padding:10px 12px;margin-bottom:10px}
.bid-figures p{margin:0;display:flex;flex-direction:column;gap:2px;min-width:0}
.bid-figures span{font-size:11px;font-weight:650;color:var(--muted)}
.bid-figures b{font-size:14px;font-weight:750;color:var(--ink);font-variant-numeric:tabular-nums;
  letter-spacing:-.02em;word-break:keep-all}
.bid-close{margin:0 0 12px;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.bid-close b{color:var(--ink-2);font-weight:700}
.bid-actions{display:flex;gap:7px;margin-top:auto}
.bid-actions .btn{flex:1 1 0;justify-content:center;font-size:12.5px;padding:9px 10px;min-width:0}
.btn-bid{background:var(--brand);color:#fff;border-color:var(--brand)}
.btn-bid:hover{background:#0f3a86;color:#fff;text-decoration:none}
.result-empty{margin:18px 2px;padding:22px;text-align:center;color:var(--muted);font-size:14px;
  background:var(--surface);border:1px dashed var(--line);border-radius:12px}
.more-wrap{display:flex;justify-content:center;margin-top:16px}
.btn-more{padding:11px 26px;font-size:13.5px}

.guide-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.guide-item{background:var(--surface);border:1px solid var(--line);border-radius:13px;
  padding:16px 17px;box-shadow:var(--shadow)}
.guide-item h3{margin:0 0 7px;font-size:14.5px;font-weight:750;letter-spacing:-.02em}
.guide-item p{margin:0;font-size:13.5px;color:var(--ink-2);line-height:1.7}
.guide-item b{color:var(--ink)}

@media (max-width:860px){
  .bid-grid{grid-template-columns:1fr}
}
@media (max-width:720px){
  .filter-main .field{flex:1 1 120px}
  .filter-main .search-field{flex:1 1 100%}
  .filter-foot{gap:10px}
  .sorts{width:100%}
  .sort{flex:1 1 0;text-align:center}
  .bid-card{padding:14px}
  .bid-figures b{font-size:13.5px}
  .search-head .search-stats{gap:6px}
  .search-stats li{padding:6px 11px}
}
@media (max-width:420px){
  .bid-figures{grid-template-columns:1fr}
  .filter-card{padding:14px}
}
@media (prefers-color-scheme:dark){
  .fcat:hover,.sort.on{background:#1a2333}
  .sort.on{color:#9cc0ff}
  .flag.region{background:#1b2230;color:#c3cad6}
  .flag.dday.soon{background:#12233d;color:#9cc0ff}
  .flag.dday.urgent{background:#3a1c1c;color:#ff8787}
  .flag.dday.closed{background:#1b2230;color:#8b93a1}
  .btn-bid{background:#2f62c9;border-color:#2f62c9}
  .btn-bid:hover{background:#3a72da}
}
@media print{
  .filter-card,.more-wrap,.result-head{display:none!important}
  .bid-grid{grid-template-columns:1fr 1fr;gap:8px}
}
"""


# --------------------------------------------------------------------------
# 스크립트
# --------------------------------------------------------------------------
SEARCH_JS = r"""
(function () {
  "use strict";
  var DATA = window.BID_SEARCH;
  var listEl = document.getElementById("resultList");
  if (!listEl || !DATA || !DATA.items) { return; }

  var nf = new Intl.NumberFormat("ko-KR");
  var DAY = 86400000, KST = 9 * 3600000;
  var G2B_PREFIX = "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=";
  var DOW = ["일", "월", "화", "수", "목", "금", "토"];
  var PAGE = 24;

  var items = DATA.items.map(function (a) {
    return { name: a[0], org: a[1], region: a[2], cat: a[3], biz: a[4], base: a[5] || 0,
             est: a[6] || 0, close: a[7] || 0, posted: a[8] || 0,
             no: a[9], ord: a[10], floor: a[11] };
  });

  var state = { q: "", region: "", biz: "", cat: "", sort: "close", hideClosed: true, limit: PAGE };
  var now = Date.now();

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtMoney(v) {
    v = Math.round(v || 0);
    if (!(v > 0)) { return "미공개"; }
    if (v < 10000) { return nf.format(v) + "원"; }
    var eok = Math.floor(v / 100000000), rem = v % 100000000, man = Math.floor(rem / 10000);
    if (rem % 10000 >= 5000) { man += 1; }
    if (man >= 10000) { eok += 1; man -= 10000; }
    if (eok && man) { return nf.format(eok) + "억 " + nf.format(man) + "만 원"; }
    if (eok) { return nf.format(eok) + "억 원"; }
    if (man) { return nf.format(man) + "만 원"; }
    return nf.format(v) + "원";
  }
  function dayNo(ms) { return Math.floor((ms + KST) / DAY); }
  function dday(close) {
    if (!close) { return ["마감 미정", "unknown"]; }
    var d = dayNo(close) - dayNo(now);
    if (d < 0) { return ["마감", "closed"]; }
    if (d === 0) { return ["오늘 마감", "urgent"]; }
    if (d <= 2) { return ["D-" + d, "urgent"]; }
    if (d <= 6) { return ["D-" + d, "soon"]; }
    return ["D-" + d, ""];
  }
  function closeLabel(close) {
    if (!close) { return "마감일 미공개"; }
    var d = new Date(close + KST);
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mm = String(d.getUTCMinutes()).padStart(2, "0");
    return (d.getUTCMonth() + 1) + "월 " + d.getUTCDate() + "일 (" + DOW[d.getUTCDay()] + ") " + hh + ":" + mm;
  }
  function calcHref(it) {
    var q = "bid-calculator.html?base=" + (it.base || 0) + "&from=search";
    if (it.floor) { q += "&floor=" + encodeURIComponent(it.floor); }
    if (it.name) { q += "&name=" + encodeURIComponent(it.name.slice(0, 44)); }
    return q;
  }
  function card(it) {
    var dd = dday(it.close);
    var g2b = G2B_PREFIX + encodeURIComponent(it.no) + "&bidPbancOrd=" + encodeURIComponent(it.ord || "000");
    var cat = (it.cat && it.cat !== "기타")
      ? '<span class="flag cat">' + esc(it.cat) + "</span>" : "";
    return '<article class="bid-card">' +
      '<div class="bid-flags">' +
        '<span class="flag dday ' + dd[1] + '">' + esc(dd[0]) + "</span>" +
        '<span class="flag biz">' + esc(it.biz || "구분없음") + "</span>" + cat +
        '<span class="flag region">' + esc(it.region) + "</span>" +
      "</div>" +
      '<h3 class="bid-title"><a href="' + esc(g2b) + '" target="_blank" rel="noopener">' +
        esc(it.name) + "</a></h3>" +
      '<p class="bid-org">' + esc(it.org) + "</p>" +
      '<div class="bid-figures">' +
        "<p><span>기초금액</span><b>" + fmtMoney(it.base) + "</b></p>" +
        "<p><span>추정가격</span><b>" + fmtMoney(it.est) + "</b></p>" +
      "</div>" +
      '<p class="bid-close">입찰마감 <b>' + esc(closeLabel(it.close)) + "</b></p>" +
      '<div class="bid-actions">' +
        '<a class="btn btn-bid" href="' + esc(calcHref(it)) + '">투찰금액 계산</a>' +
        '<a class="btn btn-ghost" href="' + esc(g2b) + '" target="_blank" rel="noopener">공고 원문</a>' +
      "</div></article>";
  }

  var qEl = document.getElementById("q");
  var regionEl = document.getElementById("region");
  var bizEl = document.getElementById("biz");
  var hideEl = document.getElementById("hideClosed");
  var countEl = document.getElementById("resultCount");
  var sumEl = document.getElementById("resultSum");
  var emptyEl = document.getElementById("resultEmpty");
  var moreEl = document.getElementById("moreBtn");

  function terms(q) {
    return q.toLowerCase().split(/\s+/).filter(function (t) { return t.length > 0; });
  }

  function filtered() {
    var ts = terms(state.q);
    var out = items.filter(function (it) {
      if (state.region && it.region !== state.region) { return false; }
      if (state.biz && it.biz !== state.biz) { return false; }
      if (state.cat && it.cat !== state.cat) { return false; }
      if (state.hideClosed && (!it.close || it.close < now)) { return false; }
      if (ts.length) {
        var hay = (it.name + " " + it.org).toLowerCase();
        for (var i = 0; i < ts.length; i++) {
          if (hay.indexOf(ts[i]) < 0) { return false; }
        }
      }
      return true;
    });
    out.sort(function (a, b) {
      if (state.sort === "latest") { return (b.posted - a.posted) || (a.close - b.close); }
      if (state.sort === "budget") { return (b.base - a.base) || (a.close - b.close); }
      // 마감임박순: 열려 있는 공고를 마감 가까운 순으로, 마감 지난 공고는 뒤로
      var ac = a.close || Infinity, bc = b.close || Infinity;
      var aOpen = ac >= now, bOpen = bc >= now;
      if (aOpen !== bOpen) { return aOpen ? -1 : 1; }
      return aOpen ? (ac - bc) : (bc - ac);
    });
    return out;
  }

  function render() {
    var rows = filtered();
    var show = rows.slice(0, state.limit);
    listEl.innerHTML = show.map(card).join("");
    var sum = 0;
    for (var i = 0; i < rows.length; i++) { sum += rows[i].base || 0; }
    countEl.innerHTML = "조건에 맞는 공고 <b>" + nf.format(rows.length) + "</b>건" +
      (rows.length > show.length ? " · " + nf.format(show.length) + "건 표시 중" : "");
    sumEl.textContent = rows.length ? "기초금액 합계 약 " + fmtMoney(sum) : "";
    emptyEl.hidden = rows.length !== 0;
    moreEl.hidden = rows.length <= show.length;
  }

  function syncUrl() {
    if (!window.history || !history.replaceState) { return; }
    var p = [];
    if (state.q) { p.push("q=" + encodeURIComponent(state.q)); }
    if (state.region) { p.push("region=" + encodeURIComponent(state.region)); }
    if (state.biz) { p.push("biz=" + encodeURIComponent(state.biz)); }
    if (state.cat) { p.push("cat=" + encodeURIComponent(state.cat)); }
    if (state.sort !== "close") { p.push("sort=" + state.sort); }
    if (!state.hideClosed) { p.push("closed=1"); }
    var url = location.pathname + (p.length ? "?" + p.join("&") : "");
    history.replaceState(null, "", url);
  }

  function readUrl() {
    var p = new URLSearchParams(location.search);
    if (p.get("q")) { state.q = p.get("q"); qEl.value = state.q; }
    if (p.get("region") && DATA.regions.indexOf(p.get("region")) >= 0) {
      state.region = p.get("region"); regionEl.value = state.region;
    }
    if (p.get("biz") && ["공사", "용역", "물품"].indexOf(p.get("biz")) >= 0) {
      state.biz = p.get("biz"); bizEl.value = state.biz;
    }
    if (p.get("cat") && DATA.categories.indexOf(p.get("cat")) >= 0) {
      state.cat = p.get("cat");
    }
    if (["close", "latest", "budget"].indexOf(p.get("sort")) >= 0) { state.sort = p.get("sort"); }
    if (p.get("closed") === "1") { state.hideClosed = false; hideEl.checked = false; }
    document.querySelectorAll(".fcat").forEach(function (b) {
      var on = (b.getAttribute("data-cat") || "") === state.cat;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll(".sort").forEach(function (b) {
      var on = b.getAttribute("data-sort") === state.sort;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  var timer = null;
  qEl.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      state.q = qEl.value.trim(); state.limit = PAGE; render(); syncUrl();
    }, 140);
  });
  regionEl.addEventListener("change", function () {
    state.region = regionEl.value; state.limit = PAGE; render(); syncUrl();
  });
  bizEl.addEventListener("change", function () {
    state.biz = bizEl.value; state.limit = PAGE; render(); syncUrl();
  });
  hideEl.addEventListener("change", function () {
    state.hideClosed = hideEl.checked; state.limit = PAGE; render(); syncUrl();
  });
  document.querySelectorAll(".fcat").forEach(function (b) {
    b.addEventListener("click", function () {
      state.cat = b.getAttribute("data-cat") || "";
      document.querySelectorAll(".fcat").forEach(function (o) {
        var on = o === b;
        o.classList.toggle("on", on);
        o.setAttribute("aria-pressed", on ? "true" : "false");
      });
      state.limit = PAGE; render(); syncUrl();
    });
  });
  document.querySelectorAll(".sort").forEach(function (b) {
    b.addEventListener("click", function () {
      state.sort = b.getAttribute("data-sort");
      document.querySelectorAll(".sort").forEach(function (o) {
        var on = o === b;
        o.classList.toggle("on", on);
        o.setAttribute("aria-pressed", on ? "true" : "false");
      });
      state.limit = PAGE; render(); syncUrl();
    });
  });
  moreEl.addEventListener("click", function () {
    state.limit += PAGE; render();
  });
  document.getElementById("resetFilter").addEventListener("click", function () {
    state = { q: "", region: "", biz: "", cat: "", sort: "close", hideClosed: true, limit: PAGE };
    qEl.value = ""; regionEl.value = ""; bizEl.value = ""; hideEl.checked = true;
    document.querySelectorAll(".fcat").forEach(function (o) {
      var on = !o.getAttribute("data-cat");
      o.classList.toggle("on", on);
      o.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll(".sort").forEach(function (o) {
      var on = o.getAttribute("data-sort") === "close";
      o.classList.toggle("on", on);
      o.setAttribute("aria-pressed", on ? "true" : "false");
    });
    render(); syncUrl();
  });

  readUrl();
  render();
  if (typeof window.gtag === "function") {
    window.gtag("event", "search_page_view", {
      notice_count: items.length, has_filter: Boolean(state.q || state.region || state.biz || state.cat)
    });
  }
})();
"""
