const state = {
  catalog: null,
  documents: [],
  groups: [],
  activeDocId: "manual-home",
  activeKind: "",
  activeTags: new Set(),
  query: "",
  searchTimer: null,
};

const elements = {
  root: document.documentElement,
  navToggle: document.querySelector("#nav-toggle"),
  search: document.querySelector("#manual-search"),
  themeSelect: document.querySelector("#theme-select"),
  kindFilters: document.querySelector("#kind-filters"),
  tagFilters: document.querySelector("#tag-filters"),
  documentNav: document.querySelector("#document-nav"),
  article: document.querySelector("#manual-article"),
  searchResults: document.querySelector("#search-results"),
  docMeta: document.querySelector("#doc-meta"),
  outline: document.querySelector("#outline-nav"),
  related: document.querySelector("#related-list"),
};

const themeStorageKey = "openfabric.agentUi.theme";
const settingsStorageKey = "openfabric.agentUi.settings";

function compactText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function allowedThemeNames() {
  return new Set(
    Array.from(elements.themeSelect?.querySelectorAll("option[value]") || [])
      .map((option) => compactText(option.value))
      .filter(Boolean),
  );
}

function normalizeThemeName(value, fallback = "github") {
  const allowed = allowedThemeNames();
  const candidate = compactText(value);
  if (allowed.has(candidate)) {
    return candidate;
  }
  const safeFallback = compactText(fallback);
  return allowed.has(safeFallback) ? safeFallback : "github";
}

function readSettingsTheme() {
  try {
    const settings = JSON.parse(localStorage.getItem(settingsStorageKey) || "{}");
    return compactText(settings?.ui_theme);
  } catch {
    return "";
  }
}

function storedThemePreference() {
  const allowed = allowedThemeNames();
  try {
    const savedTheme = compactText(localStorage.getItem(themeStorageKey));
    if (allowed.has(savedTheme)) {
      return savedTheme;
    }
  } catch {
    // Storage may be disabled; the manual can still use the theme selected for this page load.
  }
  const settingsTheme = readSettingsTheme();
  return allowed.has(settingsTheme) ? settingsTheme : "";
}

function writeThemePreference(theme) {
  try {
    localStorage.setItem(themeStorageKey, theme);
  } catch {
    // Storage may be disabled; the live theme still applies for this page.
  }
  try {
    const settings = JSON.parse(localStorage.getItem(settingsStorageKey) || "{}");
    const nextSettings = settings && typeof settings === "object" && !Array.isArray(settings)
      ? { ...settings, ui_theme: theme }
      : { ui_theme: theme };
    localStorage.setItem(settingsStorageKey, JSON.stringify(nextSettings));
  } catch {
    try {
      localStorage.setItem(settingsStorageKey, JSON.stringify({ ui_theme: theme }));
    } catch {
      // Storage may be disabled; the live theme still applies for this page.
    }
  }
}

function hashParams() {
  const raw = window.location.hash.replace(/^#/, "");
  return new URLSearchParams(raw);
}

function writeHash(docId, headingId = "") {
  const params = new URLSearchParams();
  params.set("doc", docId);
  if (headingId) {
    params.set("heading", headingId);
  }
  history.replaceState(null, "", `#${params.toString()}`);
}

async function apiJson(url) {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function applyTheme(theme, persist = true) {
  const safeTheme = normalizeThemeName(theme);
  elements.root.dataset.theme = safeTheme;
  elements.themeSelect.value = safeTheme;
  if (persist) {
    writeThemePreference(safeTheme);
  }
  renderMermaid();
}

function renderKindFilters() {
  const groups = [{ kind: "", label: "All" }, ...state.groups.map((group) => ({ kind: group.kind, label: group.label }))];
  elements.kindFilters.replaceChildren(
    ...groups.map((group) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `filter-button${group.kind === state.activeKind ? " is-active" : ""}`;
      button.textContent = group.label;
      button.addEventListener("click", () => {
        state.activeKind = group.kind;
        renderKindFilters();
        renderDocumentNav();
        runSearch();
      });
      return button;
    }),
  );
}

function renderTagFilters() {
  const tags = (state.catalog?.tags || []).slice(0, 34);
  elements.tagFilters.replaceChildren(
    ...tags.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `tag-button${state.activeTags.has(item.tag) ? " is-active" : ""}`;
      button.textContent = `${item.tag} ${item.count}`;
      button.addEventListener("click", () => {
        if (state.activeTags.has(item.tag)) {
          state.activeTags.delete(item.tag);
        } else {
          state.activeTags.add(item.tag);
        }
        renderTagFilters();
        renderDocumentNav();
        runSearch();
      });
      return button;
    }),
  );
}

function docMatchesFilters(doc) {
  if (state.activeKind && doc.kind !== state.activeKind) {
    return false;
  }
  for (const tag of state.activeTags) {
    if (!doc.tags.includes(tag)) {
      return false;
    }
  }
  return true;
}

function renderDocumentNav() {
  const groupFragments = [];
  for (const group of state.groups) {
    const docs = group.documents.filter(docMatchesFilters);
    if (!docs.length) {
      continue;
    }
    const wrapper = document.createElement("section");
    wrapper.className = "nav-group";
    const title = document.createElement("div");
    title.className = "nav-group-title";
    title.textContent = group.label;
    wrapper.append(title);
    for (const doc of docs) {
      wrapper.append(renderDocButton(doc, "doc-link"));
    }
    groupFragments.push(wrapper);
  }
  if (!groupFragments.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No documents match.";
    groupFragments.push(empty);
  }
  elements.documentNav.replaceChildren(...groupFragments);
}

function renderDocButton(doc, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `${className}${doc.id === state.activeDocId ? " is-active" : ""}`;
  const title = document.createElement("span");
  title.className = className === "doc-link" ? "doc-link-title" : "";
  title.textContent = doc.title;
  const summary = document.createElement("span");
  summary.className = className === "doc-link" ? "doc-link-summary" : "";
  summary.textContent = doc.summary || doc.kind_label || "";
  button.append(title, summary);
  button.addEventListener("click", () => loadDocument(doc.id));
  return button;
}

function renderMeta(doc) {
  const title = document.createElement("div");
  title.className = "meta-title";
  title.textContent = doc.title;

  const grid = document.createElement("div");
  grid.className = "meta-grid";
  const items = [
    `${doc.kind_label || doc.kind}`,
    `${doc.reading_time_minutes || 1} min read`,
    doc.generated ? "generated" : "static",
    doc.source_type ? `source: ${doc.source_type}` : "",
  ].filter(Boolean);
  grid.replaceChildren(...items.map((item) => {
    const div = document.createElement("div");
    div.textContent = item;
    return div;
  }));

  const tags = document.createElement("div");
  tags.className = "meta-tags";
  tags.replaceChildren(...(doc.tags || []).map((tag) => {
    const span = document.createElement("span");
    span.className = "meta-tag";
    span.textContent = tag;
    return span;
  }));
  elements.docMeta.replaceChildren(title, grid, tags);
}

function renderOutline(doc) {
  const headings = (doc.headings || []).filter((heading) => heading.level <= 3);
  if (!headings.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No headings.";
    elements.outline.replaceChildren(empty);
    return;
  }
  elements.outline.replaceChildren(...headings.map((heading) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `outline-link outline-level-${heading.level}`;
    button.textContent = heading.title;
    button.addEventListener("click", () => {
      document.getElementById(heading.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
      writeHash(state.activeDocId, heading.id);
    });
    return button;
  }));
}

async function renderRelated(docId) {
  try {
    const payload = await apiJson(`/api/manual/related/${encodeURIComponent(docId)}`);
    const items = payload.results || [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No related documents.";
      elements.related.replaceChildren(empty);
      return;
    }
    elements.related.replaceChildren(...items.map((doc) => renderDocButton(doc, "related-link")));
  } catch {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Related documents unavailable.";
    elements.related.replaceChildren(empty);
  }
}

function cssVariable(name, fallback) {
  return compactText(getComputedStyle(document.documentElement).getPropertyValue(name), fallback);
}

function mermaidConfig() {
  const bg = cssVariable("--trace-panel", "#0f151d");
  const panel = cssVariable("--panel", "#111820");
  const text = cssVariable("--text", "#d6deea");
  const muted = cssVariable("--muted", "#8795a8");
  const border = cssVariable("--border", "#263241");
  return {
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    themeVariables: {
      background: bg,
      mainBkg: panel,
      primaryColor: panel,
      primaryTextColor: text,
      primaryBorderColor: border,
      secondaryColor: bg,
      tertiaryColor: panel,
      lineColor: muted,
      textColor: text,
      clusterBkg: bg,
      clusterBorder: border,
      clusterTextColor: text,
      titleColor: text,
      edgeLabelBackground: panel,
    },
    flowchart: { htmlLabels: true, curve: "basis" },
  };
}

async function renderMermaid() {
  if (!window.mermaid?.run) {
    return;
  }
  const diagrams = Array.from(document.querySelectorAll(".manual-article .mermaid"));
  if (!diagrams.length) {
    return;
  }
  for (const diagram of diagrams) {
    if (diagram.dataset.source) {
      diagram.textContent = diagram.dataset.source;
      diagram.removeAttribute("data-processed");
    } else {
      diagram.dataset.source = diagram.textContent;
    }
  }
  window.mermaid.initialize(mermaidConfig());
  try {
    await window.mermaid.run({ nodes: diagrams });
  } catch (error) {
    for (const diagram of diagrams) {
      diagram.textContent = diagram.dataset.source || diagram.textContent;
    }
  }
}

async function loadDocument(docId, headingId = "") {
  const safeDocId = compactText(docId, "manual-home");
  elements.article.innerHTML = '<div class="loading-state">Loading document.</div>';
  try {
    const doc = await apiJson(`/api/manual/document/${encodeURIComponent(safeDocId)}`);
    state.activeDocId = doc.id;
    elements.article.innerHTML = doc.html;
    renderMeta(doc);
    renderOutline(doc);
    renderDocumentNav();
    writeHash(doc.id, headingId);
    await renderRelated(doc.id);
    await renderMermaid();
    document.body.classList.remove("nav-open");
    const target = headingId ? document.getElementById(headingId) : null;
    if (target) {
      target.scrollIntoView({ block: "start" });
    } else {
      elements.article.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "auto" });
    }
  } catch (error) {
    elements.article.innerHTML = `<h1>Document unavailable</h1><p>${String(error.message || error)}</p>`;
  }
}

function tagsQuery() {
  return Array.from(state.activeTags).join(",");
}

async function runSearch() {
  const q = compactText(state.query);
  const hasFilters = Boolean(q || state.activeKind || state.activeTags.size);
  if (!hasFilters) {
    elements.searchResults.hidden = true;
    elements.searchResults.replaceChildren();
    return;
  }
  const params = new URLSearchParams();
  if (q) {
    params.set("q", q);
  }
  if (state.activeKind) {
    params.set("kind", state.activeKind);
  }
  if (state.activeTags.size) {
    params.set("tags", tagsQuery());
  }
  params.set("limit", "12");
  try {
    const payload = await apiJson(`/api/manual/search?${params.toString()}`);
    const title = document.createElement("h2");
    title.textContent = q ? `Search: ${q}` : "Filtered documents";
    const results = payload.results || [];
    const items = results.length
      ? results.map((doc) => renderDocButton(doc, "result-link"))
      : [Object.assign(document.createElement("div"), { className: "empty-state", textContent: "No matching documents." })];
    elements.searchResults.replaceChildren(title, ...items);
    elements.searchResults.hidden = false;
  } catch (error) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = `Search unavailable: ${String(error.message || error)}`;
    elements.searchResults.replaceChildren(empty);
    elements.searchResults.hidden = false;
  }
}

function scheduleSearch() {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(runSearch, 120);
}

async function init() {
  applyTheme(storedThemePreference() || elements.root.dataset.theme || "github", false);
  state.catalog = await apiJson("/api/manual/catalog");
  state.groups = state.catalog.groups || [];
  state.documents = state.catalog.documents || [];
  renderKindFilters();
  renderTagFilters();
  renderDocumentNav();
  const params = hashParams();
  await loadDocument(params.get("doc") || "manual-home", params.get("heading") || "");
}

elements.themeSelect.addEventListener("change", () => applyTheme(elements.themeSelect.value));
elements.search.addEventListener("input", () => {
  state.query = elements.search.value;
  scheduleSearch();
});
elements.navToggle.addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});
window.addEventListener("hashchange", () => {
  const params = hashParams();
  const docId = params.get("doc") || "manual-home";
  const headingId = params.get("heading") || "";
  if (docId !== state.activeDocId) {
    loadDocument(docId, headingId);
  } else if (headingId) {
    document.getElementById(headingId)?.scrollIntoView({ block: "start" });
  }
});

init().catch((error) => {
  elements.article.innerHTML = `<h1>Manual failed to load</h1><p>${String(error.message || error)}</p>`;
});
