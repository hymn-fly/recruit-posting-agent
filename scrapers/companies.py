"""23개 회사 포털 메타데이터 (직전 스크래핑 시도에서 얻은 접근성 정보)."""
from typing import TypedDict


class Company(TypedDict):
    name: str
    portal_url: str
    access_method: str
    notes: str
    industry: str  # 회사가 속한 산업


COMPANIES: list[Company] = [
    {"name": '삼성', "industry": '전자/반도체', "portal_url": 'https://www.samsungcareers.com/hr/', "access_method": 'api_error', "notes": '정적 fetch 시 JSON 500 에러 반환, 하위 검색 경로 필요'},
    {"name": 'LG(CNS)', "industry": 'IT서비스', "portal_url": 'https://careers.lg.com/apply?c=CNS', "access_method": 'js_rendered', "notes": 'SPA, 정적 HTML에 공고 없음'},
    {"name": 'SK', "industry": '통신/에너지/반도체', "portal_url": 'https://www.skcareers.com/Recruit', "access_method": 'js_rendered', "notes": '템플릿 플레이스홀더만 반환'},
    {"name": '현대오토에버', "industry": 'IT서비스(자동차 SW)', "portal_url": 'https://career.hyundai-autoever.com/ko/apply', "access_method": 'static_html', "notes": '정상 작동, 개별 공고 URL 패턴: /ko/o/{id}'},
    {"name": '현대(현대자동차)', "industry": '자동차', "portal_url": 'https://talent.hyundai.com/apply/applyList.hc?...', "access_method": 'js_rendered', "notes": 'AJAX 렌더링'},
    {"name": '하나은행', "industry": '금융(은행)', "portal_url": 'https://hanabankhr.career.co.kr/jobs/', "access_method": 'robots_blocked', "notes": 'robots.txt 차단 — 우회 전 정책 확인 필요'},
    {"name": 'KB', "industry": '금융(금융지주)', "portal_url": 'https://careers.kbfg.com/apply/apply', "access_method": 'js_rendered', "notes": 'SPA'},
    {"name": '신한', "industry": '금융(금융지주)', "portal_url": 'https://shinhandigital.recruiter.co.kr/career/jobs', "access_method": 'js_rendered', "notes": 'SPA'},
    {"name": 'IBK기업은행', "industry": '금융(은행)', "portal_url": 'https://ibk.incruit.com/index_main_2025.asp', "access_method": 'server_error', "notes": '인크루트 플랫폼, 재시도 후에도 서버 오류'},
    {"name": '농협', "industry": '금융(은행)', "portal_url": 'https://with.nonghyup.com/jbnf/jbnfLst.do', "access_method": 'robots_blocked', "notes": '서울/경기 지역만 필터링'},
    {"name": '우리은행', "industry": '금융(은행)', "portal_url": 'https://jrs.jobkorea.co.kr/wooribank', "access_method": 'static_html', "notes": '신입 공채 섹션 단위, 개별 공고 URL 없음'},
    {"name": '포스코', "industry": '철강', "portal_url": 'https://recruit.posco.com/h22a01-front/H22A1000.html?codeNum=30', "access_method": 'robots_blocked', "notes": ''},
    {"name": '한화', "industry": '방산/에너지/제조', "portal_url": 'https://www.hanwhain.com/portal/apply/recruit', "access_method": 'robots_blocked', "notes": ''},
    {"name": 'LIG', "industry": '방산', "portal_url": 'https://ligdna.recruiter.co.kr/career/home', "access_method": 'js_rendered', "notes": 'SPA'},
    {"name": 'PwC', "industry": '컨설팅', "portal_url": 'https://www.pwcconsulting.co.kr/ko/career/consultant.html', "access_method": 'static_html', "notes": '정상 작동'},
    {"name": 'Bain', "industry": '컨설팅', "portal_url": 'https://www.bain.com/ko/careers/find-a-role/?filters=offices(277)|', "access_method": 'robots_blocked', "notes": '서울 오피스 공고 위주'},
    {"name": 'BCG', "industry": '컨설팅', "portal_url": 'https://careers.bcg.com/global/en/search-results?...', "access_method": 'js_rendered', "notes": '검색 결과가 JS로 채워짐'},
    {"name": '롯데', "industry": 'IT서비스(롯데이노베이트)', "portal_url": 'https://recruit.lotte.co.kr/apply/announcement', "access_method": 'static_html', "notes": '"롯데이노베이트" 계열사 공고만 필터링'},
    {"name": 'CJ', "industry": '식품/물류/미디어', "portal_url": 'https://recruit.cj.net/recruit/ko/recruit/recruit/list.fo', "access_method": 'static_html', "notes": '정상 작동'},
    {"name": '두산', "industry": '중공업/에너지', "portal_url": 'https://career.doosan.com/dsp/sa/RecList.jsp', "access_method": 'static_html', "notes": '목록은 정적으로 보이나 개별 공고는 JS 모달 (상세 URL 없음)'},
    {"name": '현대모비스', "industry": '자동차부품', "portal_url": 'https://careers.mobis.com/jobs', "access_method": 'static_html', "notes": '정상 작동, 상세 URL 패턴: /jobs-view?seq={id}'},
    {"name": 'LG AI연구원', "industry": 'AI연구', "portal_url": 'https://www.lgresearch.ai/careers', "access_method": 'js_rendered', "notes": 'SPA — catch.co.kr 같은 잡포털 미러로 보완 가능'},
    {"name": 'KT', "industry": '통신', "portal_url": 'https://recruit.kt.com/careers/', "access_method": 'js_rendered', "notes": 'SPA — catch.co.kr 같은 잡포털 미러로 보완 가능'},
]

COMPANY_BY_NAME = {c["name"]: c for c in COMPANIES}
