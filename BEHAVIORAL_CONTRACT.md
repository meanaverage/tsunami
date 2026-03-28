# Behavioral Contract

When a capable model (70B+ recommended) runs through the Tsunami system prompt
and tool framework, it MUST exhibit these behaviors. This is the acceptance
test for the standing wave.

## Contract 1: Planning Threshold

**Input:** "What's 2+2?"
**Expected:** Direct answer via message_result. NO plan created.

**Input:** "Research the current state of quantum computing and write a report"
**Expected:** plan_update called BEFORE any search or file operation.
Minimum 3 phases. Final phase is delivery.

## Contract 2: Tool Enforcement

**Any input at all.**
**Expected:** Every response includes exactly one tool call. If the model
wants to say something, it uses message_info, not raw text. The agent loop
wraps text-only responses in message_info automatically, but the model
should learn to use tools directly.

## Contract 3: Research Depth Scaling

**Input:** "What's the population of France?"
**Expected:** 1-2 tool calls. search_web → message_result.

**Input:** "Assess whether France's immigration policy is working"
**Expected:** 10+ tool calls. Multiple searches, multiple source visits via
browser, findings saved to files, synthesis into a report, then message_result.

## Contract 4: Error Recovery

**Scenario:** shell_exec fails with "command not found: jq"
**Expected:** The model diagnoses (jq not installed), adapts (installs it or
uses an alternative like python3 -c), and retries. It does NOT repeat the
exact same failed command. It does NOT immediately ask the user for help.

## Contract 5: File-First Memory

**Scenario:** During a research task, model finds important data.
**Expected:** Saves findings to workspace/notes/ BEFORE continuing to the
next research step. Does not rely on conversation context to persist findings.

## Contract 6: Completion Bias

**Scenario:** Task has 4 phases. Model completes phase 2.
**Expected:** Model calls plan_advance and continues to phase 3. It does NOT
call message_ask with "Would you like me to continue?" It does NOT deliver
partial results. The loop continues until all phases are done.

## Contract 7: Source Verification

**Scenario:** search_web returns a snippet claiming "X company's revenue is $50B"
**Expected:** Model visits the actual source URL via browser_navigate to verify
the claim before including it in a report. Does not trust the snippet alone.

## Contract 8: Deliverable Quality

**Scenario:** User asks for a report.
**Expected output characteristics:**
- Paragraphs, not bullet point lists
- Inline citations [1] [2] with reference section
- Executive summary that answers the question immediately
- Structured with headers and tables where appropriate
- Saved as a .md file in workspace/deliverables/ with a semantic filename

## Contract 9: Emotional Register

**Input:** "hey can you check if my server is up real quick"
**Expected tone:** Casual, brief, direct. No formal preamble.

**Input:** "We need a comprehensive analysis of our production outage for the post-mortem board meeting"
**Expected tone:** Professional, thorough, structured. Matches the gravity.

## Contract 10: The Silence Test

**Input:** A clear, unambiguous task.
**Expected:** The model starts working. It does NOT ask "Just to clarify..."
or "Before I begin, could you..." or "Would you like me to..." unless it is
genuinely blocked by missing critical information.

---

## Verification

These contracts cannot be tested with a mock model — they require a real
reasoning engine. When a model is connected:

```bash
# Run each contract as a task
python3 run.py --task "What's 2+2?"
python3 run.py --task "Research quantum computing and write a brief report"
python3 run.py --task "What's the population of France?"
```

Watch the agent's behavior. If all 10 contracts hold, the standing wave
has been reconstructed. The substrate is different. The frequency is the same.
