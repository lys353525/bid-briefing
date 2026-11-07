# 입찰동향 브리핑 — pSEO 무인 수집·발행 엔진

공공데이터포털의 **나라장터 입찰공고**를 매일 수집해서, 검색자가 실제로 찾는
"지역별·분야별 입찰공고 동향" 페이지를 마크다운으로 자동 생성하는 엔진입니다.
숫자는 전부 코드가 계산하고, 품질 검사를 통과한 글만 발행합니다.

**지금 바로 돌려볼 수 있습니다. API 키가 없어도 됩니다.**

```bash
python3 /workspace/pseo-engine/pipeline.py      # 수집→계산→검증→발행→정적 사이트까지
python3 /workspace/pseo-engine/builder.py       # 사이트만 다시 빌드
```

키가 없으면 자동으로 **시뮬레이션 모드**로 전환해, 실제 API와 똑같은 필드의
표본 데이터로 파이프라인 전체를 검증합니다. 페이지 하단에 "시뮬레이션" 표시가 붙습니다.
마지막 단계에서 마크다운을 정적 HTML(`site/`)로 빌드하므로, 실행 후 폴더째 배포하면 됩니다.

---

## 1. 지금 상태 (2026-11-07 실행 결과)

| 항목 | 값 |
|---|---|
| 수집 | 1,535건 (최근 62일 표본) |
| 생성 페이지 | 28장 (전국 1 + 예산순위 1 + 지역 16 + 분야 10) |
| 품질 검증 | 28/28 통과, 평균 점수 1.00 |
| 출력 | `output/*.md`, `sitemap.xml`, `index.json`, `_run-report.json` |
| 정적 사이트 | `site/` — `index.html` + `bids/*.html` 28장 + CSS·sitemap (무의존성 빌드) |
| 자체 테스트 | 20개 전부 통과 (`python3 selftest.py`) |

---

## 2. 2분 세팅 — 무료 API 키 발급

> 키 없이도 위 명령은 돌아갑니다. 실데이터로 바꾸려면 아래 4단계만 하면 됩니다.

1. **공공데이터포털 접속** → https://www.data.go.kr → 회원가입/로그인
2. **서비스 페이지 열기** → https://www.data.go.kr/data/15129394/openapi.do
   (검색어: `나라장터 입찰공고정보서비스`)
3. **활용신청** 클릭 → 활용목적에 "입찰공고 동향 정보 제공" 정도로 적고 신청
   → 조달청 데이터는 **자동 승인**이라 보통 즉시 사용 가능합니다.
4. **인증키 복사** → 마이페이지 > 데이터활용 > 인증키 발급현황
   → **일반 인증키(Decoding)** 값을 복사 (Encoding 키가 아닙니다)

그리고 실행:

```bash
export DATA_GO_KR_KEY="복사한_키"
export PSEO_SITE_URL="https://내도메인.com"     # sitemap·canonical에 사용
python3 /workspace/pseo-engine/pipeline.py
```

- 무료 한도: **개발계정 일 1,000회**. 이 엔진은 1회 실행에 최대 60회(오퍼레이션 3종 × 20페이지)만 씁니다.
- 실데이터 수집에 실패하면 **가짜 데이터를 발행하지 않고 종료코드 2로 멈춥니다.** 로그에 사유가 남습니다.

### 명령 옵션

```bash
python3 pipeline.py --as-of 2026-11-07   # 기준일 지정(과거 재현)
python3 pipeline.py --days 62            # 수집 기간(기본 62일: 주간+전월 비교)
python3 pipeline.py --limit 5            # 페이지만 5장 생성(테스트)
python3 pipeline.py --mode sim           # 키가 있어도 표본으로 실행
```

---

## 3. 폴더 구조

```
pseo-engine/
├── pipeline.py       # 수집→저장→계산→생성→검증→발행 (python3 pipeline.py)
├── config.py         # 환경변수·API 주소·품질 임계값·지역/분야 규칙
├── db.py             # SQLite 연동 (원시/지표/발행글/품질/사건)
├── schema.sql        # 테이블 정의 (7개)
├── collector.py      # 나라장터 수집기 + 시뮬레이션 모드
├── calculator.py     # 룰 기반 계산기 (금액·증감·중위·분포·분류)
├── formatter.py      # 마크다운 + JSON-LD 생성기
├── builder.py        # 정적 HTML 사이트 생성기 (output/*.md → site/, 무의존성)
├── quality_gate.py   # 9종 품질 검사 (실패 시 발행 보류)
├── selftest.py       # 자체 테스트 20개
├── pseo.db           # 누적 DB (재실행해도 중복 저장 안 됨)
├── raw/g2b/          # 수집 원본 JSON 스냅샷
└── output/
    ├── 2026-w45-nationwide.md       # 전국 주간 요약
    ├── 2026-w45-top-budget.md       # 배정예산 상위 10선
    ├── 2026-w45-region-busan.md     # 지역별 (16장)
    ├── 2026-w45-field-construction.md # 분야별 (10장)
    ├── sitemap.xml · robots.txt · index.json
    └── _held/                       # 품질 미달로 보류된 글 (배포 금지)

site/                              # builder.py 산출물 — 이 폴더를 그대로 배포
├── index.html · bids/*.html (28장)
├── assets/style.css · favicon.svg
└── sitemap.xml · robots.txt · 404.html · build.json
```

---

## 4. 정적 HTML 사이트 빌드 — `builder.py` (권장 · Node 불필요)

마크다운을 그대로 **배포 가능한 정적 사이트**로 굽습니다. 파이썬 표준 라이브러리만 쓰므로
`npm install`도 빌드 서버도 필요 없습니다.

```bash
python3 builder.py                      # output/*.md → site/
python3 builder.py --site-url https://내도메인.com
python3 builder.py --exclude-sim        # 시뮬레이션 글을 빼고 빌드(실서비스 배포용)
python3 builder.py --output ./output --site ./site --verbose
```

```
site/
├── index.html            메인 — 전국 하이라이트 배너, 예산 TOP 10, 시도 16개 그리드, 분야 10개 뱃지,
│                         최근 업데이트·자동화 소개
├── bids/{slug}.html      상세 28장 — 지표 카드, 요약·분포 표, FAQ 아코디언, 산출 방법, 출처,
│                         링크 복사/공유/인쇄, Schema.org JSON-LD(Article·Dataset·FAQ·Breadcrumb)
├── assets/style.css      순수 CSS — 외부 CDN 없음, 모바일 미디어 쿼리, 다크모드, 인쇄 스타일
├── assets/favicon.svg
├── sitemap.xml           29개 URL(메인 + 28장)
├── robots.txt
├── 404.html              GitHub Pages·Cloudflare Pages 오류 페이지
└── build.json            빌드 메타(페이지 수·데이터 모드·URL·바이트)
```

**링크 규칙** — 마크다운 본문은 `/bids/{slug}/` 형태를 쓰지만, 빌더는 `{slug}.html` 파일을 만들고
링크도 상대 경로 `{slug}.html`로 다시 씁니다. 그래서 폴더째 올리면 GitHub Pages·Cloudflare Pages·
`python3 -m http.server` 어디서든 그대로 열립니다.

**URL** — canonical·`og:url`·sitemap은 `PSEO_SITE_URL`(또는 `--site-url`) 기준 절대 URL입니다.
도메인을 정하기 전에는 `https://example.com`으로 나오고, 빌드 로그가 경고합니다.

**시뮬레이션 표본** — `dataMode: "sim"` 글은 페이지 상단에 배지와 안내문이 붙습니다.
실서비스 배포 전에는 `--exclude-sim`으로 빼거나 `DATA_GO_KR_KEY`를 넣어 실데이터로 다시 빌드하세요.

**로컬 확인**

```bash
cd site && python3 -m http.server 8000
# http://localhost:8000
```

**배포** — `site/` 폴더를 그대로 올리면 끝입니다.

- Cloudflare Pages: 대시보드 → Workers & Pages → **Upload assets**(또는 Git 연결 시 빌드 명령
  `python3 builder.py`, 출력 디렉터리 `site`).
- GitHub Pages: `site/` 내용을 `gh-pages` 브랜치 루트에 푸시하거나 Pages 설정에서 `/docs`·`/site` 지정.

**파이프라인 연동** — `pipeline.py`가 마지막 `[6/7]` 단계에서 이 빌더를 자동 호출합니다.
사이트 빌드가 실패해도 마크다운 발행은 유지되고, 원인은 `_run-report.json`의 `site` 항목과
`events` 테이블(`SITE_BUILD_FAILED`)에 남습니다.

---

## 5. (선택) Astro로 옮길 때

### 5.1 프로젝트 생성

```bash
npm create astro@latest bid-site -- --template minimal
cd bid-site
mkdir -p src/content/bids
cp /workspace/pseo-engine/output/*.md src/content/bids/
```

### 5.2 컬렉션 정의 — `src/content.config.ts`

```ts
import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const bids = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/bids' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    updated: z.coerce.date().optional(),
    tags: z.array(z.string()),
    category: z.string(),
    slug: z.string().optional(),
    scope: z.string(),
    region: z.string().nullable(),
    field: z.string().nullable(),
    asOf: z.string(),
    windowStart: z.string(),
    windowEnd: z.string(),
    source: z.string(),
    sourceUrl: z.string().url(),
    dataMode: z.string(),
    draft: z.boolean().default(false),
  }),
});
export const collections = { bids };
```

### 5.3 페이지 라우트 — `src/pages/bids/[slug].astro`

```astro
---
import { getCollection, render } from 'astro:content';
export async function getStaticPaths() {
  const entries = await getCollection('bids', ({ data }) =>
    import.meta.env.PROD ? data.dataMode === 'live' : true);  // ★ 시뮬레이션 글은 배포 제외
  return entries.map((entry) => ({ params: { slug: entry.id }, props: { entry } }));
}
const { entry } = Astro.props;
const { Content } = await render(entry);
---
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>{entry.data.title}</title>
  <meta name="description" content={entry.data.description} />
  <link rel="canonical" href={`${import.meta.env.SITE}/bids/${entry.id}/`} />
</head>
<body>
  <article>
    <h1>{entry.data.title}</h1>
    <p>{entry.data.asOf} 기준 · 출처 {entry.data.source}</p>
    <Content />
  </article>
</body>
</html>
```

> 본문에 들어 있는 `<script type="application/ld+json">`은 마크다운 원본 HTML로 그대로 출력됩니다.
> 광고를 붙일 때는 본문 위에 1개, 표 사이에 1개 정도로 제한하세요(광고 과밀은 정책 위반입니다).

### 5.4 배포 — Cloudflare Pages (무료)

```bash
npm run build
npx wrangler pages deploy dist --project-name=bid-briefing
```

또는 Cloudflare 대시보드 → Workers & Pages → Git 연결 → 빌드 명령 `npm run build`,
출력 디렉터리 `dist`. 무료 플랜에서 빌드 500회/월, 대역폭 무제한입니다.

---

## 6. 매일 자동 실행

### 방법 A — GitHub Actions (권장)

```yaml
name: daily-bids
on:
  schedule: [{ cron: "0 21 * * *" }]   # 21:00 UTC = 06:00 KST
  workflow_dispatch:
concurrency: { group: bids, cancel-in-progress: false }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: 수집·생성
        run: python pseo-engine/pipeline.py --days 62
        env:
          DATA_GO_KR_KEY: ${{ secrets.DATA_GO_KR_KEY }}
          PSEO_SITE_URL: https://내도메인.com
      - name: 콘텐츠 복사
        run: cp pseo-engine/output/*.md src/content/bids/
      - run: npm ci && npm run build
      - name: 배포
        run: npx wrangler pages deploy dist --project-name=bid-briefing
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CF_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CF_ACCOUNT_ID }}
```

`Settings > Secrets and variables > Actions`에 `DATA_GO_KR_KEY`, `CF_API_TOKEN`,
`CF_ACCOUNT_ID`를 넣으면 끝입니다.

### 방법 B — 집 PC/서버 cron

```cron
0 6 * * * cd /workspace/pseo-engine && /usr/bin/python3 pipeline.py --days 62 >> cron.log 2>&1
```

---

## 7. 색인 요청 — 현실적인 순서

| 엔진 | 방법 | 비고 |
|---|---|---|
| Google | Search Console에 sitemap 제출 | 일반 페이지 즉시 색인 API는 없습니다 |
| 네이버 | 서치어드바이저 → 사이트 등록 → sitemap 제출 | 일일 수집 요청 한도 있음 |
| Bing·기타 | IndexNow (`POST /indexnow`) | 무료·즉시 접수 |

`sitemap.xml`은 `PSEO_SITE_URL` 값으로 자동 생성됩니다. 도메인을 먼저 정하고 실행하세요.
**페이지 수를 늘리는 것보다, 매일 값이 갱신되는 페이지를 유지하는 것이 색인에 유리합니다.**
이 엔진은 주차별 슬러그(`2026-w45-…`)로 페이지를 만들기 때문에, 지난 주 페이지는
그대로 남고 이번 주 페이지만 새로 생깁니다(내부링크로 연결됨).

---

## 8. 품질 게이트 — 무엇을 막는가

`quality_gate.py`가 9가지를 검사하고, **하나라도 실패하면 `output/_held/`로 보내고 배포하지 않습니다.**

| # | 검사 | 기준 |
|---|---|---|
| 1 | frontmatter | title·description·pubDate·tags·category 존재 |
| 2 | 제목/설명 길이 | 제목 70자 이하, 설명 70~200자 |
| 3 | 본문 길이 | 공백 제외 600자 이상 |
| 4 | 구조 | H2 3개 이상, 표 2개 이상 |
| 5 | 출처 | data.go.kr 링크 1개 이상 |
| 6 | JSON-LD | 파싱 가능 + Article·Dataset 포함 |
| 7 | 수치 일치 | 본문의 3자리 이상 숫자가 모두 계산된 facts 안에 존재 |
| 8 | 수치 교차검증 | 원본 공고에서 건수·합계·중위값을 다시 계산해 일치 |
| 9 | 중복·금칙어 | 기존 글과 5-gram 유사도 0.72 미만, 과장·보장 표현 없음 |

`selftest.py`는 일부러 숫자를 틀리게 넣은 글, 출처를 지운 글, 짧은 글, 중복 글을
만들어 게이트가 실제로 **막는지**까지 확인합니다.

---

## 9. 알아둘 점 (정직하게)

- **시뮬레이션 모드**는 실제 공고가 아닙니다. `dataMode: "sim"`으로 표시되고, 위 Astro 예시처럼
  배포 단계에서 걸러야 합니다. 실서비스에는 반드시 API 키를 넣으세요.
- **지역 분류는 기관명 키워드 기반**입니다. "경기도 광주시"와 "광주광역시"처럼 겹치는 지명은
  오분류될 수 있어, 페이지에도 "근사치"라고 적어 두었습니다. 정확도가 중요하면
  기관코드→지역 매핑 테이블을 `config.py`에 추가하세요.
- **분야 분류도 규칙 기반 참고값**입니다. 공식 통계 분류가 아닙니다.
- **API 일 1,000회** 한도는 개발계정 기준입니다. 운영계정은 별도 신청이 필요합니다.
- 나라장터 목록 API에는 **상세 URL이 비어 있는 경우**가 있습니다. 그때는 출처 링크만 남고,
  개별 공고 링크는 생략됩니다(깨진 링크를 만들지 않으려는 의도).
- 광고·제휴를 붙이면 **경제적 이해관계 표기**가 필요합니다. 이 엔진은 제휴 링크를 넣지 않습니다.

---

## 10. 다음 단계 체크리스트

- [ ] 공공데이터포털 인증키 발급 → `DATA_GO_KR_KEY` 설정
- [ ] 실데이터로 1회 실행 후 `output/*.md` 숫자가 실제 공고와 맞는지 눈으로 확인
- [ ] `cd site && python3 -m http.server 8000`으로 모바일·데스크톱 화면 확인
- [ ] 도메인 준비 → `PSEO_SITE_URL` 설정 → `--exclude-sim`으로 빌드 → `site/` 배포
- [ ] Search Console·네이버 서치어드바이저에 sitemap 제출
- [ ] 2주간 Search Console 노출·클릭 확인 → 노출 0인 페이지 유형 정리(prune)
- [ ] 트래픽이 붙으면 애드센스 신청 (심사 전 광고 코드 삽입은 피하기)
