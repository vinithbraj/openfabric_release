const state = {
  profiles: [],
  runs: [],
  evals: [],
  selectedRunId: "",
  selectedRun: null,
  controls: {},
};
const Core = window.OpenFabricAgentUi;

const boot = document.querySelector("#app-boot-screen");
const dbPath = document.querySelector("#reliability-db-path");
const refreshButton = document.querySelector("#reliability-refresh");
const statusLine = document.querySelector("#reliability-status");
const modelList = document.querySelector("#model-list");
const runList = document.querySelector("#run-list");
const evalList = document.querySelector("#eval-list");
const taxonomyList = document.querySelector("#failure-taxonomy");
const timeline = document.querySelector("#run-timeline");
const runEvidence = document.querySelector("#run-evidence");
const runTitle = document.querySelector("#run-title");
const runVerification = document.querySelector("#run-verification");
const exportJson = document.querySelector("#export-json");
const exportMarkdown = document.querySelector("#export-markdown");
const evalRunButton = document.querySelector("#eval-run");
const controlsSave = document.querySelector("#controls-save");

const summaryNodes = {
  events: document.querySelector("#summary-events"),
  runs: document.querySelector("#summary-runs"),
  failures: document.querySelector("#summary-failures"),
  recoveries: document.querySelector("#summary-recoveries"),
  verifications: document.querySelector("#summary-verifications"),
  profiles: document.querySelector("#summary-profiles"),
};

const controls = {
  mode: document.querySelector("#control-mode"),
  verifier: document.querySelector("#control-verifier"),
  probes: document.querySelector("#control-probes"),
  repairs: document.querySelector("#control-repairs"),
  actionCap: document.querySelector("#control-action-cap"),
  envelope: document.querySelector("#control-envelope"),
};

async function apiJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = "Request failed.";
    try {
      const payload = await response.json();
      message = String(payload.detail || message);
    } catch (_error) {
      message = (await response.text()) || message;
    }
    throw new Error(message);
  }
  return response.json();
}

function setStatus(message) {
  if (statusLine) statusLine.textContent = message;
}

function notifyUiError(title, message, fix = "") {
  Core?.notifyUiError?.({ title, message, fix });
}

function setText(node, value) {
  if (node) node.textContent = String(value ?? "");
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function renderSummary(summary = {}) {
  setText(summaryNodes.events, summary.event_count || 0);
  setText(summaryNodes.runs, summary.run_count || 0);
  setText(summaryNodes.failures, summary.failure_count || 0);
  setText(summaryNodes.recoveries, summary.recovery_count || 0);
  setText(summaryNodes.verifications, summary.verification_count || 0);
  setText(summaryNodes.profiles, summary.profile_count || 0);
}

function renderControls(nextControls = {}) {
  state.controls = { ...nextControls };
  if (controls.mode) controls.mode.value = nextControls.reliability_mode || "aggressive";
  if (controls.verifier) {
    controls.verifier.checked = nextControls.reliability_verifier_enforced !== false;
  }
  if (controls.probes) controls.probes.value = String(nextControls.reliability_max_recovery_probes ?? 3);
  if (controls.repairs) {
    controls.repairs.value = String(nextControls.reliability_max_autonomous_repair_attempts ?? 2);
  }
  if (controls.actionCap) {
    controls.actionCap.value = String(nextControls.reliability_weak_model_plan_action_cap ?? 4);
  }
  if (controls.envelope) {
    controls.envelope.value = String(nextControls.reliability_approval_envelope_budget ?? 2);
  }
}

function clearList(node, message) {
  if (!node) return;
  node.replaceChildren();
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = message;
  node.append(empty);
}

function renderProfiles() {
  if (!modelList) return;
  modelList.replaceChildren();
  if (!state.profiles.length) {
    clearList(modelList, "No model profiles yet.");
    return;
  }
  for (const profile of state.profiles) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "reliability-list-item";
    const title = document.createElement("strong");
    title.textContent = profile.model_id || "unknown";
    const meta = document.createElement("span");
    meta.textContent = `${profile.total_failures || 0} failures - ${percent(profile.recovery_success_rate)} recovered`;
    const metrics = document.createElement("div");
    metrics.className = "profile-metrics";
    metrics.append(
      metric("JSON ok", percent(profile.json_validity_rate)),
      metric("Schema", profile.schema_failures),
      metric("Command", profile.command_failures),
      metric("Python", profile.python_failures),
      metric("Verify", percent(profile.verifier_quality)),
      metric("Weakness", percent(profile.weakness_score)),
    );
    item.append(title, meta, metrics);
    item.addEventListener("click", () => renderTaxonomyForProfile(profile));
    modelList.append(item);
  }
}

function metric(label, value) {
  const wrapper = document.createElement("div");
  const labelNode = document.createElement("span");
  labelNode.className = "metric-label";
  labelNode.textContent = label;
  const valueNode = document.createElement("span");
  valueNode.className = "metric-value";
  valueNode.textContent = String(value ?? 0);
  wrapper.append(labelNode, valueNode);
  return wrapper;
}

function renderTaxonomyForProfile(profile) {
  const items = [
    ["Schema", profile.schema_failures],
    ["JSON", profile.json_failures],
    ["Placeholders", profile.placeholder_failures],
    ["Command", profile.command_failures],
    ["Python", profile.python_failures],
    ["Verification", profile.verification_failures],
    ["Formatting", profile.formatting_failures],
    ["Low confidence", profile.low_confidence_failures],
  ];
  renderTaxonomy(items);
}

function renderGlobalTaxonomy() {
  const totals = new Map();
  for (const profile of state.profiles) {
    totals.set("Schema", (totals.get("Schema") || 0) + Number(profile.schema_failures || 0));
    totals.set("JSON", (totals.get("JSON") || 0) + Number(profile.json_failures || 0));
    totals.set("Placeholders", (totals.get("Placeholders") || 0) + Number(profile.placeholder_failures || 0));
    totals.set("Command", (totals.get("Command") || 0) + Number(profile.command_failures || 0));
    totals.set("Python", (totals.get("Python") || 0) + Number(profile.python_failures || 0));
    totals.set("Verification", (totals.get("Verification") || 0) + Number(profile.verification_failures || 0));
    totals.set("Formatting", (totals.get("Formatting") || 0) + Number(profile.formatting_failures || 0));
    totals.set("Low confidence", (totals.get("Low confidence") || 0) + Number(profile.low_confidence_failures || 0));
  }
  renderTaxonomy([...totals.entries()]);
}

function renderTaxonomy(items) {
  if (!taxonomyList) return;
  taxonomyList.replaceChildren();
  const sorted = [...items].sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0));
  if (!sorted.length || sorted.every((item) => Number(item[1] || 0) === 0)) {
    clearList(taxonomyList, "No failure taxonomy data yet.");
    return;
  }
  for (const [label, value] of sorted) {
    const item = document.createElement("div");
    item.className = "taxonomy-item";
    const name = document.createElement("strong");
    name.textContent = label;
    const count = document.createElement("span");
    count.textContent = String(value || 0);
    item.append(name, count);
    taxonomyList.append(item);
  }
}

function renderRuns() {
  if (!runList) return;
  runList.replaceChildren();
  if (!state.runs.length) {
    clearList(runList, "No reliability runs yet.");
    return;
  }
  for (const run of state.runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "reliability-list-item";
    button.dataset.requestId = run.request_id;
    const title = document.createElement("strong");
    title.textContent = run.request_id || "run";
    const meta = document.createElement("span");
    meta.textContent = `${run.failure_count || 0} failures - ${run.recovery_count || 0} recoveries`;
    button.append(title, meta);
    button.addEventListener("click", () => selectRun(run.request_id));
    runList.append(button);
  }
}

function renderEvals() {
  if (!evalList) return;
  evalList.replaceChildren();
  if (!state.evals.length) {
    clearList(evalList, "No eval runs yet.");
    return;
  }
  for (const evalRun of state.evals) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "reliability-list-item";
    const title = document.createElement("strong");
    title.textContent = `${percent(evalRun.score)} score`;
    const meta = document.createElement("span");
    meta.textContent = `${evalRun.recovered_cases || 0} recovered - ${evalRun.blocked_cases || 0} blocked - ${evalRun.failed_cases || 0} failed`;
    item.append(title, meta);
    item.addEventListener("click", () => {
      if (runEvidence) runEvidence.textContent = JSON.stringify(evalRun, null, 2);
      setStatus(`Eval ${evalRun.eval_id || ""}`);
    });
    evalList.append(item);
  }
}

async function selectRun(requestId) {
  if (!requestId) return;
  setStatus("Loading run...");
  const payload = await apiJson(`/api/agent/reliability/runs/${encodeURIComponent(requestId)}`);
  state.selectedRunId = requestId;
  state.selectedRun = payload;
  renderSelectedRun();
  setStatus("Ready");
}

function renderSelectedRun() {
  const run = state.selectedRun;
  if (!run) {
    if (timeline) clearList(timeline, "Select a run to inspect recovery events.");
    if (runEvidence) runEvidence.textContent = "";
    if (runTitle) runTitle.textContent = "Select a run";
    if (runVerification) {
      runVerification.textContent = "Idle";
      runVerification.dataset.status = "";
    }
    for (const link of [exportJson, exportMarkdown]) {
      if (!link) continue;
      link.href = "#";
      link.classList.add("disabled-link");
    }
    return;
  }
  if (runTitle) runTitle.textContent = run.request_id || "Reliability run";
  const verificationStatus = run.verification?.status || "unknown";
  if (runVerification) {
    runVerification.textContent = verificationStatus;
    runVerification.dataset.status = verificationStatus;
  }
  if (exportJson) {
    exportJson.href = run.report_json_url || "#";
    exportJson.classList.toggle("disabled-link", !run.report_json_url);
  }
  if (exportMarkdown) {
    exportMarkdown.href = run.report_markdown_url || "#";
    exportMarkdown.classList.toggle("disabled-link", !run.report_markdown_url);
  }
  renderTimeline(run.events || []);
  if (runEvidence) {
    const events = run.events || [];
    runEvidence.textContent = JSON.stringify(
      {
        failure_counts: run.failure_counts,
        recovery_counts: run.recovery_counts,
        verification: run.verification,
        approval_envelope: run.approval_envelope,
        evidence_audits: events
          .filter((event) => event.event_kind === "evidence_audited")
          .map((event) => event.evidence),
        coverage_reviews: events
          .filter((event) => event.event_kind === "coverage_reviewed")
          .map((event) => event.evidence),
        formatter_retries: events
          .filter((event) => event.event_kind === "formatter_retried")
          .map((event) => event.evidence),
      },
      null,
      2,
    );
  }
}

function renderTimeline(events) {
  if (!timeline) return;
  timeline.replaceChildren();
  if (!events.length) {
    clearList(timeline, "No reliability events for this run.");
    return;
  }
  for (const event of events) {
    const item = document.createElement("article");
    item.className = "timeline-item";
    const title = document.createElement("strong");
    title.textContent = event.title || event.event_kind || "Event";
    const meta = document.createElement("span");
    const labels = [event.event_kind, event.failure_kind, event.recovery_action]
      .filter(Boolean)
      .join(" / ");
    meta.textContent = `${event.created_at || ""} - ${labels}`;
    const summary = document.createElement("span");
    summary.textContent = event.summary || "";
    item.append(title, meta, summary);
    timeline.append(item);
  }
}

async function loadReliability() {
  setStatus("Loading...");
  const [profilePayload, runPayload, evalPayload] = await Promise.all([
    apiJson("/api/agent/reliability/profile"),
    apiJson("/api/agent/reliability/runs?limit=80"),
    apiJson("/api/agent/reliability/evals?limit=20"),
  ]);
  if (dbPath) dbPath.textContent = profilePayload.db_path || "";
  renderSummary(profilePayload.summary || {});
  renderControls(profilePayload.controls || {});
  state.profiles = Array.isArray(profilePayload.profiles) ? profilePayload.profiles : [];
  state.runs = Array.isArray(runPayload.runs) ? runPayload.runs : [];
  state.evals = Array.isArray(evalPayload.evals) ? evalPayload.evals : [];
  renderProfiles();
  renderGlobalTaxonomy();
  renderRuns();
  renderEvals();
  if (state.selectedRunId && state.runs.some((run) => run.request_id === state.selectedRunId)) {
    await selectRun(state.selectedRunId);
  } else if (state.runs[0]) {
    await selectRun(state.runs[0].request_id);
  } else {
    state.selectedRun = null;
    renderSelectedRun();
  }
  setStatus("Ready");
}

async function saveControls() {
  setStatus("Saving controls...");
  const payload = {
    reliability_mode: controls.mode?.value || "aggressive",
    reliability_verifier_enforced: controls.verifier?.checked === true,
    reliability_max_recovery_probes: Number(controls.probes?.value || 0),
    reliability_max_autonomous_repair_attempts: Number(controls.repairs?.value || 0),
    reliability_weak_model_plan_action_cap: Number(controls.actionCap?.value || 1),
    reliability_approval_envelope_budget: Number(controls.envelope?.value || 0),
  };
  const nextControls = await apiJson("/api/agent/runtime-controls", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderControls(nextControls);
  setStatus("Controls saved");
}

async function runEval() {
  setStatus("Running eval...");
  await apiJson("/api/agent/reliability/evals/run", { method: "POST", body: "{}" });
  await loadReliability();
  setStatus("Eval complete");
}

refreshButton?.addEventListener("click", () => {
  loadReliability().catch((error) => {
    setStatus(error.message);
    notifyUiError("Reliability refresh failed", error.message, "Check the agent server connection and refresh again.");
  });
});
controlsSave?.addEventListener("click", () => {
  saveControls().catch((error) => {
    setStatus(error.message);
    notifyUiError("Reliability controls save failed", error.message, "Review the control values and save again.");
  });
});
evalRunButton?.addEventListener("click", () => {
  runEval().catch((error) => {
    setStatus(error.message);
    notifyUiError("Reliability eval failed", error.message, "Check runtime availability and run the eval again.");
  });
});

loadReliability()
  .catch((error) => setStatus(error.message))
  .finally(() => {
    document.documentElement.classList.remove("app-booting");
    if (boot) boot.hidden = true;
  });
