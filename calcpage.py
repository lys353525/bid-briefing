# -*- coding: utf-8 -*-
"""입찰동향 브리핑 — 실무 도구 페이지(투찰금액·낙찰하한율·보증금 계산기).

builder.py 가 매일 빌드할 때 이 모듈로 `site/tools/bid-calculator.html` 을 만든다.
(수동 HTML 을 넣으면 다음 빌드에서 지워지므로 반드시 빌더를 통해 생성한다.)

구성
  CALC_CSS   : style.css 뒤에 붙는 도구 전용 스타일(변수만 사용 → 다크모드 자동 대응)
  CALC_JS    : 계산 로직(순수 바닐라 JS, 외부 라이브러리 0)
  body_html(): <main> 안쪽 HTML + 스크립트
  ld_json()  : WebApplication + BreadcrumbList + FAQPage 구조화 데이터

숫자 기준(2026-11 확인)
  · 예정가격 = 기초금액 × 사정률
  · 투찰금액(낙찰하한가) = 예정가격 × 낙찰하한율
  · 입찰보증금 = 입찰금액의 5% 이상 (국가계약법 시행령 제37조①)
  · 계약보증금 = 계약금액의 10% 이상 (같은 법 시행령 제50조①,
    지방계약법 시행령 제51조⑤ 물품·용역 10% 이상)
"""
from __future__ import annotations

import json

from config import SITE_NAME

TOOL_PATH = "tools/bid-calculator.html"
TOOL_TITLE = "나라장터 투찰금액·낙찰하한율·입찰보증금 계산기 — 입찰동향 브리핑"
TOOL_DESC = (
    "기초금액과 사정률·낙찰하한율을 넣으면 예정가격·투찰금액(낙찰하한가)과 기초금액 대비 "
    "투찰률을 바로 계산합니다. 입찰보증금 5%·계약보증금 10% 계산과 지급각서 대체 여부까지 "
    "한 화면에서. 설치·로그인 없이 무료."
)

# --------------------------------------------------------------------------
# 자주 묻는 질문 (화면용 HTML + 구조화 데이터용 평문)
# --------------------------------------------------------------------------
FAQ_ITEMS: list[tuple[str, str, str]] = [
    (
        "투찰금액은 어떻게 계산하나요?",
        "<p>예정가격은 <b>기초금액 × 사정률</b>, 투찰금액(낙찰하한가)은 <b>예정가격 × 낙찰하한율</b>입니다. "
        "예를 들어 기초금액 1억 원·사정률 100%·낙찰하한율 89.745%라면 예정가격 1억 원, "
        "투찰금액 89,745,000원이 됩니다. 이 금액보다 낮게 쓰면 적격심사에서 입찰가격 평점을 "
        "받지 못해 사실상 낙찰이 어렵습니다. 반대로 하한선을 넘겨 쓴 업체 중 가장 낮은 금액이 "
        "낙찰로 이어지므로, 실무에서는 하한가 바로 위 몇 천 원 차이에서 승부가 갈립니다.</p>",
        "예정가격은 기초금액 곱하기 사정률, 투찰금액(낙찰하한가)은 예정가격 곱하기 낙찰하한율입니다. "
        "기초금액 1억 원, 사정률 100%, 낙찰하한율 89.745%이면 예정가격 1억 원, 투찰금액 "
        "89,745,000원입니다. 이보다 낮게 쓰면 적격심사에서 입찰가격 평점을 받지 못해 낙찰이 어렵습니다.",
    ),
    (
        "사정률은 어떻게 정해지나요? 미리 알 수 있나요?",
        "<p>사정률은 복수예가를 추첨할 때 기초금액에 곱하는 비율로, 입찰 전에는 확정되지 않습니다. "
        "예가변동폭은 발주기관마다 달라 조달청은 보통 ±2%, 지방자치단체는 ±3% 수준입니다. "
        "그래서 사정률 하나를 찍기보다 <b>-2%~+2% 구간을 0.5% 간격으로 훑어보는 것</b>이 안전합니다. "
        "이 계산기의 아래 표가 바로 그 구간 시뮬레이션입니다.</p>",
        "사정률은 복수예가 추첨 때 기초금액에 곱하는 비율로 입찰 전에는 확정되지 않습니다. "
        "예가변동폭은 조달청 ±2%, 지방자치단체 ±3% 수준이라 특정 값 하나보다 구간으로 보는 것이 안전합니다.",
    ),
    (
        "낙찰하한율은 지금 얼마인가요?",
        "<p>2026년 1월 30일 이후 공고분부터 100억 원 미만 공사 적격심사의 낙찰하한율이 전 구간 "
        "2%p 올랐고, 물품·용역도 2026년 5월·7월 개정으로 2%p 상향되었습니다. 이 계산기의 프리셋은 "
        "그 기준을 반영했습니다(공사 10억 미만 89.745%, 10억~50억 88.745%, 50억~100억 87.495%, "
        "중소기업자간 경쟁 물품·시설분야 용역 89.995%, 물품 적격심사 고시금액 미만 86.245%). "
        "다만 값은 발주기관·추정가격 구간·세부기준별로 다르므로, 최종값은 반드시 공고문의 "
        "'낙찰자 결정방법'에 적힌 기준을 확인하세요.</p>",
        "2026년 1월 30일 이후 공고분부터 100억 원 미만 공사 낙찰하한율이 2%p 올랐고 물품·용역도 "
        "2026년 5월·7월 2%p 상향되었습니다. 예로 공사 10억 미만 89.745%, 10억~50억 88.745%, "
        "중소기업자간 경쟁 물품 89.995%입니다. 최종값은 공고문의 낙찰자 결정방법을 확인하세요.",
    ),
    (
        "입찰보증금은 꼭 현금으로 내야 하나요?",
        "<p>원칙은 입찰금액의 <b>100분의 5 이상</b>(국가계약법 시행령 제37조①)이지만, 같은 조 제3항의 "
        "면제 대상(법령에 따라 허가·등록을 하고 입찰공고일 현재 그 사업을 영위하는 자 등)은 "
        "<b>지급각서 제출로 갈음</b>할 수 있습니다(같은 조 제4항). 나라장터 공고에 흔히 보이는 "
        "'입찰보증금은 지급각서로 갈음한다'가 이것입니다. 다만 지급각서를 내도 낙찰 뒤 정당한 "
        "사유 없이 계약을 체결하지 않으면 보증금에 해당하는 금액이 국고에 귀속되고 "
        "입찰참가자격 제한 사유가 될 수 있으니, 면제는 '안 내도 된다'는 뜻이 아닙니다.</p>",
        "원칙은 입찰금액의 5% 이상입니다. 법령에 따라 허가·등록을 하고 공고일 현재 사업 중인 자 등은 "
        "지급각서로 갈음할 수 있지만, 낙찰 후 계약을 체결하지 않으면 그 금액이 국고에 귀속될 수 있습니다.",
    ),
    (
        "계약보증금은 10%인가요, 15%인가요?",
        "<p>국가계약은 계약금액의 <b>100분의 10 이상</b>(국가계약법 시행령 제50조①, 경제위기 고시 시 "
        "5% 이상)입니다. 지방계약은 공사의 경우 계약보증금 10% 이상을 내거나 공사이행보증서를 "
        "제출하는 방법 중에서 고르고(지방계약법 시행령 제51조①), 물품·용역은 10% 이상입니다"
        "(같은 조 제5항). 지방 공사 계약보증금률은 2023년 3월 15%에서 10%로 낮아졌습니다. "
        "계약금액 5천만 원 이하 등 일정한 경우에는 면제될 수 있습니다(같은 법 시행령 제50조⑥).</p>",
        "국가계약은 계약금액의 10% 이상(경제위기 고시 시 5% 이상), 지방계약 공사는 계약보증금 10% 이상 "
        "또는 공사이행보증서, 물품·용역은 10% 이상입니다. 계약금액 5천만 원 이하 등은 면제될 수 있습니다.",
    ),
    (
        "계산 결과와 실제 투찰금액이 왜 다른가요?",
        "<p>① 원 단위 미만 처리(절상·절사) 방식, ② 사정률이 추첨으로 정해져 확정값이 아닌 점, "
        "③ 공사에서 A값(순공사원가)을 반영하는지 여부, ④ 발주기관별 가산·감점과 세부기준, "
        "⑤ 부가세 포함 여부 때문에 몇 원에서 수십만 원까지 차이가 납니다. 이 계산기는 표준 산식으로 "
        "빠르게 범위를 잡는 도구이고, 최종 투찰금액은 나라장터 투찰 화면과 공고문 기준으로 확정하세요.</p>",
        "원 단위 처리, 사정률 미확정, A값(순공사원가) 반영 여부, 기관별 세부기준, 부가세 포함 여부 때문에 "
        "차이가 납니다. 이 계산기는 범위를 잡는 도구이고 최종 금액은 공고문과 나라장터 투찰 화면에서 확정하세요.",
    ),
    (
        "입력한 금액이 서버에 저장되나요?",
        "<p>아니요. 모든 계산은 방문자의 브라우저 안에서만 실행되고 입력값을 서버로 보내지 않습니다. "
        "회원가입이나 로그인도 필요 없고, 새로고침하면 입력값은 사라집니다.</p>",
        "아니요. 계산은 브라우저 안에서만 실행되고 입력값을 서버로 보내지 않습니다. 로그인도 필요 없습니다.",
    ),
]


def _faq_html() -> str:
    parts = []
    for idx, (q, a_html, _plain) in enumerate(FAQ_ITEMS, start=1):
        parts.append(
            f'<details class="faq-item"{" open" if idx == 1 else ""}>'
            f'<summary><span class="q">{q}</span></summary>'
            f'<div class="a">{a_html}</div></details>'
        )
    return "".join(parts)


# --------------------------------------------------------------------------
# 구조화 데이터
# --------------------------------------------------------------------------
def ld_json(site_url: str, built_at: str) -> str:
    url = f"{site_url.rstrip('/')}/{TOOL_PATH}"
    home = f"{site_url.rstrip('/')}/"
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebApplication",
                "@id": url + "#app",
                "name": "나라장터 투찰금액·낙찰하한율·입찰보증금 계산기",
                "url": url,
                "applicationCategory": "BusinessApplication",
                "applicationSubCategory": "입찰·계약 계산",
                "operatingSystem": "Web",
                "browserRequirements": "JavaScript",
                "inLanguage": "ko-KR",
                "isAccessibleForFree": True,
                "description": TOOL_DESC,
                "featureList": [
                    "기초금액·사정률·낙찰하한율로 예정가격과 투찰금액 계산",
                    "사정률 -2%~+2% 구간별 예상 투찰금액 비교표",
                    "입찰보증금 5%·계약보증금 10% 계산과 지급각서 대체 안내",
                ],
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "KRW"},
                "dateModified": built_at,
                "publisher": {"@type": "Organization", "name": SITE_NAME, "url": home},
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "홈", "item": home},
                    {"@type": "ListItem", "position": 2,
                     "name": "투찰금액·보증금 계산기", "item": url},
                ],
            },
            {
                "@type": "FAQPage",
                "@id": url + "#faq",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": q,
                        "acceptedAnswer": {"@type": "Answer", "text": plain},
                    }
                    for q, _html, plain in FAQ_ITEMS
                ],
            },
        ],
    }
    return ('<script type="application/ld+json">'
            + json.dumps(graph, ensure_ascii=False) + "</script>")


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------
def body_html(root: str = "../") -> str:
    floor_presets = [
        ("89.745", "공사 10억 미만"),
        ("88.745", "공사 10~50억"),
        ("87.495", "공사 50~100억"),
        ("89.995", "물품·시설용역"),
        ("86.245", "물품 적격심사"),
        ("82.495", "학술·SW 등"),
    ]
    floor_btns = "".join(
        f'<button type="button" class="preset" data-floor="{v}"'
        f'{" aria-pressed=\"true\"" if v == "89.745" else ""}>{lab}'
        f'<span class="preset-num">{v}%</span></button>'
        for v, lab in floor_presets
    )
    amount_chips = "".join(
        f'<button type="button" class="chip-btn" data-amount="{v}">{lab}</button>'
        for v, lab in (("50000000", "5천만"), ("100000000", "1억"),
                       ("300000000", "3억"), ("1000000000", "10억"))
    )

    return f"""<main id="main">
  <div class="wrap">
    <nav class="crumbs" aria-label="위치">
      <a href="{root}index.html">홈</a><span aria-hidden="true">/</span>
      <span aria-current="page">투찰금액·보증금 계산기</span>
    </nav>
    <header class="article-head tool-head">
      <span class="kicker">무료 계산 도구 · 설치 없음</span>
      <h1>나라장터 투찰금액·낙찰하한율·보증금 계산기</h1>
      <p class="tool-lead">기초금액과 사정률, 낙찰하한율만 넣으면 <b>예정가격과 투찰금액(낙찰하한가)</b>이
      바로 나옵니다. 입찰보증금 5%·계약보증금 10%와 지급각서 대체 여부도 같은 화면에서 확인하세요.
      입력값은 서버로 보내지 않고 방문자 브라우저에서만 계산합니다.</p>
      <div class="calc-jump">
        <a class="jump" href="#calc-bid">① 투찰금액 계산</a>
        <a class="jump" href="#calc-bond">② 보증금 계산</a>
        <a class="jump" href="#calc-faq">자주 묻는 질문</a>
      </div>
      <p class="calc-from" id="calcFrom" hidden></p>
    </header>
  </div>

  <section class="section" id="calc-bid">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>① 투찰금액·낙찰하한율 계산기</h2>
          <p class="section-sub">예정가격 = 기초금액 × 사정률, 투찰금액 = 예정가격 × 낙찰하한율.
          원 단위 미만은 절상해 표시합니다.</p>
        </div>
        <span class="section-count">실시간 계산</span>
      </div>

      <div class="calc-grid">
        <form class="calc-card" id="bidForm" autocomplete="off" onsubmit="return false">
          <div class="field">
            <label for="baseAmount">기초금액</label>
            <div class="input-money">
              <input id="baseAmount" type="text" inputmode="numeric" value="100,000,000"
                     aria-describedby="baseHint">
              <span class="unit">원</span>
            </div>
            <div class="preset-row">{amount_chips}</div>
            <p class="hint" id="baseHint">공고문의 기초금액(추정가격에 부가세를 더한 금액)을 넣으세요.</p>
          </div>

          <div class="field">
            <span class="label" id="floorLabel">낙찰하한율</span>
            <div class="preset-row preset-wrap" role="group" aria-labelledby="floorLabel">
              {floor_btns}
            </div>
            <div class="input-money compact">
              <input id="floorRate" type="number" inputmode="decimal" step="0.001" min="0" max="100"
                     value="89.745" aria-label="낙찰하한율 직접 입력(%)">
              <span class="unit">%</span>
            </div>
            <p class="hint">프리셋은 2026년 상향 기준의 참고값입니다. 공고문에 적힌 세부기준이 최종입니다.</p>
          </div>

          <div class="field">
            <label for="sajungRate">사정률</label>
            <div class="slider-row">
              <input id="sajungRange" type="range" min="96" max="104" step="0.05" value="100"
                     aria-label="사정률 슬라이더(%)">
              <div class="input-money compact slider-num">
                <input id="sajungRate" type="number" inputmode="decimal" step="0.01" min="80" max="120"
                       value="100.00" aria-label="사정률 직접 입력(%)">
                <span class="unit">%</span>
              </div>
            </div>
            <div class="preset-row">
              <button type="button" class="chip-btn" data-sajung="98">98%</button>
              <button type="button" class="chip-btn" data-sajung="100">100%</button>
              <button type="button" class="chip-btn" data-sajung="102">102%</button>
            </div>
            <p class="hint">예가변동폭은 조달청 ±2%, 지방자치단체 ±3% 수준입니다. 추첨이라 확정값은 없습니다.</p>
          </div>
        </form>

        <div class="calc-result" aria-live="polite">
          <div class="rc rc-main">
            <p class="rc-label">투찰금액(낙찰하한가)</p>
            <p class="rc-value" id="outBid">—</p>
            <p class="rc-sub" id="outRate">기초금액 대비 투찰률 —</p>
          </div>
          <div class="rc">
            <p class="rc-label">예정가격</p>
            <p class="rc-value sm" id="outPrice">—</p>
          </div>
          <p class="calc-note" id="outNote">기초금액과 비율을 입력하면 바로 계산됩니다.</p>
          <button type="button" class="btn btn-wide" id="useBidAmount">이 금액으로 보증금 계산 →</button>
        </div>
      </div>

      <div class="table-wrap">
        <table class="data-table calc-table">
          <caption class="sr-only">사정률 구간별 예정가격과 투찰금액</caption>
          <thead>
            <tr>
              <th scope="col">사정률</th>
              <th scope="col" class="num">예정가격</th>
              <th scope="col" class="num">투찰금액</th>
              <th scope="col" class="num">투찰률</th>
            </tr>
          </thead>
          <tbody id="bidTableBody">
            <tr><td colspan="4" class="calc-empty">기초금액과 낙찰하한율을 입력하면 표시됩니다.</td></tr>
          </tbody>
        </table>
      </div>
      <p class="grid-note">사정률 -2.0% ~ +2.0%를 0.5% 간격으로 계산한 값입니다(단위: 원).
      굵은 줄은 입력한 사정률과 가장 가까운 행입니다.</p>
    </div>
  </section>

  <section class="section section-alt" id="calc-bond">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>② 입찰보증금·계약보증금 계산기</h2>
          <p class="section-sub">금액과 비율을 넣으면 내야 할 보증금과 근거 조문, 지급각서 대체 여부를
          함께 보여 줍니다.</p>
        </div>
      </div>

      <div class="calc-grid">
        <form class="calc-card" id="bondForm" autocomplete="off" onsubmit="return false">
          <div class="field">
            <span class="label" id="bondTypeLabel">보증금 종류</span>
            <div class="preset-row preset-wrap" role="group" aria-labelledby="bondTypeLabel">
              <button type="button" class="preset on" data-bond="bid" data-rate="5"
                      aria-pressed="true">입찰보증금<span class="preset-num">5%</span></button>
              <button type="button" class="preset" data-bond="contract" data-rate="10"
                      aria-pressed="false">계약보증금<span class="preset-num">10%</span></button>
              <button type="button" class="preset" data-bond="etc" data-rate="15"
                      aria-pressed="false">기타 기준<span class="preset-num">15%</span></button>
            </div>
          </div>

          <div class="field">
            <label for="bondAmount">입찰금액 또는 계약금액</label>
            <div class="input-money">
              <input id="bondAmount" type="text" inputmode="numeric" value="100,000,000">
              <span class="unit">원</span>
            </div>
          </div>

          <div class="field">
            <label for="bondRate">보증금율</label>
            <div class="input-money compact">
              <input id="bondRate" type="number" inputmode="decimal" step="0.1" min="0" max="100"
                     value="5">
              <span class="unit">%</span>
            </div>
          </div>
        </form>

        <div class="calc-result" aria-live="polite">
          <div class="rc rc-main">
            <p class="rc-label">납부해야 할 보증금</p>
            <p class="rc-value" id="outBond">—</p>
            <p class="rc-sub" id="outBondBasis">입찰금액의 5% 기준</p>
          </div>
          <div class="rc">
            <p class="rc-label">적용 근거</p>
            <p class="rc-text" id="outBondLaw">국가계약법 시행령 제37조①</p>
          </div>
          <div class="bond-note" id="outBondNote"></div>
        </div>
      </div>
    </div>
  </section>

  <section class="section" id="calc-faq">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>투찰·보증금 자주 묻는 질문</h2>
          <p class="section-sub">실무에서 가장 많이 확인하는 일곱 가지입니다.</p>
        </div>
      </div>
      {_faq_html()}

      <div class="source-box">
        <p class="source-title">근거와 기준</p>
        <ul class="source-list">
          <li>국가를 당사자로 하는 계약에 관한 법률 시행령 제37조(입찰보증금)·제50조(계약보증금),
            제38조(보증금의 국고귀속) — 2026년 6월 3일 시행본</li>
          <li>지방자치단체를 당사자로 하는 계약에 관한 법률 시행령 제51조(계약의 이행보증)</li>
          <li>낙찰하한율: 기획재정부 계약예규 개정(2026년 1월 30일 시행, 100억 원 미만 공사 전 구간
            2%p 상향)과 조달청 물품·용역 적격심사 세부기준 개정(2026년 5월 26일·7월 27일 시행)</li>
          <li class="warn">낙찰하한율과 보증금 면제 범위는 발주기관·추정가격 구간·계약 유형마다 다릅니다.
            이 계산기의 값은 참고용이며, 실제 입찰에서는 공고문과 나라장터 투찰 화면이 최종입니다.</li>
          <li class="warn">이 페이지는 법률 자문이 아닙니다. 계약 판단이 걸린 사안은 발주기관 담당자나
            전문가에게 확인하세요.</li>
        </ul>
      </div>

      <div class="rel-grid">
        <a class="rel-card" href="{root}index.html#highlight">이번 주 전국 입찰 동향 <i>→</i></a>
        <a class="rel-card" href="{root}index.html#regions">지역별 브리핑 <i>→</i></a>
        <a class="rel-card" href="{root}index.html#fields">분야별 브리핑 <i>→</i></a>
      </div>
    </div>
  </section>
</main>
<noscript><div class="wrap"><p class="calc-noscript">계산은 브라우저에서 실행됩니다.
브라우저의 JavaScript를 켜면 바로 사용할 수 있습니다.</p></div></noscript>
<script>{CALC_JS}</script>
"""


CALC_CSS = r"""
/* ==========================================================================
   입찰 계산기 도구 페이지 (builder.py 가 생성)
   색은 전부 CSS 변수를 쓰므로 다크 모드에서도 그대로 대응된다.
   ========================================================================== */
.tool-head .kicker{margin-bottom:12px}
.tool-head h1{margin:10px 0 12px}
.tool-lead{color:var(--ink-2);font-size:clamp(14.5px,1.7vw,16px);line-height:1.78;max-width:74ch}
.tool-lead b{color:var(--ink)}
.calc-jump{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
.calc-from{margin:14px 0 0;padding:12px 15px;background:var(--brand-weak);color:#1c3f7d;
  border-radius:12px;font-size:13.5px;line-height:1.65}
.calc-from b{color:#123064;font-weight:750}
.calc-from[hidden]{display:none}
.jump{display:inline-flex;align-items:center;background:var(--brand-weak);color:var(--brand);
  font-size:13.5px;font-weight:700;padding:9px 15px;border-radius:999px;border:1px solid transparent}
.jump:hover{background:var(--brand);color:#fff;text-decoration:none}

.calc-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:16px;align-items:start}
.calc-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:20px;box-shadow:var(--shadow)}
.field{margin-bottom:20px}
.field:last-child{margin-bottom:0}
.field > label,.field > .label{display:block;font-size:13px;font-weight:750;color:var(--ink);margin-bottom:7px}
.hint{margin-top:6px;font-size:12px;color:var(--muted);line-height:1.6}
.input-money{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:12px;
  padding:0 14px;background:var(--surface-2);transition:border-color .15s,box-shadow .15s}
.input-money:focus-within{border-color:var(--brand-2);box-shadow:0 0 0 3px rgba(47,98,201,.14)}
.input-money input{flex:1;min-width:0;width:100%;border:0;background:transparent;outline:none;
  padding:13px 0;font-size:19px;font-weight:750;letter-spacing:-.01em;color:var(--ink);
  font-variant-numeric:tabular-nums;font-family:inherit}
.input-money.compact{margin-top:9px}
.input-money.compact input{font-size:17px;padding:10px 0}
.input-money .unit{font-size:13px;font-weight:650;color:var(--muted);white-space:nowrap}

.preset-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.preset{display:inline-flex;flex-direction:column;align-items:flex-start;gap:1px;
  background:var(--surface-2);border:1px solid var(--line);border-radius:11px;
  padding:7px 11px;cursor:pointer;color:var(--ink-2);font-size:12.5px;font-weight:700;
  line-height:1.35;transition:border-color .15s,background .15s,color .15s}
.preset:hover{border-color:#b9cdf2;background:#f8fbff;color:var(--brand)}
.preset.on{border-color:var(--brand);background:var(--brand-weak);color:var(--brand)}
.preset-num{font-size:11px;font-weight:650;color:var(--muted);font-variant-numeric:tabular-nums}
.preset.on .preset-num{color:var(--brand)}
.chip-btn{background:var(--surface-2);border:1px solid var(--line);border-radius:999px;
  padding:6px 12px;font-size:12.5px;font-weight:650;color:var(--ink-2);cursor:pointer}
.chip-btn:hover{border-color:#b9cdf2;color:var(--brand);background:#f8fbff}

.slider-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.slider-row input[type=range]{flex:1 1 160px;min-width:120px;accent-color:var(--brand);height:28px}
.slider-num{flex:0 0 132px;margin-top:0}

.calc-result{display:grid;gap:10px;align-content:start}
.rc{background:var(--surface);border:1px solid var(--line);border-radius:13px;
  padding:15px 17px;box-shadow:var(--shadow)}
.rc-main{background:linear-gradient(135deg,#0b2250 0%,#14459c 60%,#1c56bd 100%);
  border:0;color:#fff;padding:20px}
.rc-label{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.02em}
.rc-main .rc-label{color:#c5d8ff}
.rc-value{margin-top:6px;font-size:clamp(22px,3.4vw,29px);font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1.25;word-break:keep-all}
.rc-value.sm{font-size:clamp(17px,2.4vw,21px)}
.rc-sub{margin-top:7px;font-size:12.5px;color:#c8d9ff;font-variant-numeric:tabular-nums}
.rc-text{margin-top:6px;font-size:13.5px;font-weight:650;color:var(--ink-2);line-height:1.55}
.calc-note{margin:2px 2px 0;font-size:12.5px;color:var(--muted);line-height:1.65}
.calc-note b{color:var(--ink-2)}
.btn-wide{width:100%;justify-content:center;padding:11px 14px;font-size:13.5px}

.bond-note{background:var(--brand-weak);border-radius:12px;padding:14px 16px;color:#1c3f7d;
  font-size:13.5px;line-height:1.7}
.bond-note b{color:#123064}
.bond-note ul{display:grid;gap:6px}
.bond-note li{position:relative;padding-left:13px}
.bond-note li::before{content:"";position:absolute;left:2px;top:.68em;width:5px;height:5px;
  border-radius:50%;background:#6f92cf}

.calc-table{min-width:460px}
.calc-table tr.is-now td{background:var(--brand-weak);font-weight:750;color:var(--brand)}
.calc-table .now-tag{display:inline-block;margin-left:5px;font-size:10.5px;font-weight:800;
  background:var(--brand);color:#fff;border-radius:5px;padding:1px 5px;vertical-align:1px}
.calc-empty{padding:22px 12px;text-align:center;color:var(--muted);font-size:13.5px}
.source-title{margin-bottom:8px;font-size:12.5px;font-weight:800;color:var(--brand);letter-spacing:.02em}
.calc-noscript{margin:24px 0;padding:14px 16px;background:var(--accent-weak);color:var(--accent);
  border-radius:12px;font-size:13.5px}
body.tool .article-head{padding-top:6px}
body.tool .section:first-of-type{padding-top:26px}

@media (max-width:860px){
  .calc-grid{grid-template-columns:1fr;gap:14px}
  .calc-result{order:2}
  .rc-main{padding:17px}
}
@media (max-width:720px){
  .calc-card{padding:16px}
  .slider-row{gap:9px}
  .slider-num{flex:1 1 120px}
  .preset{padding:7px 9px}
  .input-money input{font-size:17.5px}
}
/* 좁은 화면에서는 표를 가로 스크롤 없이 한눈에 보이도록 줄인다 */
@media (max-width:560px){
  .calc-table{min-width:0;font-size:11.5px}
  .calc-table thead th{padding:8px 5px;font-size:10.5px}
  .calc-table td{padding:8px 5px}
  .calc-table .now-tag{margin-left:3px;padding:1px 3px;font-size:9.5px}
}
@media (prefers-color-scheme:dark){
  .preset:hover,.chip-btn:hover{background:#1a2333}
  .bond-note{background:#1a2540;color:#c9dcff}
  .bond-note b{color:#fff}
  .bond-note li::before{background:#6f92cf}
}
"""


CALC_JS = r"""
(function () {
  "use strict";
  var nf = new Intl.NumberFormat("ko-KR");
  function qid(s) { return document.getElementById(s); }

  function parseNum(v) {
    if (v === null || v === undefined) { return NaN; }
    var t = String(v).replace(/,/g, "").replace(/[^0-9.\-]/g, "");
    if (t === "" || t === "-" || t === "." || t === "-.") { return NaN; }
    var n = parseFloat(t);
    return isFinite(n) ? n : NaN;
  }
  function ceilWon(n) { return Math.ceil(n - 1e-6); }
  function won(n) { return nf.format(ceilWon(n)); }
  function plain(n) { return nf.format(ceilWon(n)); }
  function money(v) {
    var digits = String(v === null || v === undefined ? "" : v).replace(/[^0-9]/g, "");
    if (digits.length > 15) { digits = digits.slice(0, 15); }
    return digits ? nf.format(parseInt(digits, 10)) : "";
  }
  /* ---------------- ① 투찰금액 ---------------- */
  var baseEl = qid("baseAmount");
  var floorEl = qid("floorRate");
  var sajungEl = qid("sajungRate");
  var rangeEl = qid("sajungRange");
  var lastBid = NaN;

  function buildTable(base, rate, sajung) {
    var body = qid("bidTableBody");
    if (!body) { return; }
    if (!(base > 0 && rate > 0)) {
      body.innerHTML = '<tr><td colspan="4" class="calc-empty">' +
        "기초금액과 낙찰하한율을 입력하면 사정률 구간별 투찰금액이 표시됩니다.</td></tr>";
      return;
    }
    var rows = [];
    for (var i = -4; i <= 4; i++) {
      var d = i * 0.5;
      var price = base * (1 + d / 100);
      var bid = price * rate / 100;
      var ratio = bid / base * 100;
      var now = isFinite(sajung) && Math.abs(d - (sajung - 100)) < 0.249;
      rows.push("<tr" + (now ? ' class="is-now"' : "") + "><td>" +
        (d > 0 ? "+" : "") + d.toFixed(1) + "%" +
        (now ? '<span class="now-tag">현재</span>' : "") + "</td>" +
        '<td class="num">' + plain(price) + "</td>" +
        '<td class="num"><b>' + plain(bid) + "</b></td>" +
        '<td class="num">' + ratio.toFixed(3) + "%</td></tr>");
    }
    body.innerHTML = rows.join("");
  }

  function bidCompute() {
    var base = parseNum(baseEl.value);
    var rate = parseNum(floorEl.value);
    var sajung = parseNum(sajungEl.value);
    var ok = base > 0 && rate > 0 && isFinite(sajung);

    if (!ok) {
      lastBid = NaN;
      qid("outBid").textContent = "—";
      qid("outPrice").textContent = "—";
      qid("outRate").textContent = "기초금액 대비 투찰률 —";
      qid("outNote").textContent = "기초금액과 비율을 입력하면 바로 계산됩니다.";
      buildTable(NaN, NaN, NaN);
      return;
    }
    var price = base * sajung / 100;
    var bid = price * rate / 100;
    var ratio = bid / base * 100;
    lastBid = bid;

    qid("outBid").textContent = won(bid) + "원";
    qid("outPrice").textContent = won(price) + "원";
    qid("outRate").textContent = "기초금액 대비 투찰률 " + ratio.toFixed(3) + "%";
    qid("outNote").innerHTML = "기초금액 <b>" + won(base) + "원</b> × 사정률 <b>" +
      sajung.toFixed(2) + "%</b> = 예정가격 <b>" + won(price) + "원</b>, 여기에 낙찰하한율 <b>" +
      rate.toFixed(3) + "%</b>를 곱한 값입니다.";
    buildTable(base, rate, sajung);
  }

  baseEl.addEventListener("input", function () {
    baseEl.value = money(baseEl.value);
    bidCompute();
  });
  floorEl.addEventListener("input", function () {
    document.querySelectorAll("[data-floor]").forEach(function (b) {
      b.classList.remove("on");
      b.setAttribute("aria-pressed", "false");
    });
    bidCompute();
  });
  rangeEl.addEventListener("input", function () {
    sajungEl.value = Number(rangeEl.value).toFixed(2);
    bidCompute();
  });
  sajungEl.addEventListener("input", function () {
    var v = parseNum(sajungEl.value);
    if (isFinite(v)) {
      rangeEl.value = String(Math.min(104, Math.max(96, v)));
    }
    bidCompute();
  });

  document.querySelectorAll("[data-floor]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-floor]").forEach(function (b) {
        b.classList.remove("on");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("on");
      btn.setAttribute("aria-pressed", "true");
      floorEl.value = btn.getAttribute("data-floor");
      bidCompute();
    });
  });
  document.querySelectorAll("[data-sajung]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var v = parseNum(btn.getAttribute("data-sajung"));
      sajungEl.value = v.toFixed(2);
      rangeEl.value = String(v);
      bidCompute();
    });
  });
  document.querySelectorAll("[data-amount]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      baseEl.value = money(btn.getAttribute("data-amount"));
      bidCompute();
    });
  });

  /* ---------------- ② 보증금 ---------------- */
  var BOND = {
    bid: {
      label: "입찰금액의 {ratio}% 기준",
      law: "국가계약법 시행령 제37조① (입찰보증금 5% 이상)",
      note: "<b>지급각서로 갈음할 수 있습니다.</b> 같은 조 제3항의 면제 대상(법령에 따라 허가·등록을 하고 " +
        "공고일 현재 사업을 영위하는 자 등)은 현금 대신 지급각서를 냅니다(제4항). " +
        "다만 낙찰 뒤 정당한 사유 없이 계약을 체결하지 않으면 보증금에 해당하는 금액이 국고에 " +
        "귀속되므로(법 제9조③), 면제는 '내지 않아도 된다'는 뜻이 아닙니다."
    },
    contract: {
      label: "계약금액의 {ratio}% 기준",
      law: "국가계약법 시행령 제50조① · 지방계약법 시행령 제51조",
      note: "<b>계약 체결 시 내는 보증금입니다.</b> 국가계약은 계약금액의 10% 이상, 지방계약은 " +
        "공사라면 계약보증금 10% 이상이나 공사이행보증서 중에서 고르고 물품·용역은 10% 이상입니다. " +
        "계약금액 5천만 원 이하 등 일부 계약은 면제될 수 있습니다(같은 법 시행령 제50조⑥)."
    },
    etc: {
      label: "공고문에 적힌 {ratio}% 기준",
      law: "개별 입찰공고문 기준",
      note: "<b>공고문에 적힌 비율을 그대로 넣으세요.</b> 기관별 기준, 공동수급 비율, " +
        "특수 계약 유형에서는 5%·10%가 아닌 값이 적용되기도 합니다."
    }
  };
  var bondType = "bid";
  var bondAmountEl = qid("bondAmount");
  var bondRateEl = qid("bondRate");

  function bondCompute() {
    var amount = parseNum(bondAmountEl.value);
    var rate = parseNum(bondRateEl.value);
    var info = BOND[bondType] || BOND.etc;
    if (!(amount > 0) || !(rate > 0)) {
      qid("outBond").textContent = "—";
      qid("outBondBasis").textContent = "금액과 비율을 입력하세요";
    } else {
      qid("outBond").textContent = won(amount * rate / 100) + "원";
      qid("outBondBasis").textContent = info.label.replace("{ratio}", rate.toFixed(rate % 1 ? 1 : 0));
    }
    qid("outBondLaw").textContent = info.law;
    qid("outBondNote").innerHTML = info.note;
  }

  document.querySelectorAll("[data-bond]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-bond]").forEach(function (b) {
        b.classList.remove("on");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("on");
      btn.setAttribute("aria-pressed", "true");
      bondType = btn.getAttribute("data-bond");
      bondRateEl.value = btn.getAttribute("data-rate");
      bondCompute();
    });
  });
  bondAmountEl.addEventListener("input", function () {
    bondAmountEl.value = money(bondAmountEl.value);
    bondCompute();
  });
  bondRateEl.addEventListener("input", function () {
    bondType = "etc";
    document.querySelectorAll("[data-bond]").forEach(function (b) {
      b.classList.remove("on");
      b.setAttribute("aria-pressed", "false");
    });
    bondCompute();
  });

  var useBtn = qid("useBidAmount");
  if (useBtn) {
    useBtn.addEventListener("click", function () {
      if (!isFinite(lastBid)) {
        baseEl.focus();
        return;
      }
      bondAmountEl.value = plain(lastBid);
      bondCompute();
      var target = qid("calc-bond");
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      var y = document.querySelector('#bondAmount');
      if (y) { y.focus({ preventScroll: true }); }
    });
  }

  bidCompute();
  bondCompute();

  /* ---------------- ③ 공고 검색 페이지에서 넘어온 값 ----------------
     /tools/bid-search.html 의 '투찰금액 계산' 버튼은
     bid-calculator.html?base=117300000&name=공고명&from=search 로 연결된다.
     base(기초금액)를 받으면 계산기를 바로 채우고, 어떤 공고에서 왔는지도 보여 준다. */
  var params = new URLSearchParams(window.location.search);
  var fromBase = parseNum(params.get("base") || params.get("amount"));
  if (fromBase > 0) {
    baseEl.value = money(String(Math.ceil(fromBase)));
    var fromSajung = parseNum(params.get("sajung"));
    if (fromSajung > 0) {
      sajungEl.value = fromSajung.toFixed(2);
      rangeEl.value = String(Math.min(104, Math.max(96, fromSajung)));
    }
    var fromFloor = parseNum(params.get("floor"));
    if (fromFloor > 0) { floorEl.value = fromFloor; }
    bidCompute();
    var banner = qid("calcFrom");
    if (banner) {
      var rawName = String(params.get("name") || "").replace(/[<>&"']/g, "").slice(0, 60);
      var head = rawName ? "<b>" + rawName + "</b> 공고의 " : "";
      banner.innerHTML = head + "기초금액 <b>" + won(fromBase) + "원</b>으로 계산했습니다. " +
        "공고 기초금액이 다르거나 사정률·낙찰하한율을 바꾸려면 아래 입력상자를 고치세요.";
      banner.hidden = false;
    }
    if (typeof window.gtag === "function") {
      window.gtag("event", "calc_from_search", { value: Math.ceil(fromBase) });
    }
  }
})();
"""
