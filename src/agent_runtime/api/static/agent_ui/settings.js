(() => {
  "use strict";

  const Core = window.OpenFabricAgentUi;
  let MISSION_CONTROL_PRESETS = [];
  let MISSION_CONTROL_OPTIMIZATION_PROFILES = {};
  let MISSION_CONTROL_OPTIMIZATION_CONTROLLED_KEYS = [];

  const elements = {
    boot: document.querySelector("#app-boot-screen"),
    search: document.querySelector("#settings-search"),
    refresh: document.querySelector("#settings-refresh-button"),
    status: document.querySelector("#settings-sync-status"),
    scope: document.querySelector("#settings-scope-summary"),
    optimizationControl: document.querySelector("#settings-optimization-control"),
    optimizationHeat: document.querySelector("#settings-optimization-heat"),
    clarificationControl: document.querySelector("#settings-clarification-control"),
    clarificationHeat: document.querySelector("#settings-clarification-heat"),
    presets: document.querySelector("#settings-presets"),
    nav: document.querySelector("#settings-section-nav"),
    sections: document.querySelector("#settings-sections"),
    version: document.querySelector("#settings-registry-version"),
  };

  const state = {
    registry: null,
    defaults: {},
    settings: {},
    runtimeControls: {},
    settingDefinitions: new Map(),
    backendEnabled: true,
    saveTimer: null,
    dirtyKeys: new Set(),
    saving: false,
    lastSignature: "",
    syncTimer: null,
    backendUnavailable: false,
    runtimeUnavailable: false,
  };

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object || {}, key);
  }

  function compactText(value, fallback = "") {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text || fallback;
  }

  function clampNumber(value, min, max, fallback) {
    const number = Number(value);
    const safe = Number.isFinite(number) ? number : fallback;
    return Math.min(max, Math.max(min, safe));
  }

  function settingSignature(settings) {
    try {
      return JSON.stringify(settings, Object.keys(settings || {}).sort());
    } catch (_error) {
      return "";
    }
  }

  function setStatus(message, status = "saved") {
    if (!elements.status) return;
    elements.status.dataset.state = status;
    elements.status.textContent = message;
  }

  function notifyUiError(title, message, fix = "") {
    Core?.notifyUiError?.({ title, message, fix });
  }

  function finishBoot() {
    document.documentElement.classList.remove("app-booting");
    if (elements.boot) {
      elements.boot.hidden = true;
    }
  }

  async function apiJson(path, options = {}) {
    if (!Core?.apiJson) {
      throw new Error("Agent UI shared API is unavailable.");
    }
    return Core.apiJson(path, options);
  }

  function collectSettingDefinitions(registry) {
    state.settingDefinitions.clear();
    for (const section of registry?.sections || []) {
      for (const setting of section.settings || []) {
        state.settingDefinitions.set(setting.key, setting);
      }
    }
    MISSION_CONTROL_PRESETS = Array.isArray(registry?.presets) ? registry.presets : [];
    MISSION_CONTROL_OPTIMIZATION_PROFILES = isPlainObject(registry?.optimization_profiles)
      ? registry.optimization_profiles
      : {};
    MISSION_CONTROL_OPTIMIZATION_CONTROLLED_KEYS = Array.isArray(registry?.optimization_controlled_keys)
      ? registry.optimization_controlled_keys.map((key) => String(key || "")).filter(Boolean)
      : [];
  }

  function optionValues(definition) {
    return new Set((definition.options || []).map((item) => String(item.value)));
  }

  function normalizeValue(value, definition) {
    if (!definition) return value;
    if (definition.control === "boolean") {
      return value === true;
    }
    if (definition.control === "number" || definition.control === "range") {
      const limit = definition.limit || {};
      const fallback = Number(definition.default ?? 0);
      const min = Number.isFinite(Number(limit.min)) ? Number(limit.min) : Number.NEGATIVE_INFINITY;
      const max = Number.isFinite(Number(limit.max)) ? Number(limit.max) : Number.POSITIVE_INFINITY;
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return clampNumber(number, min, max, fallback);
    }
    if (definition.control === "select") {
      const candidate = String(value ?? definition.default ?? "").trim();
      const values = optionValues(definition);
      if (values.has(candidate)) return candidate;
      const fallback = String(definition.default ?? "").trim();
      return values.has(fallback) ? fallback : [...values][0] || fallback;
    }
    return String(value ?? definition.default ?? "").trim();
  }

  function normalizeSettings(input = {}) {
    const source = isPlainObject(input) ? input : {};
    const normalized = { ...source };
    for (const [key, definition] of state.settingDefinitions.entries()) {
      if (definition.readonly || definition.target === "meta") continue;
      const raw = hasOwn(source, key) ? source[key] : definition.default;
      normalized[key] = normalizeValue(raw, definition);
    }
    return normalized;
  }

  function currentValue(definition) {
    if (definition.readonly) {
      return hasOwn(definition, "value") ? definition.value : state.settings[definition.key];
    }
    return state.settings[definition.key];
  }

  function formatValue(value) {
    if (value === true) return "true";
    if (value === false) return "false";
    if (value === null || value === undefined || value === "") return "not configured";
    return String(value);
  }

  function withLegacyRequestPreferences(settings) {
    return { ...(isPlainObject(settings) ? settings : {}) };
  }

  function writeThroughLegacyRequestPreferences(settings) {
    void settings;
  }

  function persistLocalSettings() {
    writeThroughLegacyRequestPreferences(state.settings);
  }

  function applyTheme(theme) {
    const safeTheme = String(theme || "").trim();
    if (!safeTheme) return;
    document.documentElement.dataset.theme = safeTheme;
  }

  function definitionTargetsRuntime(definition) {
    return definition?.target === "runtime" || definition?.target === "both";
  }

  function definitionTargetsPreferences(definition) {
    return !definition?.readonly && definition?.target !== "runtime" && definition?.target !== "meta";
  }

  function runtimePayloadForKeys(keys) {
    const body = {};
    for (const key of keys) {
      const definition = state.settingDefinitions.get(key);
      if (!definitionTargetsRuntime(definition)) continue;
      body[key] = normalizeValue(state.settings[key], definition);
    }
    return body;
  }

  async function saveNow() {
    if (state.saving) return;
    if (state.saveTimer) {
      window.clearTimeout(state.saveTimer);
      state.saveTimer = null;
    }
    const keys = [...state.dirtyKeys];
    if (!keys.length) return;
    state.dirtyKeys.clear();
    state.saving = true;
    setStatus("Saving shared settings...", "saving");
    persistLocalSettings();
    try {
      await Core.writeSettingsBackendPreferences({ settings: state.settings });
      const runtimePayload = runtimePayloadForKeys(keys);
      if (!state.runtimeUnavailable && Object.keys(runtimePayload).length) {
        state.runtimeControls = await apiJson("/runtime-controls", {
          method: "POST",
          body: runtimePayload,
        });
        state.runtimeUnavailable = false;
      }
      state.lastSignature = settingSignature(state.settings);
      const runtimeSkipped = state.runtimeUnavailable && Object.keys(runtimePayload).length > 0;
      setStatus(
        runtimeSkipped
          ? "Saved to backend settings; live runtime controls are unavailable."
          : "Saved to backend settings.",
        runtimeSkipped ? "error" : "saved",
      );
      updateScopeSummary();
    } catch (error) {
      const message = compactText(error?.message, "unknown error");
      setStatus(`Autosave failed: ${message}`, "error");
      notifyUiError(
        "Settings autosave failed",
        message,
        "Check the backend connection, then change the setting or press Refresh to retry.",
      );
      for (const key of keys) state.dirtyKeys.add(key);
    } finally {
      state.saving = false;
    }
  }

  function scheduleAutosave(key) {
    state.dirtyKeys.add(key);
    setStatus("Autosave pending...", "saving");
    if (state.saveTimer) window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(() => {
      void saveNow();
    }, 350);
  }

  function setSettingValue(key, value, { autosave = true } = {}) {
    const definition = state.settingDefinitions.get(key);
    if (!definition || definition.readonly) return;
    state.settings[key] = normalizeValue(value, definition);
    if (key === "ui_theme") {
      applyTheme(state.settings[key]);
    }
    renderControlValue(key);
    renderOptimizationControl();
    if (key === "agent_clarification_mode") {
      renderClarificationControl();
    }
    if (autosave) scheduleAutosave(key);
  }

  function controlSelector(key) {
    return `[data-setting-control="${CSS.escape(key)}"]`;
  }

  function renderControlValue(key) {
    const definition = state.settingDefinitions.get(key);
    if (!definition) return;
    const element = document.querySelector(controlSelector(key));
    if (!element) return;
    const value = currentValue(definition);
    if (definition.readonly) {
      element.textContent = formatValue(value);
    } else if (element instanceof HTMLInputElement && element.type === "checkbox") {
      element.checked = value === true;
    } else if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
      element.value = String(value ?? "");
    }
  }

  function createControl(definition) {
    const value = currentValue(definition);
    if (definition.readonly) {
      const output = document.createElement("div");
      output.className = "mission-readonly-value";
      output.dataset.settingControl = definition.key;
      output.textContent = formatValue(value);
      return output;
    }

    let control;
    if (definition.control === "boolean") {
      control = document.createElement("input");
      control.type = "checkbox";
      control.checked = value === true;
    } else if (definition.control === "select") {
      control = document.createElement("select");
      for (const option of definition.options || []) {
        const item = document.createElement("option");
        item.value = String(option.value);
        item.textContent = String(option.label || option.value);
        control.append(item);
      }
      control.value = String(value ?? "");
    } else if (definition.control === "number" || definition.control === "range") {
      control = document.createElement("input");
      control.type = definition.control === "range" ? "range" : "number";
      const limit = definition.limit || {};
      if (limit.min !== undefined) control.min = String(limit.min);
      if (limit.max !== undefined) control.max = String(limit.max);
      control.step = definition.control === "range" ? "0.05" : "1";
      control.value = String(value ?? "");
    } else {
      control = document.createElement("input");
      control.type = "text";
      control.value = String(value ?? "");
      control.spellcheck = false;
      control.autocomplete = "off";
    }
    control.dataset.settingControl = definition.key;
    control.setAttribute("aria-label", definition.label);
    const eventName = definition.control === "range" ? "input" : "change";
    control.addEventListener(eventName, () => {
      const nextValue = control instanceof HTMLInputElement && control.type === "checkbox"
        ? control.checked
        : control.value;
      setSettingValue(definition.key, nextValue);
    });
    return control;
  }

  function createSettingRow(definition) {
    const row = document.createElement("article");
    row.className = "mission-setting-row";
    row.dataset.settingKey = definition.key;
    row.dataset.settingSearch = [
      definition.label,
      definition.key,
      definition.description,
      definition.impact,
      definition.source,
    ].join(" ").toLowerCase();

    const copy = document.createElement("div");
    copy.className = "mission-setting-copy";
    const heading = document.createElement("div");
    heading.className = "mission-setting-heading";
    const label = document.createElement("strong");
    label.className = "mission-setting-label";
    label.textContent = definition.label;
    const badge = document.createElement("span");
    badge.className = "mission-setting-badge";
    badge.textContent = definition.readonly ? "read only" : definition.target || "shared";
    heading.append(label, badge);
    copy.append(heading);

    for (const [className, text] of [
      ["mission-setting-description", definition.description],
      ["mission-setting-impact", definition.impact],
    ]) {
      if (!text) continue;
      const paragraph = document.createElement("p");
      paragraph.className = className;
      paragraph.textContent = text;
      copy.append(paragraph);
    }

    const control = document.createElement("div");
    control.className = "mission-setting-control";
    control.append(createControl(definition));
    row.append(copy, control);
    return row;
  }

  function renderPresets() {
    if (!elements.presets) return;
    elements.presets.replaceChildren();
    for (const preset of MISSION_CONTROL_PRESETS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mission-preset-button";
      button.dataset.presetId = preset.id;
      const label = document.createElement("strong");
      label.textContent = preset.label;
      const description = document.createElement("span");
      description.textContent = preset.description || "";
      button.append(label, description);
      button.addEventListener("click", () => applyPreset(preset));
      elements.presets.append(button);
    }
  }

  function profileNormalizedValues(profileId) {
    const profile = MISSION_CONTROL_OPTIMIZATION_PROFILES?.[profileId];
    if (!isPlainObject(profile?.values)) return null;
    return normalizeSettings({ ...state.defaults, ...profile.values });
  }

  function valuesEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function currentOptimizationProfile() {
    for (const profileId of ["accuracy", "speed"]) {
      const profileValues = profileNormalizedValues(profileId);
      if (!profileValues) continue;
      const exact = MISSION_CONTROL_OPTIMIZATION_CONTROLLED_KEYS.every((key) =>
        valuesEqual(state.settings[key], profileValues[key]),
      );
      if (exact) return profileId;
    }
    return "custom";
  }

  function normalizeClarificationMode(mode) {
    const value = String(mode || "balanced").trim().toLowerCase();
    const legacy = {
      material_gaps: "auto_pilot",
      llm_decides: "balanced",
      ask_any_missing: "pedantic",
    }[value];
    const normalized = legacy || value;
    return new Set(["auto_pilot", "balanced", "pedantic"]).has(normalized)
      ? normalized
      : "balanced";
  }

  function clarificationScore(mode) {
    return {
      pedantic: 0,
      balanced: 50,
      auto_pilot: 100,
    }[normalizeClarificationMode(mode)] ?? 50;
  }

  function renderClarificationControl() {
    const definition = state.settingDefinitions.get("agent_clarification_mode");
    const mode = normalizeClarificationMode(
      definition ? currentValue(definition) : state.settings.agent_clarification_mode,
    );
    const score = clarificationScore(mode);
    const hue = Math.round(190 - score * 1.6);
    const fillColor = `hsl(${hue} 72% 46%)`;
    if (elements.clarificationControl) {
      elements.clarificationControl.dataset.mode = mode;
      elements.clarificationControl.title = `Clarification: ${mode}`;
      for (const button of elements.clarificationControl.querySelectorAll("button[data-mode]")) {
        const buttonMode = normalizeClarificationMode(button.dataset.mode);
        button.setAttribute("aria-pressed", buttonMode === mode ? "true" : "false");
      }
    }
    if (elements.clarificationHeat) {
      elements.clarificationHeat.dataset.mode = mode;
      elements.clarificationHeat.dataset.score = String(score);
      elements.clarificationHeat.style.setProperty("--agent-optimization-score", String(score));
      elements.clarificationHeat.style.setProperty("--agent-optimization-color", fillColor);
      const meter = elements.clarificationHeat.querySelector("[data-agent-clarification-meter]");
      if (meter) meter.style.width = `${score}%`;
      const value = elements.clarificationHeat.querySelector("[data-agent-clarification-value]");
      if (value) value.textContent = `${score}% Auto-pilot`;
    }
  }

  function optimizationScore() {
    const profile = currentOptimizationProfile();
    if (profile === "accuracy") return 0;
    if (profile === "speed") return 100;
    const accuracy = profileNormalizedValues("accuracy");
    const speed = profileNormalizedValues("speed");
    if (!accuracy || !speed || !MISSION_CONTROL_OPTIMIZATION_CONTROLLED_KEYS.length) {
      return 50;
    }
    let speedWeight = 0;
    let totalWeight = 0;
    for (const key of MISSION_CONTROL_OPTIMIZATION_CONTROLLED_KEYS) {
      if (!hasOwn(accuracy, key) || !hasOwn(speed, key)) continue;
      totalWeight += 1;
      const value = state.settings[key];
      const matchesAccuracy = valuesEqual(value, accuracy[key]);
      const matchesSpeed = valuesEqual(value, speed[key]);
      if (matchesSpeed && !matchesAccuracy) {
        speedWeight += 1;
      } else if (!matchesSpeed && !matchesAccuracy) {
        speedWeight += 0.5;
      }
    }
    return totalWeight > 0 ? Math.round((speedWeight / totalWeight) * 100) : 50;
  }

  function renderOptimizationControl() {
    const profile = currentOptimizationProfile();
    const score = optimizationScore();
    const hue = Math.round(190 - score * 1.6);
    const fillColor = `hsl(${hue} 72% 46%)`;
    if (elements.optimizationControl) {
      elements.optimizationControl.dataset.profile = profile;
      elements.optimizationControl.title = profile === "custom"
        ? "Custom reflects your current settings."
        : `Optimize agent for ${profile === "speed" ? "Speed" : "Accuracy"}`;
      for (const button of elements.optimizationControl.querySelectorAll("button[data-profile]")) {
        const buttonProfile = String(button.dataset.profile || "");
        button.setAttribute("aria-pressed", buttonProfile === profile ? "true" : "false");
        if (buttonProfile === "custom") {
          button.title = "Custom reflects your current settings.";
        }
      }
    }
    if (elements.optimizationHeat) {
      elements.optimizationHeat.dataset.profile = profile;
      elements.optimizationHeat.dataset.score = String(score);
      elements.optimizationHeat.style.setProperty("--agent-optimization-score", String(score));
      elements.optimizationHeat.style.setProperty("--agent-optimization-color", fillColor);
      const meter = elements.optimizationHeat.querySelector("[data-agent-optimization-meter]");
      if (meter) meter.style.width = `${score}%`;
      const value = elements.optimizationHeat.querySelector("[data-agent-optimization-value]");
      if (value) value.textContent = `${score}% Speed`;
    }
  }

  function applyOptimizationProfile(profileId) {
    const profile = MISSION_CONTROL_OPTIMIZATION_PROFILES?.[profileId];
    if (profileId === "custom") {
      renderOptimizationControl();
      return;
    }
    if (!isPlainObject(profile?.values)) return;
    const changedKeys = [];
    for (const [key, value] of Object.entries(profile.values)) {
      const definition = state.settingDefinitions.get(key);
      if (!definition || definition.readonly || definition.target === "meta") continue;
      if (key === "auto_approve_commands") continue;
      state.settings[key] = normalizeValue(value, definition);
      changedKeys.push(key);
    }
    if (state.settings.ui_theme) applyTheme(state.settings.ui_theme);
    for (const key of changedKeys) renderControlValue(key);
    renderOptimizationControl();
    renderClarificationControl();
    for (const key of changedKeys) state.dirtyKeys.add(key);
    scheduleAutosave(changedKeys[0] || `optimization_${profileId}`);
  }

  function renderSections() {
    if (!elements.sections || !elements.nav) return;
    elements.sections.replaceChildren();
    elements.nav.replaceChildren();
    for (const section of state.registry?.sections || []) {
      const navLink = document.createElement("a");
      navLink.href = `#settings-section-${section.id}`;
      navLink.textContent = section.title;
      elements.nav.append(navLink);

      const details = document.createElement("details");
      details.className = "mission-settings-section";
      details.id = `settings-section-${section.id}`;
      details.open = true;
      details.dataset.sectionSearch = [
        section.title,
        section.description,
        section.impact,
      ].join(" ").toLowerCase();

      const summary = document.createElement("summary");
      summary.className = "mission-settings-section-summary";
      const title = document.createElement("h2");
      title.textContent = section.title;
      summary.append(title);

      const body = document.createElement("div");
      body.className = "mission-section-body";
      for (const text of [section.description, section.impact]) {
        if (!text) continue;
        const paragraph = document.createElement("p");
        paragraph.className = "mission-section-copy";
        paragraph.textContent = text;
        body.append(paragraph);
      }
      const settingGrid = document.createElement("div");
      settingGrid.className = "mission-section-settings";
      for (const setting of section.settings || []) {
        settingGrid.append(createSettingRow(setting));
      }
      body.append(settingGrid);
      details.append(summary, body);
      elements.sections.append(details);
    }
  }

  function updateScopeSummary() {
    if (!elements.scope) return;
    const editableCount = [...state.settingDefinitions.values()].filter(
      (definition) => !definition.readonly && definition.target !== "meta",
    ).length;
    const runtimeCount = [...state.settingDefinitions.values()].filter(definitionTargetsRuntime).length;
    elements.scope.textContent = `${editableCount} editable settings, ${runtimeCount} live runtime controls`;
  }

  function applySearchFilter() {
    const query = compactText(elements.search?.value || "").toLowerCase();
    for (const section of document.querySelectorAll(".mission-settings-section")) {
      let visibleRows = 0;
      for (const row of section.querySelectorAll(".mission-setting-row")) {
        const text = `${row.dataset.settingSearch || ""} ${section.dataset.sectionSearch || ""}`;
        const visible = !query || text.includes(query);
        row.hidden = !visible;
        if (visible) visibleRows += 1;
      }
      section.hidden = Boolean(query) && visibleRows === 0;
      if (query && visibleRows > 0) section.open = true;
    }
  }

  function applyPreset(preset) {
    if (!preset || !isPlainObject(preset.values)) return;
    if (preset.requires_confirmation) {
      const confirmed = window.confirm(
        "This preset enables auto-approval. Apply it only in a controlled local workspace.",
      );
      if (!confirmed) return;
    }
    const changedKeys = [];
    for (const [key, value] of Object.entries(preset.values)) {
      const definition = state.settingDefinitions.get(key);
      if (!definition || definition.readonly || definition.target === "meta") continue;
      state.settings[key] = normalizeValue(value, definition);
      changedKeys.push(key);
    }
    if (state.settings.ui_theme) applyTheme(state.settings.ui_theme);
    for (const key of changedKeys) renderControlValue(key);
    renderOptimizationControl();
    renderClarificationControl();
    for (const key of changedKeys) state.dirtyKeys.add(key);
    scheduleAutosave(changedKeys[0] || "preset");
  }

  async function loadRegistryAndSettings({ announce = false } = {}) {
    if (announce) setStatus("Refreshing settings...", "saving");
    const registry = await apiJson("/settings/registry");
    state.registry = registry;
    state.defaults = isPlainObject(registry.defaults) ? registry.defaults : {};
    collectSettingDefinitions(registry);

    let preferences = { settings: {} };
    let runtimeControls = {};
    state.backendUnavailable = false;
    state.runtimeUnavailable = false;
    try {
      preferences = await Core.fetchSettingsBackendPreferences();
    } catch (_error) {
      state.backendUnavailable = true;
    }
    try {
      runtimeControls = await apiJson("/runtime-controls");
    } catch (_error) {
      state.runtimeUnavailable = true;
    }
    state.backendEnabled = !state.backendUnavailable;
    state.runtimeControls = isPlainObject(runtimeControls) ? runtimeControls : {};
    const backendSettings = isPlainObject(preferences?.settings) ? preferences.settings : {};
    const hasBackendSettings = Object.keys(backendSettings).length > 0;
    const sourceSettings = hasBackendSettings ? backendSettings : {};
    const merged = normalizeSettings({
      ...state.defaults,
      ...sourceSettings,
    });
    for (const [key, definition] of state.settingDefinitions.entries()) {
      if (!definitionTargetsRuntime(definition)) continue;
      if (!hasOwn(sourceSettings, key) && hasOwn(state.runtimeControls, key)) {
        merged[key] = normalizeValue(state.runtimeControls[key], definition);
      }
    }
    state.settings = merged;
    state.lastSignature = settingSignature(state.settings);
    renderPresets();
    renderSections();
    renderOptimizationControl();
    renderClarificationControl();
    updateScopeSummary();
    if (elements.version) {
      elements.version.textContent = `Registry ${registry.version || "current"}`;
    }
    if (!hasBackendSettings) {
      for (const key of Object.keys(state.settings)) state.dirtyKeys.add(key);
      scheduleAutosave("initial_backend_seed");
    } else {
      let message = "Backend settings loaded.";
      let status = "saved";
      if (state.backendUnavailable && state.runtimeUnavailable) {
        message = "Shared settings and live runtime controls are unavailable.";
        status = "error";
      } else if (state.backendUnavailable) {
        message = "Shared backend settings are unavailable.";
        status = "error";
      } else if (state.runtimeUnavailable) {
        message = "Live runtime controls are unavailable; shared preferences loaded.";
        status = "error";
      }
      setStatus(message, status);
    }
    if (state.settings.ui_theme) applyTheme(state.settings.ui_theme);
  }

  async function syncFromBackend() {
    if (state.saving || state.dirtyKeys.size) return;
    try {
      const preferences = await Core.fetchSettingsBackendPreferences();
      state.backendUnavailable = false;
      const remote = isPlainObject(preferences?.settings) ? preferences.settings : {};
      if (!Object.keys(remote).length) return;
      const next = normalizeSettings({ ...state.defaults, ...state.settings, ...remote });
      const signature = settingSignature(next);
      if (signature && signature !== state.lastSignature) {
        state.settings = next;
        state.lastSignature = signature;
        for (const key of state.settingDefinitions.keys()) renderControlValue(key);
        renderOptimizationControl();
        renderClarificationControl();
        setStatus("Synced changes from shared backend settings.", "saved");
      }
    } catch (_error) {
      state.backendUnavailable = true;
      setStatus("Shared settings are temporarily unavailable.", "error");
    }
  }

  function startSyncTimer() {
    if (state.syncTimer) return;
    state.syncTimer = window.setInterval(() => {
      void syncFromBackend();
    }, 5000);
  }

  async function boot() {
    try {
      await loadRegistryAndSettings();
      startSyncTimer();
    } catch (error) {
      setStatus(`Could not load settings: ${compactText(error?.message, "unknown error")}`, "error");
      if (elements.sections) {
        const empty = document.createElement("div");
        empty.className = "mission-settings-empty";
        empty.textContent = "Settings registry could not be loaded.";
        elements.sections.replaceChildren(empty);
      }
    } finally {
      finishBoot();
    }
  }

  elements.search?.addEventListener("input", applySearchFilter);
  elements.refresh?.addEventListener("click", () => {
    void loadRegistryAndSettings({ announce: true });
  });
  elements.optimizationControl?.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button[data-profile]");
    if (!button) return;
    applyOptimizationProfile(String(button.dataset.profile || ""));
  });
  elements.clarificationControl?.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button[data-mode]");
    if (!button) return;
    setSettingValue("agent_clarification_mode", normalizeClarificationMode(button.dataset.mode));
  });

  void boot();
})();
