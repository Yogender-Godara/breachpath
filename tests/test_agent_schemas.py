"""The submit schemas and their Pydantic twins (no DB, no network)."""
import pytest
from pydantic import ValidationError

from agents.schemas import SUBMIT_TOOLS, FindingsOut, FixPlanOut

PAIRS = [("submit_findings", FindingsOut), ("submit_fix_plan", FixPlanOut)]


def strict(schema):
    """Every object in the schema: additionalProperties false and all properties required; no $ref."""
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        for sub in schema["properties"].values():
            strict(sub)
    if schema.get("type") == "array":
        strict(schema["items"])
    assert "$ref" not in schema


@pytest.mark.parametrize("name,model", PAIRS)
def test_json_schema_and_pydantic_model_have_the_same_fields(name, model):
    function = SUBMIT_TOOLS[name]["function"]
    assert function["name"] == name and function["description"]
    schema = function["parameters"]
    assert set(schema["properties"]) == set(model.model_fields)
    strict(schema)
    # the nested objects too
    nested = {"submit_findings": ("paths", "PathOut"), "submit_fix_plan": ("reasons", "ReasonOut")}[name]
    item_fields = set(schema["properties"][nested[0]]["items"]["properties"])
    inner = model.model_fields[nested[0]].annotation.__args__[0]
    assert item_fields == set(inner.model_fields) and inner.__name__ == nested[1]


def test_models_forbid_extra_fields_and_check_the_enum():
    ok = {"cut_eids": ["e:1"], "chosen": "custom", "reasons": [{"eid": "e:1", "why": "w"}], "notes": ""}
    assert FixPlanOut(**ok).chosen == "custom"
    with pytest.raises(ValidationError):
        FixPlanOut(**ok, extra=1)
    with pytest.raises(ValidationError):
        FixPlanOut(**{**ok, "chosen": "best"})
    with pytest.raises(ValidationError):
        FixPlanOut(**{**ok, "reasons": [{"eid": "e:1"}]})
    assert SUBMIT_TOOLS["submit_fix_plan"]["function"]["parameters"]["properties"]["chosen"]["enum"] == ["source_side", "sink_side", "custom"]
    with pytest.raises(ValidationError):
        FindingsOut(reachable_jewels=[], paths=[{"target": "t", "eids": [], "hops": 2}], truncated=False, notes="")


def test_avoid_is_optional_for_pydantic_but_required_in_the_json_schema():
    base = {"cut_eids": ["e:1"], "chosen": "custom", "reasons": [{"eid": "e:1", "why": "w"}], "notes": "n"}
    assert FixPlanOut.model_validate(base).avoid == []  # an older cached answer without `avoid` is still valid
    plan = FixPlanOut.model_validate({**base, "avoid": [{"eid": "e:2", "quote": "do not touch it"}]})
    assert (plan.avoid[0].eid, plan.avoid[0].quote) == ("e:2", "do not touch it")
    with pytest.raises(ValidationError):
        FixPlanOut.model_validate({**base, "avoid": [{"eid": "e:2"}]})  # a quote is required for every avoided edge
    with pytest.raises(ValidationError):
        FixPlanOut.model_validate({**base, "avoid": [{"eid": "e:2", "quote": "q", "why": "extra"}]})
    schema = SUBMIT_TOOLS["submit_fix_plan"]["function"]["parameters"]
    assert "avoid" in schema["required"] and schema["properties"]["avoid"]["items"]["required"] == ["eid", "quote"]
