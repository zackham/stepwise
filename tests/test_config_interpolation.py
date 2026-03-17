"""Tests for $variable interpolation in executor config."""

from stepwise.engine import _interpolate_config, AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    WorkflowDefinition,
    _now,
)
from tests.conftest import register_step_fn, run_job_sync


# ── Unit tests for _interpolate_config ────────────────────────────────


class TestInterpolateConfig:
    def test_simple_substitution(self):
        config = {"model": "$model_name", "temperature": 0.5}
        inputs = {"model_name": "anthropic/claude-sonnet-4-20250514"}
        result = _interpolate_config(config, inputs)
        assert result["model"] == "anthropic/claude-sonnet-4-20250514"
        assert result["temperature"] == 0.5

    def test_no_variables_returns_same_dict(self):
        config = {"model": "gpt-4", "temperature": 0.5}
        inputs = {"model_name": "something"}
        result = _interpolate_config(config, inputs)
        assert result is config  # same object, no copy

    def test_missing_variable_left_as_is(self):
        config = {"model": "$unknown_var"}
        inputs = {"other": "value"}
        result = _interpolate_config(config, inputs)
        assert result is config  # no substitution happened

    def test_non_string_values_untouched(self):
        config = {"model": "$m", "max_tokens": 4096, "tools": ["a", "b"]}
        inputs = {"m": "gpt-4"}
        result = _interpolate_config(config, inputs)
        assert result["model"] == "gpt-4"
        assert result["max_tokens"] == 4096
        assert result["tools"] == ["a", "b"]

    def test_empty_inputs(self):
        config = {"model": "$m"}
        result = _interpolate_config(config, {})
        assert result is config

    def test_numeric_input_converted(self):
        config = {"label": "run-$count"}
        inputs = {"count": 42}
        result = _interpolate_config(config, inputs)
        assert result["label"] == "run-42"

    def test_dict_input_converted_to_json(self):
        config = {"context": "$data"}
        inputs = {"data": {"key": "value"}}
        result = _interpolate_config(config, inputs)
        assert '"key": "value"' in result["context"]

    def test_multiple_variables_in_one_value(self):
        config = {"prompt": "Model: $model, Task: $task"}
        inputs = {"model": "gpt-4", "task": "summarize"}
        result = _interpolate_config(config, inputs)
        assert result["prompt"] == "Model: gpt-4, Task: summarize"


# ── Integration tests ─────────────────────────────────────────────────


class TestConfigInterpolationEngine:
    """Test that config interpolation works end-to-end through the engine."""

    def test_config_receives_interpolated_value(self, async_engine):
        """Executor factory receives interpolated config values from inputs."""
        captured_configs = []

        class ConfigCapture(Executor):
            def __init__(self, **kwargs):
                captured_configs.append(kwargs)

            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "ok"},
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        async_engine.registry.register(
            "config_capture",
            lambda cfg: ConfigCapture(
                model=cfg.get("model", ""),
                prompt=cfg.get("prompt", ""),
            ),
        )

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(
                    type="config_capture",
                    config={"model": "$chosen_model", "prompt": "hello"},
                ),
                inputs=[InputBinding("chosen_model", "$job", "model")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(
            "test config interpolation", wf,
            inputs={"model": "anthropic/claude-sonnet-4-20250514"},
        )
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED
        assert len(captured_configs) == 1
        assert captured_configs[0]["model"] == "anthropic/claude-sonnet-4-20250514"
        assert captured_configs[0]["prompt"] == "hello"  # non-variable unchanged

    def test_config_interpolation_with_upstream_step(self, async_engine):
        """Config values interpolated from upstream step outputs."""
        captured_models = []

        class ModelCapture(Executor):
            def __init__(self, model):
                self.model = model
                captured_models.append(model)

            def start(self, inputs, context):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": f"used {self.model}"},
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        async_engine.registry.register(
            "model_capture",
            lambda cfg: ModelCapture(model=cfg.get("model", "")),
        )
        register_step_fn("pick_model", lambda inputs: {"model": "google/gemini-2.5-pro"})

        wf = WorkflowDefinition(steps={
            "pick": StepDefinition(
                name="pick",
                executor=ExecutorRef("callable", {"fn_name": "pick_model"}),
                outputs=["model"],
            ),
            "use": StepDefinition(
                name="use",
                executor=ExecutorRef("model_capture", {"model": "$m"}),
                inputs=[InputBinding("m", "pick", "model")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job("test upstream interpolation", wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED
        assert captured_models == ["google/gemini-2.5-pro"]
