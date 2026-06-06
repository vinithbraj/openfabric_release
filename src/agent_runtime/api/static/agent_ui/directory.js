(() => {
  "use strict";

  const Core = window.OpenFabricAgentUi || {};
  const state = {
    pages: [],
    routeGroups: [],
    manualPort: "8013",
    websitePort: "8014",
    query: "",
  };

  const elements = {
    root: document.documentElement,
    boot: document.querySelector("#app-boot-screen"),
    status: document.querySelector("#directory-status"),
    count: document.querySelector("#directory-count"),
    search: document.querySelector("#directory-search"),
    pages: document.querySelector("#directory-page-list"),
    routeGroups: document.querySelector("#directory-route-groups"),
    manualLink: document.querySelector("#directory-manual-link"),
  };

  function compactText(value, fallback = "") {
    if (typeof Core.compactText === "function") {
      return Core.compactText(value, fallback);
    }
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text || fallback;
  }

  function setStatus(message, status = "loading") {
    if (!elements.status) return;
    elements.status.dataset.state = status;
    elements.status.textContent = message;
  }

  function finishBoot() {
    elements.root.classList.remove("app-booting");
    elements.boot?.remove();
  }

  async function loadDirectory() {
    if (typeof Core.apiJson === "function") {
      return Core.apiJson("/directory");
    }
    const response = await fetch("/api/agent/directory", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  function manualUrl(docId = "manual-home") {
    const safeDoc = compactText(docId, "manual-home");
    if (typeof Core.manualUrl === "function") {
      return Core.manualUrl(safeDoc, state.manualPort);
    }
    const hostname = window.location.hostname || "127.0.0.1";
    const targetHost = hostname === "0.0.0.0" || hostname === "::" ? "127.0.0.1" : hostname;
    const url = new URL(`http://${targetHost}:${state.manualPort || "8013"}/manual`);
    url.hash = `doc=${encodeURIComponent(safeDoc)}`;
    return url.toString();
  }

  function websiteUrl(path = "/website") {
    const safePath = compactText(path, "/website");
    const hostname = window.location.hostname || "127.0.0.1";
    const targetHost = hostname === "0.0.0.0" || hostname === "::" ? "127.0.0.1" : hostname;
    return new URL(`http://${targetHost}:${state.websitePort || "8014"}${safePath}`).toString();
  }

  function pathHref(page) {
    const path = compactText(page?.path, "/");
    if (path === "/manual") {
      return manualUrl("manual-home");
    }
    if (path === "/website") {
      return websiteUrl("/website");
    }
    if (page?.external === true) {
      return manualUrl("manual-home");
    }
    return path;
  }

  function routeHref(route) {
    const path = compactText(route?.path, "/");
    return path.replace(/\{([^}]+)\}/g, "$1");
  }

  function methodPill(method) {
    const pill = document.createElement("span");
    pill.className = "directory-pill";
    pill.textContent = compactText(method, "GET");
    return pill;
  }

  function manualPill(link) {
    const anchor = document.createElement("a");
    anchor.className = "directory-manual-pill";
    anchor.href = manualUrl(link?.doc_id || "manual-home");
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.textContent = compactText(link?.title, "Manual");
    return anchor;
  }

  function searchablePage(page) {
    return [
      page?.title,
      page?.path,
      page?.summary,
      ...(page?.manual_links || []).map((link) => link.title),
    ].join(" ").toLowerCase();
  }

  function searchableRoute(route) {
    return [
      route?.title,
      route?.path,
      route?.summary,
      route?.group_label,
      route?.endpoint,
      ...(route?.methods || []),
    ].join(" ").toLowerCase();
  }

  function matchesQuery(text) {
    const query = state.query.trim().toLowerCase();
    return !query || text.includes(query);
  }

  function renderPages() {
    if (!elements.pages) return;
    const pages = state.pages.filter((page) => matchesQuery(searchablePage(page)));
    if (!pages.length) {
      const empty = document.createElement("p");
      empty.className = "directory-empty";
      empty.textContent = "No primary paths match.";
      elements.pages.replaceChildren(empty);
      return;
    }
    const cards = pages.map((page) => {
      const card = document.createElement("article");
      card.className = "directory-card";

      const header = document.createElement("div");
      header.className = "directory-card-header";

      const titleWrap = document.createElement("div");
      titleWrap.className = "directory-card-title-wrap";
      const title = document.createElement("h3");
      title.className = "directory-card-title";
      title.textContent = compactText(page.title, page.path);
      const path = document.createElement("a");
      path.className = "directory-card-path";
      path.href = pathHref(page);
      path.textContent = compactText(page.path, "/");
      if (page.external === true) {
        path.target = "_blank";
        path.rel = "noopener noreferrer";
      }
      titleWrap.append(title, path);

      const meta = document.createElement("div");
      meta.className = "directory-card-meta";
      const methods = page.external === true ? ["External"] : page.methods || [];
      for (const method of methods.length ? methods : ["GET"]) {
        meta.append(methodPill(method));
      }
      if (page.route_present === false && page.external !== true) {
        meta.append(methodPill("Unavailable"));
      }
      header.append(titleWrap, meta);

      const summary = document.createElement("p");
      summary.className = "directory-card-summary";
      summary.textContent = compactText(page.summary, "Runtime path.");

      const manualLinks = document.createElement("div");
      manualLinks.className = "directory-manual-links";
      for (const link of page.manual_links || []) {
        manualLinks.append(manualPill(link));
      }

      card.append(header, summary, manualLinks);
      return card;
    });
    elements.pages.replaceChildren(...cards);
  }

  function renderRoutes() {
    if (!elements.routeGroups) return;
    const renderedGroups = [];
    for (const group of state.routeGroups) {
      const routes = (group.routes || []).filter((route) => matchesQuery(searchableRoute(route)));
      if (!routes.length) continue;

      const details = document.createElement("details");
      details.className = "directory-route-group";
      details.open = group.group !== "api";

      const summary = document.createElement("summary");
      const title = document.createElement("h3");
      title.className = "directory-route-group-title";
      title.textContent = compactText(group.label, "Routes");
      const count = document.createElement("span");
      count.className = "directory-route-group-count";
      count.textContent = `${routes.length} path${routes.length === 1 ? "" : "s"}`;
      summary.append(title, count);

      const list = document.createElement("div");
      list.className = "directory-route-list";
      for (const route of routes) {
        list.append(renderRouteRow(route));
      }

      details.append(summary, list);
      renderedGroups.push(details);
    }

    if (!renderedGroups.length) {
      const empty = document.createElement("p");
      empty.className = "directory-empty";
      empty.textContent = "No routes match.";
      elements.routeGroups.replaceChildren(empty);
      return;
    }
    elements.routeGroups.replaceChildren(...renderedGroups);
  }

  function renderRouteRow(route) {
    const row = document.createElement("article");
    row.className = "directory-route-row";

    const path = document.createElement("a");
    path.className = "directory-route-path";
    path.href = routeHref(route);
    path.textContent = compactText(route.path, "/");

    const body = document.createElement("div");
    body.className = "directory-route-body";
    const title = document.createElement("p");
    title.className = "directory-route-title";
    title.textContent = compactText(route.title, route.path);
    const summary = document.createElement("p");
    summary.className = "directory-route-summary";
    summary.textContent = compactText(route.summary, "Runtime path.");
    const meta = document.createElement("div");
    meta.className = "directory-route-meta";
    for (const method of route.methods?.length ? route.methods : ["GET"]) {
      meta.append(methodPill(method));
    }
    if (route.endpoint) {
      const endpoint = methodPill(route.endpoint);
      endpoint.title = "FastAPI endpoint name";
      meta.append(endpoint);
    }
    for (const link of route.manual_links || []) {
      meta.append(manualPill(link));
    }
    body.append(title, summary, meta);
    row.append(path, body);
    return row;
  }

  function renderAll() {
    renderPages();
    renderRoutes();
    const totalRoutes = state.routeGroups.reduce((sum, group) => sum + (group.routes || []).length, 0);
    if (elements.count) {
      elements.count.textContent = `${state.pages.length} pages, ${totalRoutes} routes`;
    }
  }

  function updateManualLinks() {
    if (elements.manualLink) {
      elements.manualLink.href = manualUrl("manual-home");
    }
    if (typeof Core.hydrateManualLinks === "function") {
      Core.hydrateManualLinks(document);
    }
  }

  elements.search?.addEventListener("input", () => {
    state.query = elements.search.value || "";
    renderAll();
  });

  loadDirectory()
    .then((payload) => {
      state.pages = Array.isArray(payload?.pages) ? payload.pages : [];
      state.routeGroups = Array.isArray(payload?.route_groups) ? payload.route_groups : [];
      state.manualPort = compactText(payload?.manual?.port, "8013");
      state.websitePort = compactText(payload?.website?.port, "8014");
      updateManualLinks();
      renderAll();
      setStatus("Directory ready", "ready");
    })
    .catch((error) => {
      setStatus(`Directory unavailable: ${error.message}`, "error");
    })
    .finally(finishBoot);
})();
