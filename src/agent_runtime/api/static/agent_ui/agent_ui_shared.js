(() => {
  "use strict";

  const apiBase = "/api/agent";
  const settingsKey = "";
  const themeKey = "";
  const selectedGatewayKey = "openfabric.agentUi.selectedGatewayId";
  const volatileStorage = new Map();
  const notificationSoundVariants = Object.freeze(["chime", "ping", "soft", "alert"]);
  const notificationSoundDefault = "chime";
  const chatPopAnimationModes = Object.freeze([
    "none",
    "soft-rise",
    "slide-up",
    "slide-side",
    "scale-pop",
    "spring",
    "flip",
    "skew-snap",
    "blur-glow",
    "drop-in",
    "stream-roll",
    "odometer",
  ]);
  const thinkingTextAnimationModes = Object.freeze([
    "none",
    "roll-up",
    "soft-rise",
    "slide-left",
    "snap-down",
    "fade",
    "pop",
    "flip",
    "blur",
    "skew",
    "bounce",
    "swing",
    "odometer",
  ]);
  const supportedThemeNames = Object.freeze([
    "github",
    "light",
    "daylight",
    "mint",
    "citrus",
    "rosewater",
    "steel",
    "graphite",
    "midnight",
    "aurora",
    "ember",
    "ubuntu",
    "ubuntu-dark",
    "dracula",
    "nord",
    "tokyo-night",
    "catppuccin-mocha",
    "solarized-dark",
    "gruvbox-dark",
    "nebula",
    "moss",
    "retro",
    "lagoon",
    "sakura",
    "harbor",
    "circuit",
    "contrast",
    "random-pastel",
  ]);

  const defaultSettings = Object.freeze({
    agent_display_name: "Agent",
    auto_approve_commands: false,
    ui_agent_mode: "llm_operator",
    ui_theme: "github",
    ui_chat_pop_animation: "none",
    ui_thinking_text_animation: "roll-up",
    operator_policy_profile: "assisted",
    reasoning_profile: "balanced",
    repair_profile: "balanced",
    workflow_execution_mode: "streaming",
    prompt_rephrase_enabled: true,
    response_streaming_enabled: true,
    sql_agent_chat_route_mode: "agentic",
    operator_workspace_cwd_guard_enabled: false,
    llm_operator_verbose_enabled: false,
    agent_learning_ledger_auto_learn_enabled: true,
    agent_command_template_cache_similarity_threshold: 0.82,
    agent_command_template_cache_secondary_similarity_threshold: 0.15,
    lrnt_enabled: true,
    lrnt_similarity_threshold: 0.92,
    lrdirect_enabled: true,
    browser_notifications_enabled: true,
    notification_sound_enabled: true,
    notification_sound_variant: notificationSoundDefault,
    notification_sound_volume: 0.55,
  });

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function readStorage(key, fallback = "") {
    return volatileStorage.has(key) ? volatileStorage.get(key) : fallback;
  }

  function writeStorage(key, value) {
    volatileStorage.set(key, String(value ?? ""));
    return true;
  }

  function readJsonStorage(key, fallback) {
    try {
      const raw = volatileStorage.get(key);
      if (!raw) {
        return fallback;
      }
      return JSON.parse(raw);
    } catch (_error) {
      return fallback;
    }
  }

  function writeJsonStorage(key, value) {
    return writeStorage(key, JSON.stringify(value));
  }

  function compactText(value, fallback = "") {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text || fallback;
  }

  function clampNumber(value, min, max, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return fallback;
    }
    return Math.min(max, Math.max(min, number));
  }

  function normalizeThemeName(value, fallback = defaultSettings.ui_theme) {
    const candidate = compactText(value);
    if (supportedThemeNames.includes(candidate)) {
      return candidate;
    }
    const safeFallback = compactText(fallback);
    return supportedThemeNames.includes(safeFallback) ? safeFallback : defaultSettings.ui_theme;
  }

  function storedThemePreference(fallback = "") {
    const safeFallback = compactText(fallback);
    return supportedThemeNames.includes(safeFallback) ? safeFallback : "";
  }

  function normalizeAgentSettings(input = {}) {
    const source = isPlainObject(input) ? input : {};
    const merged = { ...defaultSettings, ...source };
    return {
      ...defaultSettings,
      agent_display_name: compactText(merged.agent_display_name, defaultSettings.agent_display_name).slice(0, 40),
      auto_approve_commands: merged.auto_approve_commands === true,
      ui_agent_mode: ["llm_operator", "standard", "advisory"].includes(merged.ui_agent_mode)
        ? merged.ui_agent_mode
        : defaultSettings.ui_agent_mode,
      ui_theme: normalizeThemeName(merged.ui_theme),
      ui_chat_pop_animation: normalizeChatPopAnimationMode(merged.ui_chat_pop_animation),
      ui_thinking_text_animation: normalizeThinkingTextAnimationMode(
        merged.ui_thinking_text_animation,
      ),
      operator_policy_profile: ["deterministic", "assisted"].includes(merged.operator_policy_profile)
        ? merged.operator_policy_profile
        : defaultSettings.operator_policy_profile,
      reasoning_profile: ["fast", "balanced", "deep"].includes(merged.reasoning_profile)
        ? merged.reasoning_profile
        : defaultSettings.reasoning_profile,
      repair_profile: ["conservative", "balanced", "aggressive"].includes(merged.repair_profile)
        ? merged.repair_profile
        : defaultSettings.repair_profile,
      workflow_execution_mode: ["auto", "full_plan", "streaming"].includes(merged.workflow_execution_mode)
        ? merged.workflow_execution_mode
        : defaultSettings.workflow_execution_mode,
      prompt_rephrase_enabled: merged.prompt_rephrase_enabled !== false,
      response_streaming_enabled: merged.response_streaming_enabled === true,
      sql_agent_chat_route_mode: ["agentic", "direct"].includes(merged.sql_agent_chat_route_mode)
        ? merged.sql_agent_chat_route_mode
        : defaultSettings.sql_agent_chat_route_mode,
      operator_workspace_cwd_guard_enabled: merged.operator_workspace_cwd_guard_enabled !== false,
      llm_operator_verbose_enabled: merged.llm_operator_verbose_enabled === true,
      agent_learning_ledger_auto_learn_enabled: merged.agent_learning_ledger_auto_learn_enabled !== false,
      agent_command_template_cache_similarity_threshold: clampNumber(
        merged.agent_command_template_cache_similarity_threshold,
        0,
        1,
        defaultSettings.agent_command_template_cache_similarity_threshold,
      ),
      agent_command_template_cache_secondary_similarity_threshold: clampNumber(
        merged.agent_command_template_cache_secondary_similarity_threshold,
        0,
        1,
        defaultSettings.agent_command_template_cache_secondary_similarity_threshold,
      ),
      lrnt_enabled: merged.lrnt_enabled !== false,
      lrnt_similarity_threshold: clampNumber(
        merged.lrnt_similarity_threshold,
        0,
        1,
        defaultSettings.lrnt_similarity_threshold,
      ),
      lrdirect_enabled: merged.lrdirect_enabled !== false,
      browser_notifications_enabled: merged.browser_notifications_enabled !== false,
      notification_sound_enabled: merged.notification_sound_enabled !== false,
      notification_sound_variant: normalizeNotificationSoundVariant(merged.notification_sound_variant),
      notification_sound_volume: normalizeNotificationSoundVolume(merged.notification_sound_volume),
    };
  }

  function normalizeNotificationSoundVariant(value) {
    const candidate = compactText(value, notificationSoundDefault).toLowerCase();
    return notificationSoundVariants.includes(candidate) ? candidate : notificationSoundDefault;
  }

  function normalizeChatPopAnimationMode(value) {
    const candidate = compactText(value, defaultSettings.ui_chat_pop_animation).toLowerCase();
    return chatPopAnimationModes.includes(candidate) ? candidate : defaultSettings.ui_chat_pop_animation;
  }

  function normalizeThinkingTextAnimationMode(value) {
    const candidate = compactText(value, defaultSettings.ui_thinking_text_animation).toLowerCase();
    return thinkingTextAnimationModes.includes(candidate)
      ? candidate
      : defaultSettings.ui_thinking_text_animation;
  }

  function normalizeNotificationSoundVolume(value) {
    return clampNumber(value, 0, 1, defaultSettings.notification_sound_volume);
  }

  function loadAgentSettings() {
    return normalizeAgentSettings(defaultSettings);
  }

  function saveAgentSettings(settings) {
    const normalized = normalizeAgentSettings(settings);
    return normalized;
  }

  function settingsBackendHasValues(settings) {
    return settings && typeof settings === "object" && Object.keys(settings).length > 0;
  }

  function settingsBackendEnabled(payload) {
    return true;
  }

  function settingsSignature(settings) {
    try {
      return JSON.stringify(normalizeAgentSettings(settings));
    } catch (_error) {
      return "";
    }
  }

  async function fetchSettingsConfigDefaults() {
    try {
      const payload = await apiJson("/settings/config");
      return normalizeAgentSettings({ ...defaultSettings, ...(payload?.defaults || {}) });
    } catch (_error) {
      return normalizeAgentSettings(defaultSettings);
    }
  }

  function fetchSettingsBackendPreferences() {
    return apiJson("/settings/preferences");
  }

  function writeSettingsBackendPreferences({ enabled = true, settings = null } = {}) {
    const body = {};
    if (settings !== null) {
      body.settings = normalizeAgentSettings(settings);
    }
    return apiJson("/settings/preferences", { method: "PUT", body });
  }

  async function saveSharedAgentSettings(settings) {
    const normalized = saveAgentSettings(settings);
    await writeSettingsBackendPreferences({ enabled: true, settings: normalized });
    return normalized;
  }

  async function loadSharedAgentSettings({ fallbackSettings = null } = {}) {
    const defaults = await fetchSettingsConfigDefaults();
    const fallback = normalizeAgentSettings({ ...defaults, ...(fallbackSettings || loadAgentSettings()) });
    const payload = await fetchSettingsBackendPreferences();
    const enabled = true;
    const hasBackendSettings = settingsBackendHasValues(payload?.settings);
    const backendSettings = hasBackendSettings ? payload.settings : {};
    const backendOnlyBase = hasBackendSettings
      ? normalizeAgentSettings({ ...defaults, ...backendSettings })
      : null;
    const sharedBase = hasBackendSettings
      ? normalizeAgentSettings({ ...fallback, ...backendSettings })
      : fallback;
    const backendOnly = backendOnlyBase ? normalizeAgentSettings(backendOnlyBase) : backendOnlyBase;
    const shared = normalizeAgentSettings(sharedBase);
    saveAgentSettings(shared);
    if (
      enabled
      && (!hasBackendSettings || settingsSignature(shared) !== settingsSignature(backendOnly))
    ) {
      await writeSettingsBackendPreferences({ enabled: true, settings: shared });
    }
    return {
      backend_owned_settings: true,
      defaults,
      settings: shared,
    };
  }

  function settingsContext(settings) {
    normalizeAgentSettings(settings);
    return { target_ui: "agent_ui" };
  }

  function apiUrl(path, params = null) {
    const cleanPath = String(path || "").startsWith("/") ? String(path || "") : `/${path || ""}`;
    const url = new URL(`${apiBase}${cleanPath}`, window.location.origin);
    if (isPlainObject(params)) {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, String(value));
        }
      }
    }
    return `${url.pathname}${url.search}`;
  }

  function manualUrl(docId = "manual-home", port = "8013") {
    const hostname = window.location.hostname || "127.0.0.1";
    const targetHost = hostname === "0.0.0.0" || hostname === "::" ? "127.0.0.1" : hostname;
    const safePort = compactText(port, "8013");
    const safeDoc = compactText(docId, "manual-home");
    const url = new URL(`http://${targetHost}:${safePort}/manual`);
    url.hash = `doc=${encodeURIComponent(safeDoc)}`;
    return url.toString();
  }

  function hydrateManualLinks(root = document) {
    const scope = root && typeof root.querySelectorAll === "function" ? root : document;
    for (const link of scope.querySelectorAll("[data-manual-link]")) {
      const docId = link.dataset.manualDoc || "manual-home";
      const port = link.dataset.manualPort || "8013";
      link.href = manualUrl(docId, port);
    }
  }

  async function errorMessageFromResponse(response) {
    let text = "";
    try {
      text = await response.text();
    } catch (_error) {
      return `HTTP ${response.status}`;
    }
    if (!text) {
      return `HTTP ${response.status}`;
    }
    try {
      const parsed = JSON.parse(text);
      const detail = parsed && parsed.detail;
      if (Array.isArray(detail)) {
        return detail
          .map((item) => compactText(item?.msg || item?.message || item))
          .filter(Boolean)
          .join("; ") || text;
      }
      if (isPlainObject(detail)) {
        return compactText(detail.message || detail.error || JSON.stringify(detail), text);
      }
      return compactText(detail || parsed.message || parsed.error, text);
    } catch (_error) {
      return text;
    }
  }

  async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const init = { ...options, headers, cache: options.cache || "no-store" };
    delete init.params;
    if (init.body !== undefined && isPlainObject(init.body)) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(init.body);
    }
    const response = await fetch(apiUrl(path, options.params), init);
    if (!response.ok) {
      throw new Error(await errorMessageFromResponse(response));
    }
    return response;
  }

  async function apiJson(path, options = {}) {
    const response = await apiFetch(path, options);
    if (response.status === 204) {
      return {};
    }
    return response.json();
  }

  function ensureUiErrorToastStyles() {
    if (!document?.head || document.getElementById("openfabric-ui-error-toast-styles")) {
      return;
    }
    const style = document.createElement("style");
    style.id = "openfabric-ui-error-toast-styles";
    style.textContent = `
      .ui-error-toast-region {
        position: fixed;
        right: 18px;
        bottom: 18px;
        z-index: 220;
        display: grid;
        gap: 10px;
        width: min(420px, calc(100vw - 36px));
        pointer-events: none;
      }
      .ui-error-toast {
        pointer-events: auto;
        border: 1px solid rgba(220, 38, 38, 0.34);
        border-left: 4px solid #dc2626;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.96);
        box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
        color: #1f2937;
        padding: 12px 14px;
        font: 13px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        animation: ui-error-toast-in 180ms ease-out;
      }
      .ui-error-toast strong {
        display: block;
        margin-bottom: 3px;
        color: #991b1b;
        font-size: 13px;
      }
      .ui-error-toast span {
        display: block;
      }
      @keyframes ui-error-toast-in {
        from {
          opacity: 0;
          transform: translateY(10px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
    `;
    document.head.append(style);
  }

  function ensureUiErrorToastRegion() {
    if (!document?.body) {
      return null;
    }
    let region = document.getElementById("ui-error-toast-region");
    if (region) {
      return region;
    }
    ensureUiErrorToastStyles();
    region = document.createElement("div");
    region.id = "ui-error-toast-region";
    region.className = "ui-error-toast-region";
    region.setAttribute("aria-live", "assertive");
    region.setAttribute("aria-atomic", "false");
    document.body.append(region);
    return region;
  }

  function resolveUiErrorField(field) {
    if (!field) {
      return null;
    }
    if (typeof field === "string") {
      return document.querySelector(field);
    }
    return typeof field.focus === "function" ? field : null;
  }

  function markUiErrorField(field) {
    const target = resolveUiErrorField(field);
    if (!target) {
      return;
    }
    target.setAttribute("aria-invalid", "true");
    const clearInvalid = () => target.removeAttribute("aria-invalid");
    target.addEventListener("input", clearInvalid, { once: true });
    target.addEventListener("change", clearInvalid, { once: true });
    target.focus({ preventScroll: false });
  }

  function notifyUiError(input = {}, options = {}) {
    const source = typeof input === "string" ? { message: input } : (isPlainObject(input) ? input : {});
    const title = compactText(source.title, "Action failed");
    const message = compactText(source.message || source.detail || source.error, "The action could not be completed.");
    const fix = compactText(source.fix);
    const region = ensureUiErrorToastRegion();
    markUiErrorField(source.field);
    if (!region) {
      return null;
    }
    const toast = document.createElement("div");
    toast.className = "ui-error-toast";
    toast.dataset.level = "error";
    toast.setAttribute("role", "alert");
    const titleNode = document.createElement("strong");
    titleNode.textContent = title;
    const messageNode = document.createElement("span");
    messageNode.textContent = fix ? `${message} Fix: ${fix}` : message;
    toast.append(titleNode, messageNode);
    region.prepend(toast);
    const timeout = Number(options.persistMs ?? source.persistMs ?? 9000);
    if (timeout > 0) {
      window.setTimeout(() => toast.remove(), timeout);
    }
    return toast;
  }

  function submitPrompt({ prompt, agentMode = "llm_operator", llmModel = "", context = {}, conversationId = "" }) {
    const body = {
      prompt: compactText(prompt),
      agent_mode: agentMode,
      llm_model: compactText(llmModel),
      context: isPlainObject(context) ? context : {},
    };
    if (conversationId) {
      body.conversation_id = conversationId;
    }
    return apiJson("/request", { method: "POST", body });
  }

  function streamUrlForRequest(requestId, afterId = 0) {
    const params = afterId ? { after_id: afterId } : null;
    return apiUrl(`/stream/${encodeURIComponent(String(requestId || ""))}`, params);
  }

  function traceUrlForRequest(requestId) {
    return apiUrl(`/trace/${encodeURIComponent(String(requestId || ""))}`);
  }

  function normalizeStatus(value, fallback = "unknown") {
    return compactText(value, fallback).toLowerCase().replace(/[^a-z0-9_ -]+/g, "");
  }

  function statusTone(status) {
    const normalized = normalizeStatus(status);
    if (["completed", "success", "ok", "connected", "read"].includes(normalized)) {
      return "good";
    }
    if (["failed", "error", "disconnected", "cancelled", "dismissed"].includes(normalized)) {
      return "bad";
    }
    if (normalized.includes("awaiting") || normalized.includes("warning") || normalized.includes("paused")) {
      return "warn";
    }
    if (normalized.includes("running") || normalized.includes("checking") || normalized.includes("unread")) {
      return "live";
    }
    return "neutral";
  }

  function formatStatus(value, fallback = "Unknown") {
    return compactText(value, fallback)
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function formatCount(count, singular, plural = `${singular}s`) {
    const value = Number(count) || 0;
    return `${value} ${value === 1 ? singular : plural}`;
  }

  function formatTime(value, fallback = "Not scheduled") {
    const text = compactText(value);
    if (!text) {
      return fallback;
    }
    const date = new Date(text);
    if (Number.isNaN(date.getTime())) {
      return text;
    }
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  }

  function normalizeNotification(notification = {}) {
    const source = isPlainObject(notification) ? notification : {};
    return {
      ...source,
      notification_id: compactText(source.notification_id || source.id),
      title: compactText(source.title, "Notification"),
      message: compactText(source.message),
      level: normalizeStatus(source.level, "info"),
      status: normalizeStatus(source.status, "unread"),
      request_id: compactText(source.request_id),
      event_id: compactText(source.event_id),
      event_run_id: compactText(source.event_run_id),
      created_at: compactText(source.created_at),
    };
  }

  function normalizeEvent(event = {}) {
    const source = isPlainObject(event) ? event : {};
    return {
      ...source,
      event_id: compactText(source.event_id || source.id),
      title: compactText(source.title, "Untitled event"),
      prompt: compactText(source.prompt),
      status: normalizeStatus(source.status, "active"),
      event_kind: normalizeStatus(source.event_kind, "scheduled"),
      schedule_type: normalizeStatus(source.schedule_type, "interval"),
      interval_seconds: Number(source.interval_seconds) || 0,
      next_run_at: compactText(source.next_run_at),
      last_run_at: compactText(source.last_run_at),
      action_type: normalizeStatus(source.action_type, "agent_prompt"),
      notification_message: compactText(source.notification_message),
    };
  }

  function normalizeTask(task = {}) {
    const source = isPlainObject(task) ? task : {};
    return {
      ...source,
      task_id: compactText(source.task_id || source.id),
      title: compactText(source.title, "Untitled task"),
      prompt: compactText(source.prompt),
      status: normalizeStatus(source.status, "queued"),
      source: normalizeStatus(source.source, "task_sheet"),
      agent_mode: normalizeStatus(source.agent_mode, "llm_operator"),
      conversation_id: compactText(source.conversation_id),
      gateway_id: compactText(source.gateway_id),
      current_request_id: compactText(source.current_request_id),
      latest_request_id: compactText(source.latest_request_id),
      current_attempt_id: compactText(source.current_attempt_id),
      final_response_preview: compactText(source.final_response_preview),
      error_preview: compactText(source.error_preview),
      blocker_reason: compactText(source.blocker_reason),
      event_id: compactText(source.event_id),
      event_run_id: compactText(source.event_run_id),
      latest_checkpoint: isPlainObject(source.latest_checkpoint) ? source.latest_checkpoint : null,
      created_at: compactText(source.created_at),
      updated_at: compactText(source.updated_at),
    };
  }

  function listTasks(params = {}) {
    return apiJson("/tasks", { cache: "no-store", params });
  }

  function createTask(payload = {}) {
    return apiJson("/tasks", { method: "POST", body: payload });
  }

  function taskAction(taskId, action) {
    return apiJson(`/tasks/${encodeURIComponent(compactText(taskId))}/${compactText(action)}`, {
      method: "POST",
    });
  }

  function normalizeMonitor(input = {}) {
    const source = isPlainObject(input) ? input : {};
    return {
      ...source,
      monitor_id: compactText(source.monitor_id),
      title: compactText(source.title, "Untitled monitor"),
      prompt: compactText(source.prompt),
      mode: normalizeStatus(source.mode, "sample_command"),
      command: compactText(source.command),
      status: normalizeStatus(source.status, "queued"),
      condition: compactText(source.condition),
      natural_language_condition: compactText(source.natural_language_condition),
      trigger_mode: normalizeStatus(source.trigger_mode, "deterministic"),
      action_prompt: compactText(source.action_prompt),
      planner_rationale: compactText(source.planner_rationale),
      risk_notes: compactText(source.risk_notes),
      judge_interval_seconds: Number(source.judge_interval_seconds || 5),
      latest_judge_summary: compactText(source.latest_judge_summary),
      gateway_id: compactText(source.gateway_id),
      terminal_session_id: compactText(source.terminal_session_id),
      trigger_reason: compactText(source.trigger_reason),
      triggered_task_id: compactText(source.triggered_task_id),
      error_preview: compactText(source.error_preview),
      final_summary: compactText(source.final_summary),
      latest_observation: isPlainObject(source.latest_observation) ? source.latest_observation : null,
      created_at: compactText(source.created_at),
      updated_at: compactText(source.updated_at),
    };
  }

  function listMonitors(params = {}) {
    return apiJson("/monitors", { cache: "no-store", params });
  }

  function createMonitor(payload = {}) {
    return apiJson("/monitors", { method: "POST", body: payload });
  }

  function monitorAction(monitorId, action) {
    return apiJson(`/monitors/${encodeURIComponent(compactText(monitorId))}/${compactText(action)}`, {
      method: "POST",
    });
  }

  function traceFinalText(trace = {}) {
    if (!isPlainObject(trace)) {
      return "";
    }
    return compactText(trace.final_response || trace.error || trace.summary);
  }

  window.OpenFabricAgentUi = Object.freeze({
    apiBase,
    keys: Object.freeze({
      settings: settingsKey,
      theme: themeKey,
      selectedGateway: selectedGatewayKey,
    }),
    defaultSettings,
    apiUrl,
    apiFetch,
    apiJson,
    compactText,
    errorMessageFromResponse,
    formatCount,
    formatStatus,
    formatTime,
    hydrateManualLinks,
    isPlainObject,
    manualUrl,
    fetchSettingsBackendPreferences,
    fetchSettingsConfigDefaults,
    loadAgentSettings,
    loadSharedAgentSettings,
    normalizeAgentSettings,
    normalizeChatPopAnimationMode,
    normalizeThinkingTextAnimationMode,
    normalizeNotificationSoundVariant,
    normalizeNotificationSoundVolume,
    normalizeEvent,
    normalizeNotification,
    normalizeMonitor,
    normalizeTask,
    notifyUiError,
    notificationSoundDefault,
    notificationSoundVariants,
    chatPopAnimationModes,
    thinkingTextAnimationModes,
    readJsonStorage,
    readStorage,
    saveSharedAgentSettings,
    saveAgentSettings,
    settingsContext,
    statusTone,
    createTask,
    createMonitor,
    listMonitors,
    listTasks,
    monitorAction,
    streamUrlForRequest,
    submitPrompt,
    taskAction,
    traceFinalText,
    traceUrlForRequest,
    writeSettingsBackendPreferences,
    writeJsonStorage,
    writeStorage,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => hydrateManualLinks(), { once: true });
  } else {
    hydrateManualLinks();
  }
})();
