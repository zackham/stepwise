# Use Cases

Real workflow patterns. Each one solves a problem where the old approach was either manual, fragile, or both.

## Podcast production pipeline

**The problem:** Producing a podcast episode involves research, script writing, multiple rounds of editing, audio synthesis, and metadata generation. Each stage depends on the previous one. Manual process: 3-4 hours of context-switching. Automated but unstructured: no quality gates, no place to intervene when the script is off.

**The flow:** 14 steps, 3 executor types, 2 human gates.

```yaml
name: podcast-production
steps:
  research:
    executor: agent
    prompt: |
      Research this topic thoroughly: $topic
      Find key facts, expert opinions, and interesting angles.
    inputs: { topic: $job.topic }
    outputs: [findings, sources, angles]

  outline:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Create a podcast episode outline from these findings.
      Topic: $topic
      Findings: $findings
      Angles: $angles
    inputs:
      topic: $job.topic
      findings: research.findings
      angles: research.angles
    outputs: [outline, segments]

  draft-script:
    executor: agent
    prompt: |
      Write a conversational podcast script following this outline.
      Outline: $outline
      Style: Informative but casual. Two hosts.
    inputs:
      outline: outline.outline
    outputs: [script, word_count]

  editorial-review:
    executor: external
    prompt: |
      Review this podcast script ($word_count words).

      $script

      Approve, or provide revision notes?
    inputs:
      script: draft-script.script
      word_count: draft-script.word_count
    outputs: [decision, notes]
    exits:
      - name: approved
        when: "outputs.decision == 'approve'"
        action: advance
      - name: revise
        when: "outputs.decision == 'revise' and attempt < 3"
        action: loop
        target: draft-script
      - name: cap
        when: "attempt >= 3"
        action: advance

  synthesize-audio:
    run: python3 scripts/tts_synthesis.py
    inputs: { script: draft-script.script }
    outputs: [audio_path, duration_seconds]
    after: [editorial-review]

  generate-metadata:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Generate podcast metadata: title, description, tags, chapters.
      Script: $script  Duration: $duration seconds.
    inputs:
      script: draft-script.script
      duration: synthesize-audio.duration_seconds
    outputs: [title, description, tags, chapters]

  publish:
    run: python3 scripts/publish_episode.py
    inputs:
      audio: synthesize-audio.audio_path
      title: generate-metadata.title
      description: generate-metadata.description
    outputs: [episode_url, published]
```

The human reviews the script at `editorial-review` — with full context of the research and outline that produced it. If it's not right, the agent revises with editorial notes. Audio synthesis and metadata generation run in parallel after approval.

## Research synthesis with fan-out

**The problem:** Researching a complex question means breaking it into sub-topics, investigating each one, and synthesizing the results. Doing this manually is slow. Doing it with a single LLM call produces shallow analysis. You want depth on each sub-topic with a coherent synthesis.

**The flow:** Fan out across sub-topics in parallel, then merge results.

```yaml
name: research-synthesis
steps:
  plan:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Break this question into 3-5 sub-topics: $question
      Return JSON: {"topics": [{"name": "...", "focus": "..."}]}
    inputs: { question: $job.question }
    outputs: [topics]

  investigate:
    for_each: plan.topics
    as: topic
    on_error: continue
    outputs: [results]
    flow:
      steps:
        research:
          executor: agent
          prompt: |
            Deep research on: $topic_name
            Focus area: $topic_focus
            Use web search. Find primary sources.
          inputs:
            topic_name: $job.topic.name
            topic_focus: $job.topic.focus
          outputs: [findings, confidence, sources]

  synthesize:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Synthesize these research findings into a coherent analysis.
      Original question: $question
      Findings from $count sub-topics: $results
    inputs:
      question: $job.question
      results: investigate.results
      count: $job.count
    outputs: [analysis, conclusion, gaps]

  review:
    executor: external
    prompt: |
      Research synthesis complete.
      Question: $question
      Conclusion: $conclusion
      Gaps identified: $gaps

      Accept or request deeper investigation?
    inputs:
      question: $job.question
      conclusion: synthesize.conclusion
      gaps: synthesize.gaps
    outputs: [decision]
```

`for_each` runs independent agent sessions in parallel across all sub-topics. `on_error: continue` means one failed topic doesn't block the rest. The synthesis step gets all results in order, and the human reviews the final analysis.

## Deploy pipeline with approval gate

**The problem:** Deploying to production requires building, testing, and a human sign-off. The build and test steps are deterministic. The approval is judgment. The deploy is irreversible. You want all three in one declared workflow with clear boundaries.

```yaml
name: deploy
steps:
  build:
    run: |
      cd $repo_path && make build
      echo "{\"artifact\": \"build/$(git rev-parse --short HEAD).tar.gz\", \"sha\": \"$(git rev-parse --short HEAD)\"}"
    inputs: { repo_path: $job.repo_path }
    outputs: [artifact, sha]

  test:
    run: |
      cd $repo_path && make test && echo "{\"passed\": true, \"suite_count\": 47}"
    inputs: { repo_path: $job.repo_path }
    outputs: [passed, suite_count]
    after: [build]

  approve:
    executor: external
    prompt: |
      Build $sha ready. $suite_count test suites passed.

      Deploy to production?
    outputs: [decision]
    inputs:
      sha: build.sha
      suite_count: test.suite_count

  deploy:
    run: |
      kubectl set image deployment/app app=$artifact
      echo "{\"status\": \"deployed\", \"url\": \"https://app.example.com\"}"
    inputs:
      decision: approve.decision
      artifact: build.artifact
    when: "decision == 'deploy'"
    outputs: [status, url]

  rollback:
    run: |
      kubectl rollout undo deployment/app
      echo "{\"status\": \"rolled_back\"}"
    inputs:
      decision: approve.decision
    when: "decision != 'deploy'"
    outputs: [status]
```

`when` conditions on `deploy` and `rollback` mean only one branch activates based on the human's decision. The other is skipped cleanly by the engine.

## Code review with iterative feedback

**The problem:** Automated code review is either too shallow (a single LLM pass) or too noisy (dumps everything without judgment). You want an agent to review thoroughly, a human to triage, and the ability to loop back for fixes.

```yaml
name: code-review
steps:
  gather-context:
    run: |
      git diff main --stat > /tmp/diff.txt
      git log main..HEAD --oneline > /tmp/commits.txt
      echo "{\"diff_summary\": \"$(cat /tmp/diff.txt)\", \"commits\": \"$(cat /tmp/commits.txt)\"}"
    outputs: [diff_summary, commits]

  review:
    executor: agent
    prompt: |
      Review this code change. Be specific about:
      1. Bugs or logic errors
      2. Security concerns
      3. Performance issues
      4. Style/maintainability

      Diff: $diff_summary
      Commits: $commits
    inputs:
      diff_summary: gather-context.diff_summary
      commits: gather-context.commits
    outputs: [verdict, issues, suggestions]
    limits:
      cost_usd: 3.00
      duration_minutes: 10

  decide:
    executor: external
    prompt: |
      Agent verdict: $verdict
      Issues found: $issues

      Accept, request changes, or have the agent fix them?
    outputs: [decision]
    inputs:
      verdict: review.verdict
      issues: review.issues
    exits:
      - name: accept
        when: "outputs.decision == 'accept'"
        action: advance
      - name: fix
        when: "outputs.decision == 'fix'"
        action: advance
      - name: redo
        when: "outputs.decision == 'redo' and attempt < 3"
        action: loop
        target: review
      - name: give-up
        when: "attempt >= 3"
        action: advance

  apply-fixes:
    executor: agent
    prompt: |
      Apply these fixes to the codebase: $suggestions
      Run tests after each fix.
    inputs:
      decision: decide.decision
      suggestions: review.suggestions
    when: "decision == 'fix'"
    outputs: [result]
    limits:
      cost_usd: 5.00
```

The human at `decide` has three options: accept the review, have the agent apply fixes, or send it back for another review pass. Cost limits on both agent steps prevent runaway spending.

## Data pipeline with retry and validation

**The problem:** Fetching from a flaky API, transforming messy data, and validating the output. The fetch might fail. The LLM might hallucinate structure. The validation is deterministic. You want retries on the flaky parts and hard checks on the output.

```yaml
name: data-processing
steps:
  fetch:
    run: curl -s "$api_url" | jq '{records: .data, count: (.data | length)}'
    inputs: { api_url: $job.api_url }
    outputs: [records, count]
    decorators:
      - type: retry
        config: { max_retries: 3, backoff: exponential }

  transform:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Clean and normalize these $count records: $records
      Standardize dates to ISO 8601, normalize phone numbers, remove duplicates.
      Return JSON: {"cleaned": [...], "anomalies": [...]}
    inputs: { records: fetch.records, count: fetch.count }
    outputs: [cleaned, anomalies]

  validate:
    run: |
      python3 -c "
      import json, os
      cleaned = json.loads(os.environ['STEPWISE_INPUT_cleaned'])
      errors = [r for r in cleaned if not r.get('id')]
      print(json.dumps({
          'valid': len(errors) == 0,
          'record_count': len(cleaned),
          'error_count': len(errors)
      }))
      "
    inputs: { cleaned: transform.cleaned }
    outputs: [valid, record_count, error_count]
    exits:
      - name: clean
        when: "outputs.valid == True"
        action: advance
      - name: retry-transform
        when: "outputs.valid == False and attempt < 3"
        action: loop
        target: transform
      - name: escalate
        when: "attempt >= 3"
        action: escalate
```

The `retry` decorator on `fetch` handles transient network failures with exponential backoff. The exit rule on `validate` catches bad LLM output and loops back to `transform` for another attempt. After 3 failures, it escalates to a human rather than silently producing bad data.

## Waiting for external systems

**The problem:** Your workflow depends on something you don't control — a CI build, a PR review, a deployment health check. You need to wait without burning compute.

```yaml
name: wait-for-review-then-deploy
steps:
  create-pr:
    run: |
      gh pr create --title "$title" --body "$body"
      echo "{\"pr_number\": $(gh pr view --json number -q .number)}"
    inputs:
      title: $job.title
      body: $job.body
    outputs: [pr_number]

  wait-for-review:
    executor: poll
    check_command: |
      gh pr view $pr_number --json reviewDecision \
        --jq 'select(.reviewDecision != "") | {decision: .reviewDecision}'
    interval_seconds: 60
    prompt: "Waiting for PR #$pr_number review"
    inputs: { pr_number: create-pr.pr_number }
    outputs: [decision]

  deploy:
    run: scripts/deploy.sh
    inputs: { decision: wait-for-review.decision }
    when: "decision == 'APPROVED'"
    outputs: [status]
```

The `poll` executor runs `check_command` every 60 seconds. Empty stdout = not ready yet, check again. JSON on stdout = condition met, step completes. No compute burned between checks.

## Patterns to notice

Across these use cases, a few patterns repeat:

- **Human gates at judgment points, not everywhere.** The human reviews the script, not the research. Approves the deploy, not the build. Put gates where judgment matters.
- **Exit rules as quality loops.** Score < threshold? Loop back. Attempts exhausted? Escalate. Declare the quality bar in YAML, enforce it with the engine.
- **Mixed executors in one DAG.** Scripts for deterministic work, LLMs for single-call tasks, agents for complex tasks, external for human decisions, polls for waiting. Use each for what it's good at.
- **Cost limits on agent steps.** Agents can burn through API credits. `limits.cost_usd` and `limits.duration_minutes` are guardrails, not suggestions.
- **`on_error: continue` for fan-out.** One failed sub-topic shouldn't kill the whole research synthesis. Collect what you can, note what failed.

For the full YAML schema, see the [Flow Reference](flow-reference.md). For step-by-step authorship guidance, see [Writing Flows](writing-flows.md).
