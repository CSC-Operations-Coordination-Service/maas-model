"""Tests for maas_model.authz.query_plan: Cerbos query plans as OpenSearch
filters.

The plans under tests/data/cerbos/plans were captured from a real PDP (see
tests/data/cerbos/policies/widget.yaml), so these pin the AST Cerbos actually
emits, not the one its docs suggest.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from maas_model.authz.model import Plan  # noqa: E402
from maas_model.authz.query_plan import (  # noqa: E402
    UnsupportedPlanError,
    plan_to_query,
)

PLANS = Path(__file__).parent / "data" / "cerbos" / "plans"


def _captured(name):
    """the filter of a plan captured from the PDP, as a Plan"""
    plan_filter = json.loads((PLANS / f"{name}.json").read_text(encoding="UTF-8"))[
        "filter"
    ]
    return Plan(kind=plan_filter["kind"], condition=plan_filter.get("condition"))


def _conditional(expression):
    return Plan(kind=Plan.CONDITIONAL, condition={"expression": expression})


def _var(name):
    return {"variable": f"request.resource.attr.{name}"}


def _val(value):
    return {"value": value}


def _query(plan):
    return plan_to_query(plan).to_dict()


# ---------------------------------------------------------------------------
# Plans captured from the PDP
# ---------------------------------------------------------------------------


def test_always_allowed_needs_no_filter():
    assert plan_to_query(_captured("super")) is None


def test_always_denied_matches_nothing():
    assert _query(_captured("denied")) == {"match_none": {}}


def test_grants_become_a_disjunction_of_their_dimensions():
    """data_permitted, with exists() over the principal's grants unrolled by
    the planner and has() folded away"""
    assert _query(_captured("grants_user")) == {
        "bool": {
            "should": [
                {"term": {"mission": "S1"}},
                {
                    "bool": {
                        "must": [
                            {"term": {"mission": "S2"}},
                            {"term": {"phase": "E2E"}},
                        ]
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def test_assigned_to_me_is_a_term_on_the_array_field():
    """P.id in R.attr.assignees"""
    assert _query(_captured("assigned_operator")) == {"term": {"assignees": "bob"}}


def test_assignee_is_me_is_a_term():
    """R.attr.assignee == P.id"""
    assert _query(_captured("assignee_update")) == {"term": {"assignee": "bob"}}


def test_null_range_list_and_inequality_conditions():
    """R.attr.mission == null && R.attr.size >= 10
    && R.attr.name in ["a", "b"] && R.attr.name != "c" """
    assert _query(_captured("unclassified")) == {
        "bool": {
            "must": [
                {"bool": {"must_not": [{"exists": {"field": "mission"}}]}},
                {"range": {"size": {"gte": 10}}},
                {"terms": {"name": ["a", "b"]}},
                {"bool": {"must_not": [{"term": {"name": "c"}}]}},
            ]
        }
    }


# ---------------------------------------------------------------------------
# Operators, operand order and edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operator, attribute_first, bound",
    [
        ("lt", True, "lt"),
        ("le", True, "lte"),
        ("gt", True, "gt"),
        ("ge", True, "gte"),
        # "5 < R.attr.size" is "R.attr.size > 5"
        ("lt", False, "gt"),
        ("le", False, "gte"),
        ("gt", False, "lt"),
        ("ge", False, "lte"),
    ],
)
def test_ranges_follow_the_operand_order(operator, attribute_first, bound):
    operands = [_var("size"), _val(5)]
    if not attribute_first:
        operands.reverse()

    query = _query(_conditional({"operator": operator, "operands": operands}))

    assert query == {"range": {"size": {bound: 5}}}


def test_eq_works_both_ways_round():
    left = _conditional({"operator": "eq", "operands": [_var("name"), _val("a")]})
    right = _conditional({"operator": "eq", "operands": [_val("a"), _var("name")]})

    assert _query(left) == _query(right) == {"term": {"name": "a"}}


def test_not_null_is_exists():
    plan = _conditional({"operator": "ne", "operands": [_var("mission"), _val(None)]})

    assert _query(plan) == {"exists": {"field": "mission"}}


def test_not_negates_its_operand():
    plan = _conditional(
        {
            "operator": "not",
            "operands": [
                {"expression": {"operator": "eq", "operands": [_var("a"), _val(1)]}}
            ],
        }
    )

    assert _query(plan) == {"bool": {"must_not": [{"term": {"a": 1}}]}}


def test_in_a_list_holding_null_also_matches_a_missing_field():
    plan = _conditional(
        {"operator": "in", "operands": [_var("mission"), _val(["S1", None])]}
    )

    assert _query(plan) == {
        "bool": {
            "should": [
                {"terms": {"mission": ["S1"]}},
                {"bool": {"must_not": [{"exists": {"field": "mission"}}]}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_a_bare_boolean_attribute_is_a_term_on_true():
    plan = Plan(kind=Plan.CONDITIONAL, condition={"variable": "R.attr.enabled"})

    assert _query(plan) == {"term": {"enabled": True}}


def test_dotted_attributes_are_dotted_fields():
    plan = _conditional(
        {"operator": "eq", "operands": [_var("metadata.source"), _val("x")]}
    )

    assert _query(plan) == {"term": {"metadata.source": "x"}}


@pytest.mark.parametrize(
    "expression",
    [
        # arithmetic
        {
            "operator": "eq",
            "operands": [
                {"expression": {"operator": "add", "operands": [_var("a"), _val(1)]}},
                _val(2),
            ],
        },
        # attribute against attribute
        {"operator": "eq", "operands": [_var("a"), _var("b")]},
        # not a resource attribute
        {"operator": "eq", "operands": [{"variable": "request.principal.id"}, _val(1)]},
        # a lambda over a resource list
        {"operator": "exists", "operands": [_var("tags"), {"expression": {}}]},
        # a range on null
        {"operator": "lt", "operands": [_var("a"), _val(None)]},
        # eq against a collection
        {"operator": "eq", "operands": [_var("a"), _val(["x"])]},
    ],
)
def test_what_does_not_translate_is_refused(expression):
    with pytest.raises(UnsupportedPlanError):
        plan_to_query(_conditional(expression))


def test_an_unknown_plan_kind_is_refused():
    with pytest.raises(UnsupportedPlanError):
        plan_to_query(Plan(kind="KIND_SOMETHING_NEW"))
