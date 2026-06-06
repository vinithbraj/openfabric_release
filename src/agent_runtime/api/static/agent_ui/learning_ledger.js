const state = {
  lessons: [],
  proposals: [],
  runs: [],
  selectedLessonId: "",
  selectedProposalId: "",
  selectedKind: "lesson",
};
const Core = window.OpenFabricAgentUi;

const boot = document.querySelector("#app-boot-screen");
const dbPath = document.querySelector("#ledger-db-path");
const refreshButton = document.querySelector("#ledger-refresh");
const lessonStatusFilter = document.querySelector("#lesson-status-filter");
const proposalStatusFilter = document.querySelector("#proposal-status-filter");
const lessonList = document.querySelector("#lesson-list");
const proposalList = document.querySelector("#proposal-list");
const runList = document.querySelector("#run-list");
const lessonTitle = document.querySelector("#lesson-title");
const lessonStatus = document.querySelector("#lesson-status");
const lessonInstruction = document.querySelector("#lesson-instruction");
const lessonNote = document.querySelector("#lesson-note");
const lessonEvidence = document.querySelector("#lesson-evidence");
const ledgerStatus = document.querySelector("#ledger-status");
const summaryRuns = document.querySelector("#summary-runs");
const summaryDrafts = document.querySelector("#summary-drafts");
const summaryApproved = document.querySelector("#summary-approved");
const summaryProposals = document.querySelector("#summary-proposals");
const summarySuspectCache = document.querySelector("#summary-suspect-cache");

const buttons = {
  save: document.querySelector("#lesson-save"),
  approve: document.querySelector("#lesson-approve"),
  reject: document.querySelector("#lesson-reject"),
  retire: document.querySelector("#lesson-retire"),
  restore: document.querySelector("#lesson-restore"),
  digestNote: document.querySelector("#lesson-digest-note"),
  apply: document.querySelector("#proposal-apply"),
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
      message = await response.text() || message;
    }
    throw new Error(message);
  }
  return response.json();
}

function setStatus(message) {
  if (ledgerStatus) ledgerStatus.textContent = message;
}

function notifyUiError(title, message, fix = "", field = null) {
  Core?.notifyUiError?.({ title, message, fix, field });
}

function lessonById(id) {
  return state.lessons.find((lesson) => lesson.lesson_id === id) || null;
}

function proposalById(id) {
  return state.proposals.find((proposal) => proposal.proposal_id === id) || null;
}

function renderSummary(summary) {
  if (summaryRuns) summaryRuns.textContent = String(summary?.total_runs ?? 0);
  if (summaryDrafts) summaryDrafts.textContent = String(summary?.draft_lessons ?? 0);
  if (summaryApproved) summaryApproved.textContent = String(summary?.approved_lessons ?? 0);
  if (summaryProposals) {
    const proposalCount = Number(summary?.draft_proposals ?? 0)
      + Number(summary?.approved_proposals ?? 0)
      + Number(summary?.applied_proposals ?? 0);
    summaryProposals.textContent = String(proposalCount);
  }
  if (summarySuspectCache) summarySuspectCache.textContent = String(summary?.suspect_cache_entries ?? 0);
}

function renderLessons() {
  if (!lessonList) return;
  lessonList.replaceChildren();
  if (!state.lessons.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No lessons yet.";
    lessonList.append(empty);
    return;
  }
  for (const lesson of state.lessons) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ledger-list-item";
    button.dataset.lessonId = lesson.lesson_id;
    const title = document.createElement("strong");
    title.textContent = lesson.title || lesson.lesson_type || "Lesson";
    const meta = document.createElement("span");
    meta.textContent = `${lesson.status} · ${lesson.lesson_type}${lesson.auto_approved ? " · auto-learned" : ""}`;
    button.append(title, meta);
    button.addEventListener("click", () => selectLesson(lesson.lesson_id));
    lessonList.append(button);
  }
}

function renderProposals() {
  if (!proposalList) return;
  proposalList.replaceChildren();
  if (!state.proposals.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No proposals yet.";
    proposalList.append(empty);
    return;
  }
  for (const proposal of state.proposals) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ledger-list-item proposal-card";
    button.dataset.proposalId = proposal.proposal_id;
    const title = document.createElement("strong");
    title.textContent = proposal.title || proposal.target_kind || "Proposal";
    const meta = document.createElement("span");
    meta.textContent = `${proposal.status} · ${proposal.target_kind} · ${Math.round((proposal.confidence || 0) * 100)}%`;
    button.append(title, meta);
    button.addEventListener("click", () => selectProposal(proposal.proposal_id));
    proposalList.append(button);
  }
}

function renderRuns() {
  if (!runList) return;
  runList.replaceChildren();
  if (!state.runs.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No runs recorded yet.";
    runList.append(empty);
    return;
  }
  for (const run of state.runs) {
    const item = document.createElement("a");
    item.className = "ledger-list-item";
    item.href = `/api/agent/learning-ledger/runs/${encodeURIComponent(run.request_id)}`;
    item.target = "_blank";
    item.rel = "noreferrer";
    const title = document.createElement("strong");
    title.textContent = run.prompt_excerpt || run.request_id;
    const meta = document.createElement("span");
    meta.textContent = `${run.outcome} · ${run.status}`;
    item.append(title, meta);
    runList.append(item);
  }
}

function selectLesson(lessonId) {
  state.selectedKind = "lesson";
  state.selectedLessonId = lessonId;
  const lesson = lessonById(lessonId);
  if (!lesson) return;
  if (lessonTitle) lessonTitle.textContent = lesson.title || "Lesson";
  if (lessonStatus) lessonStatus.textContent = lesson.status || "draft";
  if (lessonInstruction) lessonInstruction.value = lesson.instruction || "";
  if (lessonNote) lessonNote.value = "";
  if (lessonEvidence) {
    lessonEvidence.textContent = JSON.stringify(
      {
        summary: lesson.summary,
        scope: lesson.scope,
        evidence: lesson.evidence,
        tags: lesson.tags,
        mirrored_memory_id: lesson.mirrored_memory_id,
        auto_approved: lesson.auto_approved === true,
      },
      null,
      2,
    );
  }
}

function selectProposal(proposalId) {
  state.selectedKind = "proposal";
  state.selectedProposalId = proposalId;
  const proposal = proposalById(proposalId);
  if (!proposal) return;
  if (lessonTitle) lessonTitle.textContent = proposal.title || "Proposal";
  if (lessonStatus) lessonStatus.textContent = proposal.status || "draft";
  if (lessonInstruction) {
    lessonInstruction.value = proposal.summary || proposal.rationale || JSON.stringify(proposal.draft || {}, null, 2);
  }
  if (lessonNote) lessonNote.value = "";
  if (lessonEvidence) {
    lessonEvidence.textContent = JSON.stringify(
      {
        target_kind: proposal.target_kind,
        target_id: proposal.target_id,
        source: proposal.source,
        confidence: proposal.confidence,
        draft: proposal.draft,
        evidence: proposal.evidence,
        safety_decision: proposal.safety_decision,
        auto_approved: proposal.auto_approved === true,
        applied_ref: proposal.applied_ref,
        apply_error: proposal.apply_error,
      },
      null,
      2,
    );
  }
}

async function loadLedger() {
  setStatus("Loading...");
  const [summaryPayload, lessonPayload, proposalPayload, runPayload] = await Promise.all([
    apiJson("/api/agent/learning-ledger/summary"),
    apiJson(`/api/agent/learning-ledger/lessons?status=${encodeURIComponent(lessonStatusFilter?.value || "")}`),
    apiJson(`/api/agent/learning-ledger/proposals?status=${encodeURIComponent(proposalStatusFilter?.value || "")}`),
    apiJson("/api/agent/learning-ledger/runs?limit=40"),
  ]);
  dbPath.textContent = summaryPayload.db_path || "";
  renderSummary(summaryPayload.summary || {});
  state.lessons = Array.isArray(lessonPayload.lessons) ? lessonPayload.lessons : [];
  state.proposals = Array.isArray(proposalPayload.proposals) ? proposalPayload.proposals : [];
  state.runs = Array.isArray(runPayload.runs) ? runPayload.runs : [];
  renderLessons();
  renderProposals();
  renderRuns();
  if (state.selectedKind === "proposal" && state.selectedProposalId && proposalById(state.selectedProposalId)) {
    selectProposal(state.selectedProposalId);
  } else if (state.selectedLessonId && lessonById(state.selectedLessonId)) {
    selectLesson(state.selectedLessonId);
  } else if (state.lessons[0]) {
    selectLesson(state.lessons[0].lesson_id);
  } else if (state.proposals[0]) {
    selectProposal(state.proposals[0].proposal_id);
  }
  setStatus("Ready");
}

async function mutateSelected(pathSuffix, options = {}) {
  const lesson = lessonById(state.selectedLessonId);
  if (!lesson) {
    setStatus("Select a lesson first.");
    notifyUiError(
      "No lesson selected",
      "The action could not be saved because no lesson is selected.",
      "Select a lesson, then try the action again.",
      lessonList,
    );
    return;
  }
  setStatus("Saving...");
  await apiJson(`/api/agent/learning-ledger/lessons/${encodeURIComponent(lesson.lesson_id)}${pathSuffix}`, options);
  await loadLedger();
}

async function mutateSelectedProposal(pathSuffix, options = {}) {
  const proposal = proposalById(state.selectedProposalId);
  if (!proposal) {
    setStatus("Select a proposal first.");
    notifyUiError(
      "No proposal selected",
      "The action could not be saved because no proposal is selected.",
      "Select a proposal, then try the action again.",
      proposalList,
    );
    return;
  }
  setStatus("Saving...");
  await apiJson(`/api/agent/learning-ledger/proposals/${encodeURIComponent(proposal.proposal_id)}${pathSuffix}`, options);
  await loadLedger();
}

refreshButton?.addEventListener("click", () => {
  loadLedger().catch((error) => {
    setStatus(`Refresh failed: ${error.message}`);
    notifyUiError("Ledger refresh failed", error.message, "Check the agent server connection and refresh again.");
  });
});

lessonStatusFilter?.addEventListener("change", () => {
  loadLedger().catch((error) => {
    setStatus(`Filter failed: ${error.message}`);
    notifyUiError("Ledger filter failed", error.message, "Refresh the ledger and try the filter again.");
  });
});

proposalStatusFilter?.addEventListener("change", () => {
  loadLedger().catch((error) => {
    setStatus(`Filter failed: ${error.message}`);
    notifyUiError("Ledger filter failed", error.message, "Refresh the ledger and try the filter again.");
  });
});

buttons.save?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    mutateSelectedProposal("", {
      method: "PATCH",
      body: JSON.stringify({ summary: lessonInstruction?.value || "" }),
    }).catch((error) => {
      setStatus(`Save failed: ${error.message}`);
      notifyUiError("Proposal save failed", error.message, "Review the proposal summary and save again.");
    });
    return;
  }
  mutateSelected("", {
    method: "PATCH",
    body: JSON.stringify({ instruction: lessonInstruction?.value || "" }),
  }).catch((error) => {
    setStatus(`Save failed: ${error.message}`);
    notifyUiError("Lesson save failed", error.message, "Review the lesson instruction and save again.", lessonInstruction);
  });
});

buttons.approve?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    mutateSelectedProposal("/approve", { method: "POST", body: "{}" }).catch((error) => {
      setStatus(`Approve failed: ${error.message}`);
      notifyUiError("Proposal approve failed", error.message, "Refresh proposals and try approving again.");
    });
    return;
  }
  mutateSelected("/approve", { method: "POST", body: "{}" }).catch((error) => {
    setStatus(`Remember failed: ${error.message}`);
    notifyUiError("Lesson remember failed", error.message, "Refresh lessons and try remembering again.");
  });
});

buttons.reject?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    mutateSelectedProposal("/reject", { method: "POST", body: JSON.stringify({ reason: "Rejected in learning ledger." }) }).catch((error) => {
      setStatus(`Reject failed: ${error.message}`);
      notifyUiError("Proposal reject failed", error.message, "Refresh proposals and try rejecting again.");
    });
    return;
  }
  mutateSelected("/reject", { method: "POST", body: JSON.stringify({ reason: "Rejected in learning ledger." }) }).catch((error) => {
    setStatus(`Reject failed: ${error.message}`);
    notifyUiError("Lesson reject failed", error.message, "Refresh lessons and try rejecting again.");
  });
});

buttons.retire?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    mutateSelectedProposal("/retire", { method: "POST", body: "{}" }).catch((error) => {
      setStatus(`Retire failed: ${error.message}`);
      notifyUiError("Proposal retire failed", error.message, "Refresh proposals and try retiring again.");
    });
    return;
  }
  mutateSelected("/retire", { method: "POST", body: "{}" }).catch((error) => {
    setStatus(`Retire failed: ${error.message}`);
    notifyUiError("Lesson retire failed", error.message, "Refresh lessons and try retiring again.");
  });
});

buttons.restore?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    setStatus("Restore applies to lessons.");
    notifyUiError(
      "Restore needs a lesson",
      "Restore does not apply to proposals.",
      "Select a retired lesson before restoring.",
      lessonList,
    );
    return;
  }
  mutateSelected("/restore", { method: "POST", body: "{}" }).catch((error) => {
    setStatus(`Restore failed: ${error.message}`);
    notifyUiError("Lesson restore failed", error.message, "Refresh lessons and try restoring again.");
  });
});

buttons.apply?.addEventListener("click", () => {
  if (state.selectedKind !== "proposal") {
    setStatus("Select a proposal first.");
    notifyUiError(
      "No proposal selected",
      "The proposal could not be applied because no proposal is selected.",
      "Select a proposal, then apply it.",
      proposalList,
    );
    return;
  }
  mutateSelectedProposal("/apply", { method: "POST", body: "{}" }).catch((error) => {
    setStatus(`Apply failed: ${error.message}`);
    notifyUiError("Proposal apply failed", error.message, "Refresh proposals and try applying again.");
  });
});

buttons.digestNote?.addEventListener("click", () => {
  if (state.selectedKind === "proposal") {
    setStatus("Digest note applies to lessons.");
    notifyUiError(
      "Digest note needs a lesson",
      "Digest note does not apply to proposals.",
      "Select a lesson, add a note, then digest it.",
      lessonList,
    );
    return;
  }
  const note = String(lessonNote?.value || "").trim();
  if (!note) {
    setStatus("Add a note first.");
    notifyUiError(
      "Lesson note required",
      "The note was not digested because it is empty.",
      "Add a note, then digest it again.",
      lessonNote,
    );
    return;
  }
  mutateSelected("/digest-note", { method: "POST", body: JSON.stringify({ note }) }).catch((error) => {
    setStatus(`Digest failed: ${error.message}`);
    notifyUiError("Lesson digest failed", error.message, "Review the note and digest it again.", lessonNote);
  });
});

loadLedger()
  .catch((error) => setStatus(`Learning ledger unavailable: ${error.message}`))
  .finally(() => {
    document.documentElement.classList.remove("app-booting");
    boot?.remove();
  });
