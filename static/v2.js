(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const state = { user: null, recipes: [], session: null, profile: null, accountSettings: null, generationReady: false, currentHistoryId: null, draftWarnings: [], epoch: 0, sessionEpoch: 0, timerRefreshPending: false, timers: [], notifiedTimerIds: new Set(), timerNotices: [], timerToastTimer: null, active: false, requests: new Set() };
  const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value ?? ""; if (className) node.className = className; return node; };
  const button = (label, action, className = "button button-secondary button-small") => { const node = text("button", label, className); node.type = "button"; node.dataset.action = action; return node; };
  async function request(path, options = {}) {
    const epoch = state.epoch; const controller = new AbortController(); state.requests.add(controller);
    try {
      const response = await fetch(path, { credentials: "same-origin", ...options, signal: controller.signal, headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": $("meta[name='csrf-token']").content } : {}),
        ...options.headers,
      } });
      const data = await response.json().catch(() => ({}));
      if (!state.active || epoch !== state.epoch) throw new Error("Your sign-in session ended. Sign in again to continue.");
      if (!response.ok) { if (response.status === 401) { document.dispatchEvent(new Event("sous:unauthorized")); window.location.reload(); } throw new Error(data.error || `Request failed (${response.status}).`); }
      return data;
    } finally { state.requests.delete(controller); }
  }
  const send = (path, method, body) => request(path, { method, body: JSON.stringify(body) });
  function status(selector, message, error = false) { const node = $(selector); node.textContent = message; node.hidden = false; node.classList.toggle("error", error); }
  function lines(value) { return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
  function splitList(value) { return value.split(",").map((item) => item.trim()).filter(Boolean); }
  function recipeEnvelope(recipe, scope = "personal") { return { recipe, scope }; }
  function refreshCookingCatalog(recipeId) {
    return new Promise((resolve, reject) => {
      document.dispatchEvent(new CustomEvent("sous:recipes-changed", { detail: { recipeId, resolve, reject } }));
    });
  }

  async function loadRecipes() {
    status("#library-status", "Loading your recipes…");
    try { const data = await request("/api/recipes"); state.recipes = data.recipes || []; renderRecipes(); status("#library-status", `${state.recipes.length} recipes available to you.`); }
    catch (error) { status("#library-status", error.message, true); }
  }
  function renderRecipes() {
    const host = $("#recipe-library"); host.replaceChildren();
    const query = $("#recipe-search").value.trim().toLocaleLowerCase();
    const filtered = state.recipes.filter((recipe) => `${recipe.name} ${recipe.description} ${(recipe.ingredients || []).join(" ")}`.toLocaleLowerCase().includes(query));
    if (!filtered.length) { host.append(text("p", "No recipes match yet. Create one, or make a draft with AI.")); return; }
    for (const recipe of filtered) {
      const article = document.createElement("article"); article.className = "recipe-entry";
      const body = document.createElement("div"); body.append(text("h2", recipe.name), text("p", recipe.description || ""));
      body.append(text("p", `${(recipe.ingredients || []).length} ingredients · ${(recipe.steps || []).length} steps${recipe.servings ? ` · serves ${recipe.servings}` : ""}`, "recipe-entry-meta"));
      const scope = recipe.scope === "global" ? "Shared recipe" : "Your recipe";
      body.append(text("span", scope, "badge")); article.append(body);
      const actions = document.createElement("div"); actions.className = "recipe-entry-actions";
      if (recipe.scope === "global") actions.append(button("Copy to my recipes", `copy:${recipe.id}`));
      if (recipe.can_edit) { actions.append(button("Edit", `edit:${recipe.id}`)); actions.append(button("Delete", `delete:${recipe.id}`, "button button-ghost button-small")); }
      const servingField = document.createElement("label"); servingField.className = "recipe-scale-field"; servingField.append(text("span", "Servings"));
      const servingInput = document.createElement("input"); servingInput.type = "number"; servingInput.min = "1"; servingInput.max = "24"; servingInput.step = "1"; servingInput.value = recipe.servings || 2; servingInput.className = "recipe-scale-servings"; servingInput.setAttribute("aria-label", `Servings for ${recipe.name}`); servingField.append(servingInput); actions.append(servingField);
      actions.append(button("Scale servings", `scale:${recipe.id}`));
      const start = button("Cook this", `cook:${recipe.id}`, "button button-primary button-small"); actions.append(start);
      article.append(actions); host.append(article);
    }
  }
  function ingredientRow(value = "", details = {}) {
    const row = document.createElement("div"); row.className = "ingredient-row";
    const name = document.createElement("input"); name.className = "ingredient-name"; name.value = details.name || value || ""; name.maxLength = 240; name.placeholder = "Ingredient"; name.setAttribute("aria-label", "Ingredient");
    const quantity = document.createElement("input"); quantity.className = "ingredient-quantity"; quantity.type = "number"; quantity.step = "any"; quantity.min = "0"; quantity.value = details.quantity ?? ""; quantity.placeholder = "Amount"; quantity.setAttribute("aria-label", "Numeric amount (optional)");
    const unit = document.createElement("input"); unit.className = "ingredient-unit"; unit.maxLength = 40; unit.value = details.unit || ""; unit.placeholder = "Unit"; unit.setAttribute("aria-label", "Unit (optional)");
    const remove = button("Remove", "remove-row", "button button-ghost button-small");
    row.append(name, quantity, unit, remove); return row;
  }
  function stepRow(step = {}) {
    const row = document.createElement("fieldset"); row.className = "step-editor-row";
    const title = document.createElement("input"); title.className = "step-name"; title.maxLength = 120; title.value = step.title || ""; title.placeholder = "Step title"; title.setAttribute("aria-label", "Step title");
    const instruction = document.createElement("textarea"); instruction.className = "step-instruction-edit"; instruction.rows = 2; instruction.maxLength = 2000; instruction.value = step.instruction || ""; instruction.placeholder = "What should the cook do?"; instruction.setAttribute("aria-label", "Step instruction");
    const cue = document.createElement("input"); cue.className = "step-cue"; cue.maxLength = 500; cue.value = step.visual_cue || ""; cue.placeholder = "Visible cue when complete"; cue.setAttribute("aria-label", "Visual completion cue");
    row.append(title, instruction, cue, button("Remove step", "remove-row", "button button-ghost button-small")); return row;
  }
  function fillEditor(recipe = {}) {
    $("#recipe-editor").hidden = false; $("#generator-panel").hidden = true;
    $("#recipe-editor-title").textContent = recipe.id ? "Edit recipe" : "Create recipe";
    $("#edit-recipe-id").value = recipe.id || ""; $("#edit-recipe-name").value = recipe.name || "";
    $("#edit-recipe-description").value = recipe.description || ""; $("#edit-servings").value = recipe.servings || 2;
    $("#edit-equipment").value = (recipe.equipment || []).join(", "); $("#edit-global").checked = recipe.scope === "global";
    $("#global-recipe-choice").hidden = !state.user?.is_admin || Boolean(recipe.id);
    $("#ingredient-editor").replaceChildren();
    (recipe.ingredients || [""]).forEach((item, index) => $("#ingredient-editor").append(ingredientRow(item, recipe.ingredient_details?.[index] || {})));
    $("#step-editor").replaceChildren(); (recipe.steps || [{}, {}]).forEach((step) => $("#step-editor").append(stepRow(step)));
    $("#scale-recipe").hidden = !recipe.id; $("#recipe-form-error").hidden = true;
    $("#recipe-editor").scrollIntoView({ block: "start", behavior: "smooth" }); $("#edit-recipe-name").focus({ preventScroll: true });
  }
  function readEditor() {
    const ingredients = [...document.querySelectorAll(".ingredient-row")].map((row) => row.querySelector(".ingredient-name").value.trim()).filter(Boolean);
    const ingredient_details = [...document.querySelectorAll(".ingredient-row")].filter((row) => row.querySelector(".ingredient-name").value.trim()).map((row) => {
      const quantityText = row.querySelector(".ingredient-quantity").value;
      return { name: row.querySelector(".ingredient-name").value.trim(), quantity: quantityText === "" ? null : Number(quantityText), unit: row.querySelector(".ingredient-unit").value.trim() };
    });
    const steps = [...document.querySelectorAll(".step-editor-row")].map((row) => ({ title: row.querySelector(".step-name").value.trim(), instruction: row.querySelector(".step-instruction-edit").value.trim(), visual_cue: row.querySelector(".step-cue").value.trim() }));
    return { id: $("#edit-recipe-id").value || undefined, name: $("#edit-recipe-name").value.trim(), description: $("#edit-recipe-description").value.trim(), ingredients, ingredient_details, servings: Number($("#edit-servings").value), equipment: splitList($("#edit-equipment").value), steps };
  }
  async function saveRecipe(event) {
    event.preventDefault(); const recipe = readEditor(); const id = recipe.id; const scope = $("#edit-global").checked ? "global" : "personal"; delete recipe.id;
    try {
      const result = id ? await send(`/api/recipes/${encodeURIComponent(id)}`, "PUT", { recipe }) : await send("/api/recipes", "POST", recipeEnvelope(recipe, scope));
      const savedId = result.recipe?.id || id;
      $("#recipe-editor").hidden = true; status("#library-status", "Recipe saved."); await loadRecipes();
      try { await refreshCookingCatalog(savedId); }
      catch (error) { status("#library-status", `Recipe saved, but the cooking selector could not refresh: ${error.message}`, true); }
    } catch (error) { status("#recipe-form-error", error.message, true); $("#recipe-form-error").hidden = false; }
  }
  function renderWarnings(warnings = []) { const host = $("#draft-warning"); host.replaceChildren(); host.hidden = warnings.length === 0; warnings.forEach((item) => host.append(text("p", item))); }
  function updateGenerationAccess(settings = state.accountSettings) {
    state.accountSettings = settings || null;
    const vault = settings?.vault || {}; const bedrock = settings?.bedrock || {};
    const selectedBedrockModel = Boolean(bedrock.model || (settings?.defaults?.provider === "bedrock" && settings.defaults.model));
    state.generationReady = vault.locked === false && bedrock.has_credentials === true && selectedBedrockModel;
    const note = $("#generator-connection");
    note.textContent = state.generationReady
      ? `Ready to use your AWS Bedrock connection${bedrock.model ? ` · ${bedrock.model}` : ""}. The generated recipe stays a private draft until you save it.`
      : "Add your own AWS Bedrock connection, unlock its vault, and select a model in Settings before generating recipes.";
    $("#generator-form button[type=submit]").disabled = !state.generationReady;
  }
  async function generateRecipe(event) {
    event.preventDefault(); status("#generator-error", "Generating a draft with your saved AWS connection…");
    const profile = state.profile || {};
    const payload = { dish: $("#generate-dish").value.trim(), ingredients: lines($("#generate-ingredients").value), pantry: lines($("#generate-pantry").value), equipment: lines($("#generate-equipment").value), servings: Number($("#generate-servings").value), time_budget_minutes: Number($("#generate-time").value) };
    $("#generator-form button[type=submit]").disabled = true;
    try {
      const result = await send("/api/recipes/generate", "POST", payload); renderWarnings(result.warnings || []); const draft = { ...(result.recipe || {}) }; delete draft.id; delete draft.scope; fillEditor(draft); $("#edit-global").checked = false; state.draftWarnings = result.warnings || []; status("#generator-error", "Draft ready. Review and save it to your personal library before cooking."); $("#recipe-form-error").hidden = true;
    } catch (error) { status("#generator-error", error.message, true); }
    finally { updateGenerationAccess(); }
  }
  async function loadProfile() {
    try {
      state.profile = await request("/api/profile"); const profile = state.profile;
      $("#profile-pantry").value = (profile.pantry || []).join("\n"); $("#profile-equipment").value = (profile.equipment || []).join("\n");
      $("#profile-servings").value = profile.servings || 2; $("#profile-time").value = profile.time_budget_minutes || 30;
      $("#generate-pantry").value = (profile.pantry || []).join("\n"); $("#generate-equipment").value = (profile.equipment || []).join("\n");
      $("#generate-servings").value = profile.servings || 2; $("#generate-time").value = profile.time_budget_minutes || 30;
    } catch (error) { status("#profile-error", error.message, true); $("#profile-error").hidden = false; }
  }
  async function saveProfile(event) {
    event.preventDefault();
    const payload = { pantry: lines($("#profile-pantry").value), equipment: lines($("#profile-equipment").value), servings: Number($("#profile-servings").value), time_budget_minutes: Number($("#profile-time").value) };
    try { state.profile = await send("/api/profile", "PUT", payload); status("#profile-error", "Preferences saved."); }
    catch (error) { status("#profile-error", error.message, true); }
  }

  async function recipeAction(action, id) {
    const recipe = state.recipes.find((item) => String(item.id) === id); if (!recipe) return;
    if (action === "edit") { fillEditor(recipe); return; }
    if (action === "copy") { try { const result = await send(`/api/recipes/${encodeURIComponent(id)}/copy`, "POST", {}); await loadRecipes(); await refreshCookingCatalog(result.recipe?.id); } catch (error) { status("#library-status", error.message, true); } return; }
    if (action === "delete") { if (!window.confirm(`Delete “${recipe.name}” from your library?`)) return; try { await request(`/api/recipes/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "X-CSRF-Token": $("meta[name='csrf-token']").content } }); await loadRecipes(); await refreshCookingCatalog(); } catch (error) { status("#library-status", error.message, true); } return; }
    if (action === "scale") {
      const scaleButton = document.querySelector(`[data-action='scale:${CSS.escape(id)}']`);
      const servings = Number(scaleButton?.parentElement.querySelector(".recipe-scale-servings")?.value);
      if (!Number.isInteger(servings) || servings < 1 || servings > 24) { status("#library-status", "Choose between 1 and 24 servings.", true); return; }
      return scaleDraft(id, servings);
    }
    if (action === "cook") {
      try { await refreshCookingCatalog(id); }
      catch (error) { status("#library-status", `Could not refresh the cooking selector: ${error.message}`, true); return; }
      const select = $("#recipe-select");
      if (![...select.options].some((option) => option.value === id)) { status("#library-status", "This recipe is no longer available in your cooking library.", true); return; }
      select.value = id; await document.querySelector("#nav-cooking").click(); select.dispatchEvent(new Event("change")); return;
    }
  }
  async function scaleDraft(id, servings) {
    try {
      const result = await send(`/api/recipes/${encodeURIComponent(id)}/scale`, "POST", { servings });
      renderWarnings(result.warnings || []); fillEditor(result.recipe); status("#recipe-form-error", "Scaled draft. Review amounts and save it as a new personal recipe.");
      $("#recipe-form-error").hidden = false; $("#edit-recipe-id").value = ""; $("#edit-global").checked = false; $("#recipe-editor-title").textContent = "Scaled recipe draft";
      $("#scale-recipe").hidden = true;
    } catch (error) { status("#library-status", error.message, true); }
  }

  async function loadHistory() {
    status("#history-status", "Loading your sessions…");
    try { const result = await request("/api/history"); const sessions = result.sessions || []; const host = $("#history-list"); host.replaceChildren();
      if (!sessions.length) host.append(text("li", "Your cooking sessions will appear here after you finish or end one."));
      sessions.forEach((session) => { const li = document.createElement("li"); const open = button(`${session.recipe_name || session.recipe?.name || "Cooking session"} · ${new Date(session.updated_at * 1000).toLocaleString()}`, `history:${session.id}`); open.className = "history-entry-button"; li.append(open); host.append(li); });
      status("#history-status", `${sessions.length} sessions in your history.`);
    } catch (error) { status("#history-status", error.message, true); }
  }
  async function loadHistoryDetail(id) {
    try { const result = await request(`/api/history/${encodeURIComponent(id)}`); const session = result.session; state.currentHistoryId = id; $("#history-detail").hidden = false; $("#history-recipe-name").textContent = session.recipe?.name || "Saved cooking session"; $("#history-date").textContent = new Date(session.updated_at * 1000).toLocaleString(); $("#history-note").value = session.note || ""; const events = $("#history-events"); events.replaceChildren(); (session.events || []).forEach((item) => events.append(text("li", item.message || item.kind))); $("#history-detail").scrollIntoView({ block: "nearest" }); }
    catch (error) { status("#history-status", error.message, true); }
  }
  async function saveHistoryNote(event) {
    event.preventDefault(); if (!state.currentHistoryId) return;
    try { await send(`/api/history/${encodeURIComponent(state.currentHistoryId)}/note`, "PUT", { note: $("#history-note").value.slice(0, 4000) }); status("#history-note-status", "Note saved privately."); }
    catch (error) { status("#history-note-status", error.message, true); }
  }
  async function loadUsers() {
    if (!state.user?.is_admin) return;
    try { const result = await request("/api/users"); const host = $("#user-list"); host.replaceChildren(); (result.users || []).forEach((user) => { const row = document.createElement("li"); row.append(text("span", `${user.username}${user.is_admin ? " · administrator" : ""}`)); if (user.id !== state.user.id) { const reset = document.createElement("form"); reset.className = "user-reset-form"; const password = document.createElement("input"); password.type = "password"; password.required = true; password.minLength = 12; password.maxLength = 1024; password.autocomplete = "new-password"; password.setAttribute("aria-label", `New password for ${user.username}`); const submit = text("button", "Reset password", "button button-ghost button-small"); submit.type = "submit"; reset.append(password, submit); reset.addEventListener("submit", async (event) => { event.preventDefault(); try { await send(`/api/users/${encodeURIComponent(user.id)}/password`, "PUT", { password: password.value }); password.value = ""; showStatus("Password reset. Their AWS vault is locked until they sign in again."); } catch (error) { showStatus(error.message, true); } }); row.append(reset); } host.append(row); }); }
    catch (error) { status("#user-create-error", error.message, true); }
  }
  function showStatus(message, error = false) { const node = $("#user-create-error"); node.textContent = message; node.hidden = false; node.classList.toggle("error", error); }

  async function loadSessionTools(session) {
    const isNewSession = state.session?.id !== session?.id;
    state.session = session;
    if (!session?.id) return;
    if (isNewSession) {
      state.timers = []; state.notifiedTimerIds.clear(); state.timerNotices = [];
      $("#named-timer-notice").textContent = ""; $("#named-timer-notice").hidden = true;
    }
    const sessionId = session.id; const accountEpoch = state.epoch; const sessionEpoch = ++state.sessionEpoch;
    const isCurrent = () => state.active && state.epoch === accountEpoch && state.sessionEpoch === sessionEpoch && state.session?.id === sessionId;
    if (Array.isArray(session.timers)) renderTimers(session.timers);
    else {
      try { const result = await fetchTimers(sessionId); if (isCurrent() && result) renderTimers(result); }
      catch (error) { if (isCurrent()) status("#prep-status", error.message, true); }
    }
    if (Array.isArray(session.checklist)) renderChecklist(session.checklist);
    else {
      try { const result = await request(`/api/sessions/${encodeURIComponent(sessionId)}/checklist`); if (isCurrent()) renderChecklist(result.checklist || []); }
      catch (error) { if (isCurrent()) status("#prep-status", error.message, true); }
    }
    if (!isCurrent()) return;
    renderHealth(session); renderAlerts(session.alerts || []);
  }
  function renderTimers(timers) {
    state.timers = timers || [];
    const host = $("#named-timer-list"); host.replaceChildren(); const newlyExpired = [];
    for (const timer of timers) {
      const li = document.createElement("li");
      const terminal = timer.status === "acknowledged" || Boolean(timer.acknowledged_at) ? "acknowledged" : timer.status === "cancelled" ? "cancelled" : null;
      const deadline = timer.deadline_at;
      const expired = !terminal && (timer.status === "elapsed" || (deadline && deadline * 1000 <= Date.now()));
      const labelText = terminal === "acknowledged" ? "Acknowledged" : terminal === "cancelled" ? "Cancelled" : expired ? "Time is up" : countdown(deadline);
      const label = text("span", `${timer.name || "Timer"} · ${labelText}`, expired ? "timer-finished" : terminal ? "timer-terminal" : "timer-running");
      li.append(label);
      if (expired && !state.notifiedTimerIds.has(String(timer.id))) { state.notifiedTimerIds.add(String(timer.id)); newlyExpired.push(timer); }
      if (!terminal) li.append(button("Acknowledge", `ack-timer:${timer.id}`));
      if (!terminal && timer.status === "running" && !expired) li.append(button("Cancel", `cancel-timer:${timer.id}`, "button button-ghost button-small"));
      host.append(li);
    }
    if (newlyExpired.length) {
      state.timerNotices.push(...newlyExpired.map((timer) => `${timer.name || "Timer"} is up.`));
      const message = state.timerNotices.join(" ");
      const notice = $("#named-timer-notice"); notice.textContent = message; notice.hidden = false;
      if ($("#session-panel").hidden) {
        const toast = $("#toast"); toast.textContent = message; toast.className = "toast"; toast.hidden = false;
        clearTimeout(state.timerToastTimer);
        state.timerToastTimer = setTimeout(() => { toast.hidden = true; }, 5200);
      }
    }
  }
  function countdown(deadline) { if (!deadline) return "Running"; const seconds = Math.max(0, Math.ceil(deadline - Date.now() / 1000)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} remaining`; }
  async function fetchTimers(sessionId) {
    if (state.timerRefreshPending) return null;
    const accountEpoch = state.epoch; const sessionEpoch = state.sessionEpoch;
    state.timerRefreshPending = true;
    try {
      const result = await request(`/api/sessions/${encodeURIComponent(sessionId)}/timers`);
      if (!state.active || state.epoch !== accountEpoch || state.sessionEpoch !== sessionEpoch || state.session?.id !== sessionId) return null;
      return result.timers || [];
    } finally { state.timerRefreshPending = false; }
  }
  async function refreshTimers() {
    if (!state.active || !state.session?.id || document.hidden) return;
    const sessionId = state.session.id;
    try { const timers = await fetchTimers(sessionId); if (timers) renderTimers(timers); } catch { /* next refresh or explicit action reports errors */ }
  }
  async function addTimer(event) {
    event.preventDefault(); if (!state.session?.id) return;
    const minutes = Number($("#named-timer-minutes").value); const seconds = Math.round(minutes * 60);
    if (!Number.isFinite(seconds) || seconds < 1 || seconds > 86400) { status("#prep-status", "Choose a timer from 1 second to 24 hours.", true); return; }
      try { await send(`/api/sessions/${encodeURIComponent(state.session.id)}/timers`, "POST", { name: $("#named-timer-name").value.trim() || "Timer", duration_seconds: seconds }); $("#named-timer-name").value = ""; $("#named-timer-minutes").value = ""; await refreshTimers(); }
    catch (error) { status("#prep-status", error.message, true); }
  }
  async function timerAction(action, id) {
    if (!state.session?.id) return; try {
      await send(`/api/sessions/${encodeURIComponent(state.session.id)}/timers/${encodeURIComponent(id)}/${action}`, "POST", {});
      state.timers = state.timers.map((timer) => String(timer.id) === String(id) ? { ...timer, status: action === "ack" ? "acknowledged" : "cancelled", acknowledged_at: action === "ack" ? (timer.acknowledged_at || Date.now() / 1000) : timer.acknowledged_at } : timer);
      renderTimers(state.timers); await refreshTimers();
    }
    catch (error) { status("#prep-status", error.message, true); }
  }
  function renderChecklist(items) {
    const host = $("#prep-checklist"); host.replaceChildren();
    if (!items.length) { host.append(text("li", "No prep list is available for this session.")); return; }
    items.forEach((item) => { const li = document.createElement("li"); const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.checked = Boolean(item.checked); input.dataset.itemId = item.id; const span = text("span", item.label); span.className = item.checked ? "is-checked" : ""; label.append(input, span); li.append(label, text("small", item.kind || "")); host.append(li); });
  }
  async function saveChecklist() {
    if (!state.session?.id) return; const items = [...document.querySelectorAll("#prep-checklist input[type=checkbox]")].map((input) => ({ id: input.dataset.itemId, checked: input.checked }));
    try { const result = await send(`/api/sessions/${encodeURIComponent(state.session.id)}/checklist`, "PUT", { items }); renderChecklist(result.checklist || []); }
    catch (error) { status("#prep-status", error.message, true); }
  }
  function renderHealth(session) {
    const node = $("#monitoring-health"); const health = session.monitoring_health || {};
    let key = health.state;
    if (document.querySelector("#observation-panel[aria-busy='true']")) key = "pending";
    else if (!session.monitoring) key = "paused";
    else if (!document.querySelector("#camera-indicator.on")) key = "camera_off";
    else if (health.state) key = health.state;
    else if (health.last_success_at || session.latest_observation?.created_at) {
      const at = health.last_success_at || session.latest_observation.created_at; const staleAfter = (window.sousSampleInterval || 10) * 3;
      key = Date.now() / 1000 - at > staleAfter ? "stale" : "recent";
    } else key = "unknown";
    const labels = { paused: "Guidance is paused.", camera_off: "Camera is off; no current view is available.", pending: "Checking a camera snapshot…", recent: "Recent camera check.", stale: "The last camera check is getting old.", error: "The last camera check failed. Manual controls are ready.", obscured: "The camera view may be blocked.", unknown: "No recent camera check." };
    const at = health.last_success_at || session.latest_observation?.created_at;
    node.textContent = `${labels[key] || labels.unknown}${at ? ` Last successful look: ${new Date(at * 1000).toLocaleTimeString()}.` : ""} Camera snapshots are intermittent; they cannot confirm food safety or detect every emergency.`;
    node.dataset.state = key || "unknown";
  }
  function renderAlerts(alerts) {
    const host = $("#advisory-alerts"); host.replaceChildren();
    for (const alert of alerts.filter((item) => !item.acknowledged_at)) {
      const article = document.createElement("article"); article.className = `advisory-alert ${alert.category === "possible_emergency" ? "urgent" : "caution"}`;
      article.append(text("strong", alert.category === "possible_emergency" ? "Possible emergency — check the scene now" : "Possible hazard — please check"));
      article.append(text("h3", alert.title || "Camera observation"), text("p", alert.evidence || "The camera model flagged something to review."));
      article.append(text("p", "This is an uncertain camera observation. Stay with the food and use your own judgment; Sous cannot contact emergency services."));
      article.append(button("Acknowledge alert", `ack-alert:${alert.id}`)); host.append(article);
    }
  }
  async function acknowledgeAlert(id) {
    if (!state.session?.id) return; try { const result = await send(`/api/sessions/${encodeURIComponent(state.session.id)}/alerts/${encodeURIComponent(id)}/ack`, "POST", {}); state.session = result; renderAlerts(result.alerts || []); }
    catch (error) { status("#prep-status", error.message, true); }
  }
  async function action(action) {
    const [name, id] = action.split(":", 2);
    if (name === "edit" || name === "copy" || name === "delete" || name === "scale" || name === "cook") return recipeAction(name, id);
    if (name === "history") return loadHistoryDetail(id);
    if (name === "ack-timer") return timerAction("ack", id);
    if (name === "cancel-timer") return timerAction("cancel", id);
    if (name === "ack-alert") return acknowledgeAlert(id);
  }
  function bind() {
    $("#recipe-search").addEventListener("input", renderRecipes);
    $("#new-recipe-button").addEventListener("click", () => fillEditor());
    $("#open-generator").addEventListener("click", async () => { $("#generator-panel").hidden = false; $("#recipe-editor").hidden = true; updateGenerationAccess(); if (!state.profile) await loadProfile(); $("#generator-panel").scrollIntoView({ block: "start", behavior: "smooth" }); $("#generate-dish").focus({ preventScroll: true }); });
    $("#close-generator").addEventListener("click", () => { $("#generator-panel").hidden = true; $("#generator-form").reset(); });
    $("#close-recipe-editor").addEventListener("click", () => { $("#recipe-editor").hidden = true; });
    $("#add-ingredient").addEventListener("click", () => $("#ingredient-editor").append(ingredientRow()));
    $("#add-recipe-step").addEventListener("click", () => $("#step-editor").append(stepRow()));
    $("#recipe-form").addEventListener("submit", saveRecipe);
    $("#generator-form").addEventListener("submit", generateRecipe);
    $("#profile-form").addEventListener("submit", saveProfile);
    $("#history-note-form").addEventListener("submit", saveHistoryNote);
    $("#named-timer-form").addEventListener("submit", addTimer);
    $("#recipe-library").addEventListener("click", (event) => { const node = event.target.closest("[data-action]"); if (node) action(node.dataset.action); });
    $("#history-list").addEventListener("click", (event) => { const node = event.target.closest("[data-action]"); if (node) action(node.dataset.action); });
    $("#named-timer-list").addEventListener("click", (event) => { const node = event.target.closest("[data-action]"); if (node) action(node.dataset.action); });
    $("#advisory-alerts").addEventListener("click", (event) => { const node = event.target.closest("[data-action]"); if (node) action(node.dataset.action); });
    [$("#ingredient-editor"), $("#step-editor")].forEach((host) => host.addEventListener("click", (event) => { const node = event.target.closest("[data-action='remove-row']"); if (node) node.closest(".ingredient-row, .step-editor-row")?.remove(); }));
    $("#prep-checklist").addEventListener("change", saveChecklist);
    $("#scale-recipe").addEventListener("click", () => { const servings = Number($("#edit-servings").value); const id = $("#edit-recipe-id").value; if (!id || !Number.isInteger(servings) || servings < 1 || servings > 24) return; scaleDraft(id, servings); });
    document.addEventListener("sous:ready", (event) => { state.active = true; state.user = event.detail.user; window.sousSampleInterval = event.detail.sampleInterval || 10; updateGenerationAccess(event.detail.settings); loadProfile(); loadUsers(); });
    document.addEventListener("sous:account-settings", (event) => updateGenerationAccess(event.detail.settings));
    document.addEventListener("sous:view", (event) => { if (event.detail.view === "recipes") loadRecipes(); if (event.detail.view === "history") loadHistory(); if (event.detail.view === "settings") loadProfile(); });
    document.addEventListener("sous:session", (event) => {
      const session = event.detail.session;
      if (!session?.id) {
        if (!state.user) return;
        state.sessionEpoch += 1; state.session = null; state.timers = []; state.notifiedTimerIds.clear(); state.timerNotices = [];
        clearTimeout(state.timerToastTimer); state.timerToastTimer = null;
        $("#named-timer-list").replaceChildren(); $("#named-timer-notice").textContent = ""; $("#named-timer-notice").hidden = true;
        $("#prep-checklist").replaceChildren(); $("#advisory-alerts").replaceChildren();
        return;
      }
      state.active = true;
      if (!state.session || state.session.id !== session.id) loadSessionTools(session);
      else { state.session = session; if (Array.isArray(session.timers)) renderTimers(session.timers); if (Array.isArray(session.checklist)) renderChecklist(session.checklist); renderHealth(session); renderAlerts(session.alerts || []); }
    });
    document.addEventListener("sous:users-changed", loadUsers);
    document.addEventListener("sous:unauthorized", () => {
      state.active = false; state.epoch += 1; state.sessionEpoch += 1; state.timers = []; state.notifiedTimerIds.clear(); state.timerNotices = []; clearTimeout(state.timerToastTimer); state.timerToastTimer = null;
      state.user = null; state.recipes = []; state.session = null; state.profile = null; state.accountSettings = null; state.generationReady = false; state.currentHistoryId = null; state.draftWarnings = [];
      state.requests.forEach((controller) => controller.abort());
      ["#setup-panel", "#session-panel", "#completion-panel", "#settings-panel", "#recipes-panel", "#history-panel", "#auth-panel"].forEach((selector) => { $(selector).hidden = true; });
      $("#app-nav").hidden = true; $("#account-controls").hidden = true; $("#account-name").textContent = "";
      $("#recipe-library").replaceChildren(); $("#user-list").replaceChildren(); $("#history-list").replaceChildren(); $("#history-events").replaceChildren(); $("#history-detail").hidden = true;
      $("#recipe-peek").replaceChildren(); $("#ingredient-list").replaceChildren(); $("#recipe-steps").replaceChildren(); $("#event-list").replaceChildren(); $("#voice-transcript").replaceChildren();
      $("#named-timer-list").replaceChildren(); $("#named-timer-notice").textContent = ""; $("#named-timer-notice").hidden = true;
      $("#prep-checklist").replaceChildren(); $("#advisory-alerts").replaceChildren(); $("#monitoring-health").textContent = "";
      $("#recipe-editor").hidden = true; $("#generator-panel").hidden = true; $("#draft-warning").replaceChildren();
      ["#library-status", "#history-status", "#history-note-status", "#recipe-form-error", "#generator-error", "#profile-error", "#password-error", "#user-create-error", "#prep-status", "#vault-status", "#bedrock-status", "#bedrock-source", "#voice-preference-summary", "#guidance-summary", "#session-recipe-name", "#session-title", "#step-title", "#step-instruction", "#visual-cue-text", "#recipe-progress-label", "#observation-caption", "#camera-message", "#voice-message"].forEach((selector) => { $(selector).textContent = ""; });
      ["#recipe-search", "#edit-recipe-id", "#edit-recipe-name", "#edit-recipe-description", "#edit-servings", "#edit-equipment", "#generate-dish", "#generate-ingredients", "#generate-pantry", "#generate-equipment", "#generate-servings", "#generate-time", "#profile-pantry", "#profile-equipment", "#profile-servings", "#profile-time", "#history-note", "#bedrock-region", "#bedrock-model", "#default-model"].forEach((selector) => { $(selector).value = ""; });
      $("#ingredient-editor").replaceChildren(); $("#step-editor").replaceChildren();
      ["#generator-form", "#profile-form", "#history-note-form", "#recipe-form", "#login-form", "#admin-setup-form", "#password-form", "#user-create-form"].forEach((selector) => $(selector).reset());
      $("#bedrock-access-key").value = ""; $("#bedrock-secret-key").value = ""; $("#bedrock-session-token").value = "";
    });
    setInterval(() => { refreshTimers(); if (state.session) renderHealth(state.session); }, 1000);
  }
  bind();
})();
