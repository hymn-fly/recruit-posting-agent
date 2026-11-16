"""턴 단위 상태(steps, turn_outputs) reducer 단위 테스트.

병렬 Agent가 같은 단계에 각자 증분을 돌려줘도 결과가 사라지지 않고, 새 턴(None)에는 초기화되는지 본다.
"""
from agents.state import accumulate_steps, accumulate_turn_outputs

GAP_OUTPUT = {"agent": "profile_gap", "text": "갭"}
RESEARCH_OUTPUT = {"agent": "company_research", "text": "리서치"}


def test_turn_outputs는_병렬_Agent_결과를_모두_이어_붙인다():
    after_research = accumulate_turn_outputs([], [RESEARCH_OUTPUT])
    after_gap = accumulate_turn_outputs(after_research, [GAP_OUTPUT])

    assert after_gap == [RESEARCH_OUTPUT, GAP_OUTPUT]


def test_turn_outputs는_None이_오면_이전_턴_결과를_비운다():
    assert accumulate_turn_outputs([RESEARCH_OUTPUT, GAP_OUTPUT], None) == []


def test_turn_outputs는_첫_턴처럼_현재값이_없어도_동작한다():
    assert accumulate_turn_outputs(None, [GAP_OUTPUT]) == [GAP_OUTPUT]


def test_turn_outputs는_이어_붙일_때_기존_리스트를_변경하지_않는다():
    current = [RESEARCH_OUTPUT]

    accumulate_turn_outputs(current, [GAP_OUTPUT])

    assert current == [RESEARCH_OUTPUT]


def test_steps는_병렬_Agent의_증분을_더한다():
    assert accumulate_steps(accumulate_steps(1, 1), 1) == 3


def test_steps는_None이_오면_0으로_초기화한다():
    assert accumulate_steps(3, None) == 0


def test_steps는_첫_턴처럼_현재값이_없어도_동작한다():
    assert accumulate_steps(None, 1) == 1
