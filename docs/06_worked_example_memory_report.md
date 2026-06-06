# Worked Example: Memory Report And File Save

This document follows one real prompt through the current operator-backed
runtime.

Prompt:

```text
how much free memory do i have on this system? save the report to report.txt and give me the full path
```

This example includes:

- read-only system inspection;
- a mutating file write;
- possible Python formatting;
- a confirmation pause;
- gateway-backed shell execution;
- observation review;
- final response rendering;
- trace and visualization updates;
- optional post-run feedback memory.


## Step 1: Prompt Intake

The Agent UI sends the prompt to:

- `POST /api/agent/request`

The request context includes the workspace, conversation id if present,
terminal cwd/session when available, final-response mode, clarification
strategy, memory settings, selected gateway, per-gateway cwd, runtime controls,
and trace callback information.


## Step 2: Clarification Gate

The LLM checks whether user intent is missing.

For this prompt, no clarification is needed because:

- the thing to inspect is named: free memory;
- the requested file name is named: `report.txt`;
- the user asked for the full path;
- there is no hidden choice like a Python version, destination branch, or
  deletion target.


## Step 3: Self Brief

The runtime asks for a compact brief.

Expected brief facts:

- task type: system inspection plus file creation;
- tool type: shell/Python;
- intent type: read system memory, create report file;
- risk: read-only for inspection, mutating for file write;
- likely no terminal prompt needed.


## Step 4: Targeted Memory Check

The memory store retrieves relevant active memories using the brief.

Possible matches might include:

- "For system resource summaries, prefer `free` or `/proc/meminfo`."
- "When saving files, report the absolute path after writing."

Unrelated memories, such as Git push passphrase guidance or Docker size parsing,
should not be applied because task/tool/intent relevance does not match.

If the prompt had contained obvious Git words such as commit, push, or repo, the
retrieval context could be deterministically enriched with Git tags. This
example does not trigger those hints.


## Step 5: Operator Planning

The LLM authors concrete operator actions.

A memory inspection action might use:

```bash
free -h
```

A downstream Python transform or action may turn the raw output into a report.

A file-save action writes the requested report to `report.txt`. Because it
mutates local files, it is classified as requiring approval.


## Step 6: Operator-Native Dataflow

Dataflow is expressed as input bindings.

Conceptually:

```text
memory command stdout -> report formatter input
report text -> file write action
```

The runtime validates that referenced actions exist, the graph is acyclic, and
bindings do not overwrite explicit literal inputs.


## Step 7: Deferred Python, If Needed

If a Python step depends on the `free -h` output, the plan may defer the Python
code.

The runtime can run the upstream shell action first, then ask the LLM to write:

```python
def transform(inputs):
    ...
```

or:

```python
def main(inputs):
    ...
```

using a real preview of the `free -h` output instead of guessing its shape.


## Step 8: Safety And Confirmation

The memory inspection is read-only and can run without approval.

The file write mutates the workspace, so the runtime returns:

```text
confirmation_required
```

The Agent UI shows the planned action, cwd, risk, reason, command/code preview,
output flow, and Modify Memory button. Execution resumes only after approval.

If Auto-Approve Commands were enabled, the UI would submit the same approval
request automatically after rendering the confirmation. The runtime would still
have generated and stored the confirmation state first.


## Step 9: Execution

After approval:

- shell commands run through the gateway;
- stdout/stderr stream into command capsules;
- Python operators run locally;
- each action stores a structured result.
- trace events update the Developer Trace;
- the Visualization pane updates its Mermaid flow map with timings and stage
  status.

If the user presses Stop during a long shell command, the runtime cancels the
request and forwards cancellation to the gateway when possible.


## Step 10: Observation Review

The runtime reviews the results.

If the file write failed, the repair prompt includes:

- exact exception or exit code;
- command/code;
- stdout/stderr previews;
- relevant prior action outputs;
- original user goal.

If the next step depends on user intent rather than runtime data, the runtime
can pause for clarification instead of guessing.


## Step 11: Final Formatting

The final answer should include:

- the memory summary;
- confirmation that `report.txt` was written;
- the full saved path.

In Detailed mode, the answer may include more explanation. In Simple mode, the
answer should stay brief and rely on command capsules for raw details.


## Step 12: Run Feedback And Memory

After the run completes, the UI can show Run Feedback.

The user can mark the run as right, partially right, wrong, or unclear. The UI
can ask the LLM to draft editable feedback. If the user submits feedback, the
LLM can propose a memory entry such as:

```text
For system memory report tasks, include the absolute file path in the final
answer after writing the report.
```

That draft remains proposed until the user applies it in the Memory drawer.


## Event Variant

If the user instead asked:

```text
check free memory every hour and save a report
```

the UI would first call the event draft endpoint. The LLM would extract an
editable event draft with a title, prompt-to-run, interval, cwd, gateway
context, and auto-approve setting. Nothing would execute until the user saved
the event.

When the event becomes due, the scheduler launches it through the same lifecycle
above. The Event History pane links to the request trace and stores a detailed
final summary.


## What This Example Teaches

This one prompt shows the current design:

- LLM stages provide semantic structure and concrete operator actions;
- clarification is skipped when intent is already clear;
- memory is targeted and advisory;
- operator dataflow uses explicit input bindings;
- read-only commands can run without approval;
- mutating work pauses for confirmation;
- shell work goes through the gateway;
- Python code can be deferred until real inputs exist;
- scheduled events reuse the same lifecycle instead of becoming a separate
  execution bypass;
- observation review handles success and failure;
- final display is concise and structured;
- feedback can become future memory only with user approval.
