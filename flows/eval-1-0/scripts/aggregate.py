#!/usr/bin/env python3
"""Aggregate scores and produce GO/NO-GO recommendation for eval-1.0.

Collects dimension scores from all Phase 2 scripts and Phase 3 agents.
Applies: three-state scoring, security severity veto, hard gate thresholds,
and generates remediation priorities.
"""

import json
import os
import sys


def parse_json_env(name, default=None):
    """Parse a JSON string from an environment variable."""
    val = os.environ.get(name, "")
    if not val:
        return default
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val if default is None else default


def parse_float_env(name, default=0.0):
    """Parse a float from an environment variable."""
    val = os.environ.get(name, "")
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_bool_env(name, default=False):
    """Parse a boolean from an environment variable."""
    val = os.environ.get(name, "")
    if isinstance(val, bool):
        return val
    return val.lower() in ("true", "1", "yes") if val else default


def main():
    # Collect all dimension scores
    dimensions = {}

    # Hard gate dimensions
    dimensions["core_execution"] = {
        "score_pct": parse_float_env("core_score_pct"),
        "rubric_results": parse_json_env("core_results", []),
        "is_hard_gate": True,
        "threshold": 80,
    }
    dimensions["security"] = {
        "score_pct": parse_float_env("security_score_pct"),
        "rubric_results": parse_json_env("security_results", []),
        "is_hard_gate": True,
        "threshold": 80,
    }
    dimensions["migration"] = {
        "score_pct": parse_float_env("migration_score_pct"),
        "rubric_results": parse_json_env("migration_results", []),
        "is_hard_gate": True,
        "threshold": 80,
    }
    dimensions["data_integrity"] = {
        "score_pct": parse_float_env("data_integrity_score_pct"),
        "rubric_results": parse_json_env("data_integrity_results", []),
        "is_hard_gate": True,
        "threshold": 80,
    }

    # Non-gate script dimensions
    dimensions["quality"] = {
        "score_pct": parse_float_env("quality_score_pct"),
        "rubric_results": parse_json_env("quality_results", []),
        "sub_dimensions": parse_json_env("quality_dimensions", {}),
        "is_hard_gate": False,
    }
    dimensions["adversarial"] = {
        "score_pct": parse_float_env("adversarial_score_pct"),
        "rubric_results": parse_json_env("adversarial_results", []),
        "critical_findings": parse_json_env("adversarial_critical", []),
        "is_hard_gate": False,
    }
    dimensions["new_user"] = {
        "score_pct": parse_float_env("new_user_score_pct"),
        "is_hard_gate": False,
    }

    # Synthesis dimensions
    dimensions["docs"] = {
        "score_pct": parse_float_env("docs_score_pct"),
        "rubric_results": parse_json_env("docs_results", []),
        "is_hard_gate": False,
    }
    dimensions["code_quality"] = {
        "score_pct": parse_float_env("code_score_pct"),
        "rubric_results": parse_json_env("code_results", []),
        "is_hard_gate": False,
    }
    dimensions["ux"] = {
        "score_pct": parse_float_env("ux_score_pct"),
        "rubric_results": parse_json_env("ux_results", []),
        "is_hard_gate": False,
    }

    # Security severity check
    security_has_blocker = parse_bool_env("security_has_blocker")
    security_blocker_ids = parse_json_env("security_blocker_ids", [])
    security_veto = security_has_blocker

    # Hard gate evaluation
    gates = {}
    all_gates_passed = True
    for dim_name, dim in dimensions.items():
        if not dim.get("is_hard_gate"):
            continue
        threshold = dim.get("threshold", 80)
        score = dim["score_pct"]
        passed = score >= threshold
        if dim_name == "security":
            passed = passed and not security_veto
        gates[dim_name] = {
            "score_pct": score,
            "threshold": threshold,
            "passed": passed,
            "veto": security_veto if dim_name == "security" else False,
        }
        if not passed:
            all_gates_passed = False

    # Insufficient evidence warnings
    insufficient_warnings = []
    for dim_name, dim in dimensions.items():
        results = dim.get("rubric_results", [])
        if not results:
            continue
        total = len(results)
        insufficient = sum(1 for r in results
                         if isinstance(r, dict) and r.get("result") == "insufficient_evidence")
        if total > 0 and insufficient / total > 0.3:
            insufficient_warnings.append({
                "dimension": dim_name,
                "insufficient_count": insufficient,
                "total": total,
                "pct": round(insufficient / total * 100),
            })

    # Overall score (mean of all dimension scores)
    all_scores = [dim["score_pct"] for dim in dimensions.values()]
    overall_avg = round(sum(all_scores) / len(all_scores)) if all_scores else 0

    # Recommendation
    if security_veto:
        recommendation = "NO-GO"
        recommendation_reason = (
            f"Security blocker findings: {', '.join(security_blocker_ids)}. "
            "Any security blocker is an automatic NO-GO regardless of scores."
        )
    elif not all_gates_passed:
        failed_gates = [name for name, g in gates.items() if not g["passed"]]
        recommendation = "NO-GO"
        recommendation_reason = (
            f"Hard gates failed: {', '.join(failed_gates)}. "
            "All hard gates must pass at ≥80% for GO."
        )
    elif overall_avg < 75:
        recommendation = "NO-GO"
        recommendation_reason = (
            f"Overall average score is {overall_avg}%, below the 75% threshold."
        )
    else:
        recommendation = "GO"
        recommendation_reason = (
            f"All hard gates passed, no security blockers, "
            f"overall average is {overall_avg}% (≥75%)."
        )

    # Remediation priorities
    remediation = []
    # P0: Failed hard gates
    for name, g in gates.items():
        if not g["passed"]:
            remediation.append({
                "priority": "P0",
                "dimension": name,
                "score_pct": g["score_pct"],
                "action": f"Fix {name} to reach ≥{g['threshold']}%",
                "reason": "Failed hard gate — must fix before 1.0",
            })
    if security_veto:
        remediation.append({
            "priority": "P0",
            "dimension": "security",
            "action": f"Resolve security blockers: {', '.join(security_blocker_ids)}",
            "reason": "Security blocker = automatic NO-GO",
        })
    # P1: Dimensions below 60%
    for dim_name, dim in dimensions.items():
        if dim["score_pct"] < 60 and not any(r["dimension"] == dim_name for r in remediation):
            remediation.append({
                "priority": "P1",
                "dimension": dim_name,
                "score_pct": dim["score_pct"],
                "action": f"Improve {dim_name} from {dim['score_pct']}% to ≥60%",
                "reason": "Below 60% threshold",
            })
    # P2: Dimensions below 80%
    for dim_name, dim in dimensions.items():
        if 60 <= dim["score_pct"] < 80 and not any(r["dimension"] == dim_name for r in remediation):
            remediation.append({
                "priority": "P2",
                "dimension": dim_name,
                "score_pct": dim["score_pct"],
                "action": f"Improve {dim_name} from {dim['score_pct']}% to ≥80%",
                "reason": "Below 80% — nice to fix",
            })

    # Build scorecard
    scorecard = {}
    for dim_name, dim in dimensions.items():
        results = dim.get("rubric_results", [])
        pass_count = sum(1 for r in results if isinstance(r, dict) and r.get("result") == "pass")
        fail_count = sum(1 for r in results if isinstance(r, dict) and r.get("result") == "fail")
        insufficient_count = sum(1 for r in results if isinstance(r, dict) and r.get("result") == "insufficient_evidence")
        scorecard[dim_name] = {
            "score_pct": dim["score_pct"],
            "pass_count": pass_count,
            "fail_count": fail_count,
            "insufficient_count": insufficient_count,
            "is_hard_gate": dim.get("is_hard_gate", False),
            "gate_passed": gates.get(dim_name, {}).get("passed") if dim.get("is_hard_gate") else None,
        }

    run_number = os.environ.get("eval_run_number", "1")

    output = {
        "scorecard": scorecard,
        "gates": gates,
        "all_gates_passed": all_gates_passed,
        "security_veto": security_veto,
        "overall_avg": overall_avg,
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
        "remediation": remediation,
        "insufficient_evidence_warnings": insufficient_warnings,
        "run_number": run_number,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
