(() => {
  "use strict";

  const CHUNK_BYTES = 1024;
  const MAX_PLAYBACK_SECONDS = 30;
  const MAX_SOCKET_BACKLOG = 64 * 1024;
  const MAX_TRANSCRIPT_ROWS = 20;
  const MAX_TRANSCRIPT_CHARS = 6000;

  class VoiceCompanion {
    constructor({ onStatus, onTranscript, onClear, onActive }) {
      this.onStatus = onStatus;
      this.onTranscript = onTranscript;
      this.onClear = onClear;
      this.onActive = onActive;
      this.generation = 0;
      this.active = false;
      this.ready = false;
      this.socket = null;
      this.stream = null;
      this.context = null;
      this.source = null;
      this.captureNode = null;
      this.playbackSources = new Set();
      this.playbackCursor = 0;
      this.transcripts = [];
      this.transcriptChars = 0;
      this.startedAt = 0;
      this.stopReason = "";
      this.cancelPendingConnect = null;
      this.speakingTimer = null;
    }

    get running() { return this.active; }

    async start(sessionId, csrfToken, metadata) {
      if (this.active) return;
      if (!metadata?.available) {
        this.setStatus(metadata?.reason || "Voice is unavailable. You can keep using text and cooking controls.", "error");
        return;
      }
      if (!sessionId || !navigator.mediaDevices?.getUserMedia || !window.AudioContext || !window.AudioWorkletNode || !window.WebSocket) {
        this.setStatus("This browser cannot start the voice companion. Text and cooking controls are still available.", "error");
        return;
      }
      const generation = ++this.generation;
      this.active = true;
      this.stopReason = "";
      this.clearTranscript();
      this.setStatus("Requesting microphone access…", "connecting");
      this.onActive(true);
      let permissionStream;
      try {
        permissionStream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false,
        });
        if (generation !== this.generation || !this.active) {
          permissionStream.getTracks().forEach((track) => track.stop());
          return;
        }
        this.stream = permissionStream;
        this.context = new AudioContext();
        await this.context.resume();
        await this.context.audioWorklet.addModule("/static/voice-worklet.js");
        if (generation !== this.generation || !this.active) return;
        this.source = this.context.createMediaStreamSource(this.stream);
        this.captureNode = new AudioWorkletNode(this.context, "sous-pcm-capture", {
          numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
        });
        this.captureNode.port.onmessage = (event) => {
          if (generation !== this.generation || !this.active || !this.ready || this.socket?.readyState !== WebSocket.OPEN) return;
          if (!(event.data instanceof ArrayBuffer) || event.data.byteLength !== CHUNK_BYTES) return;
          if (this.socket.bufferedAmount > MAX_SOCKET_BACKLOG) {
            this.stop("Voice connection is too slow to keep up. Stop and try again when the connection improves.", true);
            return;
          }
          this.socket.send(event.data);
        };
        this.source.connect(this.captureNode);
        this.captureNode.connect(this.context.destination);
        await this.connectSocket(sessionId, csrfToken, generation);
        if (generation !== this.generation || !this.active) return;
      } catch (error) {
        if (generation === this.generation) {
          this.stop(error?.name === "NotAllowedError" ? "Microphone permission was declined. Text and cooking controls are still available." : "Voice could not start. Text and cooking controls are still available.", true);
        }
      }
    }

    connectSocket(sessionId, csrfToken, generation) {
      return new Promise((resolve, reject) => {
        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${scheme}//${location.host}/api/sessions/${encodeURIComponent(sessionId)}/voice`);
        this.socket = socket;
        let settled = false;
        const timer = setTimeout(() => {
          if (settled) return;
          settled = true;
          reject(new Error("Voice connection timed out."));
        }, 20000);
        const finish = (error) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          if (this.cancelPendingConnect === cancel) this.cancelPendingConnect = null;
          error ? reject(error) : resolve();
        };
        const cancel = () => finish(new Error("Voice stopped."));
        this.cancelPendingConnect = cancel;
        socket.onopen = () => {
          if (generation !== this.generation || !this.active) {
            socket.close(1000, "stopped");
            finish(new Error("Voice stopped."));
            return;
          }
          socket.send(JSON.stringify({ type: "start", csrf_token: csrfToken }));
          this.setStatus("Connecting to the voice service…", "connecting");
        };
        socket.onmessage = (event) => {
          if (generation !== this.generation || !this.active) return;
          this.handleMessage(event.data);
          if (!settled && typeof event.data === "string") {
            try {
              if (JSON.parse(event.data).type === "ready") finish();
              else if (JSON.parse(event.data).type === "error") finish(new Error("Voice service could not start."));
            } catch { /* malformed service messages are ignored */ }
          }
        };
        socket.onerror = () => finish(new Error("Voice connection failed."));
        socket.onclose = () => {
          if (generation === this.generation && this.active && !this.stopReason) {
            this.stop("Voice disconnected. Start it again when you’re ready.", true);
          }
          finish();
        };
      });
    }

    handleMessage(raw) {
      if (typeof raw !== "string") return;
      let message;
      try { message = JSON.parse(raw); } catch { return; }
      if (!message || typeof message.type !== "string") return;
      if (message.type === "ready") {
        this.ready = true;
        this.setStatus("Listening. Ask about this recipe, step, or latest camera observation.", "listening");
      } else if (message.type === "audio" && typeof message.audio === "string") {
        this.playAudio(message.audio);
      } else if (message.type === "transcript" && (message.role === "user" || message.role === "assistant") && typeof message.text === "string") {
        this.updateTranscript(message.role, message.text, message.final === true);
        if (message.role === "assistant") {
          this.setStatus("Sous is replying. Interrupt any time by talking.", "speaking");
          this.armSpeakingTimeout();
        } else this.setStatus("Listening…", "listening");
      } else if (message.type === "interrupted") {
        this.flushPlayback();
        this.setStatus("Listening. Ask another question or interrupt at any time.", "listening");
      } else if (message.type === "stopped") {
        const reason = message.reason === "locked" ? "AWS settings were locked. Voice has stopped." : "Voice session ended. Start it again when you’re ready.";
        this.stop(reason, true);
      } else if (message.type === "error") {
        this.stop(typeof message.message === "string" ? message.message : "Voice stopped. Text and cooking controls are still available.", true);
      }
    }

    updateTranscript(role, text, final) {
      const clean = text.slice(0, 1000);
      const prior = this.transcripts[this.transcripts.length - 1];
      if (prior && prior.role === role && !prior.final) {
        this.transcriptChars -= prior.text.length;
        prior.text = clean;
        prior.final = final;
      } else {
        this.transcripts.push({ role, text: clean, final });
      }
      this.transcriptChars += clean.length;
      while (this.transcripts.length > MAX_TRANSCRIPT_ROWS || this.transcriptChars > MAX_TRANSCRIPT_CHARS) {
        const removed = this.transcripts.shift();
        if (removed) this.transcriptChars -= removed.text.length;
      }
      this.onTranscript(this.transcripts.slice());
    }

    playAudio(encoded) {
      if (!this.context) return;
      try {
        const estimatedBytes = Math.floor(encoded.length * 3 / 4);
        const queuedSeconds = Math.max(0, this.playbackCursor - this.context.currentTime);
        if (estimatedBytes > MAX_PLAYBACK_SECONDS * 24000 * 2
            || queuedSeconds + estimatedBytes / (24000 * 2) > MAX_PLAYBACK_SECONDS) {
          this.stop("Voice audio arrived too quickly to play safely. Stop voice and start a fresh conversation.", true);
          return;
        }
        const binary = atob(encoded);
        if (!binary.length || binary.length % 2) return;
        const samples = new Int16Array(binary.length / 2);
        for (let index = 0; index < samples.length; index += 1) {
          samples[index] = (binary.charCodeAt(index * 2) & 255) | (binary.charCodeAt(index * 2 + 1) << 8);
        }
        const seconds = samples.length / 24000;
        const now = this.context.currentTime;
        const start = Math.max(now, this.playbackCursor);
        const buffer = this.context.createBuffer(1, samples.length, 24000);
        const channel = buffer.getChannelData(0);
        for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);
        this.playbackSources.add(source);
        source.onended = () => {
          this.playbackSources.delete(source);
          if (!this.playbackSources.size && this.active) {
            clearTimeout(this.speakingTimer);
            this.speakingTimer = null;
            this.setStatus("Listening. Ask another question or interrupt at any time.", "listening");
          }
        };
        source.start(start);
        this.playbackCursor = start + seconds;
        this.setStatus("Speaking. Interrupt any time by talking.", "speaking");
        clearTimeout(this.speakingTimer);
        this.speakingTimer = null;
      } catch { /* malformed audio frames are ignored */ }
    }

    flushPlayback() {
      this.playbackSources.forEach((source) => {
        try { source.stop(); } catch { /* already ended */ }
        try { source.disconnect(); } catch { /* already disconnected */ }
      });
      this.playbackSources.clear();
      this.playbackCursor = this.context?.currentTime || 0;
    }

    clearTranscript() {
      this.transcripts = [];
      this.transcriptChars = 0;
      this.onClear();
    }

    setStatus(message, state) { this.onStatus(message, state); }

    armSpeakingTimeout() {
      clearTimeout(this.speakingTimer);
      this.speakingTimer = setTimeout(() => {
        this.speakingTimer = null;
        if (this.active && !this.playbackSources.size) {
          this.setStatus("Listening. Ask another question or interrupt at any time.", "listening");
        }
      }, 15000);
    }

    stop(message = "Voice is off. Camera guidance and manual controls work independently.", isError = false) {
      if (!this.active && !this.socket && !this.stream && !this.context) {
        this.setStatus(message, isError ? "error" : "off");
        this.clearTranscript();
        return;
      }
      this.active = false;
      this.ready = false;
      this.stopReason = "local";
      this.generation += 1;
      clearTimeout(this.speakingTimer);
      this.speakingTimer = null;
      this.cancelPendingConnect?.();
      this.cancelPendingConnect = null;
      this.onActive(false);
      this.flushPlayback();
      const socket = this.socket;
      this.socket = null;
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        if (socket.readyState === WebSocket.OPEN) {
          try { socket.send(JSON.stringify({ type: "stop" })); } catch { /* socket may be closing */ }
        }
        try { socket.close(1000, "stopped"); } catch { /* already closed */ }
      }
      if (this.captureNode) {
        this.captureNode.port.onmessage = null;
        try { this.captureNode.disconnect(); } catch { /* already disconnected */ }
        this.captureNode = null;
      }
      if (this.source) {
        try { this.source.disconnect(); } catch { /* already disconnected */ }
        this.source = null;
      }
      if (this.stream) {
        this.stream.getTracks().forEach((track) => track.stop());
        this.stream = null;
      }
      if (this.context) {
        const context = this.context;
        this.context = null;
        context.close().catch(() => {});
      }
      this.clearTranscript();
      this.setStatus(message, isError ? "error" : "off");
    }
  }

  window.SousVoiceCompanion = VoiceCompanion;
})();
