(() => {
  "use strict";

  const Core = window.OpenFabricAgentUi;
  if (!Core) {
    throw new Error("OpenFabric Agent UI shared core is missing.");
  }

  const AUDIO_DEFAULT_SILENCE_TIMEOUT_SECONDS = 2;
  const AUDIO_MIN_SILENCE_TIMEOUT_SECONDS = 1;
  const AUDIO_MAX_SILENCE_TIMEOUT_SECONDS = 10;
  const AUDIO_SILENCE_CHECK_INTERVAL_MS = 250;
  const AUDIO_SILENCE_CALIBRATION_MS = 750;
  const AUDIO_SILENCE_AMBIENT_SAMPLE_COUNT = 24;
  const AUDIO_SILENCE_AMBIENT_PERCENTILE = 0.3;
  const AUDIO_SILENCE_SPEECH_MARGIN = 0.008;
  const AUDIO_SILENCE_RECOVERY_MARGIN = 0.004;
  const AUDIO_SILENCE_SPEECH_MULTIPLIER = 1.35;
  const AUDIO_SILENCE_RECOVERY_MULTIPLIER = 1.18;
  const AUDIO_SILENCE_AMBIENT_SAMPLE_MARGIN = 0.018;
  const AUDIO_SILENCE_RECOVERY_HOLD_MS = 2000;
  const AUDIO_SILENCE_RMS_THRESHOLD = 0.016;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const elements = {
    root: $("#mobile-root"),
    backendStatus: $("#mobile-backend-status"),
    agentName: $("#mobile-agent-name"),
    themeSelect: $("#mobile-theme-select"),
    gatewaySelect: $("#mobile-gateway-select"),
    notificationButton: $("#mobile-notifications-button"),
    notificationCount: $("#mobile-notification-count"),
    notificationSheet: $("#notification-sheet"),
    notificationCloseButton: $("#mobile-notifications-close"),
    notificationList: $("#mobile-notification-list"),
    notificationRefreshButton: $("#mobile-notifications-refresh"),
    notificationMarkReadButton: $("#mobile-notifications-mark-read"),
    browserNotificationsEnabled: $("#mobile-browser-notifications-enabled"),
    browserNotificationsPermission: $("#mobile-browser-notifications-permission"),
    notificationSoundSelect: $("#mobile-notification-sound-select"),
    notificationSoundTest: $("#mobile-notification-sound-test"),
    tasksButton: $("#mobile-tasks-button"),
    taskCount: $("#mobile-task-count"),
    taskSheet: $("#mobile-task-sheet"),
    taskCloseButton: $("#mobile-tasks-close"),
    taskRefreshButton: $("#mobile-tasks-refresh"),
    taskList: $("#mobile-task-list"),
    taskTitle: $("#mobile-task-title"),
    taskPrompt: $("#mobile-task-prompt"),
    taskCreateButton: $("#mobile-task-create"),
    taskCreateStartButton: $("#mobile-task-create-start"),
    monitorsButton: $("#mobile-monitors-button"),
    monitorCount: $("#mobile-monitor-count"),
    monitorSheet: $("#mobile-monitor-sheet"),
    monitorCloseButton: $("#mobile-monitors-close"),
    monitorRefreshButton: $("#mobile-monitors-refresh"),
    monitorList: $("#mobile-monitor-list"),
    monitorTitle: $("#mobile-monitor-title"),
    monitorCommand: $("#mobile-monitor-command"),
    monitorMode: $("#mobile-monitor-mode"),
    monitorInterval: $("#mobile-monitor-interval"),
    monitorDuration: $("#mobile-monitor-duration"),
    monitorCondition: $("#mobile-monitor-condition"),
    monitorAction: $("#mobile-monitor-action"),
    monitorCreateButton: $("#mobile-monitor-create"),
    monitorCreateStartButton: $("#mobile-monitor-create-start"),
    parametersButton: $("#mobile-parameters-button"),
    parameterSheet: $("#mobile-parameter-sheet"),
    parameterCloseButton: $("#mobile-parameters-close"),
    parameterRefreshButton: $("#mobile-parameters-refresh"),
    parameterList: $("#mobile-parameter-list"),
    parameterSearch: $("#mobile-parameter-search"),
    parameterKey: $("#mobile-parameter-key"),
    parameterDescription: $("#mobile-parameter-description"),
    parameterValue: $("#mobile-parameter-value"),
    parameterAliases: $("#mobile-parameter-aliases"),
    parameterTags: $("#mobile-parameter-tags"),
    parameterSensitive: $("#mobile-parameter-sensitive"),
    parameterNewButton: $("#mobile-parameter-new"),
    parameterSaveButton: $("#mobile-parameter-save"),
    parameterRevealButton: $("#mobile-parameter-reveal"),
    parameterDeleteButton: $("#mobile-parameter-delete"),
    chatLog: $("#mobile-chat-log"),
    chatForm: $("#mobile-chat-form"),
    promptInput: $("#mobile-prompt-input"),
    parameterShortcutMenu: $("#mobile-parameter-shortcut-menu"),
    sendButton: $("#mobile-send-button"),
    stopButton: $("#mobile-stop-button"),
    newChatButton: $("#mobile-new-chat-button"),
    micButton: $("#mobile-mic-button"),
    terminalButton: $("#mobile-terminal-button"),
    micLabel: $("#mobile-mic-label"),
    micLoadingDots: $("#mobile-mic-button .mobile-loading-dots"),
    runStatus: $("#mobile-run-status"),
    backdrop: $("#mobile-sheet-backdrop"),
    toastRegion: $("#mobile-toast-region"),
  };

  const state = {
    settings: Core.loadAgentSettings(),
    agentMode: "llm_operator",
    source: null,
    requestId: "",
    conversationId: "",
    running: false,
    renderedFinalRequests: new Set(),
    traceEventIds: new Set(),
    notifications: [],
    tasks: [],
    taskCounts: {},
    monitors: [],
    monitorCounts: {},
    parameters: [],
    promptParameters: [],
    promptParametersLoaded: false,
    promptParameterShortcutTrigger: null,
    promptParameterShortcutMatches: [],
    promptParameterShortcutActiveIndex: 0,
    promptParameterShortcutSelectionArmed: false,
    selectedParameterKey: "",
    shownNotificationIds: new Set(),
    notificationAudioUnlocked: false,
    notificationAudioContext: null,
    gateways: [],
    defaultGatewayId: "",
    selectedGatewayId: Core.compactText(Core.readStorage(Core.keys.selectedGateway, "")),
    settingsSyncTimer: null,
    audioTranscriberConfig: null,
    audioTranscriberError: "",
    audioPermissionState: "unknown",
    audioPermissionWarningNotified: false,
    audioRecordingState: "idle",
    audioMediaStream: null,
    audioMediaRecorder: null,
    audioChunks: [],
    audioRecordingTimer: null,
    audioRecordingStartedAt: 0,
    audioSubmitAfterTranscription: false,
    audioSilenceWarningActive: false,
    audioSilenceDetectorTimer: null,
    audioSilenceDetectorContext: null,
    audioSilenceDetectorSource: null,
    audioSilenceDetectorAnalyser: null,
    audioSilenceDetectorBuffer: null,
    audioSilenceDetectorLastSoundMs: 0,
  };

  let mobilePromptParameterShortcutLoadPromise = null;

  function clear(node) {
    if (node) {
      node.replaceChildren();
    }
  }

  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== "") {
      node.textContent = String(text);
    }
    return node;
  }

  function appendMeta(parent, text) {
    const value = Core.compactText(text);
    if (!value) {
      return null;
    }
    const meta = el("div", "mobile-list-meta", value);
    parent.append(meta);
    return meta;
  }

  function gatewayDisplayName(gateway) {
    const label = Core.compactText(gateway?.label || gateway?.node || gateway?.host, "Gateway");
    const node = Core.compactText(gateway?.node);
    return node && node !== label ? `${label} (${node})` : label;
  }

  function gatewayPlatformLabel(gateway) {
    const label = Core.compactText(gateway?.platform_label);
    if (label) {
      return label;
    }
    const platform = Core.compactText(gateway?.platform).toLowerCase();
    if (platform === "macos") return "macOS";
    if (platform === "linux") return "Linux";
    if (platform === "windows") return "Windows";
    return "Unknown";
  }

  function gatewayLabelFromMetadata(metadata = {}) {
    const mode = Core.compactText(metadata?.mode);
    const displayName = Core.compactText(
      metadata?.gateway_display_name ||
      metadata?.gateway_nickname ||
      metadata?.gateway_node,
    );
    const platformLabel = Core.compactText(metadata?.gateway_platform_label);
    if (displayName) {
      return `Gateway: ${[displayName, platformLabel].filter(Boolean).join(" · ")}`;
    }
    return mode === "default" ? "Gateway: default" : "";
  }

  function confirmationActionArguments(action) {
    return action?.arguments && typeof action.arguments === "object" ? action.arguments : {};
  }

  function confirmationActionGatewayText(action) {
    const args = confirmationActionArguments(action);
    const routing = args.gateway_routing && typeof args.gateway_routing === "object"
      ? args.gateway_routing
      : {};
    return gatewayLabelFromMetadata({ ...routing, ...args, ...(action || {}) });
  }

  function confirmationActionLabel(action, index) {
    return Core.compactText(
      action?.title ||
      action?.label ||
      action?.description ||
      confirmationActionArguments(action).summary ||
      action?.action_id ||
      action?.node_id,
      `Action ${index}`,
    );
  }

  function confirmationActionValue(action, key) {
    if (!action || typeof action !== "object") {
      return undefined;
    }
    const args = confirmationActionArguments(action);
    return action[key] !== undefined ? action[key] : args[key];
  }

  function confirmationActionText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    if (typeof value === "string") {
      return value.trim();
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch (_error) {
      return String(value);
    }
  }

  function confirmationActionTextIsUseful(text) {
    const normalized = String(text || "").trim();
    return Boolean(normalized) && !["null", "undefined"].includes(normalized.toLowerCase());
  }

  function appendMobileConfirmationCodeBlock(parent, label, value) {
    const text = confirmationActionText(value);
    if (!confirmationActionTextIsUseful(text)) {
      return false;
    }
    const block = el("div", "mobile-confirmation-action-code");
    block.append(el("div", "mobile-confirmation-action-code-label", label));
    const pre = document.createElement("pre");
    pre.textContent = text;
    block.append(pre);
    parent.append(block);
    return true;
  }

  function gatewayStatusText(gateway) {
    const status = Core.compactText(gateway?.status, "unknown");
    return gateway?.enabled === false ? `${status} / disabled` : status;
  }

  function gatewayRecordById(gatewayId) {
    const id = Core.compactText(gatewayId);
    if (!id) {
      return null;
    }
    return state.gateways.find((gateway) => gateway.gateway_id === id) || null;
  }

  function enabledGateways() {
    return state.gateways.filter((gateway) => gateway.enabled !== false);
  }

  function selectedGatewayRecord() {
    return gatewayRecordById(state.selectedGatewayId);
  }

  function gatewayContextFromRecord(gateway) {
    if (!gateway) {
      return {};
    }
    const context = { gateway_id: gateway.gateway_id };
    const node = Core.compactText(gateway.node);
    if (node) {
      context.gateway_node = node;
    }
    [
      ["gateway_platform", gateway.platform],
      ["gateway_platform_label", gatewayPlatformLabel(gateway)],
      ["gateway_platform_version", gateway.platform_version],
      ["gateway_architecture", gateway.architecture],
      ["gateway_shell", gateway.shell],
      ["gateway_command_profile", gateway.command_profile],
    ].forEach(([key, value]) => {
      const text = Core.compactText(value);
      if (text) {
        context[key] = text;
      }
    });
    if (Array.isArray(gateway.capability_tags) && gateway.capability_tags.length) {
      context.gateway_capability_tags = gateway.capability_tags
        .map((tag) => Core.compactText(tag))
        .filter(Boolean);
    }
    return context;
  }

  function selectedGatewayContext() {
    return gatewayContextFromRecord(selectedGatewayRecord());
  }

  function ensureSelectedGateway() {
    const enabled = enabledGateways();
    if (!enabled.length) {
      state.selectedGatewayId = "";
      Core.writeStorage(Core.keys.selectedGateway, "");
      return;
    }
    const requestedId = Core.compactText(state.selectedGatewayId || Core.readStorage(Core.keys.selectedGateway, ""));
    if (requestedId && enabled.some((gateway) => gateway.gateway_id === requestedId)) {
      state.selectedGatewayId = requestedId;
      Core.writeStorage(Core.keys.selectedGateway, requestedId);
      return;
    }
    const fallback = enabled.find((gateway) => gateway.gateway_id === state.defaultGatewayId) || enabled[0];
    state.selectedGatewayId = Core.compactText(fallback?.gateway_id);
    Core.writeStorage(Core.keys.selectedGateway, state.selectedGatewayId);
  }

  function renderGatewaySelector() {
    const select = elements.gatewaySelect;
    if (!select) {
      return;
    }
    const enabled = enabledGateways();
    select.replaceChildren();
    if (!enabled.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Default";
      select.append(option);
      select.value = "";
      select.disabled = true;
      select.title = "Gateway registry unavailable";
      return;
    }
    ensureSelectedGateway();
    enabled.forEach((gateway) => {
      const option = document.createElement("option");
      option.value = gateway.gateway_id;
      option.textContent = gatewayDisplayName(gateway);
      option.title = `${gatewayPlatformLabel(gateway)} - ${gatewayStatusText(gateway)}`;
      select.append(option);
    });
    select.disabled = false;
    select.value = state.selectedGatewayId;
    const selected = selectedGatewayRecord();
    select.title = selected
      ? `Terminal gateway: ${gatewayDisplayName(selected)} - ${gatewayPlatformLabel(selected)}`
      : "Terminal gateway";
  }

  function setSelectedGatewayFromPicker(value) {
    const id = Core.compactText(value);
    const gateway = gatewayRecordById(id);
    if (!gateway) {
      renderGatewaySelector();
      return;
    }
    state.selectedGatewayId = id;
    Core.writeStorage(Core.keys.selectedGateway, id);
    renderGatewaySelector();
    showToast(`Terminal gateway: ${gatewayDisplayName(gateway)}`);
  }

  function openMobileTerminal() {
    const gatewayId = Core.compactText(state.selectedGatewayId || elements.gatewaySelect?.value);
    if (gatewayId) {
      Core.writeStorage(Core.keys.selectedGateway, gatewayId);
    }
    try {
      if (gatewayId) {
        window.localStorage?.setItem(Core.keys.selectedGateway, gatewayId);
      }
      window.localStorage?.setItem("openfabric.agentUi.terminalVisible", "true");
      window.localStorage?.setItem("openfabric.agentUi.terminalCollapsed", "false");
    } catch (_error) {
      // Navigation still reaches the desktop terminal surface if storage is unavailable.
    }
    window.location.assign("/agent-ui?terminal=1");
  }

  async function loadGateways() {
    try {
      const payload = await Core.apiJson("/gateways", { cache: "no-store" });
      state.gateways = Array.isArray(payload.gateways) ? payload.gateways : [];
      state.defaultGatewayId = Core.compactText(payload.default_gateway_id);
      ensureSelectedGateway();
      renderGatewaySelector();
    } catch (error) {
      state.gateways = [];
      state.defaultGatewayId = "";
      state.selectedGatewayId = "";
      renderGatewaySelector();
      showToast(`Gateways unavailable: ${shortStatusError(error)}`);
    }
  }

  function shortStatusError(error) {
    return Core.compactText(error?.message || error, "Something went wrong.");
  }

  function setBackendStatus(status, detail = "") {
    const normalized = ["connected", "disconnected", "checking"].includes(status) ? status : "checking";
    elements.backendStatus.dataset.state = normalized;
    const label = {
      connected: "Backend connected",
      disconnected: "Backend disconnected",
      checking: "Checking backend connection",
    }[normalized];
    elements.backendStatus.setAttribute("aria-label", detail ? `${label}: ${detail}` : label);
  }

  function setRunStatus(status, tone = "") {
    const text = Core.formatStatus(status || "Idle");
    elements.runStatus.textContent = text;
    elements.runStatus.dataset.tone = tone || Core.statusTone(status);
  }

  function showToast(message) {
    const toast = el("div", "mobile-toast", Core.compactText(message, "Done"));
    elements.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 3600);
  }

  function markActionFieldInvalid(field) {
    if (!field || typeof field.focus !== "function") {
      return;
    }
    field.setAttribute("aria-invalid", "true");
    const clearInvalid = () => field.removeAttribute("aria-invalid");
    field.addEventListener("input", clearInvalid, { once: true });
    field.addEventListener("change", clearInvalid, { once: true });
    field.focus({ preventScroll: false });
  }

  function showActionError(title, message, fix = "", field = null) {
    markActionFieldInvalid(field);
    const cleanTitle = Core.compactText(title, "Action failed");
    const cleanMessage = Core.compactText(message, "The action could not be completed.");
    const cleanFix = Core.compactText(fix);
    showToast(`${cleanTitle}: ${cleanFix ? `${cleanMessage} Fix: ${cleanFix}` : cleanMessage}`);
  }

  function notificationKey(notification) {
    return Core.compactText(notification?.notification_id || notification?.id);
  }

  function notificationRequestId(notification) {
    const metadata = Core.isPlainObject(notification?.metadata) ? notification.metadata : {};
    return Core.compactText(notification?.request_id || metadata.request_id);
  }

  function notificationTaskId(notification) {
    const metadata = Core.isPlainObject(notification?.metadata) ? notification.metadata : {};
    return Core.compactText(
      notification?.task_id
        || notification?.durable_task_id
        || metadata.task_id
        || metadata.durable_task_id,
    );
  }

  function shouldSuppressLiveTaskNotificationDelivery(notification) {
    const sourceType = Core.compactText(notification?.source_type);
    if (sourceType !== "durable_task" && sourceType !== "durable_task_local") {
      return false;
    }
    const requestId = notificationRequestId(notification);
    if (requestId && requestId === Core.compactText(state.requestId)) {
      return true;
    }
    const taskId = notificationTaskId(notification);
    if (!taskId) {
      return false;
    }
    return state.tasks.some((task) => (
      Core.compactText(task?.task_id) === taskId
      && (
        Core.compactText(task?.current_request_id) === Core.compactText(state.requestId)
        || Core.compactText(task?.latest_request_id) === Core.compactText(state.requestId)
      )
    ));
  }

  function markSuppressedLiveTaskNotificationRead(notification) {
    const id = notificationKey(notification);
    if (!id || notification.status !== "unread") {
      return false;
    }
    Core.apiJson(`/notifications/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: { status: "read" },
    }).catch(() => {});
    notification.status = "read";
    return true;
  }

  function browserNotificationsSupported() {
    return typeof window.Notification === "function";
  }

  function browserNotificationPermission() {
    return browserNotificationsSupported() ? String(window.Notification.permission || "default") : "unsupported";
  }

  function renderNotificationDeliveryControls() {
    if (elements.browserNotificationsEnabled) {
      elements.browserNotificationsEnabled.checked = state.settings.browser_notifications_enabled !== false;
    }
    if (elements.browserNotificationsPermission) {
      const permission = browserNotificationPermission();
      elements.browserNotificationsPermission.disabled =
        !browserNotificationsSupported()
        || state.settings.browser_notifications_enabled === false
        || permission !== "default";
      elements.browserNotificationsPermission.textContent =
        permission === "granted" ? "Allowed" : permission === "denied" ? "Blocked" : "Allow";
      elements.browserNotificationsPermission.title =
        permission === "granted"
          ? "Browser notifications are allowed"
          : permission === "denied"
            ? "Browser notifications are blocked in browser site settings"
            : "Allow browser notifications";
    }
    if (elements.notificationSoundSelect) {
      elements.notificationSoundSelect.value = state.settings.notification_sound_enabled === false
        ? "off"
        : Core.normalizeNotificationSoundVariant(state.settings.notification_sound_variant);
    }
  }

  async function requestBrowserNotificationPermission() {
    if (!browserNotificationsSupported()) {
      showToast("Browser notifications are not supported here.");
      renderNotificationDeliveryControls();
      return "unsupported";
    }
    let permission = browserNotificationPermission();
    if (permission === "default") {
      try {
        permission = await window.Notification.requestPermission();
      } catch (_error) {
        permission = browserNotificationPermission();
      }
    }
    renderNotificationDeliveryControls();
    if (permission === "denied") {
      showToast("Browser notifications are blocked in site settings.");
    }
    return permission;
  }

  function showBrowserNotification(notification) {
    if (
      state.settings.browser_notifications_enabled === false
      || browserNotificationPermission() !== "granted"
      || !notificationKey(notification)
    ) {
      return null;
    }
    try {
      const notice = new window.Notification(Core.compactText(notification?.title, "OpenFabric notification"), {
        body: Core.compactText(notification?.message),
        tag: notificationKey(notification),
        renotify: true,
        silent: true,
      });
      notice.onclick = () => {
        window.focus();
        const requestId = notificationRequestId(notification);
        if (requestId) {
          void openNotificationRequest(requestId);
        }
        notice.close();
      };
      return notice;
    } catch (_error) {
      return null;
    }
  }

  function notificationAudioApi() {
    return window.AudioContext || window.webkitAudioContext;
  }

  function unlockNotificationAudio() {
    state.notificationAudioUnlocked = true;
    const AudioContextCtor = notificationAudioApi();
    if (!AudioContextCtor || state.notificationAudioContext) {
      return;
    }
    try {
      state.notificationAudioContext = new AudioContextCtor();
      if (state.notificationAudioContext.state === "suspended") {
        state.notificationAudioContext.resume().catch(() => {});
      }
    } catch (_error) {
      state.notificationAudioContext = null;
    }
  }

  function notificationSoundPattern(notification) {
    const variant = Core.normalizeNotificationSoundVariant(state.settings.notification_sound_variant);
    const level = Core.compactText(notification?.level, "info").toLowerCase();
    if (variant === "alert" || level === "error") {
      return [
        { frequency: 392, offset: 0, duration: 0.12 },
        { frequency: 294, offset: 0.14, duration: 0.18 },
      ];
    }
    if (variant === "ping" || level === "warning") {
      return [{ frequency: 520, offset: 0, duration: 0.16 }];
    }
    if (variant === "soft") {
      return [
        { frequency: 660, offset: 0, duration: 0.09 },
        { frequency: 880, offset: 0.1, duration: 0.11 },
      ];
    }
    return [
      { frequency: 660, offset: 0, duration: 0.09 },
      { frequency: 990, offset: 0.1, duration: 0.13 },
    ];
  }

  function playNotificationSound(notification) {
    if (state.settings.notification_sound_enabled === false || !state.notificationAudioUnlocked) {
      return;
    }
    const AudioContextCtor = notificationAudioApi();
    if (!AudioContextCtor) {
      return;
    }
    try {
      const context = state.notificationAudioContext || new AudioContextCtor();
      state.notificationAudioContext = context;
      const play = () => {
        const volume = Core.normalizeNotificationSoundVolume(state.settings.notification_sound_volume);
        for (const note of notificationSoundPattern(notification)) {
          const oscillator = context.createOscillator();
          const gain = context.createGain();
          const start = context.currentTime + Number(note.offset || 0);
          const stop = start + Number(note.duration || 0.14);
          oscillator.type = "sine";
          oscillator.frequency.value = Number(note.frequency || 660);
          gain.gain.setValueAtTime(0.0001, start);
          gain.gain.exponentialRampToValueAtTime(Math.max(0.001, volume * 0.09), start + 0.015);
          gain.gain.exponentialRampToValueAtTime(0.0001, stop);
          oscillator.connect(gain);
          gain.connect(context.destination);
          oscillator.start(start);
          oscillator.stop(stop + 0.01);
        }
      };
      if (context.state === "suspended") {
        context.resume().then(play).catch(() => {});
      } else {
        play();
      }
    } catch (_error) {}
  }

  function deliverNotification(notification) {
    const title = Core.compactText(notification?.title, "Notification");
    const message = Core.compactText(notification?.message);
    showToast(message ? `${title}: ${message}` : title);
    showBrowserNotification(notification);
    playNotificationSound(notification);
  }

  function deliverUnreadNotifications(notifications) {
    let suppressedUnreadCount = 0;
    for (const notification of notifications) {
      const id = notificationKey(notification);
      if (!id || state.shownNotificationIds.has(id) || notification.status !== "unread") {
        continue;
      }
      if (shouldSuppressLiveTaskNotificationDelivery(notification)) {
        state.shownNotificationIds.add(id);
        if (markSuppressedLiveTaskNotificationRead(notification)) {
          suppressedUnreadCount += 1;
        }
        continue;
      }
      state.shownNotificationIds.add(id);
      deliverNotification(notification);
    }
    return suppressedUnreadCount;
  }

  function reportError(error, fallback = "Something went wrong.") {
    const detail = Core.compactText(error?.message || error, fallback);
    const message = fallback && detail !== fallback ? `${fallback} ${detail}` : detail;
    showToast(message);
    if (state.running) {
      setRunStatus("error", "bad");
    }
  }

  function updateAgentName() {
    elements.agentName.textContent = Core.compactText(state.settings.agent_display_name, "Agent").slice(0, 40);
  }

  function mobileThemeValues() {
    return new Set(
      Array.from(elements.themeSelect?.querySelectorAll("option[value]") || [])
        .map((option) => option.value)
        .filter(Boolean),
    );
  }

  function normalizeMobileTheme(value) {
    const requested = Core.compactText(value, state.settings?.ui_theme || "github");
    const allowed = mobileThemeValues();
    return allowed.has(requested) ? requested : "github";
  }

  function applyMobileTheme(value, persist = true) {
    const theme = normalizeMobileTheme(value);
    document.documentElement.dataset.theme = theme;
    if (elements.themeSelect) {
      elements.themeSelect.value = theme;
    }
    if (persist) {
      state.settings = Core.saveAgentSettings({ ...state.settings, ui_theme: theme });
      void persistMobileSettingsBackend();
    }
  }

  function bindThemePicker() {
    applyMobileTheme(state.settings?.ui_theme || "github", false);
    elements.themeSelect?.addEventListener("change", () => {
      applyMobileTheme(elements.themeSelect.value);
    });
  }

  function applyMobileChatPopAnimation() {
    if (!elements.chatLog) {
      return;
    }
    elements.chatLog.dataset.chatPopAnimation = Core.normalizeChatPopAnimationMode(
      state.settings?.ui_chat_pop_animation,
    );
  }

  function currentMobileChatPopAnimationMode() {
    return Core.normalizeChatPopAnimationMode(state.settings?.ui_chat_pop_animation);
  }

  const mobileTextOdometerAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const mobileTextOdometerDigits = "0123456789";
  const mobileTextOdometerReelLength = 6;

  function mobileTextOdometerAnimatesChar(char) {
    return /^[A-Za-z0-9]$/.test(String(char || ""));
  }

  function mobileTextOdometerRandomChar(finalChar) {
    const char = String(finalChar || "");
    const source = /^[0-9]$/.test(char) ? mobileTextOdometerDigits : mobileTextOdometerAlphabet;
    const random = source[Math.floor(Math.random() * source.length)] || source[0] || "";
    return /^[a-z]$/.test(char) ? random.toLowerCase() : random;
  }

  function createMobileTextOdometerStaticSlot(char) {
    const slot = document.createElement("span");
    slot.className = "mobile-text-odometer-static";
    slot.setAttribute("aria-hidden", "true");
    if (/\s/.test(String(char || ""))) {
      slot.classList.add("mobile-text-odometer-space");
    }
    slot.textContent = String(char || "");
    return slot;
  }

  function createMobileTextOdometerSlot(char, index) {
    const finalChar = String(char || "");
    if (!mobileTextOdometerAnimatesChar(finalChar)) {
      return createMobileTextOdometerStaticSlot(finalChar);
    }
    const slot = document.createElement("span");
    slot.className = "mobile-text-odometer-char";
    slot.setAttribute("aria-hidden", "true");
    slot.style.setProperty("--mobile-text-odometer-delay", String(Math.min(index, 42)));
    const reel = document.createElement("span");
    reel.className = "mobile-text-odometer-reel";
    reel.style.setProperty("--mobile-text-odometer-steps", String(mobileTextOdometerReelLength - 1));
    reel.style.setProperty(
      "--mobile-text-odometer-offset",
      `-${((mobileTextOdometerReelLength - 1) * 1.12).toFixed(2)}em`,
    );
    for (let reelIndex = 0; reelIndex < mobileTextOdometerReelLength; reelIndex += 1) {
      const item = document.createElement("span");
      item.textContent = reelIndex === mobileTextOdometerReelLength - 1
        ? finalChar
        : mobileTextOdometerRandomChar(finalChar);
      reel.append(item);
    }
    slot.append(reel);
    return slot;
  }

  function applyMobileChatTextOdometer(bodyNode, text) {
    const value = String(text || "");
    if (!bodyNode || currentMobileChatPopAnimationMode() !== "odometer" || !value.trim()) {
      return;
    }
    const fragment = document.createDocumentFragment();
    let animatedIndex = 0;
    [...value].forEach((char) => {
      const canAnimate = mobileTextOdometerAnimatesChar(char);
      fragment.append(canAnimate
        ? createMobileTextOdometerSlot(char, animatedIndex)
        : createMobileTextOdometerStaticSlot(char));
      if (canAnimate) {
        animatedIndex += 1;
      }
    });
    bodyNode.classList.add("mobile-text-odometer");
    bodyNode.dataset.copyText = value;
    bodyNode.setAttribute("aria-label", value);
    bodyNode.replaceChildren(fragment);
  }

  function normalizeMobileAgentMode(value) {
    const candidate = Core.compactText(value, "llm_operator");
    return ["llm_operator", "standard", "advisory"].includes(candidate) ? candidate : "llm_operator";
  }

  function applySyncedSettings(settings) {
    state.settings = Core.saveAgentSettings(settings);
    state.agentMode = normalizeMobileAgentMode(state.settings.ui_agent_mode || state.agentMode);
    updateAgentName();
    applyMobileTheme(state.settings.ui_theme || "github", false);
    applyMobileChatPopAnimation();
    renderNotificationDeliveryControls();
    updateAudioVoiceControls();
  }

  async function loadMobileSharedSettings() {
    try {
      const payload = await Core.loadSharedAgentSettings({ fallbackSettings: state.settings });
      applySyncedSettings(payload.settings);
      return payload.settings;
    } catch (error) {
      showToast(`Shared settings unavailable: ${shortStatusError(error)}`);
      return state.settings;
    }
  }

  async function persistMobileSettingsBackend() {
    try {
      applySyncedSettings(await Core.saveSharedAgentSettings(state.settings));
    } catch (error) {
      showToast(`Shared settings could not be saved: ${shortStatusError(error)}`);
    }
  }

  function resizePromptInput() {
    const input = elements.promptInput;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
  }

  function setRunning(running) {
    state.running = Boolean(running);
    const audioBusy = ["stopping", "transcribing"].includes(state.audioRecordingState);
    elements.sendButton.disabled = state.running || audioBusy;
    elements.stopButton.disabled = !state.running;
    updateAudioVoiceControls();
  }

  function traceErrorMessage(trace) {
    const detail = trace?.error_detail && typeof trace.error_detail === "object"
      ? trace.error_detail
      : null;
    const markdown = String(detail?.message_markdown || "").trim();
    return markdown || String(trace?.error || "").trim();
  }

  function addMessage(role, label, body, options = {}) {
    const article = el("article", `mobile-message mobile-message-${role || "assistant"}`);
    if (options.requestId) {
      article.dataset.requestId = options.requestId;
    }
    article.append(el("div", "mobile-message-label", label || (role === "user" ? "You" : "OpenFabric")));
    const bodyNode = el("div", "mobile-message-body");
    const bodyText = Core.compactText(body);
    bodyNode.textContent = bodyText;
    applyMobileChatTextOdometer(bodyNode, bodyText);
    article.append(bodyNode);
    if (options.actions) {
      article.append(options.actions);
    }
    elements.chatLog.append(article);
    elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
    return article;
  }

  function addSystemMessage(label, body) {
    return addMessage("system", label, body);
  }

  function requestContext() {
    return {
      ...Core.settingsContext(state.settings),
      ...selectedGatewayContext(),
      target_ui: "agent_ui_mobile",
    };
  }

  async function checkBackendConnection() {
    try {
      await Core.apiJson("/health", { cache: "no-store" });
      setBackendStatus("connected");
      return true;
    } catch (error) {
      setBackendStatus("disconnected", error?.message || "");
      return false;
    }
  }

  function closeTraceStream() {
    if (state.source) {
      state.source.close();
      state.source = null;
    }
  }

  function resetRequestState() {
    state.traceEventIds.clear();
    setRunStatus("running", "live");
  }

  function handleTraceEvent(event) {
    const id = Number(event.id || 0);
    if (id && state.traceEventIds.has(id)) {
      return;
    }
    if (id) {
      state.traceEventIds.add(id);
    }
    const type = Core.compactText(event.event_type).toLowerCase();
    if (type.includes("failed")) {
      setRunStatus("failed", "bad");
    } else if (type.includes("cancelled")) {
      setRunStatus("stopped", "warn");
    } else if (type === "request.completed") {
      const needsConfirmation = event.detail?.confirmation_required === true;
      const needsClarification = event.detail?.clarification_required === true;
      setRunStatus(needsConfirmation ? "approval needed" : needsClarification ? "input needed" : "completed");
      setRunning(false);
      closeTraceStream();
      void loadFinalTrace(state.requestId);
    } else if (type.startsWith("execution.command.") && state.running) {
      setRunStatus(gatewayLabelFromMetadata(event.detail) || "running", "live");
    } else if (state.running) {
      setRunStatus("running", "live");
    }
  }

  function startMobileTraceStream(requestId, streamUrl, options = {}) {
    closeTraceStream();
    state.requestId = Core.compactText(requestId);
    if (!state.requestId) {
      return;
    }
    if (options.reset !== false) {
      resetRequestState();
    }
    setRunning(true);
    const url = streamUrl || Core.streamUrlForRequest(state.requestId, options.afterId || 0);
    state.source = new EventSource(url);
    state.source.addEventListener("trace", (message) => {
      try {
        handleTraceEvent(JSON.parse(message.data));
      } catch (error) {
        reportError(error, "Unable to read stream event.");
      }
    });
    state.source.addEventListener("error", () => {
      if (state.running) {
        setRunStatus("disconnected", "bad");
      }
    });
  }

  async function handleChatSubmit(event) {
    event.preventDefault();
    if (handleVoiceRecordingSubmitIntent()) {
      return;
    }
    const prompt = Core.compactText(elements.promptInput.value);
    if (!prompt || state.running) {
      return;
    }
    closeMobileParameterShortcutMenu();
    const visiblePrompt = looksLikeDiscoverDbPrompt(prompt) ? redactDiscoverDbPrompt(prompt) : prompt;
    addMessage("user", "You", visiblePrompt);
    elements.promptInput.value = "";
    resizePromptInput();
    closeNotifications();
    closeMonitors();
    closeParameters();
    resetRequestState();
    setRunning(true);
    try {
      if (await maybeDiscoverDbFromChat(prompt)) {
        setRunning(false);
        return;
      }
      if (await maybeDraftParameterFromChat(prompt)) {
        setRunning(false);
        return;
      }
      if (await maybeCreateMonitorFromChat(prompt)) {
        setRunning(false);
        return;
      }
      const payload = await Core.submitPrompt({
        prompt,
        agentMode: state.agentMode,
        context: requestContext(),
        conversationId: state.conversationId,
      });
      if (payload.conversation_id) {
        state.conversationId = payload.conversation_id;
      }
      startMobileTraceStream(payload.request_id, payload.stream_url, { reset: false });
    } catch (error) {
      setRunning(false);
      addMessage("assistant", "Failure", error?.message || String(error || "Request failed."));
      reportError(error, "Request failed.");
    }
  }

  function confirmationSummary(trace) {
    const actions = Array.isArray(trace.confirmation_actions) ? trace.confirmation_actions : [];
    if (!actions.length) {
      return "The agent needs approval before continuing.";
    }
    return `The agent needs approval for ${Core.formatCount(actions.length, "action")}.`;
  }

  function renderConfirmation(trace, message) {
    const proposed = Array.isArray(trace.confirmation_actions) ? trace.confirmation_actions : [];
    if (proposed.length) {
      const list = el("div", "mobile-confirmation-action-list");
      proposed.forEach((action, index) => {
        if (!action || typeof action !== "object") {
          return;
        }
        const row = el("div", "mobile-confirmation-action");
        const title = el("div", "mobile-confirmation-action-title", confirmationActionLabel(action, index + 1));
        const gateway = confirmationActionGatewayText(action);
        row.append(title);
        if (gateway) {
          row.append(el("div", "mobile-confirmation-action-gateway", gateway));
        }
        const generatedSql = confirmationActionValue(action, "generated_sql");
        const executedSql = confirmationActionValue(action, "executed_sql") || confirmationActionValue(action, "sql");
        const generatedSqlText = confirmationActionText(generatedSql);
        const executedSqlText = confirmationActionText(executedSql);
        appendMobileConfirmationCodeBlock(row, "Generated SQL", generatedSql);
        if (confirmationActionTextIsUseful(executedSqlText) && executedSqlText !== generatedSqlText) {
          appendMobileConfirmationCodeBlock(row, "Executed SQL", executedSql);
        }
        list.append(row);
      });
      if (list.childElementCount) {
        message.append(list);
      }
    }
    const actions = el("div", "mobile-message-actions");
    const approve = el("button", "", "Approve");
    const deny = el("button", "", "Deny");
    approve.type = "button";
    deny.type = "button";
    approve.addEventListener("click", () => submitConfirmation(trace.request_id, "approve"));
    deny.addEventListener("click", () => submitConfirmation(trace.request_id, "deny"));
    actions.append(approve, deny);
    message.append(actions);
  }

  async function submitConfirmation(requestId, action) {
    try {
      const payload = await Core.apiJson(`/confirmation/${encodeURIComponent(requestId)}`, {
        method: "POST",
        body: { action, context: requestContext() },
      });
      if (payload.stream_url && payload.request_id) {
        addSystemMessage("Confirmation", action === "deny" ? "Denied." : "Approved.");
        startMobileTraceStream(payload.request_id, payload.stream_url);
      } else {
        addMessage("assistant", "Confirmation", payload.final_response || Core.formatStatus(payload.status));
        setRunStatus(payload.status || action, action === "deny" ? "warn" : "live");
      }
    } catch (error) {
      reportError(error, "Unable to submit confirmation.");
    }
  }

  function clarificationText(request) {
    return Core.compactText(
      request?.question ||
      request?.missing_information ||
      request?.reason,
      "The agent needs more detail before continuing.",
    );
  }

  function renderClarification(trace, message) {
    const request = Core.isPlainObject(trace?.clarification_request) ? trace.clarification_request : {};
    const inputKind = Core.compactText(request.input_kind || "unknown").toLowerCase();
    const primaryText = [
      request.question,
      request.missing_information,
    ].map((value) => Core.compactText(value).toLowerCase()).join(" ");
    const fullText = [
      request.question,
      request.reason,
      request.missing_information,
    ].map((value) => Core.compactText(value).toLowerCase()).join(" ");
    const secretPattern = /\b(password|passphrase|pass\s+phrase|token|api\s*key|private\s+key|ssh\s+key|credential|secret|pin)\b/i;
    const plainAnswerPattern = /\b(commit\s+message|commit\s+title|message\s+for\s+(?:the\s+)?commit|branch\s+name|python\s+version|version|file\s+path|path|directory|filename|file\s+name|device\s+label|disk\s+label|drive\s+label|usb\s+label|volume\s+label|mount\s*point|mountpoint)\b/i;
    const explicitSecretKind = ["password", "passphrase", "private_key", "token", "credential"].includes(inputKind);
    const secretInput = (
      !(plainAnswerPattern.test(primaryText) && !secretPattern.test(primaryText))
      && (request.secret_input === true || explicitSecretKind || secretPattern.test(fullText))
    );
    const parameterChoices = secretInput && Array.isArray(request.parameter_choices)
      ? request.parameter_choices.filter((choice) => Core.isPlainObject(choice) && Core.compactText(choice.key))
      : [];
    let selectedParameter = null;
    const form = el("form", "mobile-message-actions");
    const answerOptions = Array.isArray(request.options)
      ? request.options.filter((option) => (
          Core.isPlainObject(option)
          && Core.compactText(option.option_id || option.label)
        ))
      : [];
    if (parameterChoices.length) {
      const select = document.createElement("select");
      select.className = "mobile-clarification-parameter-select";
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "Type manually or choose a parameter";
      select.append(emptyOption);
      const groups = {
        relevant: document.createElement("optgroup"),
        all: document.createElement("optgroup"),
      };
      groups.relevant.label = "Relevant matches";
      groups.all.label = "All parameters";
      parameterChoices.forEach((choice) => {
        const option = document.createElement("option");
        const choiceId = Core.compactText(choice.choice_id || `param:${choice.normalized_key || choice.key}`);
        const fields = Array.isArray(choice.field_paths)
          ? choice.field_paths.map((field) => Core.compactText(field)).filter(Boolean)
          : [];
        const preferredField = fields.find((field) => {
          const normalized = field.toLowerCase().replace(/[\s-]+/g, "_");
          if (inputKind === "passphrase") return normalized.includes("passphrase");
          if (inputKind === "private_key") return normalized.includes("private_key") || normalized.includes("ssh_key");
          if (inputKind === "token") return normalized.includes("token") || normalized.includes("api_key");
          if (inputKind === "password") return normalized.includes("password") || normalized.includes("passwd");
          return false;
        }) || "";
        option.value = choiceId;
        option.dataset.key = Core.compactText(choice.key);
        option.dataset.field = preferredField;
        option.textContent = `${choice.key}${fields.length ? ` · ${fields.slice(0, 3).join(", ")}` : ""}`;
        const groupKey = Core.compactText(choice.group).toLowerCase() === "all" ? "all" : "relevant";
        groups[groupKey].append(option);
      });
      if (groups.relevant.childElementCount) select.append(groups.relevant);
      if (groups.all.childElementCount) select.append(groups.all);
      form.append(select);
      select.addEventListener("change", () => {
        const option = select.selectedOptions?.[0] || null;
        const key = Core.compactText(option?.dataset?.key);
        if (!key) {
          selectedParameter = null;
          input.value = "";
          return;
        }
        selectedParameter = {
          choiceId: option.value,
          key,
          field: Core.compactText(option.dataset.field),
        };
        input.value = `Use parameter ${key}`;
      });
    }
    if (answerOptions.length) {
      const optionWrap = el("div", "mobile-clarification-options");
      answerOptions.forEach((option) => {
        const label = Core.compactText(option.label || option.option_id);
        const button = el("button", "mobile-clarification-option", label);
        button.type = "button";
        const description = Core.compactText(option.description);
        if (description) button.title = description;
        button.addEventListener("click", () => {
          void submitClarification(trace.request_id, label, {
            selectedOptionId: Core.compactText(option.option_id || label),
          });
        });
        optionWrap.append(button);
      });
      form.append(optionWrap);
    }
    const input = secretInput ? document.createElement("input") : document.createElement("textarea");
    if (secretInput) {
      input.type = "password";
      input.autocomplete = "new-password";
    } else {
      input.rows = 3;
    }
    input.placeholder = parameterChoices.length ? "Enter a secret or select a stored parameter" : "Answer";
    const submit = el("button", "", "Continue");
    submit.type = "submit";
    form.append(input, submit);
    input.addEventListener("input", () => {
      if (selectedParameter && input.value !== `Use parameter ${selectedParameter.key}`) {
        const select = form.querySelector(".mobile-clarification-parameter-select");
        if (select) select.value = "";
        selectedParameter = null;
      }
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const selected = selectedParameter;
      const answer = selected ? `Use parameter ${selected.key}` : input.value;
      void submitClarification(trace.request_id, answer, {
        parameterChoiceId: selected?.choiceId || null,
        parameterKey: selected?.key || null,
        parameterField: selected?.field || null,
        answerIsSecret: secretInput || Boolean(selected),
      });
    });
    message.append(form);
  }

  async function submitClarification(requestId, answer, metadata = {}) {
    const text = Core.compactText(answer);
    if (!text) {
      showToast("Answer is required.");
      return;
    }
    try {
      const payload = await Core.apiJson(`/clarification/${encodeURIComponent(requestId)}`, {
        method: "POST",
        body: {
          answer: text,
          selected_option_id: metadata?.selectedOptionId || null,
          parameter_choice_id: metadata?.parameterChoiceId || null,
          parameter_key: metadata?.parameterKey || null,
          parameter_field: metadata?.parameterField || null,
          answer_is_secret: metadata?.answerIsSecret === true,
          context: requestContext(),
        },
      });
      addMessage(
        "user",
        "Clarification",
        metadata?.answerIsSecret === true
          ? (metadata?.parameterKey ? `Use parameter ${metadata.parameterKey}` : "Provided secret")
          : text,
      );
      startMobileTraceStream(payload.request_id, payload.stream_url);
    } catch (error) {
      reportError(error, "Unable to submit clarification.");
    }
  }

  function sectionText(section) {
    if (!Core.isPlainObject(section)) {
      return "";
    }
    if (typeof section.content === "string") {
      return section.content;
    }
    if (Array.isArray(section.rows) && section.rows.length) {
      return section.rows.slice(0, 12).map((row) => JSON.stringify(row)).join("\n");
    }
    if (section.content !== null && section.content !== undefined) {
      return JSON.stringify(section.content, null, 2);
    }
    return "";
  }

  function renderDisplayDocument(displayDocument, fallback = "") {
    const parts = [];
    if (fallback) {
      parts.push(fallback);
    }
    const sections = Array.isArray(displayDocument?.sections) ? displayDocument.sections : [];
    for (const section of sections) {
      const title = Core.compactText(section.title);
      const body = sectionText(section);
      if (title || body) {
        parts.push([title, body].filter(Boolean).join("\n"));
      }
    }
    return parts.join("\n\n") || Core.compactText(displayDocument?.summary);
  }

  async function loadFinalTrace(requestId) {
    const id = Core.compactText(requestId || state.requestId);
    if (!id || state.renderedFinalRequests.has(id)) {
      return;
    }
    try {
      const trace = await Core.apiJson(`/trace/${encodeURIComponent(id)}`);
      state.renderedFinalRequests.add(id);
      if (trace.conversation_id) {
        state.conversationId = trace.conversation_id;
      }
      setRunning(false);
      if (trace.confirmation_required) {
        const message = addMessage("assistant", "Confirmation", confirmationSummary(trace), { requestId: id });
        renderConfirmation(trace, message);
        setRunStatus("approval needed", "warn");
        return;
      }
      if (trace.clarification_required) {
        const message = addMessage("assistant", "Input needed", clarificationText(trace.clarification_request), {
          requestId: id,
        });
        renderClarification(trace, message);
        setRunStatus("input needed", "warn");
        return;
      }
      if (trace.display_document) {
        addMessage("assistant", "Result", renderDisplayDocument(trace.display_document, trace.final_response), {
          requestId: id,
        });
      } else if (trace.final_response || trace.error || trace.error_detail) {
        const errorText = traceErrorMessage(trace);
        addMessage("assistant", errorText ? "Failure" : "Result", trace.final_response || errorText, {
          requestId: id,
        });
      }
      setRunStatus(trace.status || "completed");
    } catch (error) {
      reportError(error, "Unable to load final trace.");
    }
  }

  async function stopCurrentRun() {
    if (!state.requestId || !state.running) {
      return;
    }
    try {
      await Core.apiJson(`/stop/${encodeURIComponent(state.requestId)}`, { method: "POST" });
      closeTraceStream();
      setRunning(false);
      setRunStatus("stopped", "warn");
      addSystemMessage("Stopped", "The active run was stopped.");
    } catch (error) {
      reportError(error, "Unable to stop the run.");
    }
  }

  function newChat() {
    closeTraceStream();
    state.requestId = "";
    state.conversationId = "";
    state.renderedFinalRequests.clear();
    setRunning(false);
    clear(elements.chatLog);
    addMessage("assistant", "OpenFabric", "Ready.");
    setRunStatus("idle", "neutral");
    closeNotifications();
    closeMonitors();
    closeParameters();
  }

  function openNotifications() {
    closeTasks();
    closeMonitors();
    closeParameters();
    elements.root.dataset.notificationsOpen = "true";
    elements.notificationButton.setAttribute("aria-expanded", "true");
    elements.notificationSheet.hidden = false;
    elements.notificationSheet.setAttribute("aria-hidden", "false");
    elements.backdrop.hidden = false;
    elements.notificationCloseButton.focus({ preventScroll: true });
  }

  function closeNotifications() {
    elements.root.dataset.notificationsOpen = "false";
    elements.notificationButton.setAttribute("aria-expanded", "false");
    elements.notificationSheet.hidden = true;
    elements.notificationSheet.setAttribute("aria-hidden", "true");
    if (elements.root.dataset.tasksOpen !== "true" && elements.root.dataset.monitorsOpen !== "true" && elements.root.dataset.parametersOpen !== "true") {
      elements.backdrop.hidden = true;
    }
  }

  async function loadNotifications() {
    try {
      const payload = await Core.apiJson("/notifications", { cache: "no-store" });
      const notifications = (Array.isArray(payload.notifications) ? payload.notifications : []).map(
        Core.normalizeNotification,
      );
      const suppressedUnread = deliverUnreadNotifications(notifications);
      state.notifications = notifications;
      const unread = Number(
        payload.counts?.unread ?? state.notifications.filter((item) => item.status === "unread").length,
      );
      const visibleUnread = Math.max(0, unread - suppressedUnread);
      elements.notificationCount.textContent = String(visibleUnread);
      elements.notificationCount.hidden = visibleUnread <= 0;
      renderNotifications();
    } catch (_error) {
      state.notifications = [];
      elements.notificationCount.hidden = true;
      renderNotifications();
    }
  }

  function renderNotifications() {
    clear(elements.notificationList);
    if (!state.notifications.length) {
      elements.notificationList.append(el("div", "mobile-list-item", "No notifications."));
      return;
    }
    state.notifications.forEach((notification) => {
      const item = el("article", "mobile-list-item");
      item.dataset.level = notification.level || "info";
      item.append(el("div", "mobile-list-title", notification.title));
      appendMeta(item, notification.message);
      appendMeta(item, `${Core.formatStatus(notification.status)} | ${Core.formatTime(notification.created_at, "")}`);
      const actions = el("div", "mobile-inline-actions");
      if (notification.request_id) {
        const open = el("button", "", "Open chat");
        open.type = "button";
        open.addEventListener("click", () => openNotificationRequest(notification.request_id));
        actions.append(open);
      }
      if (notification.status === "unread") {
        const read = el("button", "", "Read");
        read.type = "button";
        read.addEventListener("click", () => markNotificationRead(notification.notification_id));
        actions.append(read);
      }
      if (actions.children.length) {
        item.append(actions);
      }
      elements.notificationList.append(item);
    });
  }

  async function markNotificationRead(notificationId) {
    try {
      await Core.apiJson(`/notifications/${encodeURIComponent(notificationId)}`, {
        method: "PATCH",
        body: { status: "read" },
      });
      await loadNotifications();
    } catch (error) {
      reportError(error, "Unable to mark notification read.");
    }
  }

  async function markAllNotificationsRead() {
    try {
      await Core.apiJson("/notifications/mark-all-read", { method: "POST" });
      await loadNotifications();
    } catch (error) {
      reportError(error, "Unable to mark notifications read.");
    }
  }

  async function openNotificationRequest(requestId) {
    closeNotifications();
    closeTasks();
    closeMonitors();
    try {
      const trace = await Core.apiJson(`/trace/${encodeURIComponent(requestId)}`);
      state.requestId = requestId;
      state.renderedFinalRequests.delete(requestId);
      if (trace.prompt) {
        addMessage("user", "Request", trace.prompt);
      }
      await loadFinalTrace(requestId);
      if (trace.status === "running") {
        startMobileTraceStream(requestId, Core.streamUrlForRequest(requestId));
      }
    } catch (error) {
      reportError(error, "Chat unavailable.");
    }
  }

  function activeTaskCount() {
    return Number(state.taskCounts.queued || 0)
      + Number(state.taskCounts.running || 0)
      + Number(state.taskCounts.awaiting_confirmation || 0)
      + Number(state.taskCounts.awaiting_clarification || 0);
  }

  function openTasks() {
    closeNotifications();
    closeMonitors();
    closeParameters();
    elements.root.dataset.tasksOpen = "true";
    elements.tasksButton?.setAttribute("aria-expanded", "true");
    elements.taskSheet.hidden = false;
    elements.taskSheet.setAttribute("aria-hidden", "false");
    elements.backdrop.hidden = false;
    elements.taskCloseButton.focus({ preventScroll: true });
  }

  function closeTasks() {
    elements.root.dataset.tasksOpen = "false";
    elements.tasksButton?.setAttribute("aria-expanded", "false");
    elements.taskSheet.hidden = true;
    elements.taskSheet.setAttribute("aria-hidden", "true");
    if (elements.root.dataset.notificationsOpen !== "true" && elements.root.dataset.monitorsOpen !== "true" && elements.root.dataset.parametersOpen !== "true") {
      elements.backdrop.hidden = true;
    }
  }

  function taskStatusText(status) {
    return Core.formatStatus(status || "queued");
  }

  function renderTasks() {
    const count = activeTaskCount();
    if (elements.taskCount) {
      elements.taskCount.textContent = String(count);
      elements.taskCount.hidden = count <= 0;
    }
    clear(elements.taskList);
    if (!state.tasks.length) {
      elements.taskList.append(el("div", "mobile-list-item", "No durable tasks."));
      return;
    }
    state.tasks.forEach((task) => {
      const item = el("article", "mobile-list-item");
      item.dataset.level = Core.statusTone(task.status);
      item.dataset.taskId = task.task_id || "";
      item.append(el("div", "mobile-list-title", task.title || "Untitled task"));
      appendMeta(item, task.prompt);
      appendMeta(item, `${taskStatusText(task.status)} | ${Core.formatTime(task.updated_at, "")}`);
      const checkpoint = task.latest_checkpoint?.summary || task.latest_checkpoint?.title || task.blocker_reason || task.error_preview || task.final_response_preview;
      appendMeta(item, checkpoint);
      const actions = el("div", "mobile-inline-actions");
      const addAction = (label, action, disabled = false) => {
        const button = el("button", "", label);
        button.type = "button";
        button.dataset.taskAction = action;
        button.disabled = disabled;
        actions.append(button);
      };
      addAction("Open", "open", !task.latest_request_id);
      if (["queued", "interrupted"].includes(task.status)) addAction("Start", "start");
      if (!["queued", "running", "awaiting_confirmation", "awaiting_clarification"].includes(task.status)) {
        addAction("Retry", "retry");
      }
      if (["queued", "running", "awaiting_confirmation", "awaiting_clarification"].includes(task.status)) {
        addAction("Cancel", "cancel");
      }
      addAction("Archive", "archive");
      item.append(actions);
      elements.taskList.append(item);
    });
  }

  async function loadTasks() {
    try {
      const payload = await Core.apiJson("/tasks", { cache: "no-store", params: { limit: 100 } });
      state.tasks = (Array.isArray(payload.tasks) ? payload.tasks : []).map(Core.normalizeTask);
      state.taskCounts = payload.counts || {};
      renderTasks();
    } catch (error) {
      state.tasks = [];
      state.taskCounts = {};
      clear(elements.taskList);
      elements.taskList.append(el("div", "mobile-list-item", `Tasks unavailable: ${shortStatusError(error)}`));
    }
  }

  async function createMobileTask(startNow) {
    const prompt = Core.compactText(elements.taskPrompt.value || elements.promptInput.value);
    if (!prompt) {
      showToast("Task prompt is required.");
      return;
    }
    try {
      const payload = await Core.createTask({
        prompt,
        title: Core.compactText(elements.taskTitle.value),
        agent_mode: state.agentMode,
        gateway_id: state.selectedGatewayId,
        conversation_id: state.conversationId,
        context: requestContext(),
        start_now: Boolean(startNow),
      });
      elements.taskPrompt.value = "";
      elements.taskTitle.value = "";
      if (payload.request_id && payload.stream_url) {
        startMobileTraceStream(payload.request_id, payload.stream_url);
      }
      await loadTasks();
      openTasks();
    } catch (error) {
      reportError(error, "Unable to create task.");
    }
  }

  async function handleTaskAction(taskId, action) {
    const task = state.tasks.find((item) => item.task_id === taskId);
    if (action === "open") {
      if (task?.latest_request_id) {
        closeTasks();
        await openNotificationRequest(task.latest_request_id);
      }
      return;
    }
    try {
      const payload = await Core.taskAction(taskId, action);
      if (payload.request_id && payload.stream_url) {
        startMobileTraceStream(payload.request_id, payload.stream_url);
      }
      await loadTasks();
    } catch (error) {
      reportError(error, "Unable to update task.");
    }
  }

  function activeMonitorCount() {
    return Number(state.monitorCounts.queued || 0)
      + Number(state.monitorCounts.running || 0)
      + Number(state.monitorCounts.paused || 0)
      + Number(state.monitorCounts.triggered || 0);
  }

  function openMonitors() {
    closeNotifications();
    closeTasks();
    closeParameters();
    elements.root.dataset.monitorsOpen = "true";
    elements.monitorsButton?.setAttribute("aria-expanded", "true");
    elements.monitorSheet.hidden = false;
    elements.monitorSheet.setAttribute("aria-hidden", "false");
    elements.backdrop.hidden = false;
    elements.monitorCloseButton.focus({ preventScroll: true });
  }

  function closeMonitors() {
    elements.root.dataset.monitorsOpen = "false";
    elements.monitorsButton?.setAttribute("aria-expanded", "false");
    if (elements.monitorSheet) {
      elements.monitorSheet.hidden = true;
      elements.monitorSheet.setAttribute("aria-hidden", "true");
    }
    if (elements.root.dataset.notificationsOpen !== "true" && elements.root.dataset.tasksOpen !== "true" && elements.root.dataset.parametersOpen !== "true") {
      elements.backdrop.hidden = true;
    }
  }

  function parameterCsv(value) {
    return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
  }

  function parameterFieldPaths(schema, prefix = "") {
    if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
      return prefix ? [prefix] : [];
    }
    const paths = [];
    for (const [key, value] of Object.entries(schema)) {
      const next = prefix ? `${prefix}.${key}` : key;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        paths.push(...parameterFieldPaths(value, next));
      } else {
        paths.push(next);
      }
    }
    return paths;
  }

  function parameterContainsMask(value) {
    if (value === "••••") return true;
    if (Array.isArray(value)) return value.some(parameterContainsMask);
    if (value && typeof value === "object") return Object.values(value).some(parameterContainsMask);
    return false;
  }

  function normalizeMobilePromptParameters(items) {
    if (!Array.isArray(items)) {
      return [];
    }
    return items
      .filter((item) => item && typeof item === "object")
      .map((item) => ({
        key: Core.compactText(item.key),
        normalized_key: Core.compactText(item.normalized_key),
        description: Core.compactText(item.description),
        aliases: Array.isArray(item.aliases) ? item.aliases.map((alias) => Core.compactText(alias)).filter(Boolean) : [],
        tags: Array.isArray(item.tags) ? item.tags.map((tag) => Core.compactText(tag)).filter(Boolean) : [],
        sensitive: item.sensitive !== false,
        value_schema: item.value_schema && typeof item.value_schema === "object" ? item.value_schema : {},
      }))
      .filter((item) => item.key || item.normalized_key);
  }

  function invalidateMobilePromptParameterShortcuts() {
    state.promptParameters = [];
    state.promptParametersLoaded = false;
    mobilePromptParameterShortcutLoadPromise = null;
  }

  async function loadMobilePromptParameterShortcuts({ force = false } = {}) {
    if (state.promptParametersLoaded && !force) {
      return state.promptParameters;
    }
    if (mobilePromptParameterShortcutLoadPromise && !force) {
      return mobilePromptParameterShortcutLoadPromise;
    }
    mobilePromptParameterShortcutLoadPromise = (async () => {
      try {
        const payload = await Core.apiJson("/parameters", {
          cache: "no-store",
          params: { limit: 1000 },
        });
        state.promptParameters = normalizeMobilePromptParameters(payload.entries);
        state.promptParametersLoaded = true;
        return state.promptParameters;
      } catch (error) {
        state.promptParameters = [];
        state.promptParametersLoaded = false;
        console.warn("Failed to load mobile prompt parameter shortcuts", error);
        return [];
      } finally {
        mobilePromptParameterShortcutLoadPromise = null;
      }
    })();
    return mobilePromptParameterShortcutLoadPromise;
  }

  function mobileParameterShortcutMenuIsOpen() {
    return Boolean(elements.parameterShortcutMenu && !elements.parameterShortcutMenu.hidden);
  }

  function closeMobileParameterShortcutMenu() {
    state.promptParameterShortcutTrigger = null;
    state.promptParameterShortcutMatches = [];
    state.promptParameterShortcutActiveIndex = 0;
    state.promptParameterShortcutSelectionArmed = false;
    if (elements.parameterShortcutMenu) {
      elements.parameterShortcutMenu.hidden = true;
      clear(elements.parameterShortcutMenu);
    }
    elements.promptInput?.setAttribute("aria-expanded", "false");
    elements.promptInput?.removeAttribute("aria-activedescendant");
  }

  function mobileParameterShortcutSyntaxMask(text) {
    const value = String(text || "");
    const mask = Array(value.length).fill(false);
    let inSingleQuote = false;
    let inDoubleQuote = false;
    let inInlineCode = false;
    let inFencedCode = false;
    let escaped = false;
    let index = 0;
    while (index < value.length) {
      if (value.startsWith("```", index) && !inSingleQuote && !inDoubleQuote) {
        for (let offset = 0; offset < 3 && index + offset < mask.length; offset += 1) {
          mask[index + offset] = true;
        }
        inFencedCode = !inFencedCode;
        index += 3;
        escaped = false;
        continue;
      }
      const char = value[index];
      if (inFencedCode) {
        mask[index] = true;
        index += 1;
        continue;
      }
      if (char === "`" && !inSingleQuote && !inDoubleQuote) {
        mask[index] = true;
        inInlineCode = !inInlineCode;
        index += 1;
        escaped = false;
        continue;
      }
      if (inInlineCode) {
        mask[index] = true;
        index += 1;
        continue;
      }
      if (inSingleQuote) {
        mask[index] = true;
        if (char === "'" && !escaped) {
          inSingleQuote = false;
        }
        escaped = char === "\\" && !escaped;
        if (char !== "\\") {
          escaped = false;
        }
        index += 1;
        continue;
      }
      if (inDoubleQuote) {
        mask[index] = true;
        if (char === '"' && !escaped) {
          inDoubleQuote = false;
        }
        escaped = char === "\\" && !escaped;
        if (char !== "\\") {
          escaped = false;
        }
        index += 1;
        continue;
      }
      if (char === "'") {
        mask[index] = true;
        inSingleQuote = true;
        escaped = false;
      } else if (char === '"') {
        mask[index] = true;
        inDoubleQuote = true;
        escaped = false;
      }
      index += 1;
    }
    return mask;
  }

  function mobileParameterShortcutTriggerAtCursor() {
    const input = elements.promptInput;
    if (!input || input.selectionStart !== input.selectionEnd) {
      return null;
    }
    const cursor = input.selectionStart;
    const promptValue = input.value;
    const beforeCursor = promptValue.slice(0, cursor);
    const parameterIndex = beforeCursor.lastIndexOf(":p");
    if (parameterIndex < 0) {
      return null;
    }
    const mask = mobileParameterShortcutSyntaxMask(promptValue);
    if (mask[parameterIndex] || mask[parameterIndex + 1]) {
      return null;
    }
    const prefix = parameterIndex > 0 ? beforeCursor[parameterIndex - 1] : "";
    if (prefix && !/[\s([{]/.test(prefix)) {
      return null;
    }
    const query = beforeCursor.slice(parameterIndex + 2);
    if (!/^[A-Za-z0-9_.:-]*$/.test(query)) {
      return null;
    }
    return {
      start: parameterIndex,
      end: cursor,
      query: query.toLowerCase(),
    };
  }

  function moveMobilePromptCaretToEntryEdge(event) {
    const input = elements.promptInput;
    if (
      !input
      || (event.key !== "Home" && event.key !== "End")
      || event.ctrlKey
      || event.altKey
      || event.metaKey
      || event.isComposing
    ) {
      return false;
    }
    event.preventDefault();
    const valueEnd = input.value.length;
    if (event.shiftKey) {
      const anchor =
        input.selectionDirection === "backward" ? input.selectionEnd : input.selectionStart;
      if (event.key === "Home") {
        input.setSelectionRange(0, anchor, "backward");
      } else {
        input.setSelectionRange(anchor, valueEnd, "forward");
      }
    } else {
      const position = event.key === "Home" ? 0 : valueEnd;
      input.setSelectionRange(position, position);
    }
    return true;
  }

  function sameMobileParameterShortcutTrigger(left, right) {
    return Boolean(
      left
      && right
      && left.start === right.start
      && left.end === right.end
      && left.query === right.query
    );
  }

  function mobileParameterShortcutSearchValues(entry) {
    return [
      entry?.key,
      entry?.normalized_key,
      entry?.description,
      ...(Array.isArray(entry?.aliases) ? entry.aliases : []),
      ...(Array.isArray(entry?.tags) ? entry.tags : []),
      ...parameterFieldPaths(entry?.value_schema),
    ].map((value) => String(value || "").toLowerCase()).filter(Boolean);
  }

  function mobileParameterShortcutDescription(entry) {
    const fields = parameterFieldPaths(entry?.value_schema).slice(0, 3);
    const tags = Array.isArray(entry?.tags) && entry.tags.length ? ` · ${entry.tags.join(", ")}` : "";
    const detail = Core.compactText(entry?.description) || fields.join(", ") || "object";
    return `Parameter Store · ${entry?.sensitive === false ? "plain" : "sensitive"} · ${detail}${tags}`;
  }

  function mobilePromptParameterShortcutMatches(query) {
    const needle = String(query || "").toLowerCase();
    return state.promptParameters
      .filter((entry) => !needle || mobileParameterShortcutSearchValues(entry).some((value) => value.includes(needle)));
  }

  function updateMobileParameterShortcutActiveOption() {
    if (!elements.parameterShortcutMenu || !elements.promptInput) {
      return;
    }
    const options = $$(".mobile-parameter-shortcut-option", elements.parameterShortcutMenu);
    options.forEach((option, index) => {
      const active = index === state.promptParameterShortcutActiveIndex;
      option.classList.toggle("active", active);
      option.setAttribute("aria-selected", active ? "true" : "false");
      if (active) {
        elements.promptInput.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function renderMobileParameterShortcutMenu(trigger) {
    if (!elements.parameterShortcutMenu || !elements.promptInput || !trigger) {
      closeMobileParameterShortcutMenu();
      return;
    }
    const matches = mobilePromptParameterShortcutMatches(trigger.query);
    if (!matches.length) {
      closeMobileParameterShortcutMenu();
      return;
    }
    const triggerChanged = !sameMobileParameterShortcutTrigger(state.promptParameterShortcutTrigger, trigger);
    state.promptParameterShortcutTrigger = trigger;
    state.promptParameterShortcutMatches = matches;
    state.promptParameterShortcutActiveIndex = triggerChanged
      ? 0
      : Math.min(Math.max(0, state.promptParameterShortcutActiveIndex || 0), matches.length - 1);
    if (triggerChanged) {
      state.promptParameterShortcutSelectionArmed = false;
    }
    clear(elements.parameterShortcutMenu);
    matches.forEach((entry, index) => {
      const option = el("button", "mobile-parameter-shortcut-option");
      const key = entry.key || entry.normalized_key || "parameter";
      option.type = "button";
      option.id = `mobile-parameter-shortcut-option-${String(entry.normalized_key || key).replace(/[^A-Za-z0-9_-]+/g, "_")}`;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", index === state.promptParameterShortcutActiveIndex ? "true" : "false");
      option.append(
        el("span", "mobile-parameter-shortcut-name", `:p ${key}`),
        el("span", "mobile-parameter-shortcut-description", mobileParameterShortcutDescription(entry)),
      );
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });
      option.addEventListener("mouseenter", () => {
        state.promptParameterShortcutActiveIndex = index;
        updateMobileParameterShortcutActiveOption();
      });
      option.addEventListener("click", () => {
        insertMobileParameterShortcut(entry);
      });
      elements.parameterShortcutMenu.append(option);
    });
    elements.parameterShortcutMenu.hidden = false;
    elements.promptInput.setAttribute("aria-expanded", "true");
    updateMobileParameterShortcutActiveOption();
  }

  function updateMobileParameterShortcutMenu({ allowOpen = true } = {}) {
    const trigger = mobileParameterShortcutTriggerAtCursor();
    if (!trigger) {
      closeMobileParameterShortcutMenu();
      return;
    }
    if (!allowOpen && !mobileParameterShortcutMenuIsOpen()) {
      closeMobileParameterShortcutMenu();
      return;
    }
    if (!state.promptParametersLoaded) {
      void loadMobilePromptParameterShortcuts().then(() => {
        const latest = mobileParameterShortcutTriggerAtCursor();
        if (sameMobileParameterShortcutTrigger(trigger, latest)) {
          renderMobileParameterShortcutMenu(latest);
        }
      });
      return;
    }
    renderMobileParameterShortcutMenu(trigger);
  }

  function moveMobileParameterShortcutSelection(direction) {
    if (!state.promptParameterShortcutMatches.length) {
      return false;
    }
    const length = state.promptParameterShortcutMatches.length;
    state.promptParameterShortcutActiveIndex = (
      state.promptParameterShortcutActiveIndex + direction + length
    ) % length;
    state.promptParameterShortcutSelectionArmed = true;
    updateMobileParameterShortcutActiveOption();
    return true;
  }

  function insertMobileParameterShortcut(entry) {
    const input = elements.promptInput;
    const trigger = mobileParameterShortcutTriggerAtCursor();
    const key = Core.compactText(entry?.key || entry?.normalized_key);
    if (!input || !trigger || !key) {
      return false;
    }
    const value = input.value;
    input.value = value.slice(0, trigger.start) + key + value.slice(trigger.end);
    const cursor = trigger.start + key.length;
    input.focus();
    input.setSelectionRange(cursor, cursor);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    closeMobileParameterShortcutMenu();
    return true;
  }

  function insertActiveMobileParameterShortcut() {
    return insertMobileParameterShortcut(
      state.promptParameterShortcutMatches[state.promptParameterShortcutActiveIndex],
    );
  }

  function clearParameterEditor() {
    state.selectedParameterKey = "";
    if (elements.parameterKey) elements.parameterKey.value = "";
    if (elements.parameterDescription) elements.parameterDescription.value = "";
    if (elements.parameterValue) elements.parameterValue.value = "{\n  \n}";
    if (elements.parameterAliases) elements.parameterAliases.value = "";
    if (elements.parameterTags) elements.parameterTags.value = "";
    if (elements.parameterSensitive) elements.parameterSensitive.checked = true;
  }

  function populateParameterEditor(entry, valueOverride = null) {
    if (!entry) return;
    state.selectedParameterKey = Core.compactText(entry.normalized_key || entry.key);
    if (elements.parameterKey) elements.parameterKey.value = Core.compactText(entry.key);
    if (elements.parameterDescription) elements.parameterDescription.value = Core.compactText(entry.description);
    const value = valueOverride && typeof valueOverride === "object"
      ? valueOverride
      : (entry.masked_value_json && typeof entry.masked_value_json === "object" ? entry.masked_value_json : {});
    if (elements.parameterValue) elements.parameterValue.value = JSON.stringify(value, null, 2);
    if (elements.parameterAliases) elements.parameterAliases.value = Array.isArray(entry.aliases) ? entry.aliases.join(", ") : "";
    if (elements.parameterTags) elements.parameterTags.value = Array.isArray(entry.tags) ? entry.tags.join(", ") : "";
    if (elements.parameterSensitive) elements.parameterSensitive.checked = entry.sensitive !== false;
    renderParameters();
  }

  function renderParameters() {
    clear(elements.parameterList);
    if (!state.parameters.length) {
      elements.parameterList?.append(el("div", "mobile-empty", "No parameters saved yet."));
      return;
    }
    state.parameters.forEach((entry) => {
      const row = el("button", "mobile-list-item");
      row.type = "button";
      row.dataset.selected = String((entry.normalized_key || entry.key || "") === state.selectedParameterKey);
      row.append(el("strong", "", Core.compactText(entry.key || entry.normalized_key, "parameter")));
      appendMeta(row, `${entry.sensitive === false ? "plain" : "sensitive"} · ${parameterFieldPaths(entry.value_schema).slice(0, 4).join(", ") || "object"}`);
      row.addEventListener("click", () => populateParameterEditor(entry));
      elements.parameterList?.append(row);
    });
  }

  async function loadParameters() {
    const q = Core.compactText(elements.parameterSearch?.value);
    const payload = await Core.apiJson("/parameters", {
      cache: "no-store",
      params: q ? { q } : null,
    });
    state.parameters = Array.isArray(payload.entries) ? payload.entries : [];
    renderParameters();
  }

  function openParameters() {
    closeNotifications();
    closeTasks();
    closeMonitors();
    elements.root.dataset.parametersOpen = "true";
    elements.parametersButton?.setAttribute("aria-expanded", "true");
    if (elements.parameterSheet) {
      elements.parameterSheet.hidden = false;
      elements.parameterSheet.setAttribute("aria-hidden", "false");
    }
    elements.backdrop.hidden = false;
    elements.parameterCloseButton?.focus({ preventScroll: true });
  }

  function closeParameters() {
    elements.root.dataset.parametersOpen = "false";
    elements.parametersButton?.setAttribute("aria-expanded", "false");
    if (elements.parameterSheet) {
      elements.parameterSheet.hidden = true;
      elements.parameterSheet.setAttribute("aria-hidden", "true");
    }
    if (elements.root.dataset.notificationsOpen !== "true" && elements.root.dataset.tasksOpen !== "true" && elements.root.dataset.monitorsOpen !== "true") {
      elements.backdrop.hidden = true;
    }
  }

  function readParameterPayload(update = false) {
    let value = {};
    try {
      value = JSON.parse(elements.parameterValue?.value || "{}");
    } catch {
      showActionError(
        "Parameter JSON invalid",
        "The parameter was not saved because the value is not valid JSON.",
        "Correct the JSON object in the value field, then save again.",
        elements.parameterValue,
      );
      return null;
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      showActionError(
        "Parameter value must be an object",
        "The parameter was not saved because the value is not a JSON object.",
        "Use an object such as { \"token\": \"...\" }, then save again.",
        elements.parameterValue,
      );
      return null;
    }
    const payload = {
      key: Core.compactText(elements.parameterKey?.value),
      description: Core.compactText(elements.parameterDescription?.value),
      aliases: parameterCsv(elements.parameterAliases?.value),
      tags: parameterCsv(elements.parameterTags?.value),
      sensitive: elements.parameterSensitive?.checked !== false,
    };
    if (!update || !parameterContainsMask(value)) {
      payload.value_json = value;
    }
    return payload;
  }

  async function saveParameter() {
    const selected = state.selectedParameterKey;
    const payload = readParameterPayload(Boolean(selected));
    if (!payload) return;
    if (!payload.key) {
      showActionError(
        "Parameter key required",
        "The parameter was not saved because the key is missing.",
        "Enter a parameter key, then save again.",
        elements.parameterKey,
      );
      return;
    }
    const result = await Core.apiJson(
      selected ? `/parameters/${encodeURIComponent(selected)}` : "/parameters",
      { method: selected ? "PATCH" : "POST", body: payload },
    );
    populateParameterEditor(result.entry);
    invalidateMobilePromptParameterShortcuts();
    await loadParameters();
    showToast("Parameter saved.");
  }

  async function revealParameter() {
    const key = state.selectedParameterKey || Core.compactText(elements.parameterKey?.value);
    if (!key) {
      showActionError(
        "No parameter selected",
        "There is no parameter selected to reveal.",
        "Select a parameter or enter its key before revealing the value.",
        elements.parameterKey,
      );
      return;
    }
    const payload = await Core.apiJson(`/parameters/${encodeURIComponent(key)}/reveal`, { method: "POST" });
    populateParameterEditor(payload.entry, payload.value_json);
  }

  async function deleteParameter() {
    const key = state.selectedParameterKey || Core.compactText(elements.parameterKey?.value);
    if (!key) {
      showActionError(
        "No parameter selected",
        "There is no parameter selected to delete.",
        "Select a parameter or enter its key before deleting.",
        elements.parameterKey,
      );
      return;
    }
    await Core.apiJson(`/parameters/${encodeURIComponent(key)}`, { method: "DELETE" });
    clearParameterEditor();
    invalidateMobilePromptParameterShortcuts();
    await loadParameters();
  }

  function monitorStatusText(status) {
    return Core.formatStatus(status || "queued");
  }

  function renderMonitors() {
    const count = activeMonitorCount();
    if (elements.monitorCount) {
      elements.monitorCount.textContent = String(count);
      elements.monitorCount.hidden = count <= 0;
    }
    clear(elements.monitorList);
    if (!state.monitors.length) {
      elements.monitorList.append(el("div", "mobile-list-item", "No monitors yet."));
      return;
    }
    state.monitors.forEach((monitor) => {
      const item = el("article", "mobile-list-item");
      item.dataset.level = Core.statusTone(monitor.status);
      item.dataset.monitorId = monitor.monitor_id || "";
      item.append(el("div", "mobile-list-title", monitor.title || "Untitled monitor"));
      appendMeta(item, monitor.command);
      if (monitor.planner_rationale) appendMeta(item, "LLM planned");
      if (["llm_judged", "hybrid"].includes(String(monitor.trigger_mode || ""))) appendMeta(item, "LLM judged");
      appendMeta(item, `${monitorStatusText(monitor.status)} | ${Core.formatTime(monitor.updated_at, "")}`);
      appendMeta(item, monitor.latest_judge_summary || monitor.trigger_reason || monitor.latest_observation?.output_preview || monitor.error_preview || monitor.final_summary);
      const actions = el("div", "mobile-inline-actions");
      const addAction = (label, action, disabled = false) => {
        const button = el("button", "", label);
        button.type = "button";
        button.dataset.monitorAction = action;
        button.disabled = disabled;
        actions.append(button);
      };
      if (!["running", "triggered", "archived"].includes(String(monitor.status || ""))) addAction("Start", "start");
      if (["queued", "running"].includes(String(monitor.status || ""))) addAction("Pause", "pause");
      if (["queued", "running", "paused"].includes(String(monitor.status || ""))) addAction("Cancel", "cancel");
      if (monitor.triggered_task_id) addAction("Open Task", "open-task");
      addAction("Archive", "archive");
      item.append(actions);
      elements.monitorList.append(item);
    });
  }

  async function loadMonitors() {
    try {
      const payload = await Core.apiJson("/monitors", { cache: "no-store", params: { limit: 100 } });
      state.monitors = (Array.isArray(payload.monitors) ? payload.monitors : []).map(Core.normalizeMonitor);
      state.monitorCounts = payload.counts || {};
      renderMonitors();
    } catch (error) {
      state.monitors = [];
      state.monitorCounts = {};
      clear(elements.monitorList);
      elements.monitorList.append(el("div", "mobile-list-item", `Monitors unavailable: ${shortStatusError(error)}`));
    }
  }

  async function createMobileMonitor(startNow, draft = null) {
    const source = draft || {};
    const command = Core.compactText(source.command ?? elements.monitorCommand?.value);
    if (!command) {
      showToast("Monitor command is required.");
      return null;
    }
    const body = {
      prompt: Core.compactText(source.prompt || command),
      title: Core.compactText(source.title ?? elements.monitorTitle?.value),
      mode: Core.compactText(source.mode || elements.monitorMode?.value, "sample_command"),
      command,
      interval_seconds: Number(source.interval_seconds || elements.monitorInterval?.value || 5),
      duration_seconds: Number(source.duration_seconds || elements.monitorDuration?.value || 300),
      condition: Core.compactText(source.condition ?? elements.monitorCondition?.value),
      natural_language_condition: Core.compactText(source.natural_language_condition),
      trigger_mode: Core.compactText(source.trigger_mode || "deterministic"),
      action_prompt: Core.compactText(source.action_prompt ?? elements.monitorAction?.value),
      planner_rationale: Core.compactText(source.planner_rationale),
      risk_notes: Core.compactText(source.risk_notes),
      judge_interval_seconds: Number(source.judge_interval_seconds || 5),
      agent_mode: state.agentMode,
      gateway_id: state.selectedGatewayId,
      conversation_id: state.conversationId,
      context: requestContext(),
      start_now: Boolean(startNow),
    };
    try {
      const payload = await Core.apiJson("/monitors", { method: "POST", body });
      if (!draft) {
        elements.monitorTitle.value = "";
        elements.monitorCommand.value = "";
        elements.monitorCondition.value = "";
        elements.monitorAction.value = "";
      }
      await loadMonitors();
      openMonitors();
      return payload.monitor || null;
    } catch (error) {
      reportError(error, "Unable to create monitor.");
      return null;
    }
  }

  async function handleMonitorAction(monitorId, action) {
    const monitor = state.monitors.find((item) => item.monitor_id === monitorId);
    if (action === "open-task") {
      if (monitor?.triggered_task_id) {
        closeMonitors();
        await loadTasks();
        openTasks();
      }
      return;
    }
    try {
      await Core.apiJson(`/monitors/${encodeURIComponent(monitorId)}/${action}`, { method: "POST" });
      await loadMonitors();
    } catch (error) {
      reportError(error, "Unable to update monitor.");
    }
  }

  function looksLikeMonitorPrompt(prompt) {
    const text = Core.compactText(prompt).toLowerCase();
    return /^\/monitor\b/.test(text)
      || /\b(monitor|watch|observe|alert me|tell me if|notify me if)\b/.test(text)
      || /\b(nvidia-smi|free ram|gpu memory|disk space|tail\s+-f)\b/.test(text);
  }

  function looksLikeParameterPrompt(prompt) {
    const text = String(prompt || "");
    return looksLikeParameterSetPrompt(text)
      || /\b(?:parameter\s+store|param\s+store|parameters\s+store)\b/i.test(text)
      || /\b(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b/i.test(text)
      || /\b(?:stored|saved)\s+(?:in|under)\b/i.test(text)
      || /^\/(?:parameter|param|store)\b/i.test(String(prompt || ""));
  }

  function looksLikeParameterSetPrompt(prompt) {
    const text = String(prompt || "");
    const writeVerbMatch = /\b(store|save|keep|stash)\b[\s\S]{0,180}\b(?:parameter|param|credential|password|passphrase|secret|token|connection(?:\s+string)?|database|db|datasource|config|key|info|information)\b/ig;
    let match;
    let hasWriteVerb = false;
    while ((match = writeVerbMatch.exec(text)) !== null) {
      const verb = String(match[1] || "").toLowerCase();
      const prefix = text.slice(Math.max(0, match.index - 16), match.index).toLowerCase();
      if (verb === "store" && /(?:parameter|param)\s+$/.test(prefix)) {
        continue;
      }
      hasWriteVerb = true;
      break;
    }
    return /^\/(?:parameter|param|store)\b/i.test(text)
      || hasWriteVerb
      || /\b(?:store|save)\s+(?:as|to|in|into|under)\s+(?:the\s+)?(?:parameter\s+)?store\b/i.test(text)
      || /\badd\b[\s\S]{0,120}\b(?:credential|password|passphrase|secret|token|connection(?:\s+string)?|database|db|datasource|config\s+(?:value|json|entry)|parameter\s+(?:called|named|key|entry)|param\s+(?:called|named|key|entry))\b/i.test(text)
      || /\b(?:create|set|update)\b[\s\S]{0,120}\b(?:parameter|param|credential|password|secret|token)\b[\s\S]{0,80}\b(?:store|value|json|key)\b/i.test(text);
  }

  async function maybeDraftParameterFromChat(prompt) {
    if (!looksLikeParameterPrompt(prompt)) {
      return false;
    }
    try {
      const payload = await Core.apiJson("/parameters/draft", {
        method: "POST",
        body: {
          prompt,
          context: requestContext(),
          agent_mode: state.agentMode,
          llm_model: state.settings?.llm_model || "auto",
        },
      });
      if (!payload.is_parameter_request) {
        return false;
      }
      const draft = Array.isArray(payload.drafts) ? payload.drafts[0] : null;
      if (!draft) {
        addMessage("assistant", "Parameter needs detail", "I need a key and JSON object before saving.");
        setRunStatus("parameter needs detail", "warn");
        return true;
      }
      clearParameterEditor();
      if (elements.parameterKey) elements.parameterKey.value = Core.compactText(draft.key);
      if (elements.parameterDescription) elements.parameterDescription.value = Core.compactText(draft.description);
      if (elements.parameterValue) elements.parameterValue.value = JSON.stringify(draft.value || {}, null, 2);
      if (elements.parameterAliases) elements.parameterAliases.value = Array.isArray(draft.aliases) ? draft.aliases.join(", ") : "";
      if (elements.parameterTags) elements.parameterTags.value = Array.isArray(draft.tags) ? draft.tags.join(", ") : "";
      if (elements.parameterSensitive) elements.parameterSensitive.checked = draft.sensitive !== false;
      openParameters();
      addMessage("assistant", "Parameter draft ready", "Review the extracted parameter entry, edit if needed, then save it.");
      setRunStatus("parameter draft", "good");
      return true;
    } catch (error) {
      if (!looksLikeParameterSetPrompt(prompt)) {
        return false;
      }
      addMessage("assistant", "Parameter draft failed", shortStatusError(error));
      setRunStatus("parameter failed", "bad");
      return true;
    }
  }

  function looksLikeDiscoverDbPrompt(prompt) {
    return /^\/discoverdb\b/i.test(String(prompt || "").trim());
  }

  function redactDiscoverDbPrompt(prompt) {
    return String(prompt || "")
      .replace(/\b(password\s*=\s*)(?:"[^"]*"|'[^']*'|\S+)/ig, "$1[redacted]")
      .replace(/\b(user\s*=\s*)(?:"[^"]*"|'[^']*'|\S+)/ig, "$1[redacted]");
  }

  function discoverDbPreviewText(payload) {
    const discovered = Array.isArray(payload?.discovered) ? payload.discovered : [];
    const createDrafts = discovered.filter((draft) => String(draft?.operation || "create").toLowerCase() !== "update");
    const updateDrafts = discovered.filter((draft) => String(draft?.operation || "").toLowerCase() === "update");
    const skipped = Array.isArray(payload?.skipped_existing) ? payload.skipped_existing : [];
    const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
    const lines = [];
    if (createDrafts.length) {
      lines.push(`Found ${createDrafts.length} new DB profile${createDrafts.length === 1 ? "" : "s"} to save.`);
      createDrafts.forEach((draft) => {
        const dbname = draft?.value_json?.dbname || draft?.key || "database";
        lines.push(`${draft?.key || dbname} (${dbname})`);
      });
    }
    if (updateDrafts.length) {
      lines.push(`Found ${updateDrafts.length} DB profile refresh${updateDrafts.length === 1 ? "" : "es"} to save.`);
      updateDrafts.forEach((draft) => {
        const dbname = draft?.value_json?.dbname || draft?.key || "database";
        lines.push(`${draft?.key || dbname} (${dbname})`);
      });
    }
    if (createDrafts.length || updateDrafts.length) {
      lines.push("Schema and relation metadata will be refreshed when saved.");
    } else {
      lines.push("No DB profile changes were discovered.");
    }
    if (skipped.length) {
      lines.push(`Skipped ${skipped.length} existing profile${skipped.length === 1 ? "" : "s"}.`);
    }
    warnings.forEach((warning) => lines.push(`Warning: ${warning}`));
    return lines.join("\n");
  }

  function discoverDbCommitActions(drafts) {
    const actions = el("div", "mobile-message-actions");
    const button = el("button", "", `Save ${drafts.length} DB profile${drafts.length === 1 ? "" : "s"}`);
    button.type = "button";
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Saving...";
      try {
        const result = await Core.apiJson("/databases/discover/commit", {
          method: "POST",
          body: { drafts },
        });
        const created = Array.isArray(result?.created) ? result.created : [];
        const updated = Array.isArray(result?.updated) ? result.updated : [];
        const skipped = Array.isArray(result?.skipped_existing) ? result.skipped_existing : [];
        const errors = Array.isArray(result?.errors) ? result.errors : [];
        if (result?.status === "error") {
          throw new Error(errors.join("\n") || result?.error || "Database profile save failed.");
        }
        addMessage(
          "assistant",
          "Database profiles saved",
          `Saved ${created.length}; refreshed ${updated.length}; skipped ${skipped.length}.`
        );
        await loadParameters();
        openParameters();
        button.textContent = "Saved";
      } catch (error) {
        button.disabled = false;
        button.textContent = `Save ${drafts.length} DB profile${drafts.length === 1 ? "" : "s"}`;
        addMessage("assistant", "Database profile save failed", shortStatusError(error));
      }
    });
    actions.append(button);
    return actions;
  }

  async function maybeDiscoverDbFromChat(prompt) {
    if (!looksLikeDiscoverDbPrompt(prompt)) {
      return false;
    }
    try {
      const payload = await Core.apiJson("/databases/discover", {
        method: "POST",
        body: { prompt },
      });
      if (payload?.status !== "preview") {
        const errors = Array.isArray(payload?.errors) ? payload.errors.join("\n") : "";
        addMessage("assistant", "Database discovery failed", errors || payload?.error || "Discovery failed.");
        setRunStatus("database discovery failed", "bad");
        return true;
      }
      const drafts = Array.isArray(payload.discovered) ? payload.discovered : [];
      addMessage("assistant", "Database discovery preview", discoverDbPreviewText(payload), {
        actions: drafts.length ? discoverDbCommitActions(drafts) : null,
      });
      setRunStatus("database discovery preview", "good");
      return true;
    } catch (error) {
      addMessage("assistant", "Database discovery failed", shortStatusError(error));
      setRunStatus("database discovery failed", "bad");
      return true;
    }
  }

  async function maybeCreateMonitorFromChat(prompt) {
    if (!looksLikeMonitorPrompt(prompt)) {
      return false;
    }
    try {
      const payload = await Core.apiJson("/monitors/draft", {
        method: "POST",
        body: {
          prompt,
          context: requestContext(),
          agent_mode: state.agentMode,
          llm_model: state.settings?.llm_model || "auto",
        },
      });
      const draftResponse = payload.draft || payload;
      if (!draftResponse.is_monitor_request) {
        return false;
      }
      const draft = Array.isArray(draftResponse.drafts) ? draftResponse.drafts[0] : null;
      const missing = Array.isArray(draftResponse.missing_details) ? draftResponse.missing_details : [];
      if (!draft || missing.length || !Core.compactText(draft.command)) {
        addMessage("assistant", "Monitor needs detail", `I need ${missing.join(", ") || "a command or condition"} before starting that monitor.`);
        setRunStatus("monitor needs detail", "warn");
        openMonitors();
        return true;
      }
      await createMobileMonitor(true, { ...draft, prompt });
      addMessage("assistant", "Monitor started", `${draft.title || "Monitor"} is running in a background terminal.`);
      setRunStatus("monitor started", "live");
      return true;
    } catch (error) {
      addMessage("assistant", "Monitor failed", shortStatusError(error));
      setRunStatus("monitor failed", "bad");
      return true;
    }
  }

  function secureOriginHint() {
    const host = window.location.hostname;
    const local = host === "localhost" || host === "127.0.0.1" || host === "::1";
    if (!window.isSecureContext && !local) {
      return "Microphone recording requires HTTPS or localhost in this browser.";
    }
    return "";
  }

  function audioServiceUnavailableReason(config = state.audioTranscriberConfig) {
    if (state.audioTranscriberError) {
      return `Audio service unavailable: ${state.audioTranscriberError}`;
    }
    if (!("MediaRecorder" in window)) {
      return "This browser does not support local audio recording.";
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      return secureOriginHint() || "This browser does not expose microphone recording.";
    }
    if (config?.enabled === false) {
      return "The backend audio transcriber is disabled.";
    }
    if (config?.service?.status === "unreachable") {
      return config.service?.error || "The audio runtime microservice is unreachable.";
    }
    for (const [key, label] of [
      ["binary", "whisper-cli is missing"],
      ["ffmpeg", "ffmpeg is missing"],
      ["ffprobe", "ffprobe is missing"],
      ["model", "Whisper model is missing"],
    ]) {
      const value = config?.[key];
      if (value && value.available === false) {
        return `${label}.`;
      }
    }
    if (config && config.available === false) {
      return "Audio transcription is not ready.";
    }
    if (!config) {
      return "Voice input is preparing.";
    }
    return "";
  }

  async function refreshAudioPermissionState() {
    if (!navigator.permissions?.query) {
      state.audioPermissionState = "unknown";
      state.audioPermissionWarningNotified = false;
      updateAudioVoiceControls();
      return state.audioPermissionState;
    }
    try {
      const status = await navigator.permissions.query({ name: "microphone" });
      state.audioPermissionState = status?.state || "unknown";
      state.audioPermissionWarningNotified = state.audioPermissionState === "denied"
        ? state.audioPermissionWarningNotified
        : false;
      status.onchange = () => {
        state.audioPermissionState = status.state || "unknown";
        state.audioPermissionWarningNotified = state.audioPermissionState === "denied"
          ? state.audioPermissionWarningNotified
          : false;
        updateAudioVoiceControls();
      };
    } catch (_error) {
      state.audioPermissionState = "unknown";
      state.audioPermissionWarningNotified = false;
    }
    updateAudioVoiceControls();
    return state.audioPermissionState;
  }

  function microphonePermissionMessage() {
    if (state.audioPermissionState === "denied") {
      return "Microphone permission is blocked. Open this page in browser site settings and allow microphone access, then try again.";
    }
    if (state.audioPermissionState === "prompt") {
      return "Allow microphone access when the browser asks.";
    }
    return "Allow microphone access to dictate locally.";
  }

  function notifyMicrophonePermissionBlocked() {
    if (!state.audioPermissionWarningNotified) {
      addSystemMessage("Voice input", microphonePermissionMessage());
      state.audioPermissionWarningNotified = true;
    }
  }

  async function refreshAudioTranscriberConfig() {
    try {
      const response = await fetch("/api/audio-transcriber/config", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(await Core.errorMessageFromResponse(response));
      }
      state.audioTranscriberConfig = await response.json();
      state.audioTranscriberError = "";
      updateAudioVoiceControls();
      return state.audioTranscriberConfig;
    } catch (error) {
      state.audioTranscriberConfig = null;
      state.audioTranscriberError = shortStatusError(error);
      updateAudioVoiceControls();
      return null;
    }
  }

  function preferredAudioMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    if (!("MediaRecorder" in window)) {
      return "";
    }
    return candidates.find((value) => MediaRecorder.isTypeSupported?.(value)) || "";
  }

  function audioCaptureConstraints() {
    return {
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    };
  }

  function normalizeAudioSilenceTimeoutSeconds(value) {
    const parsed = Number.parseFloat(value);
    const seconds = Number.isFinite(parsed) ? parsed : AUDIO_DEFAULT_SILENCE_TIMEOUT_SECONDS;
    return Math.min(
      AUDIO_MAX_SILENCE_TIMEOUT_SECONDS,
      Math.max(AUDIO_MIN_SILENCE_TIMEOUT_SECONDS, seconds),
    );
  }

  function audioCaptureSilenceTimeoutMs() {
    const timeoutSeconds = normalizeAudioSilenceTimeoutSeconds(state.settings?.audio_silence_timeout_seconds);
    return Math.max(1000, Math.round(timeoutSeconds * 1000));
  }

  function audioCaptureSilenceWarningMs(silenceTimeoutMs) {
    return Math.min(3000, Math.max(900, Math.round(silenceTimeoutMs * 0.25)));
  }

  function audioContextCtor() {
    return window.AudioContext || window.webkitAudioContext || null;
  }

  function audioAmbientRmsFromSamples(samples, fallback) {
    const values = (Array.isArray(samples) ? samples : [])
      .map((value) => Number.parseFloat(value))
      .filter((value) => Number.isFinite(value) && value >= 0)
      .sort((left, right) => left - right);
    if (!values.length) {
      return Math.max(0, Number.parseFloat(fallback) || 0);
    }
    const index = Math.min(
      values.length - 1,
      Math.max(0, Math.floor((values.length - 1) * AUDIO_SILENCE_AMBIENT_PERCENTILE)),
    );
    return values[index];
  }

  function audioDynamicSpeechThreshold(baseThreshold, ambientRms) {
    const base = Math.max(0, Number.parseFloat(baseThreshold) || AUDIO_SILENCE_RMS_THRESHOLD);
    const ambient = Math.max(0, Number.parseFloat(ambientRms) || 0);
    return Math.max(
      base,
      ambient + AUDIO_SILENCE_SPEECH_MARGIN,
      ambient * AUDIO_SILENCE_SPEECH_MULTIPLIER,
    );
  }

  function audioDynamicRecoveryThreshold(baseThreshold, ambientRms) {
    const base = Math.max(0, Number.parseFloat(baseThreshold) || AUDIO_SILENCE_RMS_THRESHOLD);
    const ambient = Math.max(0, Number.parseFloat(ambientRms) || 0);
    return Math.max(
      base * 0.65,
      ambient + AUDIO_SILENCE_RECOVERY_MARGIN,
      ambient * AUDIO_SILENCE_RECOVERY_MULTIPLIER,
    );
  }

  function audioShouldSampleAmbientRms(rms, baseThreshold, ambientRms, calibrating) {
    if (calibrating) {
      return true;
    }
    const value = Math.max(0, Number.parseFloat(rms) || 0);
    const base = Math.max(0, Number.parseFloat(baseThreshold) || AUDIO_SILENCE_RMS_THRESHOLD);
    const ambient = Math.max(0, Number.parseFloat(ambientRms) || 0);
    return value <= Math.max(base * 2.2, ambient + AUDIO_SILENCE_AMBIENT_SAMPLE_MARGIN);
  }

  function setAudioSilenceWarningActive(active) {
    const nextActive = Boolean(active);
    if (state.audioSilenceWarningActive === nextActive) {
      return;
    }
    state.audioSilenceWarningActive = nextActive;
    updateAudioVoiceControls();
  }

  function stopMicSilenceDetection() {
    setAudioSilenceWarningActive(false);
    if (state.audioSilenceDetectorTimer) {
      window.clearInterval(state.audioSilenceDetectorTimer);
      state.audioSilenceDetectorTimer = null;
    }
    if (state.audioSilenceDetectorSource) {
      try {
        state.audioSilenceDetectorSource.disconnect();
      } catch (_error) {}
      state.audioSilenceDetectorSource = null;
    }
    state.audioSilenceDetectorAnalyser = null;
    state.audioSilenceDetectorBuffer = null;
    if (state.audioSilenceDetectorContext) {
      try {
        state.audioSilenceDetectorContext.close();
      } catch (_error) {}
      state.audioSilenceDetectorContext = null;
    }
  }

  function startMicSilenceDetection() {
    const stream = state.audioMediaStream;
    if (!stream) {
      return;
    }
    const AudioContextCtor = audioContextCtor();
    if (!AudioContextCtor) {
      return;
    }
    const silenceTimeoutMs = audioCaptureSilenceTimeoutMs();
    const baseThreshold = AUDIO_SILENCE_RMS_THRESHOLD;
    stopMicSilenceDetection();
    try {
      const context = new AudioContextCtor();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      source.connect(analyser);
      let aboveThresholdFrames = 0;
      let ambientRms = Math.max(0.002, baseThreshold * 0.5);
      let recoveryHoldUntilMs = 0;
      const ambientSamples = [];

      state.audioSilenceDetectorContext = context;
      state.audioSilenceDetectorSource = source;
      state.audioSilenceDetectorAnalyser = analyser;
      state.audioSilenceDetectorBuffer = buffer;
      state.audioSilenceDetectorLastSoundMs = Date.now();
      setAudioSilenceWarningActive(false);

      state.audioSilenceDetectorTimer = window.setInterval(() => {
        if (state.audioRecordingState !== "recording" || !state.audioSilenceDetectorAnalyser || !state.audioSilenceDetectorBuffer) {
          return;
        }
        try {
          state.audioSilenceDetectorAnalyser.getByteTimeDomainData(state.audioSilenceDetectorBuffer);
        } catch (_error) {
          return;
        }

        let energy = 0;
        for (const sample of state.audioSilenceDetectorBuffer) {
          const delta = (sample - 128) / 128;
          energy += delta * delta;
        }
        const rms = Math.sqrt(energy / state.audioSilenceDetectorBuffer.length);
        const now = Date.now();
        const calibrating = now - state.audioRecordingStartedAt < AUDIO_SILENCE_CALIBRATION_MS;
        if (audioShouldSampleAmbientRms(rms, baseThreshold, ambientRms, calibrating)) {
          ambientSamples.push(rms);
          while (ambientSamples.length > AUDIO_SILENCE_AMBIENT_SAMPLE_COUNT) {
            ambientSamples.shift();
          }
          ambientRms = audioAmbientRmsFromSamples(ambientSamples, ambientRms);
        }
        const speechThreshold = audioDynamicSpeechThreshold(baseThreshold, ambientRms);
        const recoveryThreshold = audioDynamicRecoveryThreshold(baseThreshold, ambientRms);
        const usingRecoveryThreshold = state.audioSilenceWarningActive || now < recoveryHoldUntilMs;
        const speechDetected = rms > (usingRecoveryThreshold ? recoveryThreshold : speechThreshold);
        const recoveringFromWarning = state.audioSilenceWarningActive && speechDetected;
        if (speechDetected && !calibrating) {
          aboveThresholdFrames += 1;
          if (recoveringFromWarning || aboveThresholdFrames >= 2) {
            state.audioSilenceDetectorLastSoundMs = now;
            if (usingRecoveryThreshold) {
              recoveryHoldUntilMs = now + AUDIO_SILENCE_RECOVERY_HOLD_MS;
            }
            setAudioSilenceWarningActive(false);
          }
          return;
        }
        aboveThresholdFrames = 0;
        if (calibrating) {
          state.audioSilenceDetectorLastSoundMs = now;
          return;
        }
        const silentDurationMs = now - state.audioSilenceDetectorLastSoundMs;
        if (silentDurationMs >= audioCaptureSilenceWarningMs(silenceTimeoutMs)) {
          setAudioSilenceWarningActive(true);
        }
        if (silentDurationMs > silenceTimeoutMs) {
          const timeoutSeconds = Math.max(1, Math.round(silenceTimeoutMs / 1000));
          addSystemMessage("Voice input", `Recording stopped after ${timeoutSeconds} seconds of silence.`);
          stopVoiceRecording();
        }
      }, AUDIO_SILENCE_CHECK_INTERVAL_MS);
    } catch (_error) {
      stopMicSilenceDetection();
    }
  }

  function stopAudioTracks() {
    stopMicSilenceDetection();
    if (state.audioMediaStream) {
      for (const track of state.audioMediaStream.getTracks()) {
        track.stop();
      }
    }
    state.audioMediaStream = null;
  }

  function insertTextAtCursor(input, text) {
    if (!input) {
      return;
    }
    const value = String(text || "").trim();
    if (!value) {
      return;
    }
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    const prefix = before && !/\s$/.test(before) ? " " : "";
    const suffix = after && !/^\s/.test(after) ? " " : "";
    input.value = `${before}${prefix}${value}${suffix}${after}`;
    const cursor = before.length + prefix.length + value.length + suffix.length;
    input.focus();
    input.setSelectionRange(cursor, cursor);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function updateAudioVoiceControls() {
    const recording = state.audioRecordingState === "recording";
    const transcribing = state.audioRecordingState === "transcribing";
    const stopping = state.audioRecordingState === "stopping";
    const busy = stopping || transcribing;
    const reason = audioServiceUnavailableReason();
    elements.micButton.dataset.state = state.audioRecordingState;
    elements.micButton.classList.toggle("is-recording", recording);
    elements.micButton.classList.toggle("is-transcribing", transcribing || stopping);
    elements.micButton.classList.toggle("is-silence-warning", recording && state.audioSilenceWarningActive);
    elements.micButton.disabled = busy || state.running;
    elements.micLoadingDots.hidden = !transcribing && !stopping;
    if (state.audioSilenceWarningActive && recording) {
      elements.micLabel.textContent = "Silent";
    } else if (recording) {
      elements.micLabel.textContent = "Stop";
    } else if (busy) {
      elements.micLabel.textContent = "Mic";
    } else if (state.audioPermissionState === "denied") {
      elements.micLabel.textContent = "Blocked";
    } else {
      elements.micLabel.textContent = "Mic";
    }
    const title = state.audioSilenceWarningActive && recording
      ? "Silence detected; recording will stop soon"
      : recording
        ? "Stop recording and transcribe"
        : state.audioPermissionState === "denied"
          ? microphonePermissionMessage()
          : reason && reason !== "Voice input is preparing."
            ? reason
            : "Dictate prompt locally";
    elements.micButton.title = title;
    elements.micButton.setAttribute("aria-label", title);
    if (!state.running) {
      elements.sendButton.disabled = busy;
    }
  }

  async function startVoiceRecording() {
    if (state.audioRecordingState !== "idle") {
      return;
    }
    if (state.running) {
      showToast("Stop the current run before dictating.");
      return;
    }
    if (!state.audioTranscriberConfig || state.audioTranscriberError) {
      await refreshAudioTranscriberConfig();
    }
    const unavailable = audioServiceUnavailableReason();
    if (unavailable && unavailable !== "Voice input is preparing.") {
      addSystemMessage("Voice input", unavailable);
      updateAudioVoiceControls();
      return;
    }
    if (state.audioPermissionState === "denied") {
      notifyMicrophonePermissionBlocked();
      updateAudioVoiceControls();
      return;
    }
    try {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia(audioCaptureConstraints());
      } catch (error) {
        if (error?.name === "OverconstrainedError" || error?.name === "ConstraintNotSatisfiedError") {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } else {
          throw error;
        }
      }
      state.audioPermissionState = "granted";
      state.audioPermissionWarningNotified = false;
      const mimeType = preferredAudioMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      state.audioMediaStream = stream;
      state.audioMediaRecorder = recorder;
      state.audioChunks = [];
      state.audioRecordingState = "recording";
      state.audioRecordingStartedAt = Date.now();
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) {
          state.audioChunks.push(event.data);
        }
      });
      recorder.addEventListener("stop", () => {
        void transcribeRecordedAudio();
      }, { once: true });
      recorder.start();
      const maxMs = Math.max(1, Number(state.audioTranscriberConfig?.max_duration_seconds || 120)) * 1000;
      window.clearTimeout(state.audioRecordingTimer);
      state.audioRecordingTimer = window.setTimeout(() => {
        if (state.audioRecordingState === "recording") {
          stopVoiceRecording();
        }
      }, maxMs);
      startMicSilenceDetection();
      setRunStatus("recording", "live");
      updateAudioVoiceControls();
    } catch (error) {
      stopAudioTracks();
      state.audioRecordingState = "idle";
      state.audioSubmitAfterTranscription = false;
      if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
        state.audioPermissionState = "denied";
        notifyMicrophonePermissionBlocked();
      } else {
        addSystemMessage("Voice input", `Microphone unavailable: ${shortStatusError(error)}`);
      }
      updateAudioVoiceControls();
    }
  }

  function stopVoiceRecording() {
    if (state.audioRecordingState !== "recording" || !state.audioMediaRecorder) {
      return;
    }
    state.audioRecordingState = "stopping";
    setAudioSilenceWarningActive(false);
    window.clearTimeout(state.audioRecordingTimer);
    state.audioRecordingTimer = null;
    try {
      state.audioMediaRecorder.stop();
    } catch (error) {
      state.audioRecordingState = "idle";
      state.audioSubmitAfterTranscription = false;
      stopAudioTracks();
      addSystemMessage("Voice input", `Could not stop recording: ${shortStatusError(error)}`);
    }
    stopMicSilenceDetection();
    updateAudioVoiceControls();
  }

  async function transcribeRecordedAudio() {
    const chunks = state.audioChunks.slice();
    const type = state.audioMediaRecorder?.mimeType || preferredAudioMimeType() || "audio/webm";
    const shouldSubmitAfterTranscription = state.audioSubmitAfterTranscription === true;
    state.audioRecordingState = "transcribing";
    state.audioMediaRecorder = null;
    stopAudioTracks();
    updateAudioVoiceControls();
    setRunStatus("transcribing", "live");
    try {
      if (!chunks.length) {
        addSystemMessage("Voice input", "No audio was recorded.");
      } else {
        const blob = new Blob(chunks, { type });
        const response = await fetch("/api/audio-transcriber/transcribe", {
          method: "POST",
          headers: { "Content-Type": blob.type || "audio/webm" },
          body: blob,
        });
        if (!response.ok) {
          throw new Error(await Core.errorMessageFromResponse(response));
        }
        const payload = await response.json();
        const transcript = String(payload?.transcript || "").trim();
        if (!transcript) {
          addSystemMessage("Voice input", "No speech was detected in the recording.");
        } else {
          insertTextAtCursor(elements.promptInput, transcript);
        }
      }
    } catch (error) {
      addSystemMessage("Voice input", `Transcription failed: ${shortStatusError(error)}`);
    } finally {
      stopMicSilenceDetection();
      state.audioChunks = [];
      state.audioSubmitAfterTranscription = false;
      state.audioRecordingState = "idle";
      setRunStatus("idle", "neutral");
      updateAudioVoiceControls();
      if (shouldSubmitAfterTranscription && elements.promptInput.value.trim() && !state.running) {
        window.setTimeout(() => {
          if (state.running || state.audioRecordingState !== "idle") {
            return;
          }
          if (typeof elements.chatForm.requestSubmit === "function") {
            elements.chatForm.requestSubmit(elements.sendButton);
          } else {
            elements.sendButton.click();
          }
        }, 0);
      }
    }
  }

  function handleVoiceInputButtonClick(event) {
    event?.preventDefault?.();
    if (state.audioRecordingState === "recording") {
      stopVoiceRecording();
      return;
    }
    if (state.audioRecordingState === "idle") {
      void startVoiceRecording();
    }
  }

  function handleVoiceRecordingSubmitIntent() {
    if (!["recording", "stopping", "transcribing"].includes(state.audioRecordingState)) {
      return false;
    }
    state.audioSubmitAfterTranscription = true;
    if (state.audioRecordingState === "recording") {
      stopVoiceRecording();
    }
    return true;
  }

  function bindStaticControls() {
    elements.chatForm.addEventListener("submit", handleChatSubmit);
    elements.promptInput.addEventListener("input", (event) => {
      resizePromptInput();
      if (
        event?.inputType === "insertFromPaste" ||
        event?.inputType === "insertFromDrop" ||
        event?.inputType === "insertReplacementText"
      ) {
        closeMobileParameterShortcutMenu();
        return;
      }
      state.promptParameterShortcutActiveIndex = 0;
      updateMobileParameterShortcutMenu();
    });
    elements.promptInput.addEventListener("keydown", (event) => {
      if (
        mobileParameterShortcutMenuIsOpen()
        && !event.ctrlKey
        && !event.altKey
        && !event.metaKey
        && !event.isComposing
      ) {
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
          event.preventDefault();
          moveMobileParameterShortcutSelection(event.key === "ArrowUp" ? -1 : 1);
          return;
        }
        if (event.key === "Tab" || (event.key === "Enter" && state.promptParameterShortcutSelectionArmed)) {
          event.preventDefault();
          insertActiveMobileParameterShortcut();
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          closeMobileParameterShortcutMenu();
          return;
        }
      }
      if (moveMobilePromptCaretToEntryEdge(event)) {
        updateMobileParameterShortcutMenu({ allowOpen: mobileParameterShortcutMenuIsOpen() });
        return;
      }
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        elements.chatForm.requestSubmit();
      }
    });
    elements.promptInput.addEventListener("click", () => {
      updateMobileParameterShortcutMenu({ allowOpen: mobileParameterShortcutMenuIsOpen() });
    });
    elements.promptInput.addEventListener("keyup", (event) => {
      if (
        event.key === "ArrowLeft" ||
        event.key === "ArrowRight" ||
        event.key === "Home" ||
        event.key === "End"
      ) {
        updateMobileParameterShortcutMenu({ allowOpen: mobileParameterShortcutMenuIsOpen() });
      }
    });
    elements.promptInput.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (!elements.parameterShortcutMenu?.contains(document.activeElement)) {
          closeMobileParameterShortcutMenu();
        }
      }, 0);
    });
    elements.stopButton.addEventListener("click", stopCurrentRun);
    elements.newChatButton.addEventListener("click", newChat);
    elements.micButton.addEventListener("click", handleVoiceInputButtonClick);
    elements.terminalButton?.addEventListener("click", openMobileTerminal);
    elements.gatewaySelect?.addEventListener("change", () => setSelectedGatewayFromPicker(elements.gatewaySelect.value));
    elements.notificationButton.addEventListener("click", () => {
      void loadNotifications();
      openNotifications();
    });
    elements.tasksButton?.addEventListener("click", () => {
      void loadTasks();
      openTasks();
    });
    elements.monitorsButton?.addEventListener("click", () => {
      void loadMonitors();
      openMonitors();
    });
    elements.parametersButton?.addEventListener("click", () => {
      void loadParameters();
      openParameters();
    });
    elements.notificationCloseButton.addEventListener("click", closeNotifications);
    elements.taskCloseButton?.addEventListener("click", closeTasks);
    elements.monitorCloseButton?.addEventListener("click", closeMonitors);
    elements.parameterCloseButton?.addEventListener("click", closeParameters);
    elements.backdrop.addEventListener("click", () => {
      closeNotifications();
      closeTasks();
      closeMonitors();
      closeParameters();
    });
    elements.notificationRefreshButton.addEventListener("click", loadNotifications);
    elements.notificationMarkReadButton.addEventListener("click", markAllNotificationsRead);
    elements.taskRefreshButton?.addEventListener("click", loadTasks);
    elements.parameterRefreshButton?.addEventListener("click", loadParameters);
    elements.parameterSearch?.addEventListener("input", () => {
      void loadParameters();
    });
    elements.parameterNewButton?.addEventListener("click", clearParameterEditor);
    elements.parameterSaveButton?.addEventListener("click", () => {
      void saveParameter().catch((error) => reportError(error, "Unable to save parameter."));
    });
    elements.parameterRevealButton?.addEventListener("click", () => {
      void revealParameter().catch((error) => reportError(error, "Unable to reveal parameter."));
    });
    elements.parameterDeleteButton?.addEventListener("click", () => {
      void deleteParameter().catch((error) => reportError(error, "Unable to delete parameter."));
    });
    elements.taskCreateButton?.addEventListener("click", () => {
      void createMobileTask(false);
    });
    elements.taskCreateStartButton?.addEventListener("click", () => {
      void createMobileTask(true);
    });
    elements.monitorRefreshButton?.addEventListener("click", loadMonitors);
    elements.monitorCreateButton?.addEventListener("click", () => {
      void createMobileMonitor(false);
    });
    elements.monitorCreateStartButton?.addEventListener("click", () => {
      void createMobileMonitor(true);
    });
    elements.taskList?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("button[data-task-action]");
      if (!button) return;
      const item = button.closest("[data-task-id]");
      void handleTaskAction(item?.dataset?.taskId || "", button.dataset.taskAction || "");
    });
    elements.monitorList?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("button[data-monitor-action]");
      if (!button) return;
      const item = button.closest("[data-monitor-id]");
      void handleMonitorAction(item?.dataset?.monitorId || "", button.dataset.monitorAction || "");
    });
    elements.browserNotificationsEnabled?.addEventListener("change", () => {
      state.settings = Core.saveAgentSettings({
        ...state.settings,
        browser_notifications_enabled: elements.browserNotificationsEnabled.checked,
      });
      renderNotificationDeliveryControls();
      void persistMobileSettingsBackend();
      if (elements.browserNotificationsEnabled.checked) {
        void requestBrowserNotificationPermission();
      }
    });
    elements.browserNotificationsPermission?.addEventListener("click", () => {
      void requestBrowserNotificationPermission();
    });
    elements.notificationSoundSelect?.addEventListener("change", () => {
      const value = elements.notificationSoundSelect.value;
      state.settings = Core.saveAgentSettings({
        ...state.settings,
        notification_sound_enabled: value !== "off",
        notification_sound_variant: value === "off" ? state.settings.notification_sound_variant : value,
      });
      renderNotificationDeliveryControls();
      void persistMobileSettingsBackend();
    });
    elements.notificationSoundTest?.addEventListener("click", () => {
      unlockNotificationAudio();
      playNotificationSound({ level: "info" });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeNotifications();
        closeTasks();
      }
    });
    window.addEventListener("pointerdown", unlockNotificationAudio, { once: true, passive: true });
    window.addEventListener("keydown", unlockNotificationAudio, { once: true });
  }

  async function startMobileApp() {
    updateAgentName();
    applyMobileChatPopAnimation();
    bindThemePicker();
    bindStaticControls();
    resizePromptInput();
    setRunStatus("idle", "neutral");
    renderNotificationDeliveryControls();
    updateAudioVoiceControls();
    await loadMobileSharedSettings();
    await Promise.allSettled([
      checkBackendConnection(),
      loadGateways(),
      loadTasks(),
      loadMonitors(),
      loadNotifications(),
      refreshAudioTranscriberConfig(),
      refreshAudioPermissionState(),
    ]);
    document.documentElement.classList.remove("mobile-app-booting");
    window.setInterval(checkBackendConnection, 5000);
    window.setInterval(loadGateways, 15000);
    window.setInterval(loadTasks, 15000);
    window.setInterval(loadMonitors, 15000);
    window.setInterval(loadNotifications, 20000);
    state.settingsSyncTimer = window.setInterval(loadMobileSharedSettings, 5000);
    window.addEventListener("focus", () => {
      void loadMobileSharedSettings();
      void loadGateways();
      void loadTasks();
      void loadMonitors();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        void loadMobileSharedSettings();
        void loadGateways();
        void loadTasks();
        void loadMonitors();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    void startMobileApp();
  });

  window.OpenFabricAgentMobile = {
    closeNotifications,
    handleChatSubmit,
    handleVoiceInputButtonClick,
    loadGateways,
    loadMonitors,
    loadTasks,
    loadNotifications,
    openNotifications,
    openMonitors,
    playNotificationSound,
    requestBrowserNotificationPermission,
    showBrowserNotification,
    startMobileTraceStream,
    startVoiceRecording,
    stopVoiceRecording,
  };
})();
