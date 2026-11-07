# -*- coding: utf-8 -*-
"""pSEO 정적 HTML 사이트 생성기 (builder.py).

품질 게이트를 통과한 마크다운(output/*.md)을 그대로 받아, 외부 라이브러리 없이
배포 가능한 정적 사이트(site/)를 만든다. Node.js/npm/Astro가 필요 없다.

만들어지는 것
  site/index.html              메인(전국 하이라이트·TOP10·지역 그리드·분야 뱃지)
  site/bids/{slug}.html        상세 리포트(표·지표 카드·FAQ·출처·JSON-LD)
  site/assets/style.css        공통 스타일(외부 CDN 없음, 다크모드·모바일 대응)
  site/assets/favicon.svg      파비콘
  site/sitemap.xml             사이트맵
  site/robots.txt              robots
  site/404.html                오류 페이지
  site/build.json              빌드 메타데이터

실행 예
  python3 builder.py
  python3 builder.py --site-url https://my-domain.com
  python3 builder.py --exclude-sim          # 시뮬레이션 표본을 빌드에서 제외(실서비스용)

내부 링크 규칙
  마크다운은 /bids/{slug}/ 형태를 쓰지만, 이 빌더는 {slug}.html 파일을 만든다.
  (GitHub Pages·Cloudflare Pages·python3 -m http.server 어디서든 바로 열리도록)
  따라서 렌더링 시 링크를 상대 경로 {slug}.html 로 다시 쓴다.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import (
    BASE_DIR, KST, REGIONS, SITE_NAME, SITE_URL, SOURCE, log,
)

DOW = ["월", "화", "수", "목", "금", "토", "일"]
BUILD_MARKER = ".pseo-site"


# ==========================================================================
# 1. 유틸
# ==========================================================================
def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_date(value: str) -> str:
    try:
        d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(value or "")
    return f"{d.year}년 {d.month}월 {d.day}일 ({DOW[d.weekday()]})"


def fmt_dt(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return fmt_date(value)
    return f"{dt.year}년 {dt.month}월 {dt.day}일 ({DOW[dt.weekday()]}) {dt:%H:%M}"


_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(억|만|원)")


def parse_amount(text: str) -> int:
    """'630억 9,834만 원' → 63098340000 (정렬·막대그래프 계산용, 표시는 원문 유지)."""
    if not text:
        return 0
    total = 0.0
    found = False
    for number, unit in _AMOUNT_RE.findall(str(text)):
        try:
            n = float(number.replace(",", ""))
        except ValueError:
            continue
        found = True
        if unit == "억":
            total += n * 100_000_000
        elif unit == "만":
            total += n * 10_000
        elif unit == "원":
            total += n
    return int(total) if found else 0


def parse_percent(text: str) -> Optional[float]:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def delta_chip(text: str, extra_class: str = "") -> str:
    """증감 문자열을 색이 있는 칩으로. 상승=붉은색, 하락=푸른색(한국 금융 관행)."""
    raw = (text or "").strip()
    if not raw or raw in ("—", "-", "비교 불가"):
        return f'<span class="delta flat{extra_class}">—</span>'
    pct = parse_percent(raw)
    tone = "flat"
    if pct is not None:
        tone = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    return f'<span class="delta {tone}{extra_class}">{esc(raw)}</span>'


def _num_class(text: str, first_col: bool = False) -> str:
    """표 셀 정렬용. 숫자로 읽히는 값만 오른쪽 정렬한다.

    첫 열은 순위(1, 2, 3…)일 때만 숫자로 본다 — 금액 구간 라벨('1억 원 미만')이나
    날짜('11월 3일')는 왼쪽 정렬을 유지해야 읽기 쉽다.
    """
    s = (text or "").strip()
    if not s or not re.match(r"^[+-]?\d", s):
        return ""
    if first_col:
        return "num rank-cell" if re.fullmatch(r"\d{1,3}", s) else ""
    if "월" in s and "일" in s:
        return ""
    return "num"


# ==========================================================================
# 2. Frontmatter / 마크다운 파서
# ==========================================================================
def _scalar(raw: str):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    if raw in ("null", "~", ""):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end]
    data: dict = {}
    key: Optional[str] = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(_scalar(stripped[2:]))
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v == "":
            key = k
            data[k] = []
        else:
            key = None
            data[k] = _scalar(v)
    body = text[end + 4:]
    return data, body.lstrip("\n")


_BLOCK_START = re.compile(r"^(#{2,6}\s|>\s?|\||[-*]\s|\d+\.\s|<script|<div|<table|```|---\s*$)")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_blocks(body: str) -> list[tuple[str, object]]:
    """마크다운 본문 → 블록 리스트.

    블록 종류: ('h2'|'h3'|'h4'|'p'|'quote'|'ul'|'ol'|'table'|'html'|'hr', 값)
    표 값은 dict(headers=[...], rows=[[...]]) 이다.
    """
    lines = body.split("\n")
    blocks: list[tuple[str, object]] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = re.match(r"^(#{2,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            kind = {2: "h2", 3: "h3", 4: "h4"}.get(level, "h4")
            blocks.append((kind, m.group(2).strip()))
            i += 1
            continue

        if line.lstrip().startswith("<script"):
            buf = [line]
            while "</script>" not in buf[-1] and i + 1 < n:
                i += 1
                buf.append(lines[i])
            blocks.append(("html", "\n".join(buf)))
            i += 1
            continue

        if line.lstrip().startswith(("<div", "<table", "<figure", "<p class")):
            buf = [line]
            while i + 1 < n and lines[i + 1].strip():
                i += 1
                buf.append(lines[i])
            blocks.append(("html", "\n".join(buf)))
            i += 1
            continue

        if line.strip() in ("---", "***", "___"):
            blocks.append(("hr", ""))
            i += 1
            continue

        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append(("quote", " ".join(x for x in buf if x)))
            continue

        if line.lstrip().startswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            if len(rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", c or "-") for c in rows[1]):
                headers = rows[0]
                body_rows = [r for r in rows[2:] if any(c.strip() for c in r)]
                width = len(headers)
                body_rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width]
                             for r in body_rows]
                blocks.append(("table", {"headers": headers, "rows": body_rows}))
            else:
                for r in rows:
                    blocks.append(("p", " | ".join(r)))
            continue

        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]).strip())
                i += 1
            blocks.append(("ul", items))
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip())
                i += 1
            blocks.append(("ol", items))
            continue

        buf = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not _BLOCK_START.match(lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        blocks.append(("p", " ".join(buf)))

    return blocks


# ---- 인라인 -----------------------------------------------------------------
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def rewrite_url(url: str, page: Optional["Page"], for_index: bool = False) -> str:
    """/bids/{slug}/ → 상대 .html 경로(배포·로컬 어디서나 동작)."""
    if url.startswith("/bids/"):
        slug = url[len("/bids/"):].strip("/")
        if slug:
            return f"bids/{slug}.html" if for_index else f"{slug}.html"
    if url == "/":
        return "index.html" if not for_index else "index.html"
    return url


def inline(text: str, page: Optional["Page"] = None, for_index: bool = False) -> str:
    """마크다운 인라인(링크·굵게·코드) → HTML. 먼저 이스케이프한 뒤 변환한다."""
    out = html.escape(text, quote=False)

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        url = html.unescape(url)
        target = rewrite_url(url, page, for_index)
        if re.match(r"^https?:", target):
            return (f'<a href="{esc(target)}" rel="noopener noreferrer" '
                    f'target="_blank">{label}</a>')
        return f'<a href="{esc(target)}">{label}</a>'

    out = _LINK_RE.sub(link, out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return out


# ==========================================================================
# 3. 페이지 모델
# ==========================================================================
@dataclass
class Page:
    slug: str
    title: str
    description: str
    pub: str
    updated: str
    tags: list
    category: str
    scope: str
    region: str
    field_name: Optional[str]
    as_of: str
    window_start: str
    window_end: str
    source: str
    source_url: str
    data_mode: str
    blocks: list = field(default_factory=list)
    ld_json: str = ""
    body_chars: int = 0

    @property
    def is_sim(self) -> bool:
        return self.data_mode != "live"

    @property
    def window_label(self) -> str:
        if not (self.window_start and self.window_end):
            return ""
        try:
            s = datetime.strptime(self.window_start[:10], "%Y-%m-%d")
            e = datetime.strptime(self.window_end[:10], "%Y-%m-%d")
        except ValueError:
            return ""
        if s.year == e.year and s.month == e.month:
            return f"{s.year}년 {s.month}월 {s.day}일 ~ {e.day}일 ({DOW[e.weekday()]})"
        if s.year == e.year:
            return (f"{s.year}년 {s.month}월 {s.day}일 ~ "
                    f"{e.month}월 {e.day}일 ({DOW[e.weekday()]})")
        return f"{fmt_date(self.window_start)} ~ {fmt_date(self.window_end)}"

    def metrics(self) -> dict:
        """'핵심 지표 요약' 표를 {지표: {value, prev, delta}} 로."""
        for kind, val in self.blocks:
            if kind == "table" and val["headers"] and "지표" in val["headers"][0]:
                out = {}
                for row in val["rows"]:
                    if len(row) >= 4:
                        out[row[0]] = {"value": row[1], "prev": row[2], "delta": row[3]}
                    elif len(row) >= 2:
                        out[row[0]] = {"value": row[1], "prev": "", "delta": ""}
                return out
        return {}

    def table_in_section(self, section_needle: str) -> Optional[dict]:
        inside = False
        for kind, val in self.blocks:
            if kind == "h2":
                inside = section_needle in val
                continue
            if inside and kind == "table":
                return val
        return None


def load_pages(output_dir: Path, verbose: bool = False) -> tuple[list[Page], list[str]]:
    pages: list[Page] = []
    problems: list[str] = []
    for md in sorted(output_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        if not fm.get("title") or not fm.get("slug"):
            problems.append(f"{md.name}: frontmatter(title/slug) 없음 — 건너뜀")
            continue
        blocks = parse_blocks(body)
        ld_json = ""
        clean_blocks = []
        for kind, val in blocks:
            if kind == "html" and "application/ld+json" in str(val):
                raw = str(val)
                start = raw.find(">", raw.find("<script")) + 1
                end = raw.rfind("</script>")
                ld_json = raw[start:end].strip() if end > start else ""
                continue
            clean_blocks.append((kind, val))
        page = Page(
            slug=str(fm.get("slug")),
            title=str(fm.get("title")),
            description=str(fm.get("description") or ""),
            pub=str(fm.get("pubDate") or fm.get("updated") or ""),
            updated=str(fm.get("updated") or fm.get("pubDate") or ""),
            tags=[str(t) for t in (fm.get("tags") or [])],
            category=str(fm.get("category") or ""),
            scope=str(fm.get("scope") or "page"),
            region=str(fm.get("region") or "전국"),
            field_name=(str(fm["field"]) if fm.get("field") else None),
            as_of=str(fm.get("asOf") or ""),
            window_start=str(fm.get("windowStart") or ""),
            window_end=str(fm.get("windowEnd") or ""),
            source=str(fm.get("source") or SOURCE["name"]),
            source_url=str(fm.get("sourceUrl") or SOURCE["url"]),
            data_mode=str(fm.get("dataMode") or "sim"),
            blocks=clean_blocks,
            ld_json=ld_json,
            body_chars=len(body),
        )
        pages.append(page)
        if verbose:
            print(f"  · {md.name} → {page.slug} ({len(clean_blocks)} blocks)")

    order = {"national": 0, "top": 1, "region": 2, "field": 3}
    pages.sort(key=lambda p: (order.get(p.scope, 9), -parse_amount(
        (p.metrics().get("총 배정예산") or {}).get("value", "")), p.slug))
    return pages, problems


# ==========================================================================
# 4. 공통 조각
# ==========================================================================
def brand_mark(size: int = 34) -> str:
    return (
        f'<svg class="brand-mark" width="{size}" height="{size}" viewBox="0 0 64 64" '
        f'aria-hidden="true" focusable="false"><rect width="64" height="64" rx="15" fill="#14459c"/>'
        '<rect x="14" y="34" width="8" height="16" rx="3" fill="#ffffff"/>'
        '<rect x="28" y="23" width="8" height="27" rx="3" fill="#9cc0ff"/>'
        '<rect x="42" y="13" width="8" height="37" rx="3" fill="#ffffff"/></svg>'
    )


def head_html(*, title: str, description: str, canonical: str, root: str,
              is_article: bool = False, extra: str = "", ld: str = "") -> str:
    og_type = "article" if is_article else "website"
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="naver-site-verification" content="0cf1e5c1dc15b03c73c6ce85bd37d8e81e3bb8e0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta name="robots" content="index,follow,max-image-preview:large">
<meta name="theme-color" content="#14459c">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:locale" content="ko_KR">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}assets/style.css">
{extra}{ld}
</head>"""


def site_header(root: str, active: str = "") -> str:
    def cls(key: str) -> str:
        return ' class="on"' if key == active else ""

    return f"""<a class="skip" href="#main">본문 바로가기</a>
<header class="site-head">
  <div class="wrap head-inner">
    <a class="brand" href="{root}index.html">
      {brand_mark()}
      <span class="brand-text"><strong>{esc(SITE_NAME)}</strong><small>나라장터 입찰공고 주간 동향</small></span>
    </a>
    <nav class="site-nav" aria-label="주요 메뉴">
      <a href="{root}index.html#highlight"{cls('national')}>전국 브리핑</a>
      <a href="{root}index.html#top10"{cls('top')}>예산 TOP 10</a>
      <a href="{root}index.html#regions"{cls('region')}>지역별</a>
      <a href="{root}index.html#fields"{cls('field')}>분야별</a>
      <a href="{root}index.html#about">자동화</a>
    </nav>
  </div>
</header>"""


def site_footer(root: str, built_at: str, pages: int, sim: bool) -> str:
    note = ("이 사이트의 현재 데이터는 API 키 없이 생성한 <b>시뮬레이션 표본</b>입니다. "
            "실제 입찰공고가 아닙니다." if sim else
            "수치는 공공데이터 원본 필드에서 코드로 계산했습니다. 투자·입찰 판단의 최종 근거는 "
            "나라장터 원문 공고를 확인하세요.")
    return f"""<footer class="site-foot">
  <div class="wrap foot-inner">
    <div class="foot-brand">
      {brand_mark(28)}
      <div><strong>{esc(SITE_NAME)}</strong>
      <p>지역·분야별 나라장터 입찰공고 동향을 매일 자동으로 정리합니다.</p></div>
    </div>
    <div class="foot-meta">
      <p>데이터 출처: <a href="{esc(SOURCE['url'])}" rel="noopener" target="_blank">{esc(SOURCE['name'])}</a></p>
      <p>리포트 {pages}장 · 최근 빌드 {esc(fmt_dt(built_at))}</p>
      <p><a href="{root}sitemap.xml">사이트맵</a> · <a href="{root}index.html">홈</a></p>
    </div>
  </div>
  <div class="wrap foot-note"><p>{note}</p></div>
</footer>"""


# ==========================================================================
# 5. 상세 페이지
# ==========================================================================
STAT_SECTIONS = ("핵심 지표 요약", "상위 공고 한눈에")


def render_table(table: dict, page: Page, for_index: bool = False) -> str:
    headers = table["headers"]
    rows = table["rows"]
    head = "".join(f'<th scope="col">{inline(h, page, for_index)}</th>' for h in headers)
    body = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            klass = _num_class(cell, first_col=(idx == 0))
            attr = f' class="{klass}"' if klass else ""
            cells.append(f"<td{attr}>{inline(cell, page, for_index)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<div class="table-wrap"><table class="data-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def stat_cards(table: dict, page: Page) -> str:
    headers = table["headers"]
    wide = len(headers) >= 4
    cards = []
    for row in table["rows"]:
        if wide:
            label, value, prev, delta = row[0], row[1], row[2], row[3]
            sub = (f'<span class="stat-prev">지난 기간 {esc(prev)}</span>'
                   if prev and prev not in ("—", "-") else "")
            cards.append(f"""<div class="stat">
  <p class="stat-label">{esc(label)}</p>
  <p class="stat-value">{inline(value)}</p>
  <p class="stat-sub">{sub}{delta_chip(delta)}</p>
</div>""")
        else:
            label, value = row[0], row[1]
            cards.append(f"""<div class="stat">
  <p class="stat-label">{esc(label)}</p>
  <p class="stat-value">{inline(value)}</p>
</div>""")
    return f'<div class="stat-grid">{"".join(cards)}</div>'


def render_faq(section: list[tuple[str, object]], page: Page) -> str:
    items = []
    question = None
    answer: list[str] = []

    def flush():
        nonlocal question, answer
        if question is None:
            return
        body = "".join(answer)
        items.append(f"""<details class="faq-item">
  <summary><span class="q">{inline(question, page)}</span></summary>
  <div class="a">{body}</div>
</details>""")
        question, answer = None, []

    for kind, val in section:
        if kind == "h3":
            flush()
            question = str(val)
        elif kind == "p":
            answer.append(f"<p>{inline(str(val), page)}</p>")
        elif kind == "ul":
            answer.append("<ul>" + "".join(f"<li>{inline(v, page)}</li>" for v in val) + "</ul>")
        elif kind == "table":
            answer.append(render_table(val, page))
        elif kind == "h4":
            answer.append(f"<h4>{inline(str(val), page)}</h4>")
    flush()
    return "".join(items)


def render_source(section: list[tuple[str, object]], page: Page) -> str:
    blocks = []
    for kind, val in section:
        if kind == "ul":
            items = []
            for item in val:
                text = str(item)
                if "시뮬레이션" in text or "표본" in text:
                    items.append(f'<li class="warn">{inline(text, page)}</li>')
                else:
                    items.append(f"<li>{inline(text, page)}</li>")
            blocks.append('<ul class="source-list">' + "".join(items) + "</ul>")
        elif kind == "p":
            blocks.append(f"<p>{inline(str(val), page)}</p>")
        elif kind == "table":
            blocks.append(render_table(val, page))
    return f'<div class="source-box">{"".join(blocks)}</div>'


def render_related(section: list[tuple[str, object]], page: Page) -> str:
    cards = []
    for kind, val in section:
        if kind != "ul":
            continue
        for item in val:
            text = str(item)
            m = re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", text.strip())
            if m:
                cards.append(f'<a class="rel-card" href="{esc(rewrite_url(m.group(2), page))}">'
                             f'<span>{esc(m.group(1))}</span><i aria-hidden="true">→</i></a>')
            else:
                cards.append(f'<span class="rel-card">{inline(text, page)}</span>')
    return f'<div class="rel-grid">{"".join(cards)}</div>'


def render_section(sid: str, title: str, section: list[tuple[str, object]], page: Page) -> str:
    inner: list[str] = []
    toc_title = title

    if any(key in title for key in STAT_SECTIONS):
        table = next((v for k, v in section if k == "table"), None)
        if table:
            inner.append(stat_cards(table, page))
        for kind, val in section:
            if kind == "table":
                continue
            inner.append(render_block(kind, val, page))
    elif "자주 묻는 질문" in title:
        inner.append(render_faq(section, page))
    elif "출처" in title:
        inner.append(render_source(section, page))
    elif "함께 보면" in title:
        inner.append(render_related(section, page))
    elif "산출 방법" in title:
        inner.append('<div class="method-box">')
        for kind, val in section:
            inner.append(render_block(kind, val, page))
        inner.append("</div>")
    else:
        for kind, val in section:
            inner.append(render_block(kind, val, page))

    return (f'<section class="prose-sec" id="{sid}" aria-labelledby="{sid}-t">'
            f'<h2 id="{sid}-t">{inline(toc_title, page)}</h2>{"".join(inner)}</section>')


def render_block(kind: str, val, page: Page) -> str:
    if kind == "p":
        return f"<p>{inline(str(val), page)}</p>"
    if kind == "quote":
        return f'<blockquote class="callout">{inline(str(val), page)}</blockquote>'
    if kind == "ul":
        return "<ul>" + "".join(f"<li>{inline(str(v), page)}</li>" for v in val) + "</ul>"
    if kind == "ol":
        return "<ol>" + "".join(f"<li>{inline(str(v), page)}</li>" for v in val) + "</ol>"
    if kind == "table":
        return render_table(val, page)
    if kind == "h3":
        return f"<h3>{inline(str(val), page)}</h3>"
    if kind == "h4":
        return f"<h4>{inline(str(val), page)}</h4>"
    if kind == "hr":
        return "<hr>"
    if kind == "html":
        return str(val)
    return ""


def abs_url(site_url: str, rel: str) -> str:
    """'index.html#regions' → '{site_url}/#regions' (사이트 루트 기준 절대 URL)."""
    rel = rel.replace("../", "")
    if rel.startswith("index.html"):
        rel = rel[len("index.html"):]
        return f"{site_url}/{rel.lstrip('/')}"
    return f"{site_url}/{rel.lstrip('/')}"


def breadcrumb(page: Page, root: str) -> list[tuple[str, str]]:
    home = ("홈", f"{root}index.html")
    if page.scope == "region":
        return [home, ("지역별", f"{root}index.html#regions"), (page.region, "")]
    if page.scope == "field":
        return [home, ("분야별", f"{root}index.html#fields"), (page.field_name or page.region, "")]
    if page.scope == "top":
        return [home, ("예산 순위", f"{root}index.html#top10"), ("상위 10선", "")]
    return [home, ("전국 브리핑", "")]


def render_detail(page: Page, site_url: str, built_at: str, total_pages: int = 0) -> str:
    root = "../"
    canonical = f"{site_url}/bids/{page.slug}.html"
    crumbs = breadcrumb(page, root)
    crumb_html = "".join(
        (f'<a href="{esc(url)}">{esc(label)}</a><span aria-hidden="true">/</span>'
         if url else f'<span aria-current="page">{esc(label)}</span>')
        for label, url in crumbs
    )

    lead = ""
    body_blocks: list[tuple[str, object]] = []
    for kind, val in page.blocks:
        if kind == "quote" and not lead:
            lead = str(val)
            continue
        body_blocks.append((kind, val))

    # 목차 + 본문
    sections: list[str] = []
    toc: list[tuple[str, str]] = []
    sec_idx = 0
    i = 0
    pre_blocks: list[str] = []
    while i < len(body_blocks):
        kind, val = body_blocks[i]
        if kind == "h2":
            j = i + 1
            section: list[tuple[str, object]] = []
            while j < len(body_blocks) and body_blocks[j][0] != "h2":
                section.append(body_blocks[j])
                j += 1
            if "함께 보면" not in str(val):
                sec_idx += 1
                sid = f"s{sec_idx}"
                toc.append((sid, str(val)))
                sections.append(render_section(sid, str(val), section, page))
            else:
                sec_idx += 1
                sections.append(render_section(f"s{sec_idx}", str(val), section, page))
            i = j
            continue
        pre_blocks.append(render_block(kind, val, page))
        i += 1

    toc_html = "".join(f'<li><a href="#{sid}">{esc(t)}</a></li>' for sid, t in toc)
    toc_block = (f'<aside class="toc" aria-label="목차"><p class="toc-title">목차</p>'
                 f'<ol>{toc_html}</ol></aside>' if len(toc) >= 3 else "")

    sim_badge = ('<span class="badge badge-sim">시뮬레이션 표본</span>'
                 if page.is_sim else '<span class="badge badge-live">실데이터</span>')
    scopes = {"national": "전국 브리핑", "top": "예산 순위",
              "region": f"지역별 · {page.region}",
              "field": f"분야별 · {page.field_name or page.region}"}
    scope_label = scopes.get(page.scope, page.category or "리포트")

    reading = max(2, round(page.body_chars / 850))

    crumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": idx + 1, "name": label,
             **({"item": abs_url(site_url, url)} if url else {})}
            for idx, (label, url) in enumerate(crumbs)
        ],
    }
    ld_blocks = ""
    if page.ld_json:
        ld_blocks += f'<script type="application/ld+json">\n{page.ld_json}\n</script>\n'
    ld_blocks += (f'<script type="application/ld+json">'
                  f'{json.dumps(crumb_ld, ensure_ascii=False)}</script>\n')

    extra = (f'<meta property="article:published_time" content="{esc(page.pub)}">\n'
             f'<meta property="article:modified_time" content="{esc(page.updated)}">\n'
             f'<meta property="article:section" content="{esc(page.category)}">\n')

    head = head_html(title=f"{page.title} — {SITE_NAME}", description=page.description,
                     canonical=canonical, root=root, is_article=True, extra=extra, ld=ld_blocks)

    tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in page.tags[:6])

    return f"""{head}
<body class="detail">
{site_header(root, page.scope)}
<main id="main">
  <div class="wrap">
    <nav class="crumbs" aria-label="위치">{crumb_html}</nav>

    <article class="article">
      <header class="article-head">
        <p class="kicker">{esc(scope_label)}</p>
        <h1>{esc(page.title)}</h1>
        <p class="article-meta">
          <span>{esc(fmt_date(page.as_of or page.updated))} 기준</span>
          <span class="dot" aria-hidden="true">·</span>
          <span>약 {reading}분</span>
          <span class="dot" aria-hidden="true">·</span>
          {sim_badge}
        </p>
        <div class="actions">
          <button type="button" class="btn" data-copy>링크 복사</button>
          <button type="button" class="btn" data-share>공유하기</button>
          <button type="button" class="btn btn-ghost" data-print>인쇄</button>
        </div>
      </header>

      {f'<p class="lead">{inline(lead, page)}</p>' if lead else ''}
      <div class="pre-blocks">{''.join(pre_blocks)}</div>

      <div class="layout{' with-toc' if toc_block else ''}">
        <div class="article-body">{''.join(sections)}</div>
        {toc_block}
      </div>

      <footer class="article-foot">
        <p class="tags">{tags_html}</p>
        <p class="article-note">이 리포트는 {esc(page.source)} 데이터를 코드로 집계해 자동 생성했습니다.
        원문 공고와 금액이 다를 수 있으니 입찰 전 나라장터 공고문을 확인하세요.</p>
        <div class="actions">
          <button type="button" class="btn" data-copy>링크 복사</button>
          <button type="button" class="btn" data-share>공유하기</button>
        </div>
      </footer>
    </article>
  </div>
</main>
{site_footer(root, built_at, total_pages, page.is_sim)}
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
{SHARE_JS}
</script>
</body>
</html>
"""


SHARE_JS = r"""(function () {
  var toast = document.getElementById('toast'), timer;
  function show(msg) {
    if (!toast) return;
    toast.textContent = msg; toast.classList.add('on');
    clearTimeout(timer); timer = setTimeout(function () { toast.classList.remove('on'); }, 2000);
  }
  function fallback(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', '');
    ta.style.position = 'fixed'; ta.style.top = '-1000px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); show('링크를 복사했습니다'); }
    catch (e) { show('복사가 막혔습니다. 주소창의 주소를 복사해 주세요'); }
    document.body.removeChild(ta);
  }
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { show('링크를 복사했습니다'); },
                                              function () { fallback(text); });
    } else { fallback(text); }
  }
  document.addEventListener('click', function (e) {
    var el = e.target;
    while (el && el !== document.body && !(el.dataset && (el.dataset.copy !== undefined ||
           el.dataset.share !== undefined || el.dataset.print !== undefined))) { el = el.parentElement; }
    if (!el || el === document.body) return;
    if (el.dataset.copy !== undefined) { copy(window.location.href); return; }
    if (el.dataset.print !== undefined) { window.print(); return; }
    if (el.dataset.share !== undefined) {
      if (navigator.share) {
        navigator.share({ title: document.title, url: window.location.href }).catch(function () {});
      } else { copy(window.location.href); }
    }
  });
})();"""


# ==========================================================================
# 6. 메인 페이지
# ==========================================================================
def index_body(pages: list[Page], site_url: str, built_at: str) -> str:
    nat = next((p for p in pages if p.scope == "national"), None)
    top = next((p for p in pages if p.scope == "top"), None)
    regions = [p for p in pages if p.scope == "region"]
    fields = [p for p in pages if p.scope == "field"]
    sim = any(p.is_sim for p in pages)

    # --- 하이라이트 배너 ---------------------------------------------------
    if nat:
        m = nat.metrics()
        count = m.get("공고 건수", {})
        total = m.get("총 배정예산", {})
        avg = m.get("평균 배정예산", {})
        soon = m.get("마감 3일 이내", {})
        hero_stats = []
        for label, key, big in (("공고 건수", count, True), ("총 배정예산", total, True),
                                ("평균 배정예산", avg, False), ("마감 3일 이내", soon, False)):
            if not key:
                continue
            prev_html = (f'<span class="stat-prev">지난 기간 {esc(key.get("prev"))}</span>'
                         if key.get("prev") and key.get("prev") not in ("—", "-") else "")
            hero_stats.append(f"""<div class="hstat">
  <p class="hstat-label">{esc(label)}</p>
  <p class="hstat-value">{esc(key.get("value", ""))}</p>
  <p class="hstat-sub">{prev_html}{delta_chip(key.get("delta", ""))}</p>
</div>""")
        hero_h = (f'<h1><small>이번 주 전국 입찰공고</small>'
                  f'<span class="h1-num">{esc(count.get("value", ""))}</span> · '
                  f'<span class="h1-num">{esc(total.get("value", ""))}</span></h1>')
        hero_lead = esc(nat.description)
        hero_window = nat.window_label
        hero_link = f'<a class="hero-cta" href="bids/{esc(nat.slug)}.html">전국 브리핑 자세히 보기 <span aria-hidden="true">→</span></a>'
    else:
        hero_stats, hero_h = [], '<h1>나라장터 입찰공고 주간 동향</h1>'
        hero_lead = "지역·분야별 입찰공고 동향 리포트입니다."
        hero_window = ""
        hero_link = ""

    # --- TOP 10 ------------------------------------------------------------
    rank_cards = []
    top_section = top.table_in_section("배정예산 상위 공고") if top else None
    if top_section:
        rows = top_section["rows"][:10]
        amounts = [parse_amount(r[6] if len(r) > 6 else "") for r in rows]
        peak = max(amounts) if amounts else 0
        for idx, row in enumerate(rows):
            rank = row[0] if len(row) > 0 else str(idx + 1)
            name = row[1] if len(row) > 1 else ""
            org = row[2] if len(row) > 2 else ""
            cat = row[3] if len(row) > 3 else ""
            close = row[5] if len(row) > 5 else ""
            budget = row[6] if len(row) > 6 else ""
            width = int(amounts[idx] / peak * 100) if peak and idx < len(amounts) else 0
            rank_cards.append(f"""<li class="rank-card">
  <span class="rank-no">{esc(rank)}</span>
  <div class="rank-body">
    <p class="rank-name">{esc(name)}</p>
    <p class="rank-meta">{esc(org)} <span class="sep" aria-hidden="true">·</span> <span class="tag">{esc(cat)}</span> <span class="sep" aria-hidden="true">·</span> 마감 {esc(close)}</p>
  </div>
  <div class="rank-amount">
    <b>{esc(budget)}</b>
    <span class="bar" aria-hidden="true"><i style="width:{width}%"></i></span>
  </div>
</li>""")

    # --- 지역 --------------------------------------------------------------
    region_cards = []
    reg_sorted = sorted(regions, key=lambda p: -parse_amount(
        (p.metrics().get("총 배정예산") or {}).get("value", "")))
    for p in reg_sorted:
        m = p.metrics()
        cnt = (m.get("공고 건수") or {}).get("value", "")
        bud = (m.get("총 배정예산") or {}).get("value", "")
        dl = (m.get("공고 건수") or {}).get("delta", "")
        region_cards.append(f"""<a class="region-card" href="bids/{esc(p.slug)}.html">
  <span class="region-top"><span class="region-name">{esc(p.region)}</span>{delta_chip(dl)}</span>
  <span class="region-line"><b>{esc(cnt)}</b> <span class="sep" aria-hidden="true">·</span> {esc(bud)}</span>
</a>""")
    missing = [r for r in REGIONS if r not in {p.region for p in regions}]

    # --- 분야 --------------------------------------------------------------
    field_chips = []
    for p in sorted(fields, key=lambda p: -parse_amount(
            (p.metrics().get("총 배정예산") or {}).get("value", ""))):
        m = p.metrics()
        cnt = (m.get("공고 건수") or {}).get("value", "")
        bud = (m.get("총 배정예산") or {}).get("value", "")
        field_chips.append(f"""<a class="chip" href="bids/{esc(p.slug)}.html">
  <span class="chip-name">{esc(p.field_name or p.region)}</span>
  <span class="chip-num">{esc(cnt)}</span>
  <span class="chip-budget">{esc(bud)}</span>
</a>""")

    latest = max((p.updated for p in pages), default=built_at)
    sim_note = ('<p class="sim-note"><b>시뮬레이션 표본</b> — 현재 사이트의 모든 수치는 API 키 없이 만든 '
                '표본 데이터입니다. 실제 입찰공고가 아닙니다.</p>' if sim else "")

    ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": SITE_NAME, "url": f"{site_url}/",
             "inLanguage": "ko-KR", "description": "지역·분야별 나라장터 입찰공고 주간 동향 리포트",
             "publisher": {"@type": "Organization", "name": SITE_NAME}},
            {"@type": "ItemList", "name": "배정예산 상위 입찰공고",
             "itemListElement": [
                 {"@type": "ListItem", "position": i + 1,
                  "name": (r[1] if len(r) > 1 else "")}
                 for i, r in enumerate((top_section or {}).get("rows", [])[:10])]},
        ],
    }
    ld_html = f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'

    title = f"{SITE_NAME} — 나라장터 입찰공고 주간 동향 리포트"
    desc = (nat.description if nat else
            "지역·분야별 나라장터 입찰공고 동향을 매일 자동으로 정리한 리포트 모음입니다.")
    head = head_html(title=title, description=desc, canonical=f"{site_url}/", root="", ld=ld_html)

    return f"""{head}
<body class="home">
{site_header('', 'national')}
<main id="main">

  <section class="hero" id="highlight">
    <div class="wrap hero-inner">
      <p class="eyebrow">주간 브리핑{f' · {esc(hero_window)}' if hero_window else ''}</p>
      {hero_h}
      <p class="hero-lead">{hero_lead}</p>
      <div class="hero-stats">{''.join(hero_stats)}</div>
      {hero_link}
      {sim_note}
    </div>
  </section>

  <section class="section" id="top10">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>이번 주 배정예산 TOP 10</h2>
          <p class="section-sub">게시일이 집계 기간에 속하는 공고 중 배정예산이 큰 순서입니다.</p>
        </div>
        {f'<a class="section-link" href="bids/{esc(top.slug)}.html">전체 목록 <span aria-hidden="true">→</span></a>' if top else ''}
      </div>
      <ol class="rank-list">{''.join(rank_cards) or '<li class="empty">이번 기간 상위 공고가 없습니다.</li>'}</ol>
    </div>
  </section>

  <section class="section section-alt" id="regions">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>지역별 입찰 동향</h2>
          <p class="section-sub">기관 소재지 기준으로 묶은 결과입니다. 예산 규모가 큰 순서로 배치했습니다.</p>
        </div>
        <span class="section-count">{len(regions)}개 시도</span>
      </div>
      <div class="region-grid">{''.join(region_cards)}</div>
      {f'<p class="grid-note">표본이 부족해 이번 주에 생성되지 않은 지역: {esc(", ".join(missing))}</p>' if missing else ''}
    </div>
  </section>

  <section class="section" id="fields">
    <div class="wrap">
      <div class="section-head">
        <div>
          <h2>분야별 입찰 동향</h2>
          <p class="section-sub">업종명과 공고명 키워드로 분류한 참고 기준입니다(공식 통계 분류 아님).</p>
        </div>
        <span class="section-count">{len(fields)}개 분야</span>
      </div>
      <div class="field-cloud">{''.join(field_chips)}</div>
    </div>
  </section>

  <section class="section section-alt" id="about">
    <div class="wrap about">
      <div class="about-copy">
        <h2>이 사이트는 이렇게 만들어집니다</h2>
        <p>공공데이터포털의 조달청 나라장터 입찰공고정보서비스를 매일 수집해, 지역·분야별로 집계한
        리포트를 자동 발행합니다. 본문의 모든 숫자는 원본 공고 필드에서 코드로 계산하며,
        문장을 다듬는 과정에서 새 숫자를 만들지 않습니다.</p>
        <ol class="steps">
          <li><b>수집</b><span>공사·용역·물품 공고를 최근 62일 범위로 수집해 SQLite에 누적합니다.</span></li>
          <li><b>계산</b><span>건수·합계·평균·중위값·증감률·구간 분포를 룰 기반으로 계산합니다.</span></li>
          <li><b>검증</b><span>9종 품질 게이트(수치 일치·교차검증·중복·출처)를 통과한 글만 발행합니다.</span></li>
          <li><b>발행</b><span>마크다운을 정적 HTML로 빌드해 사이트맵과 함께 배포합니다.</span></li>
        </ol>
      </div>
      <aside class="about-card">
        <p class="about-card-label">최근 업데이트</p>
        <p class="about-card-value">{esc(fmt_dt(latest))}</p>
        <dl class="about-list">
          <div><dt>발행 리포트</dt><dd>{len(pages)}장</dd></div>
          <div><dt>데이터 모드</dt><dd>{'시뮬레이션 표본' if sim else '실데이터'}</dd></div>
          <div><dt>갱신 주기</dt><dd>매일 1회</dd></div>
          <div><dt>데이터 출처</dt><dd><a href="{esc(SOURCE['url'])}" rel="noopener" target="_blank">공공데이터포털</a></dd></div>
        </dl>
        <p class="about-note">빌드 {esc(fmt_dt(built_at))}</p>
      </aside>
    </div>
  </section>

</main>
{site_footer('', built_at, len(pages), sim)}
</body>
</html>
"""


# ==========================================================================
# 7. 사이트맵 · 로봇 · 404 · 자산
# ==========================================================================
def write_sitemap(site_dir: Path, site_url: str, pages: list[Page], built_at: str) -> int:
    items = [f"  <url><loc>{esc(site_url)}/</loc><lastmod>{built_at[:10]}</lastmod>"
             f"<changefreq>daily</changefreq><priority>1.0</priority></url>"]
    for p in pages:
        lastmod = (p.updated or built_at)[:10]
        items.append(f"  <url><loc>{esc(site_url)}/bids/{esc(p.slug)}.html</loc>"
                     f"<lastmod>{lastmod}</lastmod><changefreq>daily</changefreq>"
                     f"<priority>0.8</priority></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(items) + "\n</urlset>\n")
    (site_dir / "sitemap.xml").write_text(xml, encoding="utf-8")
    return len(items)


def write_robots(site_dir: Path, site_url: str) -> None:
    (site_dir / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {site_url}/sitemap.xml\n",
        encoding="utf-8")


def write_404(site_dir: Path, site_url: str, built_at: str = "") -> None:
    head = head_html(title=f"페이지를 찾을 수 없습니다 — {SITE_NAME}",
                     description="요청한 페이지가 없습니다. 전국·지역별·분야별 입찰 동향 리포트로 이동하세요.",
                     canonical=f"{site_url}/404.html", root="")
    (site_dir / "404.html").write_text(f"""{head}
<body class="home">
{site_header('', '')}
<main id="main">
  <div class="wrap notfound">
    <p class="kicker">404</p>
    <h1>페이지를 찾을 수 없습니다</h1>
    <p>주소가 바뀌었거나, 아직 발행되지 않은 리포트입니다.</p>
    <p><a class="hero-cta" href="index.html">홈으로 이동 <span aria-hidden="true">→</span></a></p>
  </div>
</main>
{site_footer('', built_at, 0, False)}
</body>
</html>
""", encoding="utf-8")


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="입찰동향 브리핑">
<rect width="64" height="64" rx="15" fill="#14459c"/>
<rect x="14" y="34" width="8" height="16" rx="3" fill="#ffffff"/>
<rect x="28" y="23" width="8" height="27" rx="3" fill="#9cc0ff"/>
<rect x="42" y="13" width="8" height="37" rx="3" fill="#ffffff"/>
</svg>
"""


def write_assets(site_dir: Path) -> None:
    assets = site_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "style.css").write_text(CSS, encoding="utf-8")
    (assets / "favicon.svg").write_text(FAVICON, encoding="utf-8")


# ==========================================================================
# 8. 스타일
# ==========================================================================
CSS = r"""/* 입찰동향 브리핑 — 정적 HTML 스타일 (외부 의존 없음) */
:root{
  --bg:#f5f7fa; --surface:#ffffff; --surface-2:#fafbfd; --ink:#111827; --ink-2:#374151;
  --muted:#6b7280; --line:#e4e8ef; --brand:#14459c; --brand-2:#2f62c9; --brand-weak:#eaf1fe;
  --accent:#b4470f; --accent-weak:#fff2ea;
  --up:#c92a2a; --down:#1c62b9; --flat:#8b93a1;
  --radius:16px; --radius-sm:10px;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 10px 28px -18px rgba(16,24,40,.25);
  --max:1120px;
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth;scroll-padding-top:78px}
body{margin:0;background:var(--bg);color:var(--ink);font-size:16px;line-height:1.65;
  font-family:"Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
a{color:var(--brand-2);text-decoration:none}
a:hover{text-decoration:underline}
h1,h2,h3,h4{line-height:1.32;letter-spacing:-.015em;margin:0}
p{margin:0}
img,svg{max-width:100%}
ul,ol{margin:0;padding:0;list-style:none}
button{font:inherit}
.wrap{width:100%;max-width:var(--max);margin:0 auto;padding:0 20px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.skip{position:absolute;left:-9999px;top:0;background:#fff;color:var(--brand);padding:10px 16px;z-index:200;border-radius:0 0 8px 0}
.skip:focus{left:0;top:0}

/* ---------- 헤더 ---------- */
.site-head{position:sticky;top:0;z-index:60;background:rgba(255,255,255,.92);
  backdrop-filter:saturate(180%) blur(12px);-webkit-backdrop-filter:saturate(180%) blur(12px);
  border-bottom:1px solid var(--line)}
.head-inner{display:flex;align-items:center;justify-content:space-between;gap:16px;height:66px}
.brand{display:flex;align-items:center;gap:11px;color:var(--ink)}
.brand:hover{text-decoration:none}
.brand-mark{flex:0 0 auto;border-radius:9px;display:block}
.brand-text{display:flex;flex-direction:column;line-height:1.25}
.brand-text strong{font-size:16.5px;font-weight:750;letter-spacing:-.02em}
.brand-text small{font-size:11.5px;color:var(--muted);letter-spacing:-.01em}
.site-nav{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.site-nav::-webkit-scrollbar{display:none}
.site-nav a{color:var(--ink-2);font-size:14.5px;font-weight:600;padding:8px 11px;border-radius:9px;white-space:nowrap}
.site-nav a:hover{background:var(--brand-weak);color:var(--brand);text-decoration:none}
.site-nav a.on{color:var(--brand);background:var(--brand-weak)}

/* ---------- 히어로 ---------- */
.hero{position:relative;overflow:hidden;color:#fff;padding:54px 0 46px;
  background:linear-gradient(135deg,#0b2250 0%,#14459c 52%,#1c56bd 100%)}
.hero::before{content:"";position:absolute;inset:0;opacity:.45;pointer-events:none;
  background:radial-gradient(900px 320px at 82% -10%,rgba(255,255,255,.28),transparent 62%),
             radial-gradient(520px 260px at 6% 120%,rgba(120,170,255,.35),transparent 65%)}
.hero::after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.12;
  background-image:linear-gradient(rgba(255,255,255,.6) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(255,255,255,.6) 1px,transparent 1px);
  background-size:44px 44px;mask-image:linear-gradient(180deg,#000,transparent 72%);
  -webkit-mask-image:linear-gradient(180deg,#000,transparent 72%)}
.hero-inner{position:relative;z-index:1}
.eyebrow{display:inline-block;font-size:12.5px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:#cfe0ff;background:rgba(255,255,255,.14);
  border:1px solid rgba(255,255,255,.22);padding:5px 12px;border-radius:999px}
.hero h1{margin:16px 0 12px;font-size:clamp(27px,5.2vw,44px);font-weight:800;letter-spacing:-.03em}
.hero h1 small{display:block;margin-bottom:6px;font-size:clamp(13px,1.7vw,15.5px);font-weight:700;
  letter-spacing:.02em;color:#cfe0ff}
.h1-num{white-space:nowrap}
.hero-lead{max-width:58ch;color:#dbe7ff;font-size:clamp(14.5px,1.6vw,16.5px);line-height:1.72}
.hero-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:26px 0 22px}
.hstat{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);
  border-radius:14px;padding:14px 15px;backdrop-filter:blur(4px)}
.hstat-label{font-size:12px;font-weight:600;color:#c5d8ff;letter-spacing:.02em}
.hstat-value{margin-top:5px;font-size:clamp(17px,2.2vw,23px);font-weight:750;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1.28}
.hstat-sub{margin-top:7px;display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:11.5px;color:#c8d9ff}
.hero-cta{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:14.5px;
  color:#0b2250;background:#fff;padding:11px 18px;border-radius:999px;box-shadow:0 8px 20px -10px rgba(0,0,0,.6)}
.hero-cta:hover{text-decoration:none;transform:translateY(-1px)}
.sim-note{margin-top:18px;font-size:12.5px;color:#ffd9c2;background:rgba(180,71,15,.22);
  border:1px solid rgba(255,190,150,.35);padding:9px 13px;border-radius:10px;display:inline-block}
.sim-note b{color:#ffc9a3}
.hero .delta.up{color:#ffc7c7;background:rgba(255,130,130,.2)}
.hero .delta.down{color:#c2dbff;background:rgba(150,195,255,.2)}
.hero .delta.flat{color:#dbe3f0;background:rgba(255,255,255,.15)}
.hero .stat-prev{color:#bfd3f7}

/* ---------- 공통 섹션 ---------- */
.section{padding:46px 0}
.section-alt{background:var(--surface);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.section-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:20px}
.section-head h2{font-size:clamp(19px,2.4vw,23px);font-weight:800}
.section-sub{margin-top:6px;color:var(--muted);font-size:13.5px;max-width:66ch}
.section-link{font-size:13.5px;font-weight:700;white-space:nowrap}
.section-count{font-size:12.5px;font-weight:700;color:var(--brand);background:var(--brand-weak);
  padding:4px 11px;border-radius:999px;white-space:nowrap}
.grid-note{margin-top:14px;font-size:12.5px;color:var(--muted)}

/* ---------- 순위 카드 ---------- */
.rank-list{display:grid;gap:9px}
.rank-card{display:grid;grid-template-columns:44px minmax(0,1fr) auto;gap:14px;align-items:center;
  background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 18px;
  box-shadow:var(--shadow);transition:border-color .15s,transform .15s}
.rank-card:hover{border-color:#c9d8f5;transform:translateY(-1px)}
.rank-no{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;
  background:var(--brand-weak);color:var(--brand);font-weight:800;font-size:15px;font-variant-numeric:tabular-nums}
.rank-card:nth-child(1) .rank-no{background:#14459c;color:#fff}
.rank-card:nth-child(2) .rank-no{background:#2f62c9;color:#fff}
.rank-card:nth-child(3) .rank-no{background:#4a7ad8;color:#fff}
.rank-body{min-width:0}
.rank-name{font-size:15.5px;font-weight:700;letter-spacing:-.01em}
.rank-meta{margin-top:3px;font-size:12.5px;color:var(--muted)}
.rank-amount{text-align:right;min-width:150px}
.rank-amount b{font-size:15px;font-weight:750;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{display:block;margin-top:7px;height:4px;border-radius:999px;background:#eef1f6;overflow:hidden}
.bar i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#2f62c9,#7ba5ee)}
.tag{display:inline-block;font-size:11.5px;font-weight:600;color:var(--brand);background:var(--brand-weak);
  padding:2px 8px;border-radius:6px;letter-spacing:-.01em}
.sep{color:#c3c9d4}
.empty{color:var(--muted);font-size:14px;padding:18px}

/* ---------- 지역 그리드 ---------- */
.region-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(206px,1fr));gap:11px}
.region-card{display:flex;flex-direction:column;gap:7px;background:var(--surface);
  border:1px solid var(--line);border-radius:13px;padding:14px 15px;color:var(--ink);
  box-shadow:var(--shadow);transition:border-color .15s,transform .15s}
.region-card:hover{border-color:#c9d8f5;text-decoration:none;transform:translateY(-1px)}
.region-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.region-name{font-size:16.5px;font-weight:800;letter-spacing:-.02em}
.region-line{display:block;font-size:12.5px;color:var(--muted);line-height:1.5}
.region-line b{color:var(--ink-2);font-size:13.5px}

/* ---------- 분야 칩 ---------- */
.field-cloud{display:flex;flex-wrap:wrap;gap:9px}
.chip{display:inline-flex;align-items:baseline;gap:8px;background:var(--surface);
  border:1px solid var(--line);border-radius:999px;padding:9px 15px;color:var(--ink);
  box-shadow:var(--shadow);transition:border-color .15s,background .15s}
.chip:hover{border-color:#b9cdf2;background:#f8fbff;text-decoration:none}
.chip-name{font-size:14px;font-weight:700}
.chip-num{font-size:12.5px;font-weight:700;color:var(--brand)}
.chip-budget{font-size:12px;color:var(--muted);padding-left:8px;border-left:1px solid var(--line)}

/* ---------- 델타 칩 ---------- */
.delta{display:inline-flex;align-items:center;font-size:11.5px;font-weight:700;
  padding:1px 7px;border-radius:999px;font-variant-numeric:tabular-nums;white-space:nowrap}
.delta.up{color:var(--up);background:rgba(201,42,42,.09)}
.delta.down{color:var(--down);background:rgba(28,98,185,.1)}
.delta.flat{color:var(--flat);background:rgba(139,147,161,.12)}

/* ---------- 자동화 소개 ---------- */
.about{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(260px,.85fr);gap:32px;align-items:start}
.about-copy h2{font-size:clamp(19px,2.4vw,23px);font-weight:800;margin-bottom:12px}
.about-copy p{color:var(--ink-2);font-size:14.5px;line-height:1.75}
.steps{display:grid;gap:10px;margin-top:20px;counter-reset:step}
.steps li{position:relative;padding:12px 15px 12px 48px;background:var(--surface);
  border:1px solid var(--line);border-radius:12px;font-size:13.5px;color:var(--muted)}
.steps li::before{counter-increment:step;content:counter(step);position:absolute;left:14px;top:12px;
  width:22px;height:22px;display:grid;place-items:center;border-radius:7px;
  background:var(--brand-weak);color:var(--brand);font-size:12px;font-weight:800}
.steps b{display:block;color:var(--ink);font-size:14px;margin-bottom:1px}
.about-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:20px;box-shadow:var(--shadow);position:sticky;top:86px}
.about-card-label{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.about-card-value{margin-top:4px;font-size:18px;font-weight:800;letter-spacing:-.02em}
.about-list{margin:16px 0 0;display:grid;gap:9px;font-size:13.5px}
.about-list div{display:flex;justify-content:space-between;gap:12px;padding-bottom:8px;border-bottom:1px dashed var(--line)}
.about-list div:last-child{border-bottom:0;padding-bottom:0}
.about-list dt{color:var(--muted)}
.about-list dd{margin:0;font-weight:700;text-align:right}
.about-note{margin-top:14px;font-size:11.5px;color:var(--muted)}

/* ---------- 푸터 ---------- */
.site-foot{background:#0d1b33;color:#c6d2e6;margin-top:20px;padding:34px 0 22px;font-size:13.5px}
.foot-inner{display:flex;justify-content:space-between;gap:26px;flex-wrap:wrap;padding-bottom:20px;
  border-bottom:1px solid rgba(255,255,255,.12)}
.foot-brand{display:flex;gap:12px;align-items:flex-start}
.foot-brand strong{color:#fff;font-size:15.5px;font-weight:750}
.foot-brand p{margin-top:4px;color:#93a6c4;font-size:12.5px;max-width:36ch}
.foot-meta p{margin:0 0 4px;color:#93a6c4;font-size:12.5px}
.foot-meta a{color:#bcd2f5}
.foot-note{padding-top:14px}
.foot-note p{color:#8b9cb8;font-size:12px;line-height:1.7;max-width:92ch}
.site-foot a{color:#bcd2f5}

/* ---------- 상세 페이지 ---------- */
.crumbs{display:flex;flex-wrap:wrap;align-items:center;gap:7px;padding:18px 0 0;font-size:12.5px;color:var(--muted)}
.crumbs a{color:var(--muted)}
.crumbs a:hover{color:var(--brand)}
.crumbs [aria-current]{color:var(--ink-2);font-weight:700}
.article{padding:8px 0 40px}
.article-head{padding:16px 0 20px;border-bottom:1px solid var(--line)}
.kicker{display:inline-block;font-size:12px;font-weight:800;letter-spacing:.05em;color:var(--brand);
  background:var(--brand-weak);padding:4px 10px;border-radius:7px}
.article-head h1{margin:13px 0 12px;font-size:clamp(23px,3.6vw,34px);font-weight:800;letter-spacing:-.03em;line-height:1.28}
.article-meta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.badge{font-size:11.5px;font-weight:700;padding:2px 9px;border-radius:999px}
.badge-sim{color:var(--accent);background:var(--accent-weak)}
.badge-live{color:#1f7a45;background:#e7f6ec}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--surface);border:1px solid var(--line);
  color:var(--ink-2);font-size:13px;font-weight:650;padding:7px 14px;border-radius:999px;
  cursor:pointer;transition:border-color .15s,color .15s,background .15s}
.btn:hover{border-color:#b9cdf2;color:var(--brand);background:#f7faff}
.btn-ghost{background:transparent}
.lead{margin:22px 0 4px;padding:16px 18px;background:var(--surface);border-left:3px solid var(--brand-2);
  border-radius:0 12px 12px 0;color:var(--ink-2);font-size:15px;line-height:1.75;box-shadow:var(--shadow)}
.pre-blocks p{margin:18px 0 4px;color:var(--ink-2);font-size:15.5px;line-height:1.82}
.pre-blocks p strong{color:var(--ink)}
.dot{color:#b9c1cf}
.layout{display:block;margin-top:8px}
.layout.with-toc{display:grid;grid-template-columns:minmax(0,1fr) 232px;gap:38px;align-items:start}
.article-body{min-width:0}

/* 목차 */
.toc{position:sticky;top:86px;margin-top:34px;padding:16px;background:var(--surface);
  border:1px solid var(--line);border-radius:14px;font-size:13px}
.toc-title{margin:0 0 9px;font-size:11.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.toc ol{display:grid;gap:2px;counter-reset:toc}
.toc li{counter-increment:toc}
.toc a{display:block;padding:5px 8px 5px 26px;position:relative;color:var(--ink-2);border-radius:7px;line-height:1.45}
.toc a::before{content:counter(toc);position:absolute;left:6px;top:5px;font-size:10.5px;color:#a8b1c0;font-weight:700}
.toc a:hover{background:var(--brand-weak);color:var(--brand);text-decoration:none}

/* 본문 타이포그래피 */
.prose-sec{margin-top:38px}
.prose-sec > h2{font-size:clamp(18.5px,2.3vw,21.5px);font-weight:800;padding-left:13px;
  border-left:4px solid var(--brand);line-height:1.35;margin-bottom:14px}
.prose-sec h3{font-size:16.5px;font-weight:750;margin:22px 0 8px;color:var(--ink)}
.prose-sec h4{font-size:15px;font-weight:700;margin:18px 0 6px}
.prose-sec p{margin:13px 0;color:var(--ink-2);font-size:15.5px;line-height:1.82;letter-spacing:-.005em}
.prose-sec strong{color:var(--ink);font-weight:750}
.prose-sec code{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:.9em;
  background:#f1f4f9;border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.prose-sec ul,.prose-sec ol{margin:12px 0;display:grid;gap:7px}
.prose-sec ul li{position:relative;padding-left:15px;color:var(--ink-2);font-size:14.8px;line-height:1.72}
.prose-sec ul li::before{content:"";position:absolute;left:2px;top:.72em;width:5px;height:5px;
  border-radius:50%;background:#b9c9e6}
.prose-sec ol{counter-reset:item;padding-left:0}
.prose-sec ol li{position:relative;padding-left:26px;counter-increment:item;color:var(--ink-2);font-size:14.8px}
.prose-sec ol li::before{content:counter(item);position:absolute;left:0;top:.15em;width:18px;height:18px;
  display:grid;place-items:center;border-radius:5px;background:var(--brand-weak);color:var(--brand);
  font-size:11px;font-weight:800}
.callout{margin:18px 0;padding:14px 17px;background:var(--brand-weak);border-radius:12px;
  color:#1c3f7d;font-size:14.5px;line-height:1.72}
.prose-sec hr{border:0;border-top:1px solid var(--line);margin:26px 0}

/* 표 */
.table-wrap{margin:16px 0 6px;overflow-x:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
.data-table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:520px}
.data-table thead th{position:sticky;top:0;background:#f1f5fc;color:#33415c;font-weight:750;
  text-align:left;padding:10px 12px;border-bottom:1px solid #dfe6f2;white-space:nowrap;font-size:12.5px}
.data-table td{padding:10px 12px;border-bottom:1px solid #eef1f6;color:var(--ink-2);vertical-align:top;line-height:1.55}
.data-table tbody tr:nth-child(even){background:#fcfdff}
.data-table tbody tr:hover{background:#f6f9ff}
.data-table tbody tr:last-child td{border-bottom:0}
.data-table .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.data-table .rank-cell{color:#9aa3b2;font-weight:700;width:44px}
.data-table strong{color:var(--ink)}

/* 지표 카드 */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:11px;margin:16px 0 8px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:14px 15px;box-shadow:var(--shadow)}
.stat-label{font-size:12px;font-weight:700;color:var(--muted)}
.stat-value{margin-top:5px;font-size:clamp(16px,2vw,20px);font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1.3}
.stat-sub{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:7px;font-size:11.5px;color:var(--muted)}
.stat-prev{color:var(--muted)}

/* FAQ */
.faq-item{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  margin:9px 0;box-shadow:var(--shadow);overflow:hidden}
.faq-item summary{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;
  cursor:pointer;padding:15px 17px;font-weight:700;font-size:15px;color:var(--ink);
  list-style:none;position:relative}
.faq-item summary::-webkit-details-marker{display:none}
.faq-item summary .q{flex:1}
.faq-item summary::after{content:"+";flex:0 0 auto;font-size:19px;font-weight:400;color:var(--brand);
  line-height:1;transition:transform .18s}
.faq-item[open] summary::after{content:"−"}
.faq-item summary:hover{background:#f8fbff}
.faq-item .a{padding:0 17px 16px;border-top:1px solid #eef1f6}
.faq-item .a p{margin:11px 0 0;font-size:14.8px}

/* 출처 · 산출 방법 · 관련 */
.source-box{background:var(--surface-2);border:1px solid var(--line);border-left:3px solid var(--brand-2);
  border-radius:0 12px 12px 0;padding:15px 18px;margin-top:14px}
.source-list{display:grid;gap:6px}
.source-list li{font-size:13.5px;color:var(--ink-2);padding-left:14px;position:relative}
.source-list li::before{content:"";position:absolute;left:2px;top:.66em;width:5px;height:5px;border-radius:50%;background:#b9c9e6}
.source-list li.warn{color:var(--accent);font-weight:600}
.source-list li.warn::before{background:var(--accent)}
.method-box{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:6px 18px 12px;margin-top:14px}
.method-box ul li{font-size:13.8px;color:var(--ink-2)}
.method-box p{font-size:13.8px}
.rel-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:9px;margin-top:14px}
.rel-card{display:flex;align-items:center;justify-content:space-between;gap:12px;
  background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:12px 15px;
  color:var(--ink-2);font-size:13.5px;font-weight:600;box-shadow:var(--shadow)}
.rel-card:hover{border-color:#c9d8f5;color:var(--brand);text-decoration:none;background:#f8fbff}
.rel-card i{font-style:normal;color:#b6c2d6}

/* 글 하단 */
.article-foot{margin-top:36px;padding-top:20px;border-top:1px solid var(--line)}
.tags{display:flex;flex-wrap:wrap;gap:6px}
.article-note{margin-top:12px;font-size:12.5px;color:var(--muted);line-height:1.7;max-width:80ch}

/* 토스트 */
.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,14px);z-index:120;
  background:#111b2e;color:#fff;font-size:13.5px;padding:10px 18px;border-radius:999px;
  opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;box-shadow:0 12px 30px -12px rgba(0,0,0,.6)}
.toast.on{opacity:1;transform:translate(-50%,0)}

/* 404 */
.notfound{padding:70px 0 90px;max-width:640px}
.notfound h1{margin:12px 0 10px;font-size:clamp(24px,4vw,32px)}
.notfound p{color:var(--ink-2);margin-bottom:16px}
.notfound .hero-cta{background:var(--brand);color:#fff;box-shadow:none}

/* ---------- 반응형 ---------- */
@media (max-width:960px){
  .layout.with-toc{grid-template-columns:1fr}
  .toc{position:static;margin:0 0 6px;order:-1}
  .toc ol{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}
  .about{grid-template-columns:1fr;gap:22px}
  .about-card{position:static}
  .hero-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:720px){
  html{scroll-padding-top:70px}
  .head-inner{height:auto;padding:10px 0;flex-direction:column;align-items:flex-start;gap:8px}
  .site-nav{width:100%;margin:0 -4px}
  .brand-text small{display:none}
  .hero{padding:36px 0 32px}
  .hero-stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:20px 0 18px}
  .hstat{padding:11px 12px}
  .section{padding:34px 0}
  .section-head{flex-direction:column;align-items:flex-start;gap:8px}
  .rank-card{grid-template-columns:34px minmax(0,1fr);row-gap:6px;padding:13px 14px}
  .rank-no{width:30px;height:30px;font-size:14px;border-radius:8px}
  .rank-amount{grid-column:2;text-align:left;min-width:0;display:flex;align-items:center;gap:10px}
  .rank-amount .bar{margin-top:0;flex:1}
  .region-grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:9px}
  .chip{padding:8px 13px}
  .article-head h1{font-size:22.5px}
  .prose-sec p{font-size:15px}
  .data-table{font-size:13px;min-width:460px}
  .data-table td,.data-table th{padding:9px 10px}
  .foot-inner{flex-direction:column;gap:18px}
  .toc ol{grid-template-columns:1fr}
}
@media (max-width:420px){
  .hero-stats{grid-template-columns:1fr}
  .region-grid{grid-template-columns:1fr 1fr}
  .region-line{font-size:11.5px}
}

/* ---------- 다크 모드 ---------- */
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0e1117; --surface:#151a23; --surface-2:#121721; --ink:#eef1f6; --ink-2:#c3cad6;
    --muted:#949dab; --line:#262d3a; --brand:#7fa9f5; --brand-2:#84b0ff; --brand-weak:#1a2540;
    --accent:#f0a071; --accent-weak:#2a1c14;
    --up:#ff8787; --down:#74a9f0; --flat:#8b93a1;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -18px rgba(0,0,0,.7);
  }
  .site-head{background:rgba(14,17,23,.92)}
  .brand-text strong{color:var(--ink)}
  .footer,.site-foot{background:#080b11}
  .data-table thead th{background:#1b2230;color:#c9d3e3;border-bottom-color:#2b3444}
  .data-table td{border-bottom-color:#222a37}
  .data-table tbody tr:nth-child(even){background:#171d27}
  .data-table tbody tr:hover{background:#1b2331}
  .stat-value,.prose-sec strong,.rank-name{color:var(--ink)}
  .lead{background:var(--surface)}
  .hero{background:linear-gradient(135deg,#08152e 0%,#0f2f6b 55%,#15418d 100%)}
  .rank-amount .bar{background:#232b39}
  .chip:hover,.region-card:hover,.rel-card:hover,.btn:hover,.faq-item summary:hover{background:#1a2333}
  .prose-sec code{background:#1b2230;border-color:#2b3444}
  .source-box{background:#131a25}
  .callout{background:#1a2540;color:#c9dcff}
  .badge-live{color:#8fe3b0;background:#12291c}
  .toast{background:#e8eefb;color:#0e1117}
}

/* ---------- 인쇄 ---------- */
@media print{
  .site-head,.site-foot,.toc,.actions,.toast,.hero::before,.hero::after{display:none!important}
  body{background:#fff;color:#000;font-size:11.5pt}
  .wrap{max-width:none;padding:0}
  .layout.with-toc{display:block}
  .article-head{border-bottom:1px solid #ccc}
  .prose-sec{break-inside:avoid-page}
  .table-wrap{overflow:visible;border:1px solid #ccc;box-shadow:none}
  .data-table{min-width:0;font-size:9.5pt}
  a{color:#000;text-decoration:none}
  .faq-item{break-inside:avoid}
  .lead{box-shadow:none;border-left:3px solid #333}
}
"""


# ==========================================================================
# 9. 빌드
# ==========================================================================
def _reset_site(site_dir: Path, verbose: bool = False) -> None:
    """생성물만 지운다(사용자가 넣어둔 파일은 건드리지 않는다)."""
    if not site_dir.exists():
        return
    for name in ("bids", "assets"):
        target = site_dir / name
        if target.is_dir():
            shutil.rmtree(target)
            if verbose:
                print(f"  · 기존 {name}/ 정리")
    for name in ("index.html", "404.html", "sitemap.xml", "robots.txt", "build.json"):
        target = site_dir / name
        if target.exists():
            target.unlink()


def build(*, output_dir: Path | str | None = None, site_dir: Path | str | None = None,
          site_url: str | None = None, exclude_sim: bool | None = None,
          verbose: bool = False) -> dict:
    output_dir = Path(output_dir or BASE_DIR / "output")
    site_dir = Path(site_dir or BASE_DIR / "site")
    site_url = (site_url or SITE_URL).rstrip("/")
    if exclude_sim is None:
        exclude_sim = os.environ.get("PSEO_EXCLUDE_SIM", "").strip() in ("1", "true", "yes")

    if not output_dir.is_dir():
        raise FileNotFoundError(f"마크다운 출력 폴더가 없습니다: {output_dir}")

    pages, problems = load_pages(output_dir, verbose=verbose)
    dropped = [p for p in pages if exclude_sim and p.is_sim]
    if exclude_sim:
        pages = [p for p in pages if not p.is_sim]

    built_at = datetime.now(KST).isoformat(timespec="seconds")
    site_dir.mkdir(parents=True, exist_ok=True)
    _reset_site(site_dir, verbose=verbose)
    (site_dir / "bids").mkdir(parents=True, exist_ok=True)
    write_assets(site_dir)

    written: list[str] = []
    for page in pages:
        out = site_dir / "bids" / f"{page.slug}.html"
        out.write_text(render_detail(page, site_url, built_at, total_pages=len(pages)),
                       encoding="utf-8")
        written.append(out.name)
        if verbose:
            print(f"  ✔ bids/{out.name}")

    (site_dir / "index.html").write_text(index_body(pages, site_url, built_at), encoding="utf-8")
    write_404(site_dir, site_url, built_at)
    sitemap_count = write_sitemap(site_dir, site_url, pages, built_at)
    write_robots(site_dir, site_url)

    sim = any(p.is_sim for p in pages)
    info = {
        "site": SITE_NAME,
        "site_url": site_url,
        "built_at": built_at,
        "pages": len(pages),
        "detail_pages": len(written),
        "excluded_sim": len(dropped),
        "data_mode": "sim" if sim else "live",
        "sitemap_urls": sitemap_count,
        "output_dir": str(output_dir),
        "site_dir": str(site_dir),
        "problems": problems,
        "generator": "builder.py (zero-dependency)",
    }
    (site_dir / "build.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    (site_dir / BUILD_MARKER).write_text("generated by pseo-engine/builder.py\n", encoding="utf-8")

    total_bytes = sum(f.stat().st_size for f in site_dir.rglob("*") if f.is_file())
    info["total_bytes"] = total_bytes
    if verbose:
        print(f"  · {len(pages)} pages · {total_bytes/1024:.1f} KB → {site_dir}")
    return info


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="pSEO 정적 HTML 사이트 생성기 (무의존성)")
    p.add_argument("--output", help="마크다운 폴더 (기본: ./output)")
    p.add_argument("--site", help="사이트 출력 폴더 (기본: ./site)")
    p.add_argument("--site-url", help="배포 도메인 (기본: PSEO_SITE_URL 또는 https://example.com)")
    p.add_argument("--exclude-sim", action="store_true",
                   help="dataMode=sim 페이지를 빌드에서 제외 (실서비스 배포용)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv if argv is not None else sys.argv[1:])
    print("=" * 62)
    print(f" 정적 사이트 빌드 · {SITE_NAME}")
    print("=" * 62)
    try:
        info = build(output_dir=ns.output, site_dir=ns.site, site_url=ns.site_url,
                     exclude_sim=ns.exclude_sim or None, verbose=ns.verbose)
    except FileNotFoundError as exc:
        log(str(exc), "FAIL")
        return 2
    except Exception as exc:  # noqa: BLE001
        log(f"빌드 실패: {exc}", "FAIL")
        raise

    log(f"리포트 {info['pages']}장 → site/bids/*.html {info['detail_pages']}개", "OK")
    log(f"메인 site/index.html · sitemap {info['sitemap_urls']}개 URL · "
        f"{info['total_bytes']/1024:.1f} KB")
    for problem in info["problems"]:
        log(problem, "WARN")
    if info["excluded_sim"]:
        log(f"시뮬레이션 {info['excluded_sim']}장 제외(--exclude-sim)", "WARN")
    if info["data_mode"] == "sim":
        log("모든 페이지가 시뮬레이션 표본입니다. 실서비스 배포 전 --exclude-sim 또는 API 키를 사용하세요.", "WARN")
    if info["site_url"].endswith("example.com"):
        log("PSEO_SITE_URL(또는 --site-url)이 설정되지 않아 canonical/sitemap이 example.com입니다.", "WARN")
    log(f"출력: {info['site_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
