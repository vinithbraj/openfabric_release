const apiBase = "/api/agent/prompt-editor";
const Core = window.OpenFabricAgentUi;

const state = {
  mode: "prompts",
  templates: [],
  memories: [],
  parameters: [],
  selectedKey: "",
  selectedMemoryId: "",
  selectedParameterKey: "",
  detail: null,
  memoryDetail: null,
  parameterDetail: null,
  memoryAuditEvents: [],
  parameterAuditEvents: [],
  creatingMemory: false,
  creatingParameter: false,
  parameterValueRevealed: false,
  dirty: false,
  loading: false,
  variableValues: {},
};

const promptsModeButton = document.querySelector("#prompts-mode-button");
const memoriesModeButton = document.querySelector("#memories-mode-button");
const parametersModeButton = document.querySelector("#parameters-mode-button");
const templateSearch = document.querySelector("#template-search");
const scopeFilter = document.querySelector("#scope-filter");
const templateCount = document.querySelector("#template-count");
const templateList = document.querySelector("#template-list");
const editorDbPath = document.querySelector("#editor-db-path");
const appBootScreen = document.querySelector("#app-boot-screen");
const footerVersion = document.querySelector("#prompt-editor-footer-version");
const footerRuntime = document.querySelector("#prompt-editor-footer-runtime");
const footerGit = document.querySelector("#prompt-editor-footer-git");
const footerCount = document.querySelector("#prompt-editor-footer-count");
const themeSelect = document.querySelector("#theme-select");
const templateTitle = document.querySelector("#template-title");
const templateMeta = document.querySelector("#template-meta");
const templateStatus = document.querySelector("#template-status");
const dirtyIndicator = document.querySelector("#dirty-indicator");
const renderButton = document.querySelector("#render-button");
const revealParameterButton = document.querySelector("#reveal-parameter-button");
const resetButton = document.querySelector("#reset-button");
const saveButton = document.querySelector("#save-button");
const newMemoryButton = document.querySelector("#new-memory-button");
const newParameterButton = document.querySelector("#new-parameter-button");
const placeholderChips = document.querySelector("#placeholder-chips");
const templateBody = document.querySelector("#template-body");
const memoryFields = document.querySelector("#memory-fields");
const memorySummary = document.querySelector("#memory-summary");
const memoryStatus = document.querySelector("#memory-status");
const memoryKind = document.querySelector("#memory-kind");
const memoryScope = document.querySelector("#memory-scope");
const memoryModelName = document.querySelector("#memory-model-name");
const memoryModelFamily = document.querySelector("#memory-model-family");
const memoryTaskType = document.querySelector("#memory-task-type");
const memoryToolType = document.querySelector("#memory-tool-type");
const memoryIntentType = document.querySelector("#memory-intent-type");
const memoryValidatorErrorType = document.querySelector("#memory-validator-error-type");
const memoryTags = document.querySelector("#memory-tags");
const memoryRationale = document.querySelector("#memory-rationale");
const parameterFields = document.querySelector("#parameter-fields");
const parameterKey = document.querySelector("#parameter-key");
const parameterDescription = document.querySelector("#parameter-description");
const parameterAliases = document.querySelector("#parameter-aliases");
const parameterTags = document.querySelector("#parameter-tags");
const parameterSensitive = document.querySelector("#parameter-sensitive");
const parameterValueJson = document.querySelector("#parameter-value-json");
const parameterContextSummary = document.querySelector("#parameter-context-summary");
const parameterContextPromptGuidance = document.querySelector("#parameter-context-prompt-guidance");
const parameterContextClarificationGuidance = document.querySelector("#parameter-context-clarification-guidance");
const parameterContextConcepts = document.querySelector("#parameter-context-concepts");
const parameterContextMetrics = document.querySelector("#parameter-context-metrics");
const parameterContextRelationships = document.querySelector("#parameter-context-relationships");
const parameterContextJson = document.querySelector("#parameter-context-json");
const defaultPanelTitle = document.querySelector("#default-panel-title");
const variablePanelTitle = document.querySelector("#variable-panel-title");
const renderPanelTitle = document.querySelector("#render-panel-title");
const defaultStatus = document.querySelector("#default-status");
const defaultBody = document.querySelector("#default-body");
const variableCount = document.querySelector("#variable-count");
const variableFields = document.querySelector("#variable-fields");
const renderStatus = document.querySelector("#render-status");
const renderOutput = document.querySelector("#render-output");

function allowedThemes() {
  return new Set(Array.from(themeSelect?.querySelectorAll("option[value]") || []).map((item) => item.value));
}

function fallbackTheme() {
  const values = allowedThemes();
  return values.has("github") ? "github" : Array.from(values)[0] || "github";
}

function applyTheme(theme) {
  const values = allowedThemes();
  const safeTheme = values.has(theme) ? theme : fallbackTheme();
  document.documentElement.dataset.theme = safeTheme;
  if (themeSelect) {
    themeSelect.value = safeTheme;
  }
}

function initTheme() {
  applyTheme(document.documentElement.dataset.theme || fallbackTheme());
}

function finishAppBoot() {
  const root = document.documentElement;
  const reveal = () => {
    root.classList.remove("app-booting");
    root.classList.add("app-booted");
    appBootScreen?.setAttribute("aria-hidden", "true");
    window.setTimeout(() => {
      appBootScreen?.remove();
    }, 220);
  };
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(reveal);
  });
}

function setStatus(message, tone = "") {
  if (!templateStatus) return;
  templateStatus.textContent = message || "";
  templateStatus.classList.toggle("error", tone === "error");
  templateStatus.classList.toggle("success", tone === "success");
}

function notifyUiError(title, message, fix = "", field = null) {
  Core?.notifyUiError?.({ title, message, fix, field });
}

function setLoading(loading) {
  state.loading = Boolean(loading);
  updateActions();
}

function setDirty(dirty) {
  state.dirty = Boolean(dirty);
  if (dirtyIndicator) {
    dirtyIndicator.textContent = state.dirty ? "Unsaved" : "Saved";
    dirtyIndicator.classList.toggle("unsaved", state.dirty);
  }
  updateActions();
}

function currentEditorDetail() {
  if (state.mode === "memories") return state.memoryDetail;
  if (state.mode === "parameters") return state.parameterDetail;
  return state.detail;
}

function setMemoryFormDisabled(disabled) {
  for (const field of Array.from(memoryFields?.querySelectorAll("input, select, textarea") || [])) {
    field.disabled = disabled;
  }
  if (state.mode === "memories") {
    for (const field of Array.from(variableFields?.querySelectorAll("textarea") || [])) {
      field.disabled = disabled;
    }
  }
}

function setParameterFormDisabled(disabled) {
  for (const field of Array.from(parameterFields?.querySelectorAll("input, select, textarea") || [])) {
    field.disabled = disabled;
  }
}

function updateActions() {
  const isMemoryMode = state.mode === "memories";
  const isParameterMode = state.mode === "parameters";
  const hasDetail = Boolean(currentEditorDetail());
  const disabled = state.loading || !hasDetail;
  if (templateBody) templateBody.disabled = disabled || isParameterMode;
  setMemoryFormDisabled(disabled);
  setParameterFormDisabled(disabled);
  if (renderButton) {
    renderButton.hidden = isMemoryMode || isParameterMode;
    renderButton.disabled = disabled || isMemoryMode || isParameterMode;
  }
  if (revealParameterButton) {
    revealParameterButton.hidden = !isParameterMode;
    revealParameterButton.disabled = disabled || state.creatingParameter || state.parameterValueRevealed;
  }
  if (resetButton) {
    resetButton.hidden = isMemoryMode || isParameterMode;
    resetButton.disabled = disabled || isMemoryMode || isParameterMode || !state.detail?.has_default;
  }
  if (newMemoryButton) {
    newMemoryButton.hidden = !isMemoryMode;
    newMemoryButton.disabled = state.loading;
  }
  if (newParameterButton) {
    newParameterButton.hidden = !isParameterMode;
    newParameterButton.disabled = state.loading;
  }
  if (saveButton) saveButton.disabled = disabled || !state.dirty;
}

function apiDetailMessage(detail, fallback) {
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || item?.message || JSON.stringify(item))
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    return detail.message || detail.error || JSON.stringify(detail);
  }
  return String(detail || fallback || "Request failed");
}

async function requestJson(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { ...options, headers, cache: "no-store" });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {}
  if (!response.ok) {
    throw new Error(apiDetailMessage(payload?.detail, response.statusText));
  }
  return payload || {};
}

function compactTimestamp(value) {
  const updated = String(value || "").trim();
  if (!updated) return "";
  return updated.replace("T", " ").replace("+00:00", " UTC");
}

function displayDbPath(dbPath) {
  const text = String(dbPath || "").trim();
  if (!text) return "";
  const normalized = text.replaceAll("\\", "/");
  const parts = normalized.split("/").filter((part) => part.length > 0);
  return parts.length ? parts[parts.length - 1] : text;
}

function templateScope(template) {
  return String(template?.metadata?.scope || "uncategorized");
}

function memoryScopeValue(memory) {
  return String(memory?.scope || "global");
}

function memoryTitle(memory) {
  return String(memory?.summary || memory?.preview || memory?.memory_id || "Untitled memory").trim();
}

function templateUpdated(template) {
  return compactTimestamp(template?.updated_at);
}

function renderScopeFilter() {
  if (!scopeFilter) return;
  const previous = scopeFilter.value;
  const records = state.mode === "memories"
    ? state.memories
    : state.mode === "parameters"
      ? state.parameters
      : state.templates;
  const scopes = Array.from(
    new Set(
      records.flatMap((record) => {
        if (state.mode === "memories") return [memoryScopeValue(record)];
        if (state.mode === "parameters") {
          const tags = Array.isArray(record.tags) ? record.tags : [];
          return tags.length ? tags : [record.sensitive === false ? "plain" : "sensitive"];
        }
        return [templateScope(record)];
      })
    )
  ).sort();
  scopeFilter.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = state.mode === "parameters" ? "All tags" : "All scopes";
  scopeFilter.append(all);
  for (const scope of scopes) {
    const option = document.createElement("option");
    option.value = scope;
    option.textContent = scope;
    scopeFilter.append(option);
  }
  scopeFilter.value = scopes.includes(previous) ? previous : "";
}

function filteredTemplates() {
  const query = String(templateSearch?.value || "").trim().toLowerCase();
  const scope = String(scopeFilter?.value || "");
  return state.templates.filter((template) => {
    const metadataText = JSON.stringify(template.metadata || {}).toLowerCase();
    const matchesQuery =
      !query ||
      String(template.prompt_key || "").toLowerCase().includes(query) ||
      String(template.preview || "").toLowerCase().includes(query) ||
      metadataText.includes(query);
    const matchesScope = !scope || templateScope(template) === scope;
    return matchesQuery && matchesScope;
  });
}

function filteredMemories() {
  const query = String(templateSearch?.value || "").trim().toLowerCase();
  const scope = String(scopeFilter?.value || "");
  return state.memories.filter((memory) => {
    const tagsText = Array.isArray(memory.tags) ? memory.tags.join(" ").toLowerCase() : "";
    const matchesQuery =
      !query ||
      String(memory.memory_id || "").toLowerCase().includes(query) ||
      String(memory.preview || "").toLowerCase().includes(query) ||
      String(memory.summary || "").toLowerCase().includes(query) ||
      tagsText.includes(query) ||
      String(memory.task_type || "").toLowerCase().includes(query) ||
      String(memory.tool_type || "").toLowerCase().includes(query);
    const matchesScope = !scope || memoryScopeValue(memory) === scope;
    return matchesQuery && matchesScope;
  });
}

function filteredParameters() {
  const query = String(templateSearch?.value || "").trim().toLowerCase();
  const scope = String(scopeFilter?.value || "");
  return state.parameters.filter((entry) => {
    const tags = Array.isArray(entry.tags) ? entry.tags : [];
    const tagText = tags.join(" ").toLowerCase();
    const fieldsText = JSON.stringify(entry.value_schema || {}).toLowerCase();
    const contextText = JSON.stringify(entry.context_json || {}).toLowerCase();
    const matchesQuery =
      !query ||
      String(entry.key || "").toLowerCase().includes(query) ||
      String(entry.normalized_key || "").toLowerCase().includes(query) ||
      String(entry.description || "").toLowerCase().includes(query) ||
      tagText.includes(query) ||
      fieldsText.includes(query) ||
      contextText.includes(query);
    const matchesScope = !scope || tags.includes(scope) || (scope === "plain" && entry.sensitive === false) || (scope === "sensitive" && entry.sensitive !== false);
    return matchesQuery && matchesScope;
  });
}

function createTemplatePill(text, className = "") {
  const pill = document.createElement("span");
  pill.className = `template-pill ${className}`.trim();
  pill.textContent = text;
  pill.title = text;
  return pill;
}

function renderPromptList() {
  if (!templateList) return;
  const templates = filteredTemplates();
  templateList.replaceChildren();
  if (templateCount) {
    templateCount.textContent = `${templates.length} of ${state.templates.length} prompts`;
  }
  if (footerCount) footerCount.textContent = `${state.templates.length} prompts`;
  for (const template of templates) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "template-row";
    button.classList.toggle("active", template.prompt_key === state.selectedKey);

    const key = document.createElement("span");
    key.className = "template-row-key";
    key.textContent = template.prompt_key || "";
    key.title = key.textContent;

    const meta = document.createElement("span");
    meta.className = "template-row-meta";
    meta.append(createTemplatePill(templateScope(template), "scope"));
    meta.append(
      createTemplatePill(
        template.edited ? "edited" : "default",
        `state ${template.edited ? "edited" : "default"}`
      )
    );
    meta.append(createTemplatePill(`v${template.version || 1}`, "version"));

    button.append(key, meta);
    button.addEventListener("click", () => selectTemplate(template.prompt_key));
    templateList.append(button);
  }
}

function renderMemoryList() {
  if (!templateList) return;
  const memories = filteredMemories();
  templateList.replaceChildren();
  if (templateCount) {
    templateCount.textContent = `${memories.length} of ${state.memories.length} memories`;
  }
  if (footerCount) footerCount.textContent = `${state.memories.length} memories`;
  for (const memory of memories) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "template-row memory-row";
    button.classList.toggle("active", memory.memory_id === state.selectedMemoryId);

    const key = document.createElement("span");
    key.className = "template-row-key";
    key.textContent = memoryTitle(memory);
    key.title = key.textContent;

    const meta = document.createElement("span");
    meta.className = "template-row-meta";
    meta.append(createTemplatePill(memory.status || "active", `state ${memory.status || "active"}`));
    meta.append(createTemplatePill(memory.memory_kind || "task_memory", "scope"));
    meta.append(createTemplatePill(memory.scope || "global", "version"));

    button.append(key, meta);
    button.addEventListener("click", () => selectMemory(memory.memory_id));
    templateList.append(button);
  }
}

function parameterTitle(entry) {
  return String(entry?.key || entry?.normalized_key || "parameter").trim();
}

function renderParameterList() {
  if (!templateList) return;
  const parameters = filteredParameters();
  templateList.replaceChildren();
  if (templateCount) {
    templateCount.textContent = `${parameters.length} of ${state.parameters.length} parameters`;
  }
  if (footerCount) footerCount.textContent = `${state.parameters.length} parameters`;
  for (const entry of parameters) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "template-row memory-row parameter-row";
    const keyValue = entry.normalized_key || entry.key || "";
    button.classList.toggle("active", keyValue === state.selectedParameterKey);

    const key = document.createElement("span");
    key.className = "template-row-key";
    key.textContent = parameterTitle(entry);
    key.title = key.textContent;

    const meta = document.createElement("span");
    meta.className = "template-row-meta";
    meta.append(createTemplatePill(entry.sensitive === false ? "plain" : "sensitive", `state ${entry.sensitive === false ? "plain" : "active"}`));
    const tags = Array.isArray(entry.tags) ? entry.tags : [];
    if (tags[0]) meta.append(createTemplatePill(tags[0], "scope"));
    if (entry.context_json && Object.keys(entry.context_json).length) {
      meta.append(createTemplatePill("context", "version"));
    }

    button.append(key, meta);
    button.addEventListener("click", () => selectParameter(keyValue));
    templateList.append(button);
  }
}

function renderTemplateList() {
  if (state.mode === "memories") {
    renderMemoryList();
  } else if (state.mode === "parameters") {
    renderParameterList();
  } else {
    renderPromptList();
  }
}

function renderChips(values, emptyText = "no variables", chipPrefix = "") {
  if (!placeholderChips) return;
  placeholderChips.replaceChildren();
  if (!values.length) {
    const empty = document.createElement("span");
    empty.className = "template-pill default";
    empty.textContent = emptyText;
    placeholderChips.append(empty);
    return;
  }
  for (const value of values) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "placeholder-chip";
    chip.textContent = chipPrefix ? `${chipPrefix}${value}` : `\${${value}}`;
    chip.addEventListener("click", () => {
      const field = document.querySelector(`[data-variable-input="${CSS.escape(value)}"]`);
      field?.focus();
    });
    placeholderChips.append(chip);
  }
}

function renderVariableFields(variables) {
  if (!variableFields) return;
  variableFields.replaceChildren();
  if (variableCount) variableCount.textContent = String(variables.length);
  if (!variables.length) {
    const empty = document.createElement("div");
    empty.className = "empty-editor-state";
    empty.textContent = "No variables";
    variableFields.append(empty);
    return;
  }
  for (const variable of variables) {
    const row = document.createElement("div");
    row.className = "variable-field";
    const label = document.createElement("label");
    const inputId = `variable-${variable.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    label.setAttribute("for", inputId);
    label.textContent = variable;
    const input = document.createElement("input");
    input.id = inputId;
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.dataset.variableInput = variable;
    input.value = state.variableValues[variable] || "";
    input.addEventListener("input", () => {
      state.variableValues[variable] = input.value;
    });
    row.append(label, input);
    variableFields.append(row);
  }
}

function applyTemplateDetail(template) {
  state.detail = template;
  state.selectedKey = template?.prompt_key || "";
  state.variableValues = {};
  const variables = Array.isArray(template?.variables) ? template.variables : [];
  if (templateTitle) templateTitle.textContent = template?.prompt_key || "Select a prompt";
  if (templateMeta) {
    const updated = templateUpdated(template);
    templateMeta.textContent = [
      templateScope(template),
      `v${template?.version || 1}`,
      template?.status || "active",
      updated ? `updated ${updated}` : "",
    ]
      .filter(Boolean)
      .join(" | ");
  }
  if (templateBody) {
    templateBody.value = template?.body || "";
    templateBody.setAttribute("aria-label", "Prompt template body");
  }
  if (defaultPanelTitle) defaultPanelTitle.textContent = "Default";
  if (variablePanelTitle) variablePanelTitle.textContent = "Variables";
  if (renderPanelTitle) renderPanelTitle.textContent = "Preview";
  if (defaultBody) defaultBody.textContent = template?.default_body || "No checked-in default.";
  if (defaultStatus) defaultStatus.textContent = template?.edited ? "Edited" : "Default";
  if (renderOutput) renderOutput.textContent = "";
  if (renderStatus) renderStatus.textContent = "";
  renderChips(variables, "no variables");
  renderVariableFields(variables);
  setStatus(template?.validation_error || "", template?.validation_error ? "error" : "");
  setDirty(false);
  renderTemplateList();
}

function setMemoryFieldValues(memory) {
  if (memorySummary) memorySummary.value = memory?.summary || "";
  if (memoryStatus) memoryStatus.value = memory?.status || "active";
  if (memoryKind) memoryKind.value = memory?.memory_kind || "task_memory";
  if (memoryScope) memoryScope.value = memory?.scope || "global";
  if (memoryModelName) memoryModelName.value = memory?.model_name || "";
  if (memoryModelFamily) memoryModelFamily.value = memory?.model_family || "";
  if (memoryTaskType) memoryTaskType.value = memory?.task_type || "";
  if (memoryToolType) memoryToolType.value = memory?.tool_type || "";
  if (memoryIntentType) memoryIntentType.value = memory?.intent_type || "";
  if (memoryValidatorErrorType) memoryValidatorErrorType.value = memory?.validator_error_type || "";
  if (memoryTags) memoryTags.value = Array.isArray(memory?.tags) ? memory.tags.join(", ") : "";
  if (memoryRationale) memoryRationale.value = memory?.rationale || "";
}

function renderMemoryExamples(memory) {
  if (!variableFields) return;
  variableFields.replaceChildren();
  if (variableCount) {
    const count =
      (Array.isArray(memory?.safe_examples) ? memory.safe_examples.length : 0) +
      (Array.isArray(memory?.blocked_examples) ? memory.blocked_examples.length : 0);
    variableCount.textContent = String(count);
  }
  const fields = [
    ["memory-safe-examples", "Safe examples", memory?.safe_examples || []],
    ["memory-blocked-examples", "Blocked examples", memory?.blocked_examples || []],
  ];
  for (const [id, labelText, values] of fields) {
    const row = document.createElement("div");
    row.className = "variable-field";
    const label = document.createElement("label");
    label.setAttribute("for", id);
    label.textContent = labelText;
    const textarea = document.createElement("textarea");
    textarea.id = id;
    textarea.spellcheck = false;
    textarea.value = Array.isArray(values) ? values.join("\n") : "";
    textarea.addEventListener("input", () => setDirty(true));
    row.append(label, textarea);
    variableFields.append(row);
  }
}

function renderMemoryMetadata(memory) {
  if (defaultBody) {
    defaultBody.textContent = memory
      ? [
          `Memory ID: ${memory.memory_id || ""}`,
          `Created: ${compactTimestamp(memory.created_at) || "-"}`,
          `Updated: ${compactTimestamp(memory.updated_at) || "-"}`,
          `Use count: ${memory.use_count ?? 0}`,
          `Last used: ${compactTimestamp(memory.last_used_at) || "-"}`,
          `Provenance: ${memory.provenance || "manual"}`,
          `Request ID: ${memory.request_id || "-"}`,
        ].join("\n")
      : "Select a memory.";
  }
  if (defaultStatus) defaultStatus.textContent = memory?.status || "";
}

function renderMemoryAudit(events) {
  if (!renderOutput) return;
  if (!Array.isArray(events) || !events.length) {
    renderOutput.textContent = "No audit events.";
    return;
  }
  renderOutput.textContent = events
    .slice()
    .reverse()
    .map((event) => {
      const timestamp = compactTimestamp(event.timestamp);
      const actor = event.actor ? ` by ${event.actor}` : "";
      return `${timestamp} | ${event.event_type || "memory.event"}${actor}`;
    })
    .join("\n");
}

function applyMemoryDetail(memory, auditEvents = [], options = {}) {
  state.memoryDetail = memory;
  state.memoryAuditEvents = Array.isArray(auditEvents) ? auditEvents : [];
  state.creatingMemory = Boolean(options.creating);
  state.selectedMemoryId = state.creatingMemory ? "" : memory?.memory_id || "";
  if (templateTitle) {
    templateTitle.textContent = state.creatingMemory
      ? "New memory"
      : memory?.memory_id || "Select a memory";
  }
  if (templateMeta) {
    templateMeta.textContent = memory
      ? [
          memory.status || "active",
          memory.memory_kind || "task_memory",
          memory.scope || "global",
          memory.updated_at ? `updated ${compactTimestamp(memory.updated_at)}` : "",
        ]
          .filter(Boolean)
          .join(" | ")
      : "";
  }
  if (templateBody) {
    templateBody.value = memory?.instruction || "";
    templateBody.setAttribute("aria-label", "Memory instruction");
  }
  if (defaultPanelTitle) defaultPanelTitle.textContent = "Metadata";
  if (variablePanelTitle) variablePanelTitle.textContent = "Examples";
  if (renderPanelTitle) renderPanelTitle.textContent = "Audit";
  if (renderStatus) renderStatus.textContent = Array.isArray(auditEvents) ? String(auditEvents.length) : "0";
  setMemoryFieldValues(memory);
  renderMemoryMetadata(memory);
  renderMemoryExamples(memory);
  renderMemoryAudit(auditEvents);
  renderChips(Array.isArray(memory?.tags) ? memory.tags : [], "no tags", "#");
  setStatus("", "");
  setDirty(false);
  renderTemplateList();
}

function defaultParameterContext() {
  return {
    version: 1,
    user_provided_domain_context: {
      summary: "",
      prompt_guidance: "",
      clarification_guidance: "",
      concepts: [],
      metrics: [],
      relationships: [],
    },
  };
}

function parameterDomainContext(contextJson) {
  const context = contextJson && typeof contextJson === "object" && !Array.isArray(contextJson)
    ? contextJson
    : defaultParameterContext();
  const domain = context.user_provided_domain_context;
  if (domain && typeof domain === "object" && !Array.isArray(domain)) return domain;
  return defaultParameterContext().user_provided_domain_context;
}

function safeJson(value, fallback) {
  try {
    return JSON.stringify(value ?? fallback, null, 2);
  } catch (_error) {
    return JSON.stringify(fallback, null, 2);
  }
}

function parseObjectJson(text, label) {
  let value;
  try {
    value = JSON.parse(String(text || "{}"));
  } catch (error) {
    throw new Error(`${label} must parse as JSON.`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return value;
}

function parseArrayJson(text, label) {
  let value;
  try {
    value = JSON.parse(String(text || "[]"));
  } catch (_error) {
    throw new Error(`${label} must parse as JSON.`);
  }
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be a JSON array.`);
  }
  return value;
}

function fillParameterContextFields(contextJson) {
  const context = contextJson && typeof contextJson === "object" && !Array.isArray(contextJson)
    ? contextJson
    : defaultParameterContext();
  const domain = parameterDomainContext(context);
  if (parameterContextSummary) parameterContextSummary.value = String(domain.summary || "");
  if (parameterContextPromptGuidance) parameterContextPromptGuidance.value = String(domain.prompt_guidance || "");
  if (parameterContextClarificationGuidance) parameterContextClarificationGuidance.value = String(domain.clarification_guidance || "");
  if (parameterContextConcepts) parameterContextConcepts.value = safeJson(domain.concepts || [], []);
  if (parameterContextMetrics) parameterContextMetrics.value = safeJson(domain.metrics || [], []);
  if (parameterContextRelationships) parameterContextRelationships.value = safeJson(domain.relationships || [], []);
  if (parameterContextJson) parameterContextJson.value = safeJson(context, defaultParameterContext());
}

function buildParameterContextFromFields() {
  const raw = parseObjectJson(parameterContextJson?.value || "{}", "context_json");
  const domain = {
    ...parameterDomainContext(raw),
    summary: String(parameterContextSummary?.value || ""),
    prompt_guidance: String(parameterContextPromptGuidance?.value || ""),
    clarification_guidance: String(parameterContextClarificationGuidance?.value || ""),
    concepts: parseArrayJson(parameterContextConcepts?.value || "[]", "Concepts JSON"),
    metrics: parseArrayJson(parameterContextMetrics?.value || "[]", "Metrics JSON"),
    relationships: parseArrayJson(parameterContextRelationships?.value || "[]", "Relationships JSON"),
  };
  return {
    ...raw,
    version: Number(raw.version || 1),
    user_provided_domain_context: domain,
  };
}

function syncParameterContextJsonFromStructured() {
  try {
    const context = buildParameterContextFromFields();
    if (parameterContextJson) parameterContextJson.value = safeJson(context, defaultParameterContext());
  } catch (_error) {
    // Leave invalid partial edits visible until save.
  }
}

function fillParameterValue(entry, valueOverride) {
  const value = valueOverride && typeof valueOverride === "object" && !Array.isArray(valueOverride)
    ? valueOverride
    : (entry?.masked_value_json && typeof entry.masked_value_json === "object" ? entry.masked_value_json : {});
  if (parameterValueJson) parameterValueJson.value = safeJson(value, {});
}

function applyParameterDetail(entry, contextJson = null, auditEvents = [], options = {}) {
  state.parameterDetail = entry;
  state.parameterAuditEvents = Array.isArray(auditEvents) ? auditEvents : [];
  state.creatingParameter = Boolean(options.creating);
  state.parameterValueRevealed = Boolean(options.valueRevealed);
  state.selectedParameterKey = state.creatingParameter ? "" : String(entry?.normalized_key || entry?.key || "");
  if (templateTitle) templateTitle.textContent = state.creatingParameter ? "New parameter" : parameterTitle(entry);
  if (templateMeta) {
    templateMeta.textContent = entry
      ? [
          entry.sensitive === false ? "plain" : "sensitive",
          Array.isArray(entry.tags) && entry.tags.length ? entry.tags.join(", ") : "",
          entry.updated_at ? `updated ${compactTimestamp(entry.updated_at)}` : "",
        ].filter(Boolean).join(" | ")
      : "";
  }
  if (parameterKey) parameterKey.value = entry?.key || "";
  if (parameterDescription) parameterDescription.value = entry?.description || "";
  if (parameterAliases) parameterAliases.value = Array.isArray(entry?.aliases) ? entry.aliases.join(", ") : "";
  if (parameterTags) parameterTags.value = Array.isArray(entry?.tags) ? entry.tags.join(", ") : "";
  if (parameterSensitive) parameterSensitive.checked = entry?.sensitive !== false;
  fillParameterValue(entry, options.valueJsonOverride);
  fillParameterContextFields(contextJson || entry?.context_json || defaultParameterContext());
  if (templateBody) templateBody.value = "";
  if (defaultPanelTitle) defaultPanelTitle.textContent = "Summary";
  if (variablePanelTitle) variablePanelTitle.textContent = "Fields";
  if (renderPanelTitle) renderPanelTitle.textContent = "Audit";
  if (defaultStatus) defaultStatus.textContent = entry?.sensitive === false ? "Plain" : "Sensitive";
  if (defaultBody) {
    defaultBody.textContent = entry
      ? [
          `Key: ${entry.key || ""}`,
          `Normalized: ${entry.normalized_key || ""}`,
          `Created: ${compactTimestamp(entry.created_at) || "-"}`,
          `Updated: ${compactTimestamp(entry.updated_at) || "-"}`,
          `Use count: ${entry.use_count ?? 0}`,
          `Last used: ${compactTimestamp(entry.last_used_at) || "-"}`,
        ].join("\n")
      : "Select a parameter.";
  }
  if (variableFields) {
    variableFields.replaceChildren();
    if (variableCount) variableCount.textContent = String((entry?.env && typeof entry.env === "object" ? Object.keys(entry.env).length : 0));
    const fields = document.createElement("pre");
    fields.className = "default-body";
    fields.textContent = safeJson({
      value_schema: entry?.value_schema || {},
      context_schema: entry?.context_schema || {},
      env_names: entry?.env ? Object.keys(entry.env) : [],
    }, {});
    variableFields.append(fields);
  }
  if (renderStatus) renderStatus.textContent = Array.isArray(auditEvents) ? String(auditEvents.length) : "0";
  if (renderOutput) {
    renderOutput.textContent = Array.isArray(auditEvents) && auditEvents.length
      ? auditEvents.slice().reverse().map((event) => `${compactTimestamp(event.timestamp)} | ${event.event_type || "parameter.event"}${event.actor ? ` by ${event.actor}` : ""}`).join("\n")
      : "No audit events.";
  }
  renderChips(Array.isArray(entry?.tags) ? entry.tags : [], "no tags", "#");
  setStatus(state.parameterValueRevealed ? "Value revealed" : "", state.parameterValueRevealed ? "success" : "");
  setDirty(false);
  renderTemplateList();
}

async function loadTemplates(selectKey = state.selectedKey) {
  setLoading(true);
  try {
    const payload = await requestJson(`${apiBase}/templates`);
    state.templates = Array.isArray(payload.templates) ? payload.templates : [];
    if (state.mode === "prompts" && editorDbPath) {
      editorDbPath.textContent = displayDbPath(payload.db_path);
      editorDbPath.title = payload.db_path || "";
    }
    renderScopeFilter();
    renderTemplateList();
    const selected =
      state.templates.find((template) => template.prompt_key === selectKey)?.prompt_key ||
      state.templates[0]?.prompt_key ||
      "";
    if (state.mode === "prompts") {
      if (selected) {
        await loadTemplate(selected, { skipDirtyCheck: true });
      } else {
        applyTemplateDetail(null);
      }
    }
  } catch (error) {
    setStatus(error.message || "Failed to load prompts", "error");
  } finally {
    setLoading(false);
  }
}

async function loadMemories(selectMemoryId = state.selectedMemoryId) {
  setLoading(true);
  try {
    const payload = await requestJson(`${apiBase}/memories`);
    state.memories = Array.isArray(payload.memories) ? payload.memories : [];
    if (state.mode === "memories" && editorDbPath) {
      editorDbPath.textContent = displayDbPath(payload.db_path);
      editorDbPath.title = payload.db_path || "";
    }
    renderScopeFilter();
    renderTemplateList();
    const selected =
      state.memories.find((memory) => memory.memory_id === selectMemoryId)?.memory_id ||
      state.memories[0]?.memory_id ||
      "";
    if (state.mode === "memories") {
      if (selected) {
        await loadMemory(selected, { skipDirtyCheck: true });
      } else {
        applyMemoryDetail(null);
      }
    }
  } catch (error) {
    setStatus(error.message || "Failed to load memories", "error");
  } finally {
    setLoading(false);
  }
}

async function loadParameters(selectKey = state.selectedParameterKey) {
  setLoading(true);
  try {
    const payload = await requestJson(`${apiBase}/parameters?limit=1000`).catch(() => requestJson("/api/agent/parameters?limit=1000"));
    state.parameters = Array.isArray(payload.entries) ? payload.entries : [];
    if (state.mode === "parameters" && editorDbPath) {
      editorDbPath.textContent = displayDbPath(payload.db_path);
      editorDbPath.title = payload.db_path || "";
    }
    renderScopeFilter();
    renderTemplateList();
    const selected =
      state.parameters.find((entry) => (entry.normalized_key || entry.key) === selectKey)?.normalized_key ||
      state.parameters.find((entry) => entry.key === selectKey)?.normalized_key ||
      state.parameters[0]?.normalized_key ||
      state.parameters[0]?.key ||
      "";
    if (state.mode === "parameters") {
      if (selected) {
        await loadParameter(selected, { skipDirtyCheck: true });
      } else {
        applyParameterDetail(null, defaultParameterContext());
      }
    }
  } catch (error) {
    setStatus(error.message || "Failed to load parameters", "error");
  } finally {
    setLoading(false);
  }
}

async function loadEditorVersion() {
  try {
    const payload = await requestJson("/api/agent/version");
    const display = String(payload.display || payload.version || "").trim();
    const runtimeVersion = String(payload.runtime_version || "").trim();
    const gitHash = String(payload.git_hash || "").trim();
    const dirty = payload.dirty === true;
    if (footerVersion) {
      footerVersion.textContent = display ? `OpenFabric ${display}` : "OpenFabric";
    }
    if (footerRuntime) {
      footerRuntime.textContent = runtimeVersion ? `runtime ${runtimeVersion}` : "";
    }
    if (footerGit) {
      footerGit.textContent = gitHash ? `git ${gitHash}${dirty ? " dirty" : " clean"}` : "";
    }
  } catch (_error) {
    if (footerVersion) {
      footerVersion.textContent = "OpenFabric";
    }
  }
}

async function loadTemplate(promptKey, options = {}) {
  if (!promptKey) return;
  if (!options.skipDirtyCheck && state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  setLoading(true);
  try {
    const payload = await requestJson(`${apiBase}/templates/${encodeURIComponent(promptKey)}`);
    applyTemplateDetail(payload.template);
  } catch (error) {
    setStatus(error.message || "Failed to load prompt", "error");
  } finally {
    setLoading(false);
  }
}

async function loadMemory(memoryId, options = {}) {
  if (!memoryId) return;
  if (!options.skipDirtyCheck && state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  setLoading(true);
  try {
    const payload = await requestJson(`${apiBase}/memories/${encodeURIComponent(memoryId)}`);
    applyMemoryDetail(payload.memory, payload.audit_events);
  } catch (error) {
    setStatus(error.message || "Failed to load memory", "error");
  } finally {
    setLoading(false);
  }
}

async function loadParameter(key, options = {}) {
  if (!key) return;
  if (!options.skipDirtyCheck && state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  setLoading(true);
  try {
    const payload = await requestJson(`/api/agent/parameters/${encodeURIComponent(key)}?include_audit=true`);
    applyParameterDetail(payload.entry, payload.context_json || payload.entry?.context_json || defaultParameterContext(), payload.audit_events || []);
  } catch (error) {
    setStatus(error.message || "Failed to load parameter", "error");
  } finally {
    setLoading(false);
  }
}

function selectTemplate(promptKey) {
  loadTemplate(promptKey);
}

function selectMemory(memoryId) {
  loadMemory(memoryId);
}

function selectParameter(key) {
  loadParameter(key);
}

function parseCommaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseLineList(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function collectMemoryPayload() {
  const safeExamples = document.querySelector("#memory-safe-examples");
  const blockedExamples = document.querySelector("#memory-blocked-examples");
  return {
    instruction: templateBody?.value || "",
    summary: memorySummary?.value || "",
    status: memoryStatus?.value || "active",
    memory_kind: memoryKind?.value || "task_memory",
    scope: memoryScope?.value || "global",
    model_name: memoryModelName?.value || "",
    model_family: memoryModelFamily?.value || "",
    task_type: memoryTaskType?.value || "",
    tool_type: memoryToolType?.value || "",
    intent_type: memoryIntentType?.value || "",
    validator_error_type: memoryValidatorErrorType?.value || "",
    safe_examples: parseLineList(safeExamples?.value || ""),
    blocked_examples: parseLineList(blockedExamples?.value || ""),
    tags: parseCommaList(memoryTags?.value || ""),
    provenance: "manual",
    rationale: memoryRationale?.value || "",
  };
}

function parameterValueContainsMask(value) {
  if (value === "••••") return true;
  if (Array.isArray(value)) return value.some(parameterValueContainsMask);
  if (value && typeof value === "object") return Object.values(value).some(parameterValueContainsMask);
  return false;
}

function collectParameterPayload() {
  const key = String(parameterKey?.value || "").trim();
  const valueJson = parseObjectJson(parameterValueJson?.value || "{}", "Value JSON");
  const contextJson = buildParameterContextFromFields();
  const payload = {
    key,
    description: String(parameterDescription?.value || "").trim(),
    aliases: parseCommaList(parameterAliases?.value || ""),
    tags: parseCommaList(parameterTags?.value || ""),
    sensitive: parameterSensitive?.checked !== false,
    context_json: contextJson,
  };
  if (state.creatingParameter || state.parameterValueRevealed || !parameterValueContainsMask(valueJson)) {
    payload.value_json = valueJson;
  }
  return payload;
}

async function saveTemplate() {
  if (state.mode === "memories") {
    await saveMemory();
    return;
  }
  if (state.mode === "parameters") {
    await saveParameter();
    return;
  }
  if (!state.detail || !templateBody) return;
  setLoading(true);
  setStatus("Saving...");
  try {
    const payload = await requestJson(`${apiBase}/templates/${encodeURIComponent(state.selectedKey)}`, {
      method: "PATCH",
      body: JSON.stringify({ body: templateBody.value }),
    });
    applyTemplateDetail(payload.template);
    await loadTemplates(payload.template.prompt_key);
    setStatus("Saved", "success");
  } catch (error) {
    const message = error.message || "Save failed";
    setStatus(message, "error");
    notifyUiError(
      "Prompt save failed",
      message,
      "Review the prompt body and try saving again.",
      templateBody,
    );
  } finally {
    setLoading(false);
  }
}

async function saveParameter() {
  if (!state.parameterDetail) return;
  let payloadBody;
  try {
    payloadBody = collectParameterPayload();
  } catch (error) {
    const message = error.message || "Parameter JSON is invalid";
    setStatus(message, "error");
    notifyUiError(
      "Parameter JSON invalid",
      message,
      "Correct the parameter JSON object, then save again.",
      parameterValueJson,
    );
    return;
  }
  if (!payloadBody.key) {
    setStatus("Parameter key cannot be empty", "error");
    notifyUiError(
      "Parameter key required",
      "The parameter was not saved because the key is missing.",
      "Enter a parameter key, then save again.",
      parameterKey,
    );
    return;
  }
  if (state.creatingParameter && !payloadBody.value_json) {
    setStatus("New parameters need a value_json object", "error");
    notifyUiError(
      "Parameter value required",
      "New parameters need a value_json object.",
      "Enter a JSON object value, then save again.",
      parameterValueJson,
    );
    return;
  }
  setLoading(true);
  setStatus("Saving parameter...");
  try {
    const url = state.creatingParameter
      ? "/api/agent/parameters"
      : `/api/agent/parameters/${encodeURIComponent(state.selectedParameterKey)}`;
    const payload = await requestJson(url, {
      method: state.creatingParameter ? "POST" : "PATCH",
      body: JSON.stringify(payloadBody),
    });
    state.creatingParameter = false;
    await loadParameters(payload.entry?.normalized_key || payload.entry?.key || payloadBody.key);
    setStatus("Parameter saved", "success");
  } catch (error) {
    const message = error.message || "Parameter save failed";
    setStatus(message, "error");
    notifyUiError(
      "Parameter save failed",
      message,
      "Review the key and JSON value, then save again.",
    );
  } finally {
    setLoading(false);
  }
}

async function saveMemory() {
  if (!state.memoryDetail || !templateBody) return;
  const payloadBody = collectMemoryPayload();
  if (!String(payloadBody.instruction || "").trim()) {
    setStatus("Memory instruction cannot be empty", "error");
    notifyUiError(
      "Memory instruction required",
      "The memory was not saved because the instruction is empty.",
      "Enter the instruction the agent should remember, then save again.",
      templateBody,
    );
    return;
  }
  setLoading(true);
  setStatus("Saving memory...");
  try {
    const url = state.creatingMemory
      ? `${apiBase}/memories`
      : `${apiBase}/memories/${encodeURIComponent(state.selectedMemoryId)}`;
    const payload = await requestJson(url, {
      method: state.creatingMemory ? "POST" : "PATCH",
      body: JSON.stringify(payloadBody),
    });
    state.creatingMemory = false;
    applyMemoryDetail(payload.memory, payload.audit_events || []);
    await loadMemories(payload.memory.memory_id);
    setStatus("Memory saved", "success");
  } catch (error) {
    const message = error.message || "Memory save failed";
    setStatus(message, "error");
    notifyUiError(
      "Memory save failed",
      message,
      "Review the memory instruction and try saving again.",
    );
  } finally {
    setLoading(false);
  }
}

async function resetTemplate() {
  if (!state.detail || !state.detail.has_default) return;
  if (!window.confirm(`Reset ${state.selectedKey} to the checked-in default?`)) {
    return;
  }
  setLoading(true);
  setStatus("Resetting...");
  try {
    const payload = await requestJson(
      `${apiBase}/templates/${encodeURIComponent(state.selectedKey)}/reset`,
      { method: "POST" }
    );
    applyTemplateDetail(payload.template);
    await loadTemplates(payload.template.prompt_key);
    setStatus("Reset", "success");
  } catch (error) {
    const message = error.message || "Reset failed";
    setStatus(message, "error");
    notifyUiError(
      "Prompt reset failed",
      message,
      "Refresh the prompt editor and try resetting again.",
    );
  } finally {
    setLoading(false);
  }
}

function collectVariables() {
  const variables = {};
  for (const input of Array.from(variableFields?.querySelectorAll("[data-variable-input]") || [])) {
    variables[input.dataset.variableInput] = input.value;
  }
  return variables;
}

async function renderTemplate() {
  if (!state.detail) return;
  setLoading(true);
  if (renderStatus) renderStatus.textContent = "Rendering";
  if (renderOutput) renderOutput.textContent = "";
  try {
    const payload = await requestJson(
      `${apiBase}/templates/${encodeURIComponent(state.selectedKey)}/render`,
      {
        method: "POST",
        body: JSON.stringify({ variables: collectVariables() }),
      }
    );
    if (renderOutput) renderOutput.textContent = payload.rendered || "";
    if (renderStatus) renderStatus.textContent = "Rendered";
    setStatus("Rendered", "success");
  } catch (error) {
    if (renderOutput) renderOutput.textContent = error.message || "Render failed";
    if (renderStatus) renderStatus.textContent = "Error";
    const message = error.message || "Render failed";
    setStatus(message, "error");
    notifyUiError(
      "Prompt render failed",
      message,
      "Check the variables and prompt body, then render again.",
    );
  } finally {
    setLoading(false);
  }
}

function createNewMemory() {
  if (state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  const memory = {
    memory_id: "",
    instruction: "",
    summary: "",
    status: "active",
    memory_kind: "task_memory",
    scope: "global",
    model_name: "",
    model_family: "",
    task_type: "",
    tool_type: "",
    intent_type: "",
    validator_error_type: "",
    safe_examples: [],
    blocked_examples: [],
    tags: [],
    provenance: "manual",
    request_id: "",
    rationale: "",
    created_at: "",
    updated_at: "",
    use_count: 0,
    last_used_at: "",
  };
  applyMemoryDetail(memory, [], { creating: true });
  window.setTimeout(() => templateBody?.focus(), 0);
}

function createNewParameter() {
  if (state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  const entry = {
    key: "",
    normalized_key: "",
    value_schema: {},
    masked_value_json: {},
    context_json: defaultParameterContext(),
    context_schema: {},
    description: "",
    aliases: [],
    tags: [],
    sensitive: true,
    created_at: "",
    updated_at: "",
    use_count: 0,
    last_used_at: "",
  };
  applyParameterDetail(entry, defaultParameterContext(), [], { creating: true, valueJsonOverride: {}, valueRevealed: true });
  window.setTimeout(() => parameterKey?.focus(), 0);
}

async function revealParameterValue() {
  const key = state.selectedParameterKey || parameterKey?.value || "";
  if (!key || state.creatingParameter) return;
  setLoading(true);
  try {
    const payload = await requestJson(`/api/agent/parameters/${encodeURIComponent(key)}/reveal`, {
      method: "POST",
    });
    applyParameterDetail(
      payload.entry,
      payload.context_json || payload.entry?.context_json || defaultParameterContext(),
      state.parameterAuditEvents,
      { valueJsonOverride: payload.value_json || {}, valueRevealed: true },
    );
  } catch (error) {
    const message = error.message || "Reveal failed";
    setStatus(message, "error");
    notifyUiError(
      "Parameter reveal failed",
      message,
      "Refresh parameters and try revealing the value again.",
      parameterKey,
    );
  } finally {
    setLoading(false);
  }
}

async function setEditorMode(mode) {
  const nextMode = mode === "memories" ? "memories" : mode === "parameters" ? "parameters" : "prompts";
  if (state.mode === nextMode) return;
  if (state.dirty && !window.confirm("Discard unsaved changes?")) {
    return;
  }
  state.mode = nextMode;
  document.body.dataset.editorMode = nextMode;
  promptsModeButton?.classList.toggle("active", nextMode === "prompts");
  memoriesModeButton?.classList.toggle("active", nextMode === "memories");
  parametersModeButton?.classList.toggle("active", nextMode === "parameters");
  if (templateSearch) {
    templateSearch.value = "";
    templateSearch.placeholder = nextMode === "memories"
      ? "Search memories"
      : nextMode === "parameters"
        ? "Search parameters"
        : "Search prompts";
  }
  if (memoryFields) memoryFields.hidden = nextMode !== "memories";
  if (parameterFields) parameterFields.hidden = nextMode !== "parameters";
  setStatus("", "");
  renderScopeFilter();
  renderTemplateList();
  if (nextMode === "memories") {
    await loadMemories();
  } else if (nextMode === "parameters") {
    await loadParameters();
  } else {
    await loadTemplates();
  }
  updateActions();
}

templateSearch?.addEventListener("input", renderTemplateList);
scopeFilter?.addEventListener("change", renderTemplateList);
themeSelect?.addEventListener("change", () => applyTheme(themeSelect.value));
promptsModeButton?.addEventListener("click", () => {
  void setEditorMode("prompts");
});
memoriesModeButton?.addEventListener("click", () => {
  void setEditorMode("memories");
});
parametersModeButton?.addEventListener("click", () => {
  void setEditorMode("parameters");
});
newMemoryButton?.addEventListener("click", createNewMemory);
newParameterButton?.addEventListener("click", createNewParameter);

templateBody?.addEventListener("input", () => {
  if (state.mode === "memories") {
    setDirty(templateBody.value !== (state.memoryDetail?.instruction || ""));
  } else if (state.mode === "parameters") {
    setDirty(true);
  } else {
    setDirty(templateBody.value !== (state.detail?.body || ""));
  }
});

for (const field of [
  memorySummary,
  memoryStatus,
  memoryKind,
  memoryScope,
  memoryModelName,
  memoryModelFamily,
  memoryTaskType,
  memoryToolType,
  memoryIntentType,
  memoryValidatorErrorType,
  memoryTags,
  memoryRationale,
]) {
  field?.addEventListener("input", () => setDirty(true));
  field?.addEventListener("change", () => setDirty(true));
}

for (const field of [
  parameterKey,
  parameterDescription,
  parameterAliases,
  parameterTags,
  parameterSensitive,
  parameterValueJson,
  parameterContextSummary,
  parameterContextPromptGuidance,
  parameterContextClarificationGuidance,
  parameterContextConcepts,
  parameterContextMetrics,
  parameterContextRelationships,
]) {
  field?.addEventListener("input", () => {
    if (field !== parameterValueJson && field !== parameterKey && field !== parameterDescription && field !== parameterAliases && field !== parameterTags && field !== parameterSensitive) {
      syncParameterContextJsonFromStructured();
    }
    setDirty(true);
  });
  field?.addEventListener("change", () => setDirty(true));
}

parameterContextJson?.addEventListener("change", () => {
  try {
    const context = parseObjectJson(parameterContextJson.value || "{}", "context_json");
    fillParameterContextFields(context);
    setDirty(true);
    setStatus("", "");
  } catch (error) {
    setStatus(error.message || "context_json is invalid", "error");
  }
});

saveButton?.addEventListener("click", saveTemplate);
revealParameterButton?.addEventListener("click", revealParameterValue);
resetButton?.addEventListener("click", resetTemplate);
renderButton?.addEventListener("click", renderTemplate);

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (!saveButton?.disabled) {
      saveTemplate();
    }
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

initTheme();
const startupParams = new URLSearchParams(window.location.search || "");
const startupMode = window.location.pathname.includes("parameter-editor")
  ? "parameters"
  : (startupParams.get("mode") === "parameters" ? "parameters" : startupParams.get("mode") === "memories" ? "memories" : "prompts");
const startupKey = startupParams.get("key") || "";
state.mode = startupMode;
document.body.dataset.editorMode = startupMode;
promptsModeButton?.classList.toggle("active", startupMode === "prompts");
memoriesModeButton?.classList.toggle("active", startupMode === "memories");
parametersModeButton?.classList.toggle("active", startupMode === "parameters");
if (memoryFields) memoryFields.hidden = startupMode !== "memories";
if (parameterFields) parameterFields.hidden = startupMode !== "parameters";
if (templateSearch) {
  templateSearch.placeholder = startupMode === "memories"
    ? "Search memories"
    : startupMode === "parameters"
      ? "Search parameters"
      : "Search prompts";
}
const templatesStartup = startupMode === "parameters"
  ? loadParameters(startupKey)
  : startupMode === "memories"
    ? loadMemories(startupKey)
    : loadTemplates(startupKey);
const versionStartup = loadEditorVersion();
Promise.allSettled([templatesStartup, versionStartup]).finally(finishAppBoot);
