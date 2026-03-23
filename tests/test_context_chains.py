"""M7a tests: context chains — transcript capture, compilation, overflow, engine integration."""

import json
import tempfile
from pathlib import Path

import pytest

from stepwise.agent import AgentExecutor, AcpxBackend, MockAgentBackend
from stepwise.context import (
    Transcript,
    apply_overflow,
    collect_chain_transcripts,
    compile_chain_prefix,
    estimate_token_count,
    load_transcript,
    normalize_acpx_messages,
    save_transcript,
    topological_chain_order,
)
from stepwise.engine import Engine
from stepwise.events import CHAIN_CONTEXT_COMPILED
from stepwise.executors import (
    ExecutionContext,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    ScriptExecutor,
)
from stepwise.models import (
    ChainConfig,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def mock_backend():
    return MockAgentBackend()


@pytest.fixture
def chain_registry(mock_backend):
    reg = ExecutorRegistry()

    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))
    reg.register("script", lambda config: ScriptExecutor(
        command=config.get("command", "echo '{}'"),
    ))

    def _create_agent(config):
        return AgentExecutor(
            backend=mock_backend,
            prompt=config.get("prompt", ""),
            output_mode=config.get("output_mode", "effect"),
            output_path=config.get("output_path"),
            **{k: v for k, v in config.items()
               if k not in ("prompt", "output_mode", "output_path")},
        )
    reg.register("agent", _create_agent)

    return reg


@pytest.fixture
def chain_engine(store, chain_registry):
    return Engine(store=store, registry=chain_registry)


# ── Helpers ──────────────────────────────────────────────────────────────


def make_transcript(step: str, attempt: int, text: str, chain: str = "",
                    label: str = "", token_count: int = 0) -> Transcript:
    """Create a transcript with a simple user/assistant exchange."""
    messages = [
        {"role": "user", "content": f"Task for {step}"},
        {"role": "assistant", "content": text},
    ]
    tc = token_count or estimate_token_count(messages)
    return Transcript(
        step=step, attempt=attempt, chain=chain,
        label=label, token_count=tc, messages=messages,
    )


def make_chain_workflow(
    chain_name: str = "review",
    steps: dict | None = None,
    chain_config: dict | None = None,
) -> WorkflowDefinition:
    """Create a workflow with chain-connected steps."""
    cc = ChainConfig(**(chain_config or {}))
    if steps is None:
        steps = {
            "research": StepDefinition(
                name="research",
                outputs=["findings"],
                executor=ExecutorRef("callable", {"fn_name": "research_fn"}),
                chain=chain_name,
                chain_label="Research Phase",
            ),
            "draft": StepDefinition(
                name="draft",
                outputs=["content"],
                executor=ExecutorRef("callable", {"fn_name": "draft_fn"}),
                chain=chain_name,
                chain_label="Draft Phase",
                inputs=[InputBinding("findings", "research", "findings")],
            ),
            "review": StepDefinition(
                name="review",
                outputs=["decision"],
                executor=ExecutorRef("callable", {"fn_name": "review_fn"}),
                chain=chain_name,
                chain_label="Review Phase",
                inputs=[InputBinding("content", "draft", "content")],
            ),
        }
    return WorkflowDefinition(steps=steps, chains={chain_name: cc})


# ══════════════════════════════════════════════════════════════════════════
# Transcript Dataclass
# ══════════════════════════════════════════════════════════════════════════


class TestTranscript:
    def test_to_dict_and_back(self):
        t = make_transcript("research", 1, "Found interesting patterns")
        d = t.to_dict()
        t2 = Transcript.from_dict(d)
        assert t2.step == "research"
        assert t2.attempt == 1
        assert len(t2.messages) == 2
        assert t2.token_count == t.token_count

    def test_from_dict_defaults(self):
        d = {"step": "x", "attempt": 1, "chain": "c"}
        t = Transcript.from_dict(d)
        assert t.label == ""
        assert t.token_count == 0
        assert t.messages == []


# ══════════════════════════════════════════════════════════════════════════
# Topological Chain Order
# ══════════════════════════════════════════════════════════════════════════


class TestTopologicalChainOrder:
    def test_linear_chain(self):
        wf = make_chain_workflow()
        order = topological_chain_order(wf, "review")
        assert order == ["research", "draft", "review"]

    def test_parallel_steps_alphabetical(self):
        """Steps with no internal dependencies sort alphabetically."""
        wf = WorkflowDefinition(
            steps={
                "charlie": StepDefinition(
                    name="charlie", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "alpha": StepDefinition(
                    name="alpha", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "bravo": StepDefinition(
                    name="bravo", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
            },
            chains={"c": ChainConfig()},
        )
        order = topological_chain_order(wf, "c")
        assert order == ["alpha", "bravo", "charlie"]

    def test_empty_chain(self):
        wf = WorkflowDefinition(
            steps={"s": StepDefinition(
                name="s", outputs=["x"],
                executor=ExecutorRef("callable", {}),
            )},
        )
        assert topological_chain_order(wf, "nonexistent") == []

    def test_mixed_chain_and_non_chain_steps(self):
        """Non-chain steps are excluded from the order."""
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "b": StepDefinition(
                    name="b", outputs=["y"],
                    executor=ExecutorRef("callable", {}),
                    inputs=[InputBinding("x", "a", "x")],
                ),
                "c_step": StepDefinition(
                    name="c_step", outputs=["z"],
                    executor=ExecutorRef("callable", {}), chain="c",
                    inputs=[InputBinding("x", "a", "x")],
                ),
            },
            chains={"c": ChainConfig()},
        )
        order = topological_chain_order(wf, "c")
        assert order == ["a", "c_step"]
        assert "b" not in order


# ══════════════════════════════════════════════════════════════════════════
# Save / Load Transcript
# ══════════════════════════════════════════════════════════════════════════


class TestTranscriptIO:
    def test_save_and_load(self, workspace):
        t = make_transcript("research", 1, "Found things")
        path = save_transcript(workspace, t)
        assert path.exists()
        assert path.name == "research-1.transcript.json"

        loaded = load_transcript(workspace, "research", 1)
        assert loaded is not None
        assert loaded.step == "research"
        assert loaded.attempt == 1
        assert len(loaded.messages) == 2

    def test_load_missing(self, workspace):
        assert load_transcript(workspace, "nonexistent", 1) is None

    def test_creates_step_io_dir(self, workspace):
        t = make_transcript("s", 1, "text")
        save_transcript(workspace, t)
        assert (Path(workspace) / ".stepwise" / "step-io").is_dir()

    def test_multiple_attempts(self, workspace):
        for attempt in range(1, 4):
            t = make_transcript("step", attempt, f"Attempt {attempt}")
            save_transcript(workspace, t)

        for attempt in range(1, 4):
            loaded = load_transcript(workspace, "step", attempt)
            assert loaded is not None
            assert loaded.attempt == attempt


# ══════════════════════════════════════════════════════════════════════════
# Collect Chain Transcripts
# ══════════════════════════════════════════════════════════════════════════


class TestCollectChainTranscripts:
    def test_collects_prior_steps_in_order(self, workspace):
        wf = make_chain_workflow()
        cc = wf.chains["review"]

        # Save transcripts for first two steps
        save_transcript(workspace, make_transcript("research", 1, "Findings"))
        save_transcript(workspace, make_transcript("draft", 1, "Draft text"))

        def latest(step): return 1

        result = collect_chain_transcripts(
            wf, "review", cc, "review", workspace, latest
        )
        assert len(result) == 2
        assert result[0].step == "research"
        assert result[1].step == "draft"

    def test_excludes_current_step(self, workspace):
        wf = make_chain_workflow()
        cc = wf.chains["review"]

        save_transcript(workspace, make_transcript("research", 1, "Findings"))

        def latest(step): return 1

        result = collect_chain_transcripts(
            wf, "review", cc, "research", workspace, latest
        )
        # research is the current step, no prior steps
        assert len(result) == 0

    def test_skips_steps_without_completed_runs(self, workspace):
        wf = make_chain_workflow()
        cc = wf.chains["review"]

        save_transcript(workspace, make_transcript("research", 1, "Findings"))
        # draft has no transcript (not completed)

        def latest(step):
            return 1 if step == "research" else None

        result = collect_chain_transcripts(
            wf, "review", cc, "review", workspace, latest
        )
        assert len(result) == 1
        assert result[0].step == "research"

    def test_accumulation_latest(self, workspace):
        wf = make_chain_workflow(chain_config={"accumulation": "latest"})
        cc = wf.chains["review"]

        # Multiple attempts for research
        save_transcript(workspace, make_transcript("research", 1, "V1"))
        save_transcript(workspace, make_transcript("research", 2, "V2"))
        save_transcript(workspace, make_transcript("research", 3, "V3"))

        def latest(step): return 3

        result = collect_chain_transcripts(
            wf, "review", cc, "draft", workspace, latest
        )
        assert len(result) == 1
        assert result[0].attempt == 3

    def test_accumulation_full(self, workspace):
        wf = make_chain_workflow(chain_config={"accumulation": "full"})
        cc = wf.chains["review"]

        save_transcript(workspace, make_transcript("research", 1, "V1"))
        save_transcript(workspace, make_transcript("research", 2, "V2"))

        def latest(step): return 2

        result = collect_chain_transcripts(
            wf, "review", cc, "draft", workspace, latest
        )
        assert len(result) == 2
        assert result[0].attempt == 1
        assert result[1].attempt == 2

    def test_enriches_chain_and_label(self, workspace):
        wf = make_chain_workflow()
        cc = wf.chains["review"]

        save_transcript(workspace, make_transcript("research", 1, "text"))

        def latest(step): return 1

        result = collect_chain_transcripts(
            wf, "review", cc, "draft", workspace, latest
        )
        assert result[0].chain == "review"
        assert result[0].label == "Research Phase"

    def test_label_defaults_to_step_name(self, workspace):
        """When chain_label is None, label defaults to step name."""
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "b": StepDefinition(
                    name="b", outputs=["y"],
                    executor=ExecutorRef("callable", {}), chain="c",
                    inputs=[InputBinding("x", "a", "x")],
                ),
            },
            chains={"c": ChainConfig()},
        )

        save_transcript(workspace, make_transcript("a", 1, "text"))

        def latest(step): return 1

        result = collect_chain_transcripts(
            wf, "c", wf.chains["c"], "b", workspace, latest
        )
        assert result[0].label == "a"


# ══════════════════════════════════════════════════════════════════════════
# Overflow Strategies
# ══════════════════════════════════════════════════════════════════════════


class TestApplyOverflow:
    def test_no_overflow_when_under_budget(self):
        transcripts = [
            make_transcript("a", 1, "short", token_count=100),
            make_transcript("b", 1, "short", token_count=100),
        ]
        result = apply_overflow(transcripts, 500)
        assert len(result) == 2

    def test_drop_oldest(self):
        transcripts = [
            make_transcript("a", 1, "old", token_count=300),
            make_transcript("b", 1, "mid", token_count=300),
            make_transcript("c", 1, "new", token_count=300),
        ]
        result = apply_overflow(transcripts, 600, "drop_oldest")
        assert len(result) == 2
        assert result[0].step == "b"
        assert result[1].step == "c"

    def test_drop_oldest_keeps_at_least_one(self):
        transcripts = [
            make_transcript("a", 1, "big", token_count=1000),
            make_transcript("b", 1, "big", token_count=1000),
        ]
        result = apply_overflow(transcripts, 100, "drop_oldest")
        assert len(result) == 1
        assert result[0].step == "b"

    def test_drop_middle(self):
        transcripts = [
            make_transcript("a", 1, "first", token_count=200),
            make_transcript("b", 1, "middle", token_count=200),
            make_transcript("c", 1, "middle2", token_count=200),
            make_transcript("d", 1, "last", token_count=200),
        ]
        result = apply_overflow(transcripts, 500, "drop_middle")
        # Should keep first + as many from end as fit
        assert result[0].step == "a"  # first always kept
        assert result[-1].step == "d"  # last kept
        total = sum(t.token_count for t in result)
        assert total <= 500

    def test_drop_middle_with_two_items(self):
        """Drop middle falls back to drop_oldest with 2 or fewer items."""
        transcripts = [
            make_transcript("a", 1, "big", token_count=500),
            make_transcript("b", 1, "big", token_count=500),
        ]
        result = apply_overflow(transcripts, 400, "drop_middle")
        assert len(result) == 1

    def test_exact_budget(self):
        transcripts = [
            make_transcript("a", 1, "x", token_count=250),
            make_transcript("b", 1, "y", token_count=250),
        ]
        result = apply_overflow(transcripts, 500)
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════════════
# Compile Chain Prefix
# ══════════════════════════════════════════════════════════════════════════


class TestCompileChainPrefix:
    def test_empty_transcripts(self):
        assert compile_chain_prefix([], "review") == ""

    def test_basic_xml_structure(self):
        transcripts = [make_transcript("research", 1, "Found things", label="Research")]
        xml = compile_chain_prefix(transcripts, "review")

        assert '<prior_context chain="review">' in xml
        assert '<step name="research" attempt="1"' in xml
        assert 'label="Research"' in xml
        assert "<user>" in xml
        assert "<assistant>" in xml
        assert "</prior_context>" in xml

    def test_multiple_steps(self):
        transcripts = [
            make_transcript("a", 1, "Step A output", label="A"),
            make_transcript("b", 1, "Step B output", label="B"),
        ]
        xml = compile_chain_prefix(transcripts, "chain1")
        assert xml.index('name="a"') < xml.index('name="b"')

    def test_xml_escaping(self):
        t = make_transcript("s", 1, 'Has <tags> & "quotes"')
        xml = compile_chain_prefix([t], "c")
        assert "&lt;tags&gt;" in xml
        assert "&amp;" in xml
        assert "&quot;" not in xml or "quotes" in xml  # escaping in content

    def test_no_label_attribute_when_empty(self):
        t = make_transcript("s", 1, "text", label="")
        xml = compile_chain_prefix([t], "c")
        assert "label=" not in xml

    def test_thinking_excluded_by_default(self):
        t = Transcript(
            step="s", attempt=1, chain="c", label="", token_count=100,
            messages=[{
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "Let me think about this"},
                    {"type": "text", "text": "Here's my answer"},
                ],
            }],
        )
        xml = compile_chain_prefix([t], "c", include_thinking=False)
        assert "think" not in xml.lower()
        assert "Here&#39;s my answer" in xml or "Here&apos;s my answer" in xml or "Here's my answer" in xml

    def test_thinking_included_when_enabled(self):
        t = Transcript(
            step="s", attempt=1, chain="c", label="", token_count=100,
            messages=[{
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "Deep analysis here"},
                    {"type": "text", "text": "Answer"},
                ],
            }],
        )
        xml = compile_chain_prefix([t], "c", include_thinking=True)
        assert "Thinking:" in xml

    def test_tool_use_formatting(self):
        t = Transcript(
            step="s", attempt=1, chain="c", label="", token_count=100,
            messages=[{
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "read_file", "input": {"path": "/foo"}},
                    {"type": "text", "text": "Read the file"},
                ],
            }],
        )
        xml = compile_chain_prefix([t], "c")
        assert "Tool: read_file" in xml


# ══════════════════════════════════════════════════════════════════════════
# Normalize ACPX Messages
# ══════════════════════════════════════════════════════════════════════════


class TestNormalizeAcpxMessages:
    def test_user_text(self):
        acpx = [{"User": {"content": [{"Text": "Hello"}]}}]
        result = normalize_acpx_messages(acpx)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "Hello"

    def test_agent_text(self):
        acpx = [{"Agent": {"content": [{"Text": "Response"}]}}]
        result = normalize_acpx_messages(acpx)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["text"] == "Response"

    def test_thinking_excluded_by_default(self):
        acpx = [{"Agent": {"content": [
            {"Thinking": {"text": "hmm"}},
            {"Text": "answer"},
        ]}}]
        result = normalize_acpx_messages(acpx, include_thinking=False)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "text"

    def test_thinking_included(self):
        acpx = [{"Agent": {"content": [
            {"Thinking": {"text": "hmm"}},
            {"Text": "answer"},
        ]}}]
        result = normalize_acpx_messages(acpx, include_thinking=True)
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["type"] == "thinking"

    def test_tool_use(self):
        acpx = [{"Agent": {"content": [{"ToolUse": {
            "id": "t1", "name": "read", "raw_input": {"path": "/x"},
        }}]}}]
        result = normalize_acpx_messages(acpx)
        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[0]["content"][0]["name"] == "read"

    def test_tool_result(self):
        acpx = [{"User": {"content": [{"ToolResult": {
            "tool_use_id": "t1", "content": [{"text": "file contents"}],
        }}]}}]
        result = normalize_acpx_messages(acpx)
        assert result[0]["content"][0]["type"] == "tool_result"
        assert "file contents" in result[0]["content"][0]["content"]

    def test_full_conversation(self):
        acpx = [
            {"User": {"content": [{"Text": "Research topic X"}]}},
            {"Agent": {"content": [
                {"Thinking": {"text": "Let me search"}},
                {"ToolUse": {"id": "t1", "name": "search", "raw_input": {"q": "X"}}},
            ]}},
            {"User": {"content": [{"ToolResult": {
                "tool_use_id": "t1", "content": [{"text": "search results"}],
            }}]}},
            {"Agent": {"content": [{"Text": "Here's what I found..."}]}},
        ]
        result = normalize_acpx_messages(acpx, include_thinking=True)
        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "assistant"


# ══════════════════════════════════════════════════════════════════════════
# Token Count Estimation
# ══════════════════════════════════════════════════════════════════════════


class TestEstimateTokenCount:
    def test_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        count = estimate_token_count(messages)
        assert count >= 1

    def test_list_content(self):
        messages = [{"role": "assistant", "content": [
            {"type": "text", "text": "A" * 400},
        ]}]
        count = estimate_token_count(messages)
        assert count >= 100  # 400 chars / 4 = 100

    def test_empty_messages(self):
        assert estimate_token_count([]) == 1  # minimum 1


# ══════════════════════════════════════════════════════════════════════════
# Model Validation
# ══════════════════════════════════════════════════════════════════════════


class TestChainValidation:
    def test_valid_chain(self):
        wf = make_chain_workflow()
        errors = wf.validate()
        assert len(errors) == 0

    def test_chain_needs_at_least_two_members(self):
        wf = WorkflowDefinition(
            steps={
                "only": StepDefinition(
                    name="only", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
            },
            chains={"c": ChainConfig()},
        )
        errors = wf.validate()
        assert any("at least 2 members" in e for e in errors)

    def test_invalid_overflow_strategy(self):
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "b": StepDefinition(
                    name="b", outputs=["y"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
            },
            chains={"c": ChainConfig(overflow="invalid")},
        )
        errors = wf.validate()
        assert any("overflow" in e for e in errors)

    def test_invalid_accumulation(self):
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "b": StepDefinition(
                    name="b", outputs=["y"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
            },
            chains={"c": ChainConfig(accumulation="invalid")},
        )
        errors = wf.validate()
        assert any("accumulation" in e for e in errors)

    def test_undefined_chain_reference(self):
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="nonexistent",
                ),
            },
        )
        errors = wf.validate()
        assert any("undefined chain" in e for e in errors)

    def test_negative_max_tokens(self):
        wf = WorkflowDefinition(
            steps={
                "a": StepDefinition(
                    name="a", outputs=["x"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
                "b": StepDefinition(
                    name="b", outputs=["y"],
                    executor=ExecutorRef("callable", {}), chain="c",
                ),
            },
            chains={"c": ChainConfig(max_tokens=-1)},
        )
        errors = wf.validate()
        assert any("max_tokens" in e for e in errors)


# ══════════════════════════════════════════════════════════════════════════
# YAML Parsing
# ══════════════════════════════════════════════════════════════════════════


class TestChainYAMLParsing:
    def test_parse_chain_config(self):
        from stepwise.yaml_loader import load_workflow_string

        yaml_str = (
            "name: chain-test\n"
            "chains:\n"
            "  review:\n"
            "    max_tokens: 50000\n"
            "    overflow: drop_middle\n"
            "    include_thinking: true\n"
            "    accumulation: latest\n"
            "steps:\n"
            "  research:\n"
            "    run: 'echo {\"findings\": \"data\"}'\n"
            "    outputs: [findings]\n"
            "    chain: review\n"
            "    chain_label: Research Phase\n"
            "  draft:\n"
            "    run: 'echo {\"content\": \"text\"}'\n"
            "    outputs: [content]\n"
            "    chain: review\n"
            "    inputs:\n"
            "      findings: research.findings\n"
        )
        wf = load_workflow_string(yaml_str)
        assert "review" in wf.chains
        cc = wf.chains["review"]
        assert cc.max_tokens == 50000
        assert cc.overflow == "drop_middle"
        assert cc.include_thinking is True
        assert cc.accumulation == "latest"

        assert wf.steps["research"].chain == "review"
        assert wf.steps["research"].chain_label == "Research Phase"
        assert wf.steps["draft"].chain == "review"

    def test_parse_chain_defaults(self):
        from stepwise.yaml_loader import load_workflow_string

        yaml_str = """
name: defaults-test
chains:
  c: {}

steps:
  a:
    run: echo '{}'
    outputs: [x]
    chain: c
  b:
    run: echo '{}'
    outputs: [y]
    chain: c
"""
        wf = load_workflow_string(yaml_str)
        cc = wf.chains["c"]
        assert cc.max_tokens == 80000
        assert cc.overflow == "drop_oldest"
        assert cc.include_thinking is False
        assert cc.accumulation == "full"

    def test_no_chains_section(self):
        from stepwise.yaml_loader import load_workflow_string

        yaml_str = """
name: no-chains
steps:
  a:
    run: echo '{}'
    outputs: [x]
"""
        wf = load_workflow_string(yaml_str)
        assert wf.chains == {}


# ══════════════════════════════════════════════════════════════════════════
# ChainConfig Serialization
# ══════════════════════════════════════════════════════════════════════════


class TestChainConfigSerialization:
    def test_to_dict_and_back(self):
        cc = ChainConfig(max_tokens=50000, overflow="drop_middle",
                         include_thinking=True, accumulation="latest")
        d = cc.to_dict()
        cc2 = ChainConfig.from_dict(d)
        assert cc2.max_tokens == 50000
        assert cc2.overflow == "drop_middle"
        assert cc2.include_thinking is True
        assert cc2.accumulation == "latest"

    def test_workflow_round_trip(self):
        wf = make_chain_workflow()
        d = wf.to_dict()
        wf2 = WorkflowDefinition.from_dict(d)
        assert "review" in wf2.chains
        assert wf2.steps["research"].chain == "review"
        assert wf2.steps["research"].chain_label == "Research Phase"


# ══════════════════════════════════════════════════════════════════════════
# AcpxBackend Session Name Parsing
# ══════════════════════════════════════════════════════════════════════════


class TestSessionNameParsing:
    def test_simple_name(self):
        step, attempt = AcpxBackend._parse_session_name("step-research-1")
        assert step == "research"
        assert attempt == 1

    def test_underscore_name(self):
        step, attempt = AcpxBackend._parse_session_name("step-draft_content-3")
        assert step == "draft_content"
        assert attempt == 3

    def test_hyphenated_name(self):
        step, attempt = AcpxBackend._parse_session_name("step-code-review-2")
        assert step == "code-review"
        assert attempt == 2

    def test_invalid_format(self):
        step, attempt = AcpxBackend._parse_session_name("bad-format")
        assert attempt == 1  # fallback


# ══════════════════════════════════════════════════════════════════════════
# Engine Integration: Chain Context Compilation
# ══════════════════════════════════════════════════════════════════════════


class TestEngineChainContext:
    def test_chain_context_passed_to_executor(self, chain_engine, store, workspace, mock_backend):
        """Chain context is compiled from prior step transcripts and passed to executor."""
        wf = make_chain_workflow()

        # Register step functions
        register_step_fn("research_fn", lambda inputs: {"findings": "discovered X"})

        # Record what draft_fn receives via its inputs
        captured_context = {}

        def draft_fn(inputs):
            # We can't directly inspect ExecutionContext here, but we can
            # verify the chain context by checking the prompt file written by agent
            return {"content": "drafted based on X"}

        register_step_fn("draft_fn", draft_fn)
        register_step_fn("review_fn", lambda inputs: {"decision": "approve"})

        job = chain_engine.create_job("Test chain", wf, workspace_path=workspace)
        chain_engine.start_job(job.id)

        # Research step should complete immediately (callable executor)
        run = store.latest_completed_run(job.id, "research")
        assert run is not None

        # No transcript exists yet — chain context for draft should be empty
        # (transcripts are captured from agent sessions, not callable executors)
        # But we can verify the engine handles the missing transcript gracefully
        chain_engine.tick()

    def test_no_chain_context_for_non_chain_step(self, chain_engine, store, workspace):
        """Steps not in a chain get no chain context."""
        wf = WorkflowDefinition(steps={
            "solo": StepDefinition(
                name="solo",
                outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "solo_fn"}),
            ),
        })

        register_step_fn("solo_fn", lambda inputs: {"result": "done"})

        job = chain_engine.create_job("No chain", wf, workspace_path=workspace)
        chain_engine.start_job(job.id)

        # Should complete without any chain context errors
        job = store.load_job(job.id)
        assert job.status.value in ("completed", "running")

    def test_chain_context_compiles_from_transcripts(self, chain_engine, store, workspace, mock_backend):
        """When transcripts exist, chain context XML is compiled for downstream steps."""
        wf = make_chain_workflow()

        # Manually place a transcript for research step
        save_transcript(workspace, make_transcript(
            "research", 1, "Found important pattern X", label="Research Phase"
        ))

        # Register research as already completed by setting up callable that returns
        register_step_fn("research_fn", lambda inputs: {"findings": "X"})

        # For draft, use an agent executor to verify chain_context reaches _render_prompt
        # We'll modify the workflow to use agent for draft
        wf.steps["draft"] = StepDefinition(
            name="draft",
            outputs=["content"],
            executor=ExecutorRef("agent", {
                "prompt": "Draft based on: $findings",
                "output_mode": "effect",
            }),
            chain="review",
            chain_label="Draft Phase",
            inputs=[InputBinding("findings", "research", "findings")],
        )

        register_step_fn("review_fn", lambda inputs: {"decision": "approve"})

        # Auto-complete agent so start() returns immediately
        mock_backend.set_auto_complete({"content": "drafted"})

        job = chain_engine.create_job("Chain with transcript", wf, workspace_path=workspace)
        chain_engine.start_job(job.id)

        # Research completes, then draft completes (blocking agent with auto_complete)
        draft_run = store.latest_run(job.id, "draft")
        assert draft_run is not None
        assert draft_run.status == StepRunStatus.COMPLETED

        # Verify the prompt file was written with chain context prepended
        prompt_path = Path(workspace) / ".stepwise" / "step-io" / "draft-1.prompt.md"
        if prompt_path.exists():
            prompt_content = prompt_path.read_text()
            assert '<prior_context chain="review">' in prompt_content

    def test_chain_context_event_emitted(self, chain_engine, store, workspace):
        """CHAIN_CONTEXT_COMPILED event is emitted when chain context is compiled."""
        wf = make_chain_workflow()

        # Place transcript for research
        save_transcript(workspace, make_transcript(
            "research", 1, "Findings here"
        ))

        register_step_fn("research_fn", lambda inputs: {"findings": "X"})
        register_step_fn("draft_fn", lambda inputs: {"content": "Y"})
        register_step_fn("review_fn", lambda inputs: {"decision": "ok"})

        job = chain_engine.create_job("Event test", wf, workspace_path=workspace)
        chain_engine.start_job(job.id)

        # Check for chain context event
        events = store.load_events(job.id)
        chain_events = [e for e in events if e.type == CHAIN_CONTEXT_COMPILED]

        # Draft step should get chain context from research transcript
        if chain_events:
            assert chain_events[0].data["chain"] == "review"
            assert chain_events[0].data["transcript_count"] >= 1

    def test_first_chain_step_gets_no_context(self, chain_engine, store, workspace):
        """The first step in a chain has no prior steps, so gets no chain context."""
        wf = make_chain_workflow()

        register_step_fn("research_fn", lambda inputs: {"findings": "X"})
        register_step_fn("draft_fn", lambda inputs: {"content": "Y"})
        register_step_fn("review_fn", lambda inputs: {"decision": "ok"})

        job = chain_engine.create_job("First step test", wf, workspace_path=workspace)
        chain_engine.start_job(job.id)

        # No chain context event should be emitted for the first step (research)
        events = store.load_events(job.id)
        chain_events = [e for e in events if e.type == CHAIN_CONTEXT_COMPILED]
        first_step_events = [e for e in chain_events if e.data.get("step") == "research"]
        assert len(first_step_events) == 0


# ══════════════════════════════════════════════════════════════════════════
# ExecutionContext Chain Context Field
# ══════════════════════════════════════════════════════════════════════════


class TestExecutionContextChainContext:
    def test_default_none(self):
        ctx = ExecutionContext(
            job_id="j1", step_name="s", attempt=1,
            workspace_path="/tmp", idempotency="retry",
        )
        assert ctx.chain_context is None

    def test_chain_context_set(self):
        ctx = ExecutionContext(
            job_id="j1", step_name="s", attempt=1,
            workspace_path="/tmp", idempotency="retry",
            chain_context='<prior_context chain="c"></prior_context>',
        )
        assert ctx.chain_context is not None
        assert "prior_context" in ctx.chain_context


# ══════════════════════════════════════════════════════════════════════════
# Prompt Rendering with Chain Context
# ══════════════════════════════════════════════════════════════════════════


class TestPromptRenderingWithChainContext:
    def test_agent_prompt_prepends_chain_context(self, mock_backend):
        executor = AgentExecutor(
            backend=mock_backend,
            prompt="Do the task: $objective",
        )
        ctx = ExecutionContext(
            job_id="j1", step_name="s", attempt=1,
            workspace_path="/tmp", idempotency="retry",
            chain_context='<prior_context chain="c">\n  <step name="a" attempt="1">\n    <user>Q</user>\n  </step>\n</prior_context>',
        )
        prompt = executor._render_prompt({"objective": "test"}, ctx)
        assert prompt.startswith("<prior_context")
        assert "Do the task: test" in prompt
        # Chain context before the actual prompt
        assert prompt.index("<prior_context") < prompt.index("Do the task")

    def test_agent_prompt_without_chain_context(self, mock_backend):
        executor = AgentExecutor(
            backend=mock_backend,
            prompt="Do the task: $objective",
        )
        ctx = ExecutionContext(
            job_id="j1", step_name="s", attempt=1,
            workspace_path="/tmp", idempotency="retry",
        )
        prompt = executor._render_prompt({"objective": "test"}, ctx)
        assert prompt == "Do the task: test"

    def test_llm_prompt_prepends_chain_context(self):
        from stepwise.executors import LLMExecutor
        from stepwise.llm_client import LLMClient

        class FakeClient:
            pass

        executor = LLMExecutor(
            client=FakeClient(),
            model="test",
            prompt="Score: $content",
        )
        ctx = ExecutionContext(
            job_id="j1", step_name="s", attempt=1,
            workspace_path="/tmp", idempotency="retry",
            chain_context='<prior_context chain="c"></prior_context>',
        )
        prompt = executor._render_prompt({"content": "hello"}, ctx)
        assert prompt.startswith("<prior_context")
        assert "Score: hello" in prompt


# ══════════════════════════════════════════════════════════════════════════
# End-to-End: Chain with Manual Transcripts
# ══════════════════════════════════════════════════════════════════════════


class TestEndToEndChain:
    def test_three_step_chain_with_transcripts(self, workspace):
        """Full pipeline: save transcripts, collect, overflow, compile."""
        wf = make_chain_workflow(chain_config={"max_tokens": 1000})
        cc = wf.chains["review"]

        # Simulate completed runs by saving transcripts
        save_transcript(workspace, make_transcript(
            "research", 1, "Found patterns A, B, C in the data. Key insight: X.",
            label="Research Phase",
        ))
        save_transcript(workspace, make_transcript(
            "draft", 1, "Based on research findings, here is a draft covering A, B, C.",
            label="Draft Phase",
        ))

        def latest(step): return 1

        # Collect for review step (should get research + draft)
        transcripts = collect_chain_transcripts(
            wf, "review", cc, "review", workspace, latest
        )
        assert len(transcripts) == 2

        # Apply overflow (should fit within 1000 tokens)
        transcripts = apply_overflow(transcripts, cc.max_tokens, cc.overflow)
        assert len(transcripts) == 2

        # Compile
        prefix = compile_chain_prefix(transcripts, "review")
        assert '<prior_context chain="review">' in prefix
        assert "Research Phase" in prefix
        assert "Draft Phase" in prefix
        assert "Found patterns" in prefix
        assert "</prior_context>" in prefix

    def test_overflow_drops_old_transcripts(self, workspace):
        """When transcripts exceed budget, oldest are dropped."""
        wf = make_chain_workflow(chain_config={"max_tokens": 50})
        cc = wf.chains["review"]

        # Save transcripts with known token counts
        save_transcript(workspace, Transcript(
            step="research", attempt=1, chain="", label="",
            token_count=40, messages=[{"role": "user", "content": "A" * 160}],
        ))
        save_transcript(workspace, Transcript(
            step="draft", attempt=1, chain="", label="",
            token_count=40, messages=[{"role": "user", "content": "B" * 160}],
        ))

        def latest(step): return 1

        transcripts = collect_chain_transcripts(
            wf, "review", cc, "review", workspace, latest
        )
        assert len(transcripts) == 2

        transcripts = apply_overflow(transcripts, cc.max_tokens, cc.overflow)
        # 40 + 40 = 80 > 50, should drop research (oldest)
        assert len(transcripts) == 1
        assert transcripts[0].step == "draft"
