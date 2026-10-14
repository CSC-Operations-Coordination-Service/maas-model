"""Cerbos query plans as OpenSearch queries.

A list can't check its documents one by one -- pages would come back short --
so it asks Cerbos for a query plan instead: the policy conditions partially
evaluated against the principal, leaving only the parts that read the
resource. This module turns what is left into an OpenSearch filter, e.g. the
``data_permitted`` derived role for a principal granted mission S1 becomes::

    {"term": {"mission": "S1"}}

and "assigned to me" (``P.id in R.attr.assignees``) becomes a term on the
``assignees`` array.

What translates: ``and``, ``or``, ``not``, ``eq``, ``ne``, ``lt``, ``le``,
``gt``, ``ge`` and ``in``, between one resource attribute and constants.
Anything else -- arithmetic, ``timestamp()``, lambdas over resource lists --
raises UnsupportedPlanError, so the list is refused rather than widened.

Comparisons become term and range queries: conditions should compare
keyword, numeric, date or boolean fields, not analyzed text.

Note: the planner folds ``has(R.attr.x)`` to true. Resource attributes are
always sent with every mapped field (null when unset, see attributes.py), so
policies guard with ``R.attr.x != null``, which means the same thing to a
check and to a plan.
"""

from typing import Any, Dict, List, Optional, Tuple

from opensearchpy import Q
from opensearchpy.helpers.query import Query

from maas_model.authz.model import Plan

RESOURCE_ATTRIBUTE_PREFIXES = ("request.resource.attr.", "R.attr.")

# what @authorize hands a collection route: the filter to apply, None for
# no restriction
RowFilter = Optional[Query]

# range bound for "attribute <op> value", and for "value <op> attribute"
_RANGE_BOUNDS = {
    "lt": ("lt", "gt"),
    "le": ("lte", "gte"),
    "gt": ("gt", "lt"),
    "ge": ("gte", "lte"),
}


class UnsupportedPlanError(ValueError):
    """The plan holds an expression with no OpenSearch translation here."""


def plan_to_query(plan: Plan) -> Optional[Query]:
    """The OpenSearch filter selecting the documents a plan allows.

    Returns:
        None when every document is allowed, a ``match_none`` query when none
        is, the translated condition otherwise.

    Raises:
        UnsupportedPlanError: on an unknown plan kind or an expression this
            module does not translate.
    """
    if plan.kind == Plan.ALWAYS_ALLOWED:
        return None

    if plan.kind == Plan.ALWAYS_DENIED:
        return Q("match_none")

    if plan.kind == Plan.CONDITIONAL:
        return _operand_to_query(plan.condition or {})

    raise UnsupportedPlanError(f"unknown query plan kind {plan.kind!r}")


def _operand_to_query(operand: Dict[str, Any]) -> Query:
    """an operand used as a condition: an expression, a constant or a bare
    boolean attribute"""
    if "expression" in operand:
        return _expression_to_query(operand["expression"])

    if "value" in operand and isinstance(operand["value"], bool):
        return Q("match_all") if operand["value"] else Q("match_none")

    if "variable" in operand:
        return Q("term", **{_field(operand["variable"]): True})

    raise UnsupportedPlanError(f"cannot use {operand!r} as a condition")


def _expression_to_query(expression: Dict[str, Any]) -> Query:
    operator = expression.get("operator")
    operands = expression.get("operands", [])

    if operator == "and":
        return Q("bool", must=[_operand_to_query(operand) for operand in operands])

    if operator == "or":
        return Q(
            "bool",
            should=[_operand_to_query(operand) for operand in operands],
            minimum_should_match=1,
        )

    if operator == "not":
        return Q("bool", must_not=[_operand_to_query(operand) for operand in operands])

    if operator in ("eq", "ne"):
        field, value, _ = _attribute_and_constant(operator, operands)
        if isinstance(value, (list, dict)):
            raise UnsupportedPlanError(f"{operator} against a collection: {value!r}")

        if value is None:
            matches = Q("exists", field=field)
            return matches if operator == "ne" else Q("bool", must_not=[matches])

        matches = Q("term", **{field: value})
        return matches if operator == "eq" else Q("bool", must_not=[matches])

    if operator in _RANGE_BOUNDS:
        field, value, attribute_first = _attribute_and_constant(operator, operands)
        if value is None or isinstance(value, (bool, list, dict)):
            raise UnsupportedPlanError(f"{operator} against {value!r}")

        bound = _RANGE_BOUNDS[operator][0 if attribute_first else 1]
        return Q("range", **{field: {bound: value}})

    if operator == "in":
        return _in_to_query(operands)

    raise UnsupportedPlanError(f"unsupported query plan operator {operator!r}")


def _in_to_query(operands: List[Dict[str, Any]]) -> Query:
    field, value, attribute_first = _attribute_and_constant("in", operands)

    if not attribute_first:
        # "value in R.attr.list": the array field holds the value
        if value is None or isinstance(value, (list, dict)):
            raise UnsupportedPlanError(f"in: {value!r} in an attribute")
        return Q("term", **{field: value})

    # "R.attr.x in [values]"
    if not isinstance(value, list):
        raise UnsupportedPlanError(f"in: attribute in {value!r}")

    values = [item for item in value if item is not None]
    matches = Q("terms", **{field: values})

    if len(values) == len(value):
        return matches

    # null is in the list: a document without the field matches too
    return Q(
        "bool",
        should=[matches, Q("bool", must_not=[Q("exists", field=field)])],
        minimum_should_match=1,
    )


def _attribute_and_constant(
    operator: str, operands: List[Dict[str, Any]]
) -> Tuple[str, Any, bool]:
    """(field, constant, whether the attribute came first) of a binary
    comparison between one resource attribute and one constant"""
    if len(operands) == 2:
        first, second = operands

        if "variable" in first and "value" in second:
            return _field(first["variable"]), second["value"], True

        if "value" in first and "variable" in second:
            return _field(second["variable"]), first["value"], False

    raise UnsupportedPlanError(
        f"{operator} needs one resource attribute and one constant: {operands!r}"
    )


def _field(variable: str) -> str:
    """the OpenSearch field a plan variable reads"""
    for prefix in RESOURCE_ATTRIBUTE_PREFIXES:
        if variable.startswith(prefix):
            return variable[len(prefix) :]

    raise UnsupportedPlanError(f"{variable!r} is not a resource attribute")
