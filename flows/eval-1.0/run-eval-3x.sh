#!/bin/bash
# Adaptive 3-run evaluation harness for Stepwise 1.0 readiness.
#
# Usage: ./flows/eval-1.0/run-eval-3x.sh [stepwise_path]
#
# Runs the eval-1.0 flow up to 3 times with adaptive early stopping:
# - If Run 1 fails a hard gate, Runs 2 and 3 are skipped
# - Final output includes variance analysis across completed runs

set -euo pipefail

STEPWISE_PATH="${1:-$(pwd)}"
RESULTS_DIR="$STEPWISE_PATH/reports"
mkdir -p "$RESULTS_DIR"

echo "═══════════════════════════════════════════════════════"
echo "  Stepwise 1.0 Evaluation — Adaptive 3-Run Harness"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "Project path: $STEPWISE_PATH"
echo "Results dir:  $RESULTS_DIR"
echo ""

# Collect results for variance analysis
declare -a RUN_SCORES=()
declare -a RUN_RECOMMENDATIONS=()
declare -a RUN_FILES=()
COMPLETED_RUNS=0

run_eval() {
    local run_num="$1"
    echo "───────────────────────────────────────────────────────"
    echo "  Run $run_num of 3"
    echo "───────────────────────────────────────────────────────"
    echo ""

    local output
    output=$(uv run stepwise run --wait --local eval-1.0 \
        --var "stepwise_path=$STEPWISE_PATH" \
        --var "eval_run_number=$run_num" \
        2>/dev/null) || true

    if [ -z "$output" ]; then
        echo "ERROR: Run $run_num produced no output"
        return 1
    fi

    # Save raw output
    local output_file="$RESULTS_DIR/eval-run-${run_num}-raw.json"
    echo "$output" > "$output_file"
    RUN_FILES+=("$output_file")

    # Parse key fields from the job output
    local recommendation
    recommendation=$(echo "$output" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Navigate to aggregate-scores output
    runs = data if isinstance(data, dict) else {}
    print(runs.get('recommendation', 'UNKNOWN'))
except: print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

    local overall_avg
    overall_avg=$(echo "$output" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('overall_avg', 0))
except: print(0)
" 2>/dev/null || echo "0")

    local all_gates
    all_gates=$(echo "$output" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('all_gates_passed', False))
except: print(False)
" 2>/dev/null || echo "False")

    RUN_SCORES+=("$overall_avg")
    RUN_RECOMMENDATIONS+=("$recommendation")
    COMPLETED_RUNS=$((COMPLETED_RUNS + 1))

    echo ""
    echo "  Result: $recommendation (overall: ${overall_avg}%)"
    echo "  Gates:  $all_gates"
    echo ""

    # Return 1 if gates failed (triggers early stopping)
    if [ "$all_gates" = "False" ] || [ "$all_gates" = "false" ]; then
        return 1
    fi
    return 0
}

# ── Run 1 ──────────────────────────────────────────────────
if ! run_eval 1; then
    echo "⚠ Run 1 failed hard gates or produced errors."
    echo "  Skipping Runs 2 and 3 (adaptive early stopping)."
    echo ""
else
    # ── Run 2 ──────────────────────────────────────────────────
    if ! run_eval 2; then
        echo "⚠ Run 2 failed hard gates."
        echo "  Skipping Run 3."
        echo ""
    else
        # ── Run 3 ──────────────────────────────────────────────────
        run_eval 3 || true
    fi
fi

# ── Variance Analysis ──────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
echo "  Summary — $COMPLETED_RUNS run(s) completed"
echo "═══════════════════════════════════════════════════════"
echo ""

for i in $(seq 0 $((COMPLETED_RUNS - 1))); do
    echo "  Run $((i + 1)): ${RUN_RECOMMENDATIONS[$i]} (${RUN_SCORES[$i]}%)"
done
echo ""

if [ "$COMPLETED_RUNS" -ge 2 ]; then
    # Compute variance across runs
    python3 -c "
import json, sys

scores = [float(s) for s in sys.argv[1:]]
n = len(scores)
mean = sum(scores) / n
variance = sum((s - mean) ** 2 for s in scores) / n
std_dev = variance ** 0.5

print(f'  Score variance: {variance:.1f}')
print(f'  Std deviation:  {std_dev:.1f}%')
print()

if std_dev > 15:
    print('  ⚠ HIGH VARIANCE (>15%) — agent dimensions may be inconsistent')
    print('    Consider reviewing agent synthesis scores across runs.')
elif std_dev > 5:
    print('  ℹ Moderate variance — some agent score fluctuation is normal')
else:
    print('  ✓ Low variance — scores are consistent across runs')
" "${RUN_SCORES[@]}"
else
    echo "  (Variance analysis requires ≥2 completed runs)"
fi

echo ""
echo "Raw results saved to: $RESULTS_DIR/"
echo ""
