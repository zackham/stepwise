# Use Cases

Real-world workflow patterns you can adapt for your own projects.

## Code review pipeline

Agent reviews code changes, human decides whether to accept or loop back for another pass.

```yaml
name: code-review
steps:
  gather-context:
    run: git diff main --stat && git log main..HEAD --oneline
    outputs: [diff_summary, commits]

  review:
    executor: agent
    prompt: |
      Review this code change. Identify bugs, style issues, and improvements.
      Diff: $diff_summary  Commits: $commits
    inputs:
      diff_summary: gather-context.diff_summary
      commits: gather-context.commits
    outputs: [verdict, issues, suggestions]

  decide:
    executor: human
    prompt: "Review found $issues. Accept or request changes?"
    outputs: [decision]
    inputs: { issues: review.issues }
    exits:
      - name: accept
        when: "outputs.decision == 'accept'"
        action: advance
      - name: redo
        when: "outputs.decision == 'request-changes'"
        action: loop
        target: review
```

The loop between `decide` and `review` lets the human iterate with the agent until the code is clean.

## Content pipeline

LLM researches and drafts, human reviews, loop back to revise or advance to publish.

```yaml
name: content-pipeline
steps:
  research:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Research this topic: $topic
      Return JSON: {"outline": "...", "key_points": [...], "sources": [...]}
    inputs: { topic: $job.topic }
    outputs: [outline, key_points, sources]

  draft:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Write a blog post. Outline: $outline  Key points: $key_points
      Return JSON: {"title": "...", "body": "...", "summary": "..."}
    inputs:
      outline: research.outline
      key_points: research.key_points
    outputs: [title, body, summary]

  review:
    executor: human
    prompt: "Title: $title\n\n$body\n\nApprove or revise?"
    outputs: [decision]
    inputs: { title: draft.title, body: draft.body }
    exits:
      - name: approve
        when: "outputs.decision == 'approve'"
        action: advance
      - name: revise
        when: "outputs.decision == 'revise'"
        action: loop
        target: draft

  publish:
    run: |
      curl -s -X POST "$CMS_URL/api/posts" \
        -d "{\"title\": \"$title\", \"body\": \"$body\"}" | jq '{url: .url, published: true}'
    inputs: { title: draft.title, body: draft.body }
    outputs: [url, published]
    sequencing: [review]
```

Content never publishes without human approval, but the LLM does the heavy lifting on research and drafting.

## Deploy pipeline

Build, test, human approval, then a route step branches to deploy or rollback.

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
    run: cd $repo_path && make test && echo "{\"passed\": true}"
    inputs: { repo_path: $job.repo_path }
    outputs: [passed]
    sequencing: [build]

  approve:
    executor: human
    prompt: "Build $sha ready. Tests passed. Deploy to production?"
    outputs: [decision]
    inputs: { sha: build.sha }

  deploy-or-rollback:
    route:
      - name: deploy
        when: "inputs.decision == 'deploy'"
        flow:
          steps:
            run-deploy:
              run: |
                kubectl set image deployment/app app=$artifact
                echo "{\"status\": \"deployed\"}"
              inputs: { artifact: $job.artifact }
              outputs: [status]
      - name: rollback
        flow:
          steps:
            run-rollback:
              run: kubectl rollout undo deployment/app && echo "{\"status\": \"rolled_back\"}"
              outputs: [status]
    inputs: { decision: approve.decision, artifact: build.artifact }
    outputs: [status]
```

The `route:` step branches based on the human's decision. Each branch is its own sub-flow, keeping deploy and rollback logic cleanly separated.

## Research synthesis

Fan out LLM calls across sub-topics with for-each, then synthesize the results.

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
          executor: llm
          model: anthropic/claude-sonnet-4
          prompt: |
            Research: $topic_name — $topic_focus
            Return JSON: {"findings": "...", "confidence": 0.0-1.0}
          inputs: { topic_name: $job.topic.name, topic_focus: $job.topic.focus }
          outputs: [findings, confidence]

  synthesize:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Synthesize these findings into a coherent analysis.
      Question: $question  Findings: $results
      Return JSON: {"analysis": "...", "conclusion": "..."}
    inputs: { question: $job.question, results: investigate.results }
    outputs: [analysis, conclusion]
```

`for_each:` runs independent LLM calls in parallel across all sub-topics. `on_error: continue` means one failed topic does not block the rest. Results arrive in source order.

## Data processing with retry

Fetch from a flaky API (with retry decorator), LLM transforms the data, script validates output.

```yaml
name: data-processing
steps:
  fetch:
    run: curl -s "$api_url" | jq '{records: .data, count: (.data | length)}'
    inputs: { api_url: $job.api_url }
    outputs: [records, count]
    decorators:
      - type: retry
        max_attempts: 3
        delay_seconds: 5

  transform:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: |
      Clean and normalize these $count records: $records
      Return JSON: {"cleaned": [...], "anomalies": [...]}
    inputs: { records: fetch.records, count: fetch.count }
    outputs: [cleaned, anomalies]

  validate:
    run: |
      python3 -c "
      import json, os
      cleaned = json.loads(os.environ['cleaned'])
      errors = [r for r in cleaned if not r.get('id')]
      print(json.dumps({'valid': len(errors)==0, 'record_count': len(cleaned), 'errors': errors[:5]}))
      "
    inputs: { cleaned: transform.cleaned }
    outputs: [valid, record_count, errors]
```

The `retry` decorator on `fetch` automatically retries on failure with a 5-second delay. The LLM handles messy transformation, and a deterministic script validates output for hard guarantees on data quality.
