# -*- coding: utf-8 -*-
"""pSEO 엔진 설정값 모음.

우선순위: CLI 인자 > 환경변수 > 이 파일의 기본값.
외부 패키지는 쓰지 않는다(표준 라이브러리만).

주요 환경변수
--------------
DATA_GO_KR_KEY     공공데이터포털 서비스키(Decoding/원본 키). 없으면 시뮬레이션 모드.
PSEO_SITE_URL      배포 도메인 (예: https://bid-briefing.pages.dev)
PSEO_SITE_NAME     사이트 이름
PSEO_DB            SQLite 파일 경로
PSEO_OUTPUT        발행 마크다운 디렉터리
PSEO_SITE          정적 사이트 출력 디렉터리
PSEO_RAW           원시 수집 JSON 디렉터리
PSEO_MODE          live | sim  (강제 지정)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# .env 파일 자동 로드
env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

KST = timezone(timedelta(hours=9), "KST")

# ---------------------------------------------------------------- 사이트 정보
SITE_NAME = os.environ.get("PSEO_SITE_NAME", "입찰동향 브리핑")
SITE_URL = os.environ.get("PSEO_SITE_URL", "https://example.com").rstrip("/")
SITE_LOCALE = "ko_KR"
SITE_AUTHOR = "입찰동향 브리핑 편집부"

# ---------------------------------------------------------------- 데이터 출처
SOURCE = {
    "name": "조달청 나라장터 입찰공고정보서비스",
    "short_name": "나라장터 입찰공고",
    "provider": "조달청",
    "url": "https://www.data.go.kr/data/15129394/openapi.do",
    "api_base": "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02",
    "license": "공공데이터포털 이용허락범위 제한 없음",
    "update_cycle": "실시간(공고 게시 즉시)",
    "daily_limit": "개발계정 일 1,000회",
}

# 업무구분별 목록 오퍼레이션 (공사/용역/물품). 외자(Frgcpt)는 원화 예산이 없어 제외.
API_OPERATIONS = {
    "공사": {"path": "/getBidPblancListInfoCnstwk02", "biz_type": "공사"},
    "용역": {"path": "/getBidPblancListInfoServc02", "biz_type": "용역"},
    "물품": {"path": "/getBidPblancListInfoThng02", "biz_type": "물품"},
}

# ---------------------------------------------------------------- 수집 설정
COLLECT = {
    # 페이지 계산에 필요한 기간. 최근 7일 + 직전 7일 + 전월 비교(30+30일)를 덮는다.
    "days": 14,
    "rows_per_page": 100,          # 100건 단위 (응답 속도 및 안정성 최적화)
    "max_pages": 3,                # 오퍼레이션당 최대 3페이지 (총 900건 내외 실시간 수집)
    "timeout_sec": 30,
    "retries": 3,                  # 지수 백오프 1s, 2s, 4s
    "backoff_base": 1.0,
    "request_pause_sec": 0.25,     # 오퍼레이션 사이 예의상 간격
}

# ---------------------------------------------------------------- 품질 임계값
THRESHOLDS = {
    # 발행 최소 조건
    "min_body_chars": 600,          # 본문 순수 텍스트 기준(공백 제외)
    "min_h2": 3,                    # H2 섹션 최소 개수
    "min_tables": 2,                # 마크다운 표 최소 개수
    "min_source_links": 1,          # 1차 데이터 출처 링크 최소 개수
    "max_title_chars": 70,
    "desc_min_chars": 70,
    "desc_max_chars": 200,
    "duplicate_jaccard": 0.72,      # 기존 발행 글과 5-gram 유사도 상한
    # 페이지 생성 최소 표본(얇은 페이지 방지)
    "min_region_notices": 5,
    "min_field_notices": 8,
    "min_national_notices": 20,
    # 수치 검증
    "allowed_short_number_len": 2,  # 1~2자리 숫자는 순위·개월 등 구조 값으로 허용
}

BANNED_PHRASES = [
    "100% 보장", "무조건", "확실히 오릅니다", "수익 보장", "원금 보장",
    "대박", "부자 되세요", "지금 바로 구매", "클릭하세요", "최저가 보장",
    "반드시 사야", "절대 손해 보지",
]

REQUIRED_FRONTMATTER = ["title", "description", "pubDate", "tags", "category"]

# ---------------------------------------------------------------- 지역/분류
REGIONS = [
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
]

REGION_KEYWORDS = {
    "서울": ["서울"],
    "부산": ["부산"],
    "대구": ["대구"],
    "인천": ["인천"],
    "광주": ["광주광역시"],
    "대전": ["대전"],
    "울산": ["울산"],
    "세종": ["세종"],
    "경기": ["경기", "수원", "성남", "고양", "용인", "부천", "안산", "안양", "남양주", "화성", "평택", "의정부", "파주", "김포", "광명", "군포", "하남", "오산", "이천", "안성", "구리", "포천", "양주", "동두천", "과천", "여주", "광주시"],
    "강원": ["강원", "춘천", "원주", "강릉", "속초", "동해", "태백", "삼척", "홍천", "횡성", "평창", "정선", "철원", "화천", "양구", "인제", "양양", "영월"],
    "충북": ["충북", "충청북도", "청주", "충주", "제천", "보은", "옥천", "영동", "진천", "괴산", "음성", "단양", "증평"],
    "충남": ["충남", "충청남도", "천안", "공주", "보령", "아산", "서산", "논산", "계룡", "당진", "금산", "부여", "서천", "청양", "홍성", "예산군", "태안"],
    "전북": ["전북", "전라북도", "전주", "군산", "익산", "정읍", "남원", "김제", "완주", "진안", "무주", "장수", "임실", "순창", "고창", "부안"],
    "전남": ["전남", "전라남도", "목포", "여수", "순천", "나주", "광양", "담양", "곡성", "구례", "고흥", "보성", "화순", "장흥", "강진", "해남", "영암", "무안", "함평", "영광", "장성", "완도", "진도", "신안"],
    "경북": ["경북", "경상북도", "포항", "경주", "김천", "안동", "구미", "영주", "영천", "상주", "문경", "경산", "군위", "의성", "청송", "영양", "영덕", "청도", "고령", "성주", "칠곡", "예천", "봉화", "울진", "울릉"],
    "경남": ["경남", "경상남도", "창원", "진주", "통영", "사천", "김해", "밀양", "거제", "양산", "의령", "함안", "창녕", "고성", "남해", "하동", "산청", "함양", "거창", "합천"],
    "제주": ["제주"],
}

CATEGORIES = [
    {"key": "construction", "label": "시설공사",
     "keywords": ["건축", "도장", "방수", "증축", "개축", "철거", "창호", "도어", "바닥", "화장실", "옥상", "외벽", "석면", "구조물", "보수공사", "개선공사", "정비공사", "단열", "조적", "미장", "타일"]},
    {"key": "civil", "label": "토목·조경",
     "keywords": ["토목", "도로", "포장", "하천", "배수", "상하수도", "조경", "가로수", "식재", "보도", "교량", "옹벽", "측구", "정원", "잔디", "수목"]},
    {"key": "electric", "label": "전기·통신",
     "keywords": ["전기", "전력", "조명", "LED", "변압", "수배전", "통신", "케이블", "광케이블", "CCTV", "방송설비", "무정전"]},
    {"key": "fire", "label": "소방·설비",
     "keywords": ["소방", "스프링클러", "감지기", "기계설비", "냉난방", "보일러", "승강기", "엘리베이터", "급배수", "위생설비", "공조"]},
    {"key": "facility", "label": "청소·시설관리",
     "keywords": ["청소", "시설관리", "경비", "미화", "방역", "소독", "주차관리", "유지관리 용역", "건물관리", "안전관리"]},
    {"key": "itsw", "label": "정보통신·SW",
     "keywords": ["정보시스템", "소프트웨어", "SW", "시스템 구축", "홈페이지", "데이터", "AI", "인공지능", "클라우드", "정보화", "앱", "플랫폼", "전산", "네트워크", "보안", "DB"]},
    {"key": "education", "label": "교육·행사",
     "keywords": ["교육", "연수", "행사", "축제", "프로그램 운영", "체험", "캠프", "강사", "컨설팅 운영", "워크숍", "박람회", "홍보"]},
    {"key": "goods", "label": "물품·구매",
     "keywords": ["구매", "물품", "납품", "설치 구매", "장비 구매", "컴퓨터", "노트북", "가구", "도서", "차량", "버스", "냉난방기 구매", "복사기", "프린터", "실험기구", "기자재"]},
    {"key": "research", "label": "연구·컨설팅",
     "keywords": ["연구", "용역 연구", "타당성", "평가", "분석", "조사", "컨설팅", "기본계획", "마스터플랜", "학술"]},
    {"key": "medical", "label": "의료·보건",
     "keywords": ["의료", "보건", "병원", "의약품", "의료장비", "방사선", "진료", "검진", "백신", "간호"]},
]

BUDGET_BUCKETS = [
    ("1억 원 미만", 0, 100_000_000),
    ("1억~5억 원", 100_000_000, 500_000_000),
    ("5억~10억 원", 500_000_000, 1_000_000_000),
    ("10억~50억 원", 1_000_000_000, 5_000_000_000),
    ("50억 원 이상", 5_000_000_000, None),
]

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


# ---------------------------------------------------------------- 설정 객체
@dataclass(frozen=True)
class Settings:
    mode: str = "auto"                     # auto | live | sim
    as_of: date = field(default_factory=lambda: datetime.now(KST).date())
    days: int = COLLECT["days"]
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("PSEO_DB", str(BASE_DIR / "pseo.db"))))
    output_dir: Path = field(default_factory=lambda: Path(os.environ.get("PSEO_OUTPUT", str(BASE_DIR / "output"))))
    site_dir: Path = field(default_factory=lambda: Path(os.environ.get("PSEO_SITE", str(BASE_DIR / "site"))))
    raw_dir: Path = field(default_factory=lambda: Path(os.environ.get("PSEO_RAW", str(BASE_DIR / "raw"))))
    api_key: str = field(default_factory=lambda: os.environ.get("DATA_GO_KR_KEY", "").strip())
    max_pages: int = COLLECT["max_pages"]
    rows_per_page: int = COLLECT["rows_per_page"]
    limit_pages: int = 0                   # 0=무제한(전체 생성)
    allow_few: bool = False                # 발행 10장 미만이어도 성공 처리
    verbose: bool = False
    force: bool = False                    # 품질 실패 페이지도 강제 발행(테스트용, 기본 금지)

    def resolved_mode(self) -> str:
        if self.mode in ("live", "sim"):
            return self.mode
        return "live" if self.api_key else "sim"

    def with_changes(self, **kw) -> "Settings":
        return replace(self, **kw)

    # 편의 프로퍼티
    @property
    def window_end(self) -> date:
        return self.as_of

    @property
    def window_start(self) -> date:
        return self.as_of - timedelta(days=6)


def default_settings() -> Settings:
    return Settings()


def now_kst() -> datetime:
    return datetime.now(KST)


def log(message: str, level: str = "INFO") -> None:
    """파이프라인 공통 로그. 사람이 읽기 좋은 한 줄 형식."""
    stamp = datetime.now(KST).strftime("%H:%M:%S")
    prefix = {"INFO": "·", "OK": "✔", "WARN": "!", "FAIL": "✘", "STEP": "▶"}.get(level, "·")
    stream = sys.stderr if level in ("WARN", "FAIL") else sys.stdout
    print(f"[{stamp}] {prefix} {message}", file=stream, flush=True)
