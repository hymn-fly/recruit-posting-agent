"""mcp_server.query_server의 검색 필터 조립(build_posting_filter_conditions) 단위 테스트. DB 없이 SQL 조각만 확인한다."""
from mcp_server.query_server import build_posting_filter_conditions


def test_필터가_없으면_활성이면서_마감이_안_지난_조건만_붙는다():
    conditions, args = build_posting_filter_conditions(None, None, None, None, None, only_active=True)

    assert conditions[0] == "j.is_active"
    assert conditions[1].startswith("(j.deadline IS NULL OR j.deadline >= ")   # 마감일 없는 상시 공고는 남긴다
    assert len(conditions) == 2
    assert args == []


def test_마감일_조건은_마감일이_있는_공고만_남기고_일수는_바인딩한다():
    conditions, args = build_posting_filter_conditions(
        ["AI"], None, None, None, None, only_active=False, deadline_within_days=14, require_deadline=True)

    assert conditions[0] == "j.title ILIKE ANY($1::text[])"
    assert conditions[1] == "j.deadline IS NOT NULL"
    assert conditions[2].startswith("j.deadline BETWEEN ") and conditions[2].endswith("+ $2::int")
    assert args == [["%AI%"], 14]


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
