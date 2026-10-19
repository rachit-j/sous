(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const state = {
    config: null,
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

  function fillSetup(config) {
    const recipes = config.recipes || [];
    const providers = (config.providers || []).filter((provider) => provider.configured);
    const recipeSelect = $("#recipe-select");
    recipeSelect.replaceChildren();
    recipes.forEach((recipe) => recipeSelect.add(new Option(recipe.name, recipe.id)));
    const providerSelect = $("#provider-select");
    providerSelect.replaceChildren();
    providers.forEach((provider) => providerSelect.add(new Option(provider.name, provider.id)));
    $("#start-session").disabled = providers.length === 0 || recipes.length === 0;
    if (providers.length === 0) showToast("No cooking guide is configured on this computer. Configure a provider on the server, then reload.", true);
    if (providers.length) providerSelect.value = providers[0].id;
    updateModelDefault();
    updateRecipePeek();
  }

  function updateRecipePeek() {
    const recipe = state.config?.recipes?.find((item) => item.id === $("#recipe-select").value);
    if (!recipe) return;
    const peek = $("#recipe-peek");
    peek.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = recipe.description || recipe.name;
    const ingredients = document.createElement("ul");
    (recipe.ingredients || []).forEach((ingredient) => {
      const item = document.createElement("li");
      item.textContent = ingredient;
      ingredients.append(item);
    });
    peek.append(title, ingredients);
  }

  function updateModelDefault() {
    const provider = state.config?.providers?.find((item) => item.id === $("#provider-select").value);
    const input = $("#model-input");
    if (!input.dataset.touched || !input.value.trim()) input.value = provider?.default_model || "";
  }

  async function boot() {
    try {
      state.config = await api("/api/config");
      fillSetup(state.config);
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
    $("#monitoring-toggle").disabled = session.status !== "active";
    $("#camera-toggle").disabled = session.status !== "active" || state.cameraRequestPending;
    $("#camera-toggle").textContent = state.camera ? "Turn off camera" : "Turn on camera";
    $("#voice-toggle").disabled = false;
    $("#command-input").disabled = false;
    $("#command-form button[type=submit]").disabled = false;
    const step = recipe.steps[session.step_index];
    const last = session.step_index === recipe.steps.length - 1;
    $("#session-recipe-name").textContent = recipe.name;
    $("#session-title").textContent = session.status === "completed" ? "That’s a good plate." : step.title;
    $("#mode-badge").textContent = session.mode === "assistive" ? "Talk me through it" : "Keep an eye out";
    const badge = $("#monitoring-badge");
    badge.classList.toggle("active", session.monitoring);
    badge.lastElementChild.textContent = session.monitoring ? "Guidance on" : "Guidance paused";
    $("#step-count").textContent = `Step ${session.step_index + 1} of ${recipe.steps.length}`;
    $("#step-number").textContent = String(session.step_index + 1).padStart(2, "0");
    $("#step-title").textContent = step.title;
    $("#step-instruction").textContent = step.instruction;
    $("#visual-cue-text").textContent = step.visual_cue;
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
    $("#restore-resume").disabled = session.monitoring;
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
    const canCapture = state.session?.status === "active" && state.recipe && state.session.monitoring && state.camera && document.visibilityState === "visible";
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
  }

  function cancelObservationRequest() {
    state.observationController?.abort();
    state.observationController = null;
  }

  async function captureAndAnalyze(manual) {
    if (!state.session || !state.recipe || !state.camera || !state.session.monitoring || document.visibilityState !== "visible" || state.observationInFlight) return;
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
    $("#camera-caption").textContent = "Analyzing…";
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
      if (state.camera && $("#camera-caption").textContent === "Analyzing…") $("#camera-caption").textContent = "Camera preview";
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
    if (!session?.timer_deadline) {
      $("#timer-readout").textContent = "—:—";
      $("#timer-status").textContent = "No timer running";
      $("#cancel-timer").disabled = true;
      return;
    }
    const remaining = Math.max(0, Math.ceil(session.timer_deadline - Date.now() / 1000));
    const minutes = Math.floor(remaining / 60);
    const seconds = remaining % 60;
    $("#timer-readout").textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    $("#timer-status").textContent = session.timer_alerted ? "Timer finished" : remaining === 0 ? "Time is up" : "Counting down";
    $("#cancel-timer").disabled = false;
  }

  function announceTimer() {
    $("#timer-status").textContent = "Timer finished";
    showToast("Timer finished.");
    if (state.session?.mode === "monitoring" || state.session?.mode === "assistive") speak("Your timer is finished.");
  }

  function speak(message) {
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
      $("#voice-support").textContent = "Speech recognition isn’t available in this browser. Text and buttons work just as well.";
      $("#voice-toggle").hidden = true;
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
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
    $("#voice-support").textContent = "Voice input uses your browser’s speech service. Camera and microphone are separate permissions.";
  }

  async function handleCommand(raw, fromSpeech = false) {
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
    stopCamera();
    cancelObservationRequest();
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
    $("#completion-title").textContent = completed ? "That’s a good plate." : "Session ended for now.";
    $("#completion-copy").textContent = completed ? "You’ve finished cooking. Enjoy your meal." : "Your progress is saved. Start a new session whenever you’re ready.";
    setConnection(completed ? "Session complete" : "Session ended");
    $("#new-session").focus({ preventScroll: true });
  }

  async function createSession(event) {
    event.preventDefault();
    // Invalidate any permission request or stream belonging to an earlier session.
    stopCamera();
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
      button.innerHTML = 'Start cooking <span aria-hidden="true">↗</span>';
    }
  }

  function bindEvents() {
    $("#setup-form").addEventListener("submit", createSession);
    $("#recipe-select").addEventListener("change", updateRecipePeek);
    $("#provider-select").addEventListener("change", updateModelDefault);
    $("#model-input").addEventListener("input", (event) => { event.currentTarget.dataset.touched = "true"; });
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
      $("#completion-panel").hidden = true;
      $("#setup-panel").hidden = false;
      $("#start-session").disabled = false;
      $("#start-session").innerHTML = 'Start cooking <span aria-hidden="true">↗</span>';
      setConnection("Ready to cook");
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") cancelObservationRequest();
      updateCaptureLoop();
    });
    window.addEventListener("pagehide", cleanupSession, { once: true });
  }

  bindEvents();
  setupVoice();
  boot();
})();
