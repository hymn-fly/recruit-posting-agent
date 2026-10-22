"""mcp_server.server의 검색 필터 조립(build_posting_filter_conditions) 단위 테스트. DB 없이 SQL 조각만 확인한다."""
from mcp_server.server import build_posting_filter_conditions


def test_필터가_없으면_활성_공고_조건만_붙는다():
    conditions, args = build_posting_filter_conditions(None, None, None, None, None, only_active=True)

    assert conditions == ["j.is_active"]
    assert args == []


def test_값은_모두_바인딩_파라미터로_넘기고_번호가_이어진다():
    conditions, args = build_posting_filter_conditions(
        [" MLOps ", "", "엠엘옵스"], "SK", "MLOps", "서울", 3, only_active=False)

    assert conditions == ["j.title ILIKE ANY($1::text[])", "c.name ILIKE $2", "j.category ILIKE $3",
                          "j.location ILIKE $4", "(j.min_years IS NULL OR j.min_years <= $5)"]
    assert args == [["%MLOps%", "%엠엘옵스%"], "%SK%", "%MLOps%", "%서울%", 3]   # 빈 키워드·공백 제거


def test_SQL_조각에_사용자_입력이_직접_들어가지_않는다():
    conditions, _ = build_posting_filter_conditions(["'; DROP TABLE job_postings; --"], None, None, None,
                                                    None, only_active=False)

    assert "DROP" not in " ".join(conditions)
