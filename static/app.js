(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const START_LABEL = 'Start cooking <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>';
  const state = {
    config: null,
    settings: null,
    settingsView: false,
    discoveredModels: [],
    recipe: null,
    session: null,
    camera: null,
    cameraRequestId: 0,
    cameraRequestPending: false,
    captureTimer: null,
    uiGeneration: 0,
    observationInFlight: false,
    actionQueue: Promise.resolve(),
    timerLoop: null,
    listening: false,
    recognition: null,
    restartRecognition: false,
    lastSpokenAlert: "",
    toastTimer: null,
    restored: false,
    renderedStepKey: "",
    companion: null,
  };

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.method && options.method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
        ...options.headers,
      },
    });
    let data;
    try { data = await response.json(); } catch { data = {}; }
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  };

  const post = (path, body, options = {}) => api(path, { method: "POST", body: JSON.stringify(body), ...options });

  function setConnection(label, kind = "ready") {
    $("#connection-label").textContent = label;
    $("#connection-state").className = `connection-state ${kind}`;
  }

  function showToast(message, isError = false) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.className = `toast${isError ? " error" : ""}`;
    toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { toast.hidden = true; }, 5200);
  }

  function setCameraMessage(message = "", isError = false) {
    $("#camera-message").textContent = message;
    $("#camera-message").classList.toggle("error", isError);
  }

  function setVoiceMessage(message = "", isError = false) {
    $("#voice-message").textContent = message;
    $("#voice-message").classList.toggle("error", isError);
  }

  function fillSetup(config, applySavedDefaults = false) {
    const recipes = config.recipes || [];
    const providers = config.providers || [];
    const recipeSelect = $("#recipe-select");
    const previousRecipe = recipeSelect.value;
    recipeSelect.replaceChildren();
    recipes.forEach((recipe) => recipeSelect.add(new Option(recipe.name, recipe.id)));
    if (recipes.some((recipe) => recipe.id === previousRecipe)) recipeSelect.value = previousRecipe;
    const providerSelect = $("#provider-select");
    const previousProvider = providerSelect.value;
    providerSelect.replaceChildren();
    providers.forEach((provider) => {
      const option = new Option(provider.name, provider.id);
      option.disabled = !provider.configured;
      providerSelect.add(option);
    });
    const savedProvider = config.defaults?.provider;
    const configuredDefault = providers.find((provider) => provider.id === savedProvider && provider.configured);
    const desiredProvider = savedProvider
      ? providers.find((provider) => provider.id === savedProvider)
      : (configuredDefault || providers.find((provider) => provider.configured) || providers[0]);
    if (desiredProvider) providerSelect.value = desiredProvider.id;
    const providerChanged = previousProvider !== providerSelect.value;
    if (applySavedDefaults || providerChanged) {
      $("#model-input").dataset.touched = "";
      $("#model-input").dataset.provider = providerSelect.value;
      $("#model-input").value = "";
    }
    updateModelDefault(applySavedDefaults || providerChanged);
    updateRecipePeek();
    renderGuidanceSummary();
    updateStartAvailability();
  }

  function renderGuidanceSummary() {
    const provider = state.config?.providers?.find((item) => item.id === $("#provider-select").value);
    const model = $("#model-input").value.trim() || provider?.default_model || "model not selected";
    $("#guidance-summary").textContent = provider ? `${provider.name} · ${model}` : "Choose a configured model to guide this recipe.";
    const defaultProvider = state.config?.providers?.find((item) => item.id === state.config?.defaults?.provider);
    const unavailableDefault = Boolean(state.config?.defaults?.provider && !defaultProvider?.configured);
    $("#provider-locked-notice").hidden = !unavailableDefault;
    if (unavailableDefault) {
      $("#provider-locked-notice p").textContent = defaultProvider?.id === "bedrock" && defaultProvider.locked
        ? "Bedrock is the saved default, but its settings are locked. Unlock it to start a session."
        : `${defaultProvider?.name || "The saved provider"} is not configured. Configure it in Settings or choose a session override.`;
    }
  }

  function updateStartAvailability() {
    const provider = state.config?.providers?.find((item) => item.id === $("#provider-select").value);
    $("#start-session").disabled = !provider?.configured || !(state.config?.recipes || []).length;
  }

  function bedrockLockedForSession() {
    return state.session?.provider === "bedrock"
      && state.config?.providers?.find((provider) => provider.id === "bedrock")?.locked === true;
  }

  function updateRecipePeek() {
    const recipe = state.config?.recipes?.find((item) => item.id === $("#recipe-select").value);
    if (!recipe) return;
    const peek = $("#recipe-peek");
    peek.replaceChildren();
    const ingredients = Array.isArray(recipe.ingredients) ? recipe.ingredients : [];
    const steps = Array.isArray(recipe.steps) ? recipe.steps : [];
    const head = document.createElement("div");
    head.className = "peek-head";
    const title = document.createElement("h2");
    title.className = "peek-title";
    title.textContent = recipe.name;
    const meta = document.createElement("p");
    meta.className = "peek-meta";
    meta.textContent = `${countLabel(steps.length, "step")} · ${countLabel(ingredients.length, "ingredient")}`;
    head.append(title, meta);
    peek.append(head);
    if (recipe.description) {
      const description = document.createElement("p");
      description.className = "peek-description";
      description.textContent = recipe.description;
      peek.append(description);
    }
    const columns = document.createElement("div");
    columns.className = "peek-columns";
    columns.append(
      peekList("Ingredients", "ul", ingredients),
      peekList("Steps", "ol", steps.map((step) => step.title)),
    );
    peek.append(columns);
  }

  function peekList(label, tag, items) {
    const group = document.createElement("div");
    group.className = "peek-group";
    const heading = document.createElement("h3");
    heading.className = "list-label";
    heading.textContent = label;
    const list = document.createElement(tag);
    list.className = tag === "ol" ? "peek-steps" : "peek-ingredients";
    items.forEach((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      list.append(item);
    });
    group.append(heading, list);
    return group;
  }

  function countLabel(count, noun) {
    return `${count} ${noun}${count === 1 ? "" : "s"}`;
  }

  function updateModelDefault(force = false) {
    const provider = state.config?.providers?.find((item) => item.id === $("#provider-select").value);
    const input = $("#model-input");
    if (force || !input.dataset.touched || input.dataset.provider !== provider?.id || !input.value.trim()) {
      input.value = provider?.default_model || "";
      input.dataset.touched = "";
      input.dataset.provider = provider?.id || "";
    }
    renderGuidanceSummary();
    updateStartAvailability();
  }

  function setActiveNav(settingsView) {
    state.settingsView = settingsView;
    $("#nav-cooking").classList.toggle("active", !settingsView);
    $("#nav-settings").classList.toggle("active", settingsView);
    if (settingsView) $("#nav-settings").setAttribute("aria-current", "page");
    else $("#nav-settings").removeAttribute("aria-current");
    if (!settingsView) $("#nav-cooking").setAttribute("aria-current", "page");
    else $("#nav-cooking").removeAttribute("aria-current");
  }

  function clearSettingsSecrets() {
    ["#vault-init-passphrase", "#vault-confirm-passphrase", "#vault-unlock-passphrase",
      "#bedrock-access-key", "#bedrock-secret-key", "#bedrock-session-token"].forEach((selector) => {
      const input = $(selector);
      if (input) input.value = "";
    });
    $("#remove-session-token").checked = false;
  }

  function setFormError(selector, message = "") {
    const node = $(selector);
    node.textContent = message;
    node.hidden = !message;
  }

  function renderSettings() {
    const settings = state.settings;
    if (!settings) return;
    const initialized = Boolean(settings.vault.initialized);
    const unlocked = initialized && !settings.vault.locked;
    $("#vault-init-form").hidden = initialized;
    $("#vault-unlock-form").hidden = !initialized || unlocked;
    $("#vault-locked-controls").hidden = !unlocked;
    $("#vault-lock-button").disabled = !unlocked;
    $("#vault-status").textContent = !initialized
      ? "Not initialized. Create a passphrase to save Bedrock credentials."
      : unlocked ? "Unlocked for this server run." : "Locked. Unlock after a restart to use saved Bedrock settings.";

    const bedrock = settings.bedrock || {};
    const source = bedrock.source || "none";
    $("#bedrock-source").textContent = initialized && !unlocked ? "Vault locked" : source === "managed" ? "Saved in vault" : source === "environment" ? "Server environment" : "Not configured";
    $("#bedrock-status").textContent = initialized && !unlocked
      ? "Unlock to view Bedrock connection details/status."
      : bedrock.has_credentials === true
      ? `Saved connection${bedrock.region ? ` · ${bedrock.region}` : ""}${bedrock.has_session_token ? " · session token saved" : ""}`
      : source === "environment" ? "Using server environment credentials." : "No saved Bedrock credentials.";
    $("#bedrock-locked-callout").hidden = unlocked;
    $("#bedrock-locked-callout p").textContent = initialized
      ? "Unlock the vault to view or change the saved connection. The credential fields are never returned to this page."
      : "Create a vault passphrase before saving Bedrock credentials. The credential fields are never returned to this page.";
    $("#bedrock-form").querySelectorAll("input, button").forEach((node) => { node.disabled = !unlocked; });
    $("#bedrock-form").querySelectorAll("input[type=text]").forEach((node) => { node.readOnly = !unlocked; });
    $("#bedrock-region").value = bedrock.region || "";
    $("#bedrock-model").value = bedrock.model || "";
    $("#bedrock-access-key").value = "";
    $("#bedrock-secret-key").value = "";
    $("#bedrock-session-token").value = "";
    $("#remove-token-choice").hidden = bedrock.has_session_token !== true;
    $("#credential-replacement-note").hidden = bedrock.has_credentials !== true;
    $("#delete-bedrock").hidden = bedrock.has_credentials !== true || !unlocked;
    $("#model-discovery").hidden = !unlocked;
    const regionMatches = (bedrock.region || "") === $("#bedrock-region").value.trim();
    $("#discover-models").disabled = !unlocked || bedrock.has_credentials !== true || !regionMatches;
    $("#bedrock-model-picker").disabled = !unlocked || bedrock.has_credentials !== true || !regionMatches;
    $("#custom-model-field").hidden = !unlocked;
    if (!unlocked || !bedrock.has_credentials) resetModelPicker();

    const defaults = settings.defaults || {};
    const providerSelect = $("#default-provider");
    providerSelect.replaceChildren();
    (state.config?.providers || []).forEach((provider) => providerSelect.add(new Option(provider.name, provider.id)));
    const firstConfigured = (state.config?.providers || []).find((provider) => provider.configured);
    providerSelect.value = defaults.provider || firstConfigured?.id || "bedrock";
    const selectedDefault = state.config?.providers?.find((provider) => provider.id === providerSelect.value);
    $("#default-model").value = defaults.model || bedrock.model || selectedDefault?.default_model || "";
    $("#default-provider").disabled = !unlocked;
    $("#default-model").disabled = !unlocked;
    $("#save-defaults").disabled = !unlocked;
    $("#defaults-lock-help").hidden = unlocked;
    $("#sample-interval").value = String(settings.sample_interval ?? 10);
    $("#sample-interval").disabled = false;
    $("#save-interval").disabled = false;
    $("#voice-preference").value = settings.voice?.voice_id || "tiffany";
    const voiceReason = settings.voice?.reason || "";
    $("#voice-preference-summary").textContent = voiceReason
      ? `${settings.voice?.model || "Amazon Nova 2 Sonic"} is independent of the camera guidance model. ${voiceReason}`
      : `${settings.voice?.model || "Amazon Nova 2 Sonic"}, independent of the camera guidance model. Choose the voice used when you start the companion.`;
    clearSettingsSecrets();
    renderGuidanceSummary();
    updateStartAvailability();
  }

  function resetModelPicker(message = "Load models after saving a connection") {
    state.discoveredModels = [];
    const picker = $("#bedrock-model-picker");
    picker.replaceChildren(new Option(message, ""));
    $("#model-discovery-note").textContent = "Model listing is metadata only. It does not check whether your account can invoke a model.";
  }

  async function refreshSettingsState() {
    const [settings, config] = await Promise.all([api("/api/settings"), api("/api/config")]);
    const previousDefaults = JSON.stringify(state.config?.defaults || null);
    state.settings = settings;
    state.config = config;
    renderSettings();
    fillSetup(config, previousDefaults !== JSON.stringify(config.defaults || null));
  }

  async function showSettings() {
    if (state.settingsView) return;
    stopVoiceCompanion("Voice stopped because Settings was opened.");
    $("#nav-settings").disabled = true;
    if (state.session?.status === "active") {
      // Invalidate a pending permission prompt; a late stream will be stopped by enableCamera's generation guard.
      if (state.cameraRequestPending) {
        state.cameraRequestId += 1;
        state.cameraRequestPending = false;
      }
      clearInterval(state.captureTimer);
      state.captureTimer = null;
      $("#analyze-now").disabled = true;
      cancelObservationRequest();
      try {
        await runAction("pause", {}, { announce: false, silent: true });
      } catch (error) {
        showToast(error.message || "Pause guidance before opening Settings.", true);
        updateCaptureLoop();
        $("#camera-toggle").disabled = state.session?.status !== "active";
        $("#nav-settings").disabled = false;
        return;
      }
    }
    cancelObservationRequest();
    if (state.listening && state.recognition) {
      state.listening = false;
      state.restartRecognition = false;
      try { state.recognition.stop(); } catch { /* inactive */ }
      $("#voice-toggle").textContent = "Start listening";
      setVoiceMessage("Voice input is off while Settings is open.");
    }
    clearSettingsSecrets();
    // Keep the voice selection unavailable until the async settings refresh
    // has supplied the persisted voice_id; otherwise renderSettings can race
    // and overwrite a selection made just after opening Settings.
    $("#voice-preference").disabled = true;
    $("#save-voice-preference").disabled = true;
    $("#setup-panel").hidden = true;
    $("#session-panel").hidden = true;
    $("#completion-panel").hidden = true;
    $("#settings-panel").hidden = false;
    setActiveNav(true);
    $("#nav-settings").disabled = false;
    $("#settings-title").focus({ preventScroll: true });
    try {
      await refreshSettingsState();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      $("#voice-preference").disabled = false;
      $("#save-voice-preference").disabled = false;
    }
  }

  function showCooking() {
    if (!state.settingsView) return;
    clearSettingsSecrets();
    $("#settings-panel").hidden = true;
    if (state.session?.status === "active") {
      $("#session-panel").hidden = false;
      $("#setup-panel").hidden = true;
      renderSession();
    } else if (state.session?.status === "completed" || state.session?.status === "ended") {
      $("#completion-panel").hidden = false;
    } else {
      $("#setup-panel").hidden = false;
    }
    setActiveNav(false);
    $("#main").focus({ preventScroll: true });
  }

  async function saveBedrockConnection({ model, asDefault = false } = {}) {
    const region = $("#bedrock-region").value.trim();
    const accessKey = $("#bedrock-access-key").value.trim();
    const secretKey = $("#bedrock-secret-key").value;
    const token = $("#bedrock-session-token").value;
    if (Boolean(accessKey) !== Boolean(secretKey)) {
      setFormError("#bedrock-error", "Enter both the access key ID and secret access key to replace credentials.");
      return;
    }
    const body = { region, model: model ?? $("#bedrock-model").value.trim() };
    if (accessKey && secretKey) {
      body.access_key_id = accessKey;
      body.secret_access_key = secretKey;
    }
    if (token) body.session_token = token;
    else if ($("#remove-session-token").checked && !(accessKey && secretKey)) body.clear_session_token = true;
    setFormError("#bedrock-error");
    $("#save-bedrock").disabled = true;
    try {
      await api("/api/settings/bedrock", { method: "PUT", body: JSON.stringify(body) });
      clearSettingsSecrets();
      if (asDefault) {
        const chosenModel = body.model;
        await api("/api/settings/defaults", { method: "PUT", body: JSON.stringify({ provider: "bedrock", model: chosenModel }) });
      }
      await refreshSettingsState();
      setFormError("#bedrock-error", "Saved. Sous has not tested AWS access.");
      $("#bedrock-error").classList.remove("settings-error");
      $("#bedrock-error").classList.add("settings-success");
      setTimeout(() => $("#bedrock-error").classList.remove("settings-success"), 5000);
    } catch (error) {
      setFormError("#bedrock-error", error.message);
      $("#bedrock-error").classList.remove("settings-success");
    } finally {
      $("#save-bedrock").disabled = !state.settings || state.settings.vault.locked;
    }
  }

  async function discoverBedrockModels() {
    setFormError("#model-error");
    $("#discover-models").disabled = true;
    $("#discover-models").textContent = "Loading models…";
    try {
      const result = await api("/api/settings/bedrock/models", { headers: { "X-CSRF-Token": csrfToken } });
      state.discoveredModels = result.models || [];
      const picker = $("#bedrock-model-picker");
      picker.replaceChildren(new Option(state.discoveredModels.length ? "Choose a model" : "No models listed", ""));
      state.discoveredModels.forEach((model) => {
        const suffix = model.kind === "inference_profile" ? " · inference profile" : "";
        picker.add(new Option(`${model.name}${suffix}`, model.id));
      });
      picker.disabled = !state.discoveredModels.length;
      if (result.warning) $("#model-discovery-note").textContent = result.warning;
      else $("#model-discovery-note").textContent = "Model listing is metadata only. It does not check whether your account can invoke a model.";
    } catch (error) {
      resetModelPicker("Model list unavailable");
      $("#bedrock-model-picker").disabled = false;
      setFormError("#model-error", `${error.message} You can still enter a model or inference profile ID manually.`);
      $("#custom-model-field").hidden = false;
    } finally {
      $("#discover-models").textContent = "Load available models";
      $("#discover-models").disabled = state.settings?.vault?.locked || state.settings?.bedrock?.has_credentials !== true;
    }
  }

  function chooseDiscoveredModel() {
    const model = state.discoveredModels.find((item) => item.id === $("#bedrock-model-picker").value);
    if (!model) return;
    $("#bedrock-model").value = model.id;
    $("#custom-model-field").hidden = false;
  }

  async function saveDefaults(event) {
    event.preventDefault();
    setFormError("#defaults-error");
    $("#save-defaults").disabled = true;
    try {
      await api("/api/settings/defaults", { method: "PUT", body: JSON.stringify({
        provider: $("#default-provider").value,
        model: $("#default-model").value.trim(),
      }) });
      await refreshSettingsState();
      setFormError("#defaults-error", "Saved for new sessions.");
      $("#defaults-error").classList.remove("settings-error");
      $("#defaults-error").classList.add("settings-success");
      setTimeout(() => $("#defaults-error").classList.remove("settings-success"), 5000);
    } catch (error) {
      setFormError("#defaults-error", error.message);
    } finally {
      $("#save-defaults").disabled = state.settings?.vault?.locked !== false;
    }
  }

  async function saveSampleInterval(event) {
    event.preventDefault();
    const interval = Number($("#sample-interval").value);
    setFormError("#interval-error");
    if (!Number.isInteger(interval) || interval < 1 || interval > 15) {
      setFormError("#interval-error", "Choose a whole number from 1 to 15 seconds.");
      return;
    }
    $("#save-interval").disabled = true;
    try {
      const settings = await api("/api/settings/preferences", { method: "PUT", body: JSON.stringify({ sample_interval: interval }) });
      state.settings.sample_interval = settings.sample_interval;
      state.config.sample_interval = settings.sample_interval;
      updateCaptureLoop();
      setFormError("#interval-error", "Saved. New checks use this interval.");
      $("#interval-error").classList.remove("settings-error");
      $("#interval-error").classList.add("settings-success");
      setTimeout(() => $("#interval-error").classList.remove("settings-success"), 5000);
    } catch (error) {
      setFormError("#interval-error", error.message);
    } finally {
      $("#save-interval").disabled = false;
    }
  }

  async function saveVoicePreference(event) {
    event.preventDefault();
    const voiceId = $("#voice-preference").value;
    setFormError("#voice-preference-error");
    $("#save-voice-preference").disabled = true;
    try {
      const settings = await api("/api/settings/preferences", { method: "PUT", body: JSON.stringify({ voice_id: voiceId }) });
      state.settings.voice = settings.voice;
      $("#voice-preference").value = settings.voice?.voice_id || voiceId;
      setFormError("#voice-preference-error", "Saved for the next voice session.");
      $("#voice-preference-error").classList.remove("settings-error");
      $("#voice-preference-error").classList.add("settings-success");
      setTimeout(() => $("#voice-preference-error").classList.remove("settings-success"), 5000);
    } catch (error) {
      setFormError("#voice-preference-error", error.message);
    } finally {
      $("#save-voice-preference").disabled = false;
    }
  }

  async function initializeVault(event) {
    event.preventDefault();
    const passphrase = $("#vault-init-passphrase").value;
    const confirmation = $("#vault-confirm-passphrase").value;
    setFormError("#vault-init-error");
    if (passphrase.length < 12 || passphrase.length > 1024) {
      setFormError("#vault-init-error", "Use a passphrase between 12 and 1024 characters.");
      clearSettingsSecrets();
      return;
    }
    if (passphrase !== confirmation) {
      setFormError("#vault-init-error", "The passphrases do not match.");
      clearSettingsSecrets();
      return;
    }
    $("#vault-init-form button[type=submit]").disabled = true;
    try {
      await post("/api/settings/vault", { passphrase });
      await refreshSettingsState();
      setFormError("#vault-init-error");
    } catch (error) {
      setFormError("#vault-init-error", error.message);
    } finally {
      clearSettingsSecrets();
      $("#vault-init-form button[type=submit]").disabled = false;
    }
  }

  async function unlockVault(event) {
    event.preventDefault();
    const passphrase = $("#vault-unlock-passphrase").value;
    setFormError("#vault-unlock-error");
    $("#vault-unlock-form button[type=submit]").disabled = true;
    try {
      await post("/api/settings/unlock", { passphrase });
      await refreshSettingsState();
      setFormError("#vault-unlock-error");
    } catch (error) {
      setFormError("#vault-unlock-error", error.message);
    } finally {
      clearSettingsSecrets();
      $("#vault-unlock-form button[type=submit]").disabled = false;
    }
  }

  async function lockVault() {
    stopVoiceCompanion("Voice stopped because AWS settings were locked.");
    $("#vault-lock-button").disabled = true;
    try {
      await post("/api/settings/lock", {});
      clearSettingsSecrets();
      resetModelPicker("Unlock the vault to load models");
      await refreshSettingsState();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      $("#vault-lock-button").disabled = state.settings?.vault?.locked !== false;
    }
  }

  async function removeBedrockConnection() {
    setFormError("#bedrock-error");
    $("#delete-bedrock").disabled = true;
    try {
      await api("/api/settings/bedrock", { method: "DELETE" });
      clearSettingsSecrets();
      resetModelPicker();
      await refreshSettingsState();
    } catch (error) {
      setFormError("#bedrock-error", error.message);
    } finally {
      $("#delete-bedrock").disabled = state.settings?.bedrock?.has_credentials !== true;
    }
  }

  function setModelDefaultProvider() {
    const providerId = $("#default-provider").value;
    const provider = state.config?.providers?.find((item) => item.id === providerId);
    $("#default-model").value = providerId === "bedrock"
      ? ($("#bedrock-model").value.trim() || provider?.default_model || "")
      : (provider?.default_model || "");
  }

  async function boot() {
    try {
      const [config, settings] = await Promise.all([api("/api/config"), api("/api/settings")]);
      state.config = config;
      state.settings = settings;
      fillSetup(state.config);
      renderSettings();
      const { session } = await api("/api/sessions/active");
      $("#loading-panel").hidden = true;
      if (session) {
        state.session = session;
        state.recipe = session.recipe || null;
        state.restored = true;
        // Always invalidate any pre-refresh monitoring state before presenting recovery.
        const paused = await post(`/api/sessions/${session.id}/actions`, { action: "pause" });
        state.session = paused;
        state.recipe = paused.recipe || null;
        $("#setup-panel").hidden = true;
        $("#session-panel").hidden = false;
        $("#restore-notice").hidden = false;
        renderSession();
        setConnection("Session restored");
      } else {
        $("#setup-panel").hidden = false;
        setConnection("Ready to cook");
      }
      $("#app-nav").hidden = false;
      setActiveNav(false);
      state.timerLoop = setInterval(tickTimer, 250);
    } catch (error) {
      $("#loading-panel").hidden = true;
      setConnection("Connection problem", "problem");
      showToast(error.message || "Could not load Sous. Reload the page to try again.", true);
    }
  }

  function runAction(action, extra = {}, options = {}) {
    const operation = async () => {
      if (!state.session) throw new Error("There is no active cooking session.");
      if (action === "resume" && bedrockLockedForSession()) {
        const error = new Error("Unlock Bedrock settings before resuming camera guidance. Manual controls and timers are still available.");
        showToast(error.message, true);
        throw error;
      }
      if (action !== "timer_ack") {
        state.uiGeneration += 1;
        cancelObservationRequest();
      }
      try {
        const session = await post(`/api/sessions/${state.session.id}/actions`, { action, ...extra });
        acceptSession(session, { announce: options.announce !== false, source: action });
        return session;
      } catch (error) {
        if (error.status === 409) {
          await refreshSession();
          if (options.quietConflict) return state.session;
        }
        if (options.silent) throw error;
        showToast(error.message, true);
        throw error;
      }
    };
    state.actionQueue = state.actionQueue.then(operation, operation);
    return state.actionQueue;
  }

  async function refreshSession() {
    if (!state.session) return;
    const generation = ++state.uiGeneration;
    try {
      const session = await api(`/api/sessions/${state.session.id}`);
      if (generation === state.uiGeneration) acceptSession(session, { announce: false });
    } catch (error) {
      showToast(`Could not refresh session: ${error.message}`, true);
    }
  }

  function acceptSession(session, options = {}) {
    const previous = state.session;
    if (previous && (previous.id !== session.id || session.status !== "active")) {
      stopVoiceCompanion(session.status === "active" ? "Voice stopped because the cooking session changed." : "Voice stopped because the cooking session ended.");
    }
    if (previous && previous.id === session.id) {
      if (session.revision < previous.revision) return false;
      if (session.revision === previous.revision && session.updated_at < previous.updated_at) return false;
      if (previous.status !== "active" && session.status === "active") return false;
      if (session.updated_at === previous.updated_at && previous.timer_alerted && !session.timer_alerted) return false;
    }
    state.session = session;
    state.recipe = session.recipe || null;
    renderSession();
    if (previous && options.announce !== false) announceStateChange(previous, session, options.source);
    if (session.status !== "active") finishSessionUi(session.status);
    updateCaptureLoop();
    return true;
  }

  function announceStateChange(previous, current, source) {
    if (state.recipe && previous.step_index !== current.step_index) {
      const step = state.recipe.steps[current.step_index];
      if (current.mode === "assistive") speak(`${step.title}. ${step.instruction}`);
      else if (current.mode === "monitoring") speak(`Next step: ${step.title}.`);
    }
    if (state.recipe && previous.mode !== current.mode && current.mode === "assistive") {
      const step = state.recipe.steps[current.step_index];
      speak(`${step.title}. ${step.instruction}`);
    }
    if (source === "timer_ack" && !previous.timer_alerted && current.timer_alerted) {
      announceTimer();
    }
  }

  function renderSession() {
    const session = state.session;
    if (!session) return;
    state.recipe = session.recipe || null;
    const recipe = state.recipe;
    if (!recipe || !Array.isArray(recipe.steps) || recipe.steps.length === 0) {
      renderUnavailableRecipeSession(session);
      return;
    }
    $("#recipe-unavailable").hidden = true;
    $("#step-progress").hidden = false;
    $("#current-step").hidden = false;
    $("#recipe-panel").hidden = false;
    $("#observation-panel").hidden = false;
    $("#voice-panel").hidden = false;
    $("#correct-details").hidden = false;
    const lockedBedrock = bedrockLockedForSession();
    $("#monitoring-toggle").disabled = session.status !== "active" || lockedBedrock;
    $("#bedrock-session-locked-notice").hidden = !lockedBedrock;
    $("#camera-toggle").disabled = session.status !== "active" || state.cameraRequestPending;
    $("#camera-toggle").textContent = state.camera ? "Turn off camera" : "Turn on camera";
    $("#voice-toggle").disabled = false;
    $("#command-input").disabled = false;
    $("#command-form button[type=submit]").disabled = false;
    const step = recipe.steps[session.step_index];
    const last = session.step_index === recipe.steps.length - 1;
    $("#session-recipe-name").textContent = recipe.name;
    $("#session-title").textContent = session.status === "completed" ? "Cooking complete." : step.title;
    $("#mode-badge").textContent = session.mode === "assistive" ? "Talk me through it" : "Keep an eye out";
    const badge = $("#monitoring-badge");
    badge.classList.toggle("active", session.monitoring);
    badge.lastElementChild.textContent = session.monitoring ? "Guidance on" : "Guidance paused";
    $("#step-count").textContent = `Step ${session.step_index + 1} of ${recipe.steps.length}`;
    $("#step-number").textContent = String(session.step_index + 1).padStart(2, "0");
    $("#step-title").textContent = step.title;
    $("#step-instruction").textContent = step.instruction;
    $("#visual-cue-text").textContent = step.visual_cue;
    const stepKey = `${session.id}:${session.step_index}`;
    if (state.renderedStepKey && state.renderedStepKey !== stepKey) {
      const card = $("#current-step");
      card.classList.remove("step-changed");
      void card.offsetWidth;
      card.classList.add("step-changed");
    }
    state.renderedStepKey = stepKey;
    const fraction = (session.step_index + 1) / recipe.steps.length;
    $("#progress-fill").style.width = `${fraction * 100}%`;
    const progress = $("#progress-bar");
    progress.setAttribute("aria-valuemax", String(recipe.steps.length));
    progress.setAttribute("aria-valuenow", String(session.step_index + 1));
    $("#back-button").disabled = session.step_index === 0 || session.status !== "active";
    $("#next-button").hidden = last;
    $("#next-button").disabled = session.status !== "active";
    $("#finish-button").hidden = !last;
    $("#finish-button").disabled = session.status !== "active";
    $("#monitoring-toggle").textContent = session.monitoring ? "Pause guidance" : "Resume guidance";
    $("#restore-notice").hidden = !state.restored || session.monitoring || session.status !== "active";
    $("#restore-resume").disabled = session.monitoring || lockedBedrock;
    updateElapsedTime();
    $("#recipe-progress-label").textContent = `Step ${session.step_index + 1} of ${recipe.steps.length}`;
    renderRecipeLists();
    renderObservation(session.latest_observation);
    renderEvents(session.events || []);
    updateTimerDisplay();
    setConnection(session.status === "active" ? "Session saved" : "Session complete");
  }

  function renderUnavailableRecipeSession(session) {
    if (state.camera || state.cameraRequestPending) stopCamera();
    $("#recipe-unavailable").hidden = false;
    $("#step-progress").hidden = true;
    $("#current-step").hidden = true;
    $("#recipe-panel").hidden = true;
    $("#observation-panel").hidden = true;
    $("#voice-panel").hidden = true;
    $("#correct-details").hidden = true;
    $("#next-button").hidden = true;
    $("#finish-button").hidden = true;
    $("#back-button").disabled = true;
    $("#next-button").disabled = true;
    $("#finish-button").disabled = true;
    $("#monitoring-toggle").disabled = true;
    $("#restore-resume").disabled = true;
    $("#camera-toggle").disabled = true;
    $("#camera-toggle").textContent = "Unavailable";
    $("#analyze-now").disabled = true;
    $("#camera-caption").textContent = "Saved recipe unavailable";
    setCameraMessage("Visual guidance is disabled because this session has no saved recipe.");
    $("#session-title").textContent = "Recipe unavailable";
    $("#session-recipe-name").textContent = "Saved cooking session";
    $("#mode-badge").textContent = session.mode === "assistive" ? "Talk me through it" : "Keep an eye out";
    const badge = $("#monitoring-badge");
    badge.classList.remove("active");
    badge.lastElementChild.textContent = "Guidance unavailable";
    $("#restore-notice").hidden = true;
    $("#bedrock-session-locked-notice").hidden = !bedrockLockedForSession();
    renderEvents(session.events || []);
    updateTimerDisplay();
    setConnection("Recipe unavailable", "problem");
  }

  function renderRecipeLists() {
    const recipe = state.recipe;
    const ingredients = $("#ingredient-list");
    ingredients.replaceChildren();
    (recipe.ingredients || []).forEach((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      ingredients.append(item);
    });
    const steps = $("#recipe-steps");
    steps.replaceChildren();
    recipe.steps.forEach((step, index) => {
      const item = document.createElement("li");
      item.textContent = step.title;
      item.classList.toggle("current", index === state.session.step_index);
      if (index === state.session.step_index) item.setAttribute("aria-current", "step");
      steps.append(item);
    });
    const correct = $("#correct-select");
    const keepCorrectionChoice = $("#correct-details").open;
    const existing = correct.value;
    correct.replaceChildren();
    recipe.steps.forEach((step, index) => correct.add(new Option(`${index + 1}. ${step.title}`, String(index))));
    correct.value = keepCorrectionChoice && existing ? existing : String(state.session.step_index);
  }

  function renderObservation(observation) {
    const host = $("#observation-content");
    const pill = $("#confidence-pill");
    host.replaceChildren();
    if (!observation) {
      $("#observation-caption").textContent = state.camera ? "Waiting for the first look" : "No camera observations yet";
      pill.hidden = true;
      const empty = document.createElement("p");
      empty.textContent = "Camera observations will appear here. They’re a helpful cue, never a food-safety check.";
      host.append(empty);
      return;
    }
    $("#observation-caption").textContent = `Last look ${formatClock(observation.created_at)}`;
    pill.textContent = `${Math.round(observation.confidence * 100)}% confidence`;
    pill.hidden = false;
    const activity = document.createElement("p");
    activity.className = "observation-text";
    activity.textContent = observation.activity || "No activity description.";
    const evidence = document.createElement("p");
    evidence.className = "evidence-text";
    evidence.textContent = observation.evidence || "No visual detail was returned.";
    host.append(activity, evidence);
    if (observation.alert) {
      const alert = document.createElement("p");
      alert.className = "evidence-text";
      alert.textContent = `A note: ${observation.alert}`;
      host.append(alert);
    }
    if (observation.alert && observation.alert !== state.lastSpokenAlert) {
      state.lastSpokenAlert = observation.alert;
      if (state.session.monitoring) speak(observation.alert);
    } else if (!observation.alert) {
      state.lastSpokenAlert = "";
    }
  }

  function renderEvents(events) {
    const list = $("#event-list");
    list.replaceChildren();
    if (!events.length) {
      const item = document.createElement("li");
      item.className = "empty-events";
      item.textContent = "Your cooking notes will show up here.";
      list.append(item);
      return;
    }
    events.slice(0, 12).forEach((event) => {
      const item = document.createElement("li");
      const time = document.createElement("time");
      time.dateTime = new Date(event.created_at * 1000).toISOString();
      time.textContent = formatClock(event.created_at);
      const message = document.createElement("p");
      message.textContent = event.message;
      item.append(time, message);
      list.append(item);
    });
  }

  function formatClock(unixSeconds) {
    if (!unixSeconds) return "just now";
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(unixSeconds * 1000));
  }

  function updateElapsedTime() {
    if (!state.session) return;
    const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - state.session.step_started_at));
    const hours = Math.floor(elapsed / 3600);
    const minutes = Math.floor((elapsed % 3600) / 60);
    const seconds = elapsed % 60;
    const duration = hours ? `${hours}h ${String(minutes).padStart(2, "0")}m` : minutes ? `${minutes}m ${String(seconds).padStart(2, "0")}s` : `${seconds}s`;
    $("#elapsed-time").textContent = `${duration} on this step`;
  }

  function updateCaptureLoop() {
    clearInterval(state.captureTimer);
    state.captureTimer = null;
    const canCapture = !state.settingsView && state.session?.status === "active" && state.recipe && state.session.monitoring && state.camera && document.visibilityState === "visible";
    $("#analyze-now").disabled = !canCapture || state.observationInFlight;
    if (canCapture) state.captureTimer = setInterval(() => captureAndAnalyze(false), (state.config?.sample_interval || 10) * 1000);
  }

  async function enableCamera() {
    if (!state.session || state.session.status !== "active" || !state.recipe || state.cameraRequestPending) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraMessage("Camera access is unavailable here. You can continue with the step buttons and timer.", true);
      return;
    }
    const requestId = ++state.cameraRequestId;
    const sessionId = state.session.id;
    const isCurrentRequest = () => state.cameraRequestId === requestId
      && state.session?.id === sessionId
      && state.session.status === "active"
      && Boolean(state.recipe);
    state.cameraRequestPending = true;
    $("#camera-toggle").disabled = true;
    setCameraMessage("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      if (!isCurrentRequest()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      state.cameraRequestPending = false;
      state.camera = stream;
      const video = $("#camera-video");
      video.srcObject = stream;
      video.hidden = false;
      $("#camera-empty").hidden = true;
      $("#camera-caption").textContent = "Camera preview";
      $("#camera-indicator").classList.add("on");
      $("#camera-toggle").textContent = "Turn off camera";
      $("#camera-toggle").disabled = false;
      renderObservation(state.session.latest_observation);
      updateCaptureLoop();
    } catch (error) {
      if (!isCurrentRequest()) return;
      state.cameraRequestPending = false;
      $("#camera-toggle").disabled = false;
      setCameraMessage(cameraErrorMessage(error), true);
      showToast(cameraErrorMessage(error), true);
    }
  }

  function cameraErrorMessage(error) {
    if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return "Camera permission was declined. Step controls and timer are ready whenever you are.";
    if (error?.name === "NotFoundError") return "No camera was found. Step controls and timer still work.";
    return "Could not start the camera. Check your browser permission, or continue with the manual controls.";
  }

  function stopCamera() {
    state.cameraRequestId += 1;
    state.cameraRequestPending = false;
    clearInterval(state.captureTimer);
    state.captureTimer = null;
    if (state.camera) state.camera.getTracks().forEach((track) => track.stop());
    state.camera = null;
    const video = $("#camera-video");
    video.pause();
    video.srcObject = null;
    video.hidden = true;
    $("#camera-empty").hidden = false;
    $("#camera-caption").textContent = "Camera is off";
    $("#camera-indicator").classList.remove("on");
    $("#camera-toggle").textContent = state.recipe ? "Turn on camera" : "Unavailable";
    $("#camera-toggle").disabled = !state.recipe || state.session?.status !== "active";
    $("#analyze-now").disabled = true;
    cancelObservationRequest();
  }

  function cancelObservationRequest() {
    const wasPending = state.observationInFlight;
    state.observationController?.abort();
    state.observationController = null;
    setAnalysisPending(false);
    if (wasPending) {
      setCameraMessage("");
      if (state.camera && $("#camera-caption").textContent === "Analyzing") {
        $("#camera-caption").textContent = "Camera preview";
      }
    }
  }

  // Visual-only state for an in-flight observation request; camera-message carries the announcement.
  function setAnalysisPending(pending) {
    $("#analysis-dots").hidden = !pending;
    if (pending) $("#observation-panel").setAttribute("aria-busy", "true");
    else $("#observation-panel").removeAttribute("aria-busy");
  }

  async function captureAndAnalyze(manual) {
    if (state.settingsView || !state.session || !state.recipe || !state.camera || !state.session.monitoring || document.visibilityState !== "visible" || state.observationInFlight) return;
    const video = $("#camera-video");
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth || !video.videoHeight) {
      if (manual) setCameraMessage("Camera is warming up. Try Analyze now again in a moment.");
      return;
    }
    const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const context = canvas.getContext("2d", { alpha: false });
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const image = canvas.toDataURL("image/jpeg", 0.75);
    const revision = state.session.revision;
    const generation = state.uiGeneration;
    const controller = new AbortController();
    state.observationController = controller;
    state.observationInFlight = true;
    $("#analyze-now").disabled = true;
    $("#camera-caption").textContent = "Analyzing";
    setAnalysisPending(true);
    setCameraMessage("Analyzing… This first look can take a little longer while the local model wakes up.");
    try {
      const session = await post(`/api/sessions/${state.session.id}/observations`, { image, revision }, { signal: controller.signal });
      if (generation === state.uiGeneration && session.revision >= state.session.revision) {
        acceptSession(session, { announce: true, source: "observation" });
        $("#camera-caption").textContent = "Camera preview";
        setCameraMessage("Look complete.");
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      if (error.status === 409) {
        await refreshSession();
      } else if (error.status === 502 || error.status === 503) {
        $("#camera-caption").textContent = "Camera preview";
        setCameraMessage(`${error.message} Manual controls still work.`, true);
        if (manual) showToast(error.message, true);
      } else {
        $("#camera-caption").textContent = "Camera preview";
        setCameraMessage(`${error.message} You can retry or continue manually.`, true);
      }
    } finally {
      state.observationInFlight = false;
      if (state.observationController === controller) state.observationController = null;
      // A cancelled request has already cleared the indicator; never clear one that belongs to a newer request.
      if (!state.observationController) setAnalysisPending(false);
      if (state.camera && $("#camera-caption").textContent === "Analyzing") $("#camera-caption").textContent = "Camera preview";
      updateCaptureLoop();
    }
  }

  function tickTimer() {
    if (!state.session || state.session.status !== "active") return;
    updateElapsedTime();
    updateTimerDisplay();
    const session = state.session;
    if (session.timer_deadline && !session.timer_alerted && Date.now() / 1000 >= session.timer_deadline && !state.timerAcking) {
      state.timerAcking = true;
      runAction("timer_ack", {}, { quietConflict: true, silent: true })
        .catch((error) => showToast(error.message, true))
        .finally(() => { state.timerAcking = false; });
    }
  }

  function updateTimerDisplay() {
    const session = state.session;
    const readout = $("#timer-readout");
    if (!session?.timer_deadline) {
      readout.dataset.state = "idle";
      readout.textContent = "—:—";
      $("#timer-status").textContent = "No timer running";
      $("#cancel-timer").disabled = true;
      return;
    }
    const remaining = Math.max(0, Math.ceil(session.timer_deadline - Date.now() / 1000));
    const minutes = Math.floor(remaining / 60);
    const seconds = remaining % 60;
    readout.dataset.state = session.timer_alerted || remaining === 0 ? "done" : "running";
    readout.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    $("#timer-status").textContent = session.timer_alerted ? "Timer finished" : remaining === 0 ? "Time is up" : "Counting down";
    $("#cancel-timer").disabled = false;
  }

  function announceTimer() {
    $("#timer-status").textContent = "Timer finished";
    showToast("Timer finished.");
    if (state.session?.mode === "monitoring" || state.session?.mode === "assistive") speak("Your timer is finished.");
  }

  function speak(message) {
    if (state.companion?.running) return;
    if (!message || !window.speechSynthesis || !window.SpeechSynthesisUtterance) return;
    if (state.recognition && state.listening) {
      state.restartRecognition = true;
      try { state.recognition.stop(); } catch { /* already stopping */ }
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(message);
    utterance.lang = document.documentElement.lang || "en-US";
    utterance.onend = resumeRecognitionAfterSpeech;
    utterance.onerror = resumeRecognitionAfterSpeech;
    window.speechSynthesis.speak(utterance);
  }

  function resumeRecognitionAfterSpeech() {
    if (!state.restartRecognition || !state.recognition || !state.listening) return;
    state.restartRecognition = false;
    try { state.recognition.start(); } catch { /* browser can still be ending the last recognition */ }
  }

  function setupVoice() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      $("#legacy-voice-support").textContent = "Speech recognition isn’t available in this browser. Text and buttons work just as well.";
      $("#voice-toggle").hidden = true;
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      if (state.companion?.running) return;
      const transcript = event.results[event.results.length - 1][0].transcript;
      $("#command-input").value = transcript;
      handleCommand(transcript, true);
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        state.listening = false;
        $("#voice-toggle").textContent = "Start listening";
        setVoiceMessage("Microphone permission was declined. Use text or buttons instead.", true);
      } else if (event.error !== "no-speech" && event.error !== "aborted") {
        setVoiceMessage("Voice input paused. Text and buttons are still available.", true);
      }
    };
    recognition.onend = () => {
      if (state.restartRecognition && !window.speechSynthesis?.speaking && state.listening) {
        state.restartRecognition = false;
        try { recognition.start(); } catch { /* restart only when the browser accepts it */ }
      }
    };
    state.recognition = recognition;
    $("#legacy-voice-support").textContent = "Optional step commands use your browser’s speech service. Microphone permission is separate from the camera.";
  }

  function renderCompanionTranscript(entries) {
    const list = $("#voice-transcript");
    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("li");
      empty.className = "voice-transcript-empty";
      empty.textContent = "Your conversation appears here while voice is on, then clears when you stop.";
      list.append(empty);
      return;
    }
    entries.forEach((entry) => {
      const row = document.createElement("li");
      row.className = `voice-line ${entry.role}`;
      const speaker = document.createElement("strong");
      speaker.textContent = entry.role === "user" ? "You" : "Sous";
      const text = document.createElement("span");
      text.textContent = entry.text;
      row.append(speaker, text);
      list.append(row);
    });
    list.scrollTop = list.scrollHeight;
  }

  function setCompanionStatus(message, kind = "off") {
    const status = $("#companion-status");
    status.textContent = message;
    status.dataset.state = kind;
  }

  function setCompanionActive(active) {
    $("#companion-toggle").textContent = active ? "Stop voice" : "Start voice";
    $("#companion-toggle").classList.toggle("button-primary", !active);
    $("#companion-toggle").classList.toggle("button-secondary", active);
    $("#companion-toggle").setAttribute("aria-pressed", String(active));
    $("#voice-toggle").disabled = active || !state.recognition;
    if (active) {
      if (state.listening && state.recognition) {
        state.listening = false;
        state.restartRecognition = false;
        try { state.recognition.stop(); } catch { /* already stopping */ }
        $("#voice-toggle").textContent = "Start listening";
      }
      window.speechSynthesis?.cancel();
    }
  }

  function stopVoiceCompanion(message = "Voice is off. Camera guidance and manual controls work independently.") {
    state.companion?.stop(message);
  }

  function setupVoiceCompanion() {
    state.companion = new window.SousVoiceCompanion({
      onStatus: setCompanionStatus,
      onTranscript: renderCompanionTranscript,
      onClear: () => renderCompanionTranscript([]),
      onActive: setCompanionActive,
    });
    $("#companion-toggle").addEventListener("click", () => {
      if (state.companion.running) {
        stopVoiceCompanion();
        return;
      }
      if (!state.session || state.session.status !== "active") {
        setCompanionStatus("Start a cooking session before starting voice.", "error");
        return;
      }
      if (state.settingsView || document.visibilityState !== "visible") {
        setCompanionStatus("Return to the visible Cooking view to start voice.", "error");
        return;
      }
      if (state.settings?.vault?.locked || bedrockLockedForSession()) {
        setCompanionStatus("Unlock AWS settings before starting voice. Text and cooking controls are still available.", "error");
        return;
      }
      state.companion.start(state.session.id, csrfToken, state.settings?.voice);
    });
  }

  async function handleCommand(raw, fromSpeech = false) {
    if (fromSpeech && state.companion?.running) return;
    const command = raw.trim().toLowerCase().replace(/[.!?]+$/g, "");
    if (!command) return;
    setVoiceMessage(fromSpeech ? `Heard: “${raw.trim()}”` : "");
    const run = (action, extra) => runAction(action, extra).catch(() => {});
    if (/^(next|next step|go next|continue)$/.test(command)) return run("next");
    if (/^(back|go back|previous|previous step)$/.test(command)) return run("back");
    if (/^(repeat|repeat that|say that again)$/.test(command)) {
      const step = state.recipe?.steps[state.session?.step_index];
      if (step) speak(`${step.title}. ${step.instruction}`);
      return;
    }
    if (/^(pause|pause guidance|pause monitoring)$/.test(command)) return run("pause");
    if (/^(resume|resume guidance|resume monitoring|start guidance)$/.test(command)) return run("resume");
    if (/^(cancel timer|stop timer)$/.test(command)) return run("timer_cancel");
    const timer = command.match(/^set timer for\s+(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s*(minutes?|mins?|seconds?|secs?)$/);
    if (timer) {
      const words = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, a: 1, an: 1 };
      const amount = words[timer[1]] ?? Number(timer[1]);
      const unit = timer[2];
      const seconds = Math.round(amount * (/^(m|min|minute)/.test(unit) ? 60 : 1));
      if (!Number.isFinite(seconds) || seconds < 1 || seconds > 86400) {
        setVoiceMessage("Choose a timer from 1 second to 24 hours.", true);
        return;
      }
      return run("timer_set", { seconds });
    }
    if (command === "finish cooking" || command === "finish") {
      if (state.recipe && state.session?.step_index === state.recipe.steps.length - 1) return finishCooking();
      setVoiceMessage("Finish is available on the last recipe step.", true);
      return;
    }
    setVoiceMessage("Try next step, back, repeat, pause, resume, set timer for 1 minute, or cancel timer.");
  }

  function cleanupSession() {
    stopVoiceCompanion("Voice stopped because the cooking session ended.");
    stopCamera();
    cancelObservationRequest();
    clearSettingsSecrets();
    if (state.recognition) {
      state.listening = false;
      state.recognition.onend = null;
      try { state.recognition.stop(); } catch { /* inactive */ }
    }
    window.speechSynthesis?.cancel();
    clearInterval(state.captureTimer);
  }

  async function finishCooking() {
    try {
      const session = await runAction("finish");
      if (session.status === "completed") showCompletion(session.status);
    } catch { /* message already shown */ }
  }

  async function endCooking() {
    if (!state.session) return;
    try {
      const session = await runAction("end");
      cleanupSession();
      showCompletion(session.status);
    } catch { /* message already shown */ }
  }

  function finishSessionUi(status) {
    if (status === "active") return;
    cleanupSession();
    showCompletion(status);
  }

  function showCompletion(status) {
    $("#session-panel").hidden = true;
    $("#setup-panel").hidden = true;
    $("#completion-panel").hidden = false;
    const completed = status === "completed";
    $("#completion-panel").dataset.status = completed ? "completed" : "ended";
    $("#completion-title").textContent = completed ? "Cooking complete." : "Session ended for now.";
    $("#completion-copy").textContent = completed ? "You’ve finished cooking. Enjoy your meal." : "Your progress is saved. Start a new session whenever you’re ready.";
    setConnection(completed ? "Session complete" : "Session ended");
    $("#new-session").focus({ preventScroll: true });
  }

  async function createSession(event) {
    event.preventDefault();
    // Invalidate any permission request or stream belonging to an earlier session.
    stopCamera();
    stopVoiceCompanion(state.companion?.running
      ? "Voice stopped because the cooking session changed."
      : undefined);
    cancelObservationRequest();
    const form = new FormData(event.currentTarget);
    const button = $("#start-session");
    button.disabled = true;
    button.textContent = "Starting…";
    try {
      const session = await post("/api/sessions", {
        recipe_id: String(form.get("recipe_id")),
        provider: String(form.get("provider")),
        model: String(form.get("model")).trim(),
        mode: String(form.get("mode")),
      });
      state.session = session;
      state.recipe = session.recipe || null;
      state.restored = false;
      $("#setup-panel").hidden = true;
      $("#session-panel").hidden = false;
      renderSession();
      setConnection("Session saved");
      if (session.mode === "assistive" && state.recipe?.steps?.[0]) speak(`${state.recipe.steps[0].title}. ${state.recipe.steps[0].instruction}`);
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
      button.innerHTML = START_LABEL;
    }
  }

  function bindEvents() {
    $("#nav-cooking").addEventListener("click", showCooking);
    $("#nav-settings").addEventListener("click", showSettings);
    $("#settings-unlock-link").addEventListener("click", showSettings);
    $("#session-unlock-link").addEventListener("click", showSettings);
    $("#setup-form").addEventListener("submit", createSession);
    $("#recipe-select").addEventListener("change", updateRecipePeek);
    $("#provider-select").addEventListener("change", () => updateModelDefault(true));
    $("#model-input").addEventListener("input", (event) => {
      event.currentTarget.dataset.touched = "true";
      event.currentTarget.dataset.provider = $("#provider-select").value;
      renderGuidanceSummary();
    });
    $("#vault-init-form").addEventListener("submit", initializeVault);
    $("#vault-unlock-form").addEventListener("submit", unlockVault);
    $("#vault-lock-button").addEventListener("click", lockVault);
    $("#bedrock-form").addEventListener("submit", (event) => { event.preventDefault(); saveBedrockConnection(); });
    $("#delete-bedrock").addEventListener("click", removeBedrockConnection);
    $("#discover-models").addEventListener("click", discoverBedrockModels);
    $("#bedrock-model-picker").addEventListener("change", chooseDiscoveredModel);
    $("#use-custom-model").addEventListener("click", () => { $("#custom-model-field").hidden = false; $("#bedrock-model").focus(); });
    $("#save-model").addEventListener("click", () => saveBedrockConnection());
    $("#default-bedrock-model").addEventListener("click", () => saveBedrockConnection({ asDefault: true }));
    $("#bedrock-region").addEventListener("input", () => {
      resetModelPicker("Region changed · load models again");
      setFormError("#model-error");
      const savedRegion = state.settings?.bedrock?.region || "";
      const sameRegion = $("#bedrock-region").value.trim() === savedRegion;
      $("#discover-models").disabled = !sameRegion || state.settings?.vault?.locked !== false || state.settings?.bedrock?.has_credentials !== true;
      $("#bedrock-model-picker").disabled = true;
      if (!sameRegion) $("#model-discovery-note").textContent = "Save the new region before loading its model list.";
    });
    $("#bedrock-access-key").addEventListener("input", () => {
      $("#credential-replacement-note").hidden = !$("#bedrock-access-key").value;
    });
    $("#remove-session-token").addEventListener("change", (event) => {
      $("#bedrock-session-token").disabled = event.currentTarget.checked || state.settings?.vault?.locked !== false;
    });
    $("#defaults-form").addEventListener("submit", saveDefaults);
    $("#default-provider").addEventListener("change", setModelDefaultProvider);
    $("#interval-form").addEventListener("submit", saveSampleInterval);
    $("#voice-preference-form").addEventListener("submit", saveVoicePreference);
    $("#next-button").addEventListener("click", () => runAction("next").catch(() => {}));
    $("#back-button").addEventListener("click", () => runAction("back").catch(() => {}));
    $("#finish-button").addEventListener("click", finishCooking);
    $("#end-session").addEventListener("click", endCooking);
    $("#monitoring-toggle").addEventListener("click", () => runAction(state.session.monitoring ? "pause" : "resume").catch(() => {}));
    $("#restore-resume").addEventListener("click", () => runAction("resume").then(() => { state.restored = false; renderSession(); }).catch(() => {}));
    $("#correct-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const stepIndex = Number($("#correct-select").value);
      const note = $("#correct-note").value.trim();
      runAction("correct", { step_index: stepIndex, ...(note ? { note } : {}) })
        .then(() => { $("#correct-details").open = false; $("#correct-note").value = ""; })
        .catch(() => {});
    });
    $("#camera-toggle").addEventListener("click", () => state.camera ? stopCamera() : enableCamera());
    $("#analyze-now").addEventListener("click", () => captureAndAnalyze(true));
    $("#timer-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const minutes = Number($("#timer-minutes").value);
      const seconds = Math.round(minutes * 60);
      if (!Number.isFinite(seconds) || seconds < 1 || seconds > 86400) {
        showToast("Choose a timer from 1 second to 24 hours.", true);
        return;
      }
      runAction("timer_set", { seconds }).then(() => { $("#timer-minutes").value = ""; }).catch(() => {});
    });
    $("#cancel-timer").addEventListener("click", () => runAction("timer_cancel").catch(() => {}));
    $("#command-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#command-input");
      const command = input.value;
      input.value = "";
      handleCommand(command);
    });
    $("#voice-toggle").addEventListener("click", () => {
      if (state.companion?.running) return;
      if (!state.recognition) return;
      if (state.listening) {
        state.listening = false;
        state.restartRecognition = false;
        state.recognition.stop();
        $("#voice-toggle").textContent = "Start listening";
        setVoiceMessage("Voice input is off.");
      } else {
        state.listening = true;
        setVoiceMessage("Listening. Say a step, timer, pause, or repeat command.");
        $("#voice-toggle").textContent = "Stop listening";
        try { state.recognition.start(); } catch {
          state.listening = false;
          $("#voice-toggle").textContent = "Start listening";
          setVoiceMessage("The browser could not start voice input. Use text or buttons instead.", true);
        }
      }
    });
    $("#new-session").addEventListener("click", () => {
      stopCamera();
      cancelObservationRequest();
      state.session = null;
      state.recipe = null;
      state.restored = false;
      fillSetup(state.config || {}, true);
      $("#completion-panel").hidden = true;
      $("#setup-panel").hidden = false;
      $("#start-session").disabled = false;
      $("#start-session").innerHTML = START_LABEL;
      setConnection("Ready to cook");
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") {
        cancelObservationRequest();
        stopVoiceCompanion("Voice stopped because this tab is hidden.");
      }
      updateCaptureLoop();
    });
    window.addEventListener("pagehide", cleanupSession, { once: true });
  }

  bindEvents();
  setupVoice();
  setupVoiceCompanion();
  boot();
})();
