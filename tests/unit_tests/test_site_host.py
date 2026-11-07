"""mcp_server.server의 사이트 호스트 정규화(normalize_site_host) 단위 테스트. list_known_urls/list_filtered_urls의 비교 기준이다."""
import pytest

from mcp_server.server import POSTING_URL_HOST_SQL, normalize_site_host


@pytest.mark.parametrize("site_url, expected_host", [
    ("https://www.saramin.co.kr", "saramin.co.kr"),                                  # www. 제거
    ("https://www.saramin.co.kr/zf_user/search/recruit?searchword=AI", "saramin.co.kr"),   # 경로·쿼리 무시
    ("saramin.co.kr", "saramin.co.kr"),                                              # 스킴 없는 도메인
    ("//www.wanted.co.kr/company/29822", "wanted.co.kr"),                            # 스킴 생략형
    ("HTTPS://Career.OliveYoung.com:443/ko/apply", "career.oliveyoung.com"),         # 대소문자·포트
    ("https://career.hyundai-autoever.com/ko/apply", "career.hyundai-autoever.com"),   # 하위 도메인은 유지
])
def test_사이트_링크에서_비교용_호스트를_뽑는다(site_url, expected_host):
    assert normalize_site_host(site_url) == expected_host


@pytest.mark.parametrize("blank_site_url", ["", "   ", "https://", "/zf_user/search"])
def test_호스트를_읽을_수_없으면_오류(blank_site_url):
    with pytest.raises(ValueError):
        normalize_site_host(blank_site_url)


def test_SQL_식은_www_제거_정규식을_역슬래시_하나로_쓴다():
    assert "'^www\\.'" in POSTING_URL_HOST_SQL   # '\\.'(역슬래시 둘)이면 DB에서 '역슬래시+아무 문자'가 되어 어긋난다
