const form = document.querySelector("#job-form");
const fileInput = document.querySelector("#pdf");
const fileLabel = document.querySelector("#file-label");
const submit = document.querySelector("#submit");
const stage = document.querySelector("#stage");
const statusPill = document.querySelector("#status-pill");
const result = document.querySelector("#result");
const steps = Array.from(document.querySelectorAll(".step"));
const voiceOptions = document.querySelector("#voice-options");
const voiceStatus = document.querySelector("#voice-status");
const voiceProviderInput = document.querySelector("#voice-provider");
const voiceProviderOptions = document.querySelector("#voice-provider-options");
const videoFormat = document.querySelector("#video-format");
const maxMinutes = document.querySelector('input[name="max_minutes"]');
const renderFps = document.querySelector("#render-fps");
const renderEstimate = document.querySelector("#render-estimate");
const qualityOptions = document.querySelector("#quality-options");
const fpsOptions = document.querySelector("#fps-options");
const qualityMode = document.querySelector("#quality-mode");
const renderTimeValue = document.querySelector("#render-time-value");
const progressPanel = document.querySelector("#progress-panel");
const progressLabel = document.querySelector("#progress-label");
const progressPercent = document.querySelector("#progress-percent");
const progressFill = document.querySelector("#progress-fill");
const progressDetail = document.querySelector("#progress-detail");
const progressEta = document.querySelector("#progress-eta");

let pollTimer = null;
let previewAudio = new Audio();
let activePreviewButton = null;
let statusReadFailures = 0;
let activeJobId = localStorage.getItem("saffron_active_job") || "";
let activeVoiceProvider = "elevenlabs";
let voiceProviderMeta = {};
let providerVoiceData = { elevenlabs: [], deepgram: [] };
let defaultVoiceModels = { elevenlabs: "", deepgram: "" };
let providerPreviewEnabled = { elevenlabs: false, deepgram: false };

const RENDER_QUALITIES = [
  {
    value: "480p",
    label: "480p Fast",
    speed: "Fast",
    description: "Fast preview render",
    horizontal: [854, 480],
    vertical: [480, 854],
    factor: 1.8,
  },
  {
    value: "720p",
    label: "720p Balanced",
    speed: "Balance",
    description: "Good quality with faster turnaround",
    horizontal: [1280, 720],
    vertical: [720, 1280],
    factor: 4,
  },
  {
    value: "1080p",
    label: "1080p Best",
    speed: "Best",
    description: "Full HD final export",
    horizontal: [1920, 1080],
    vertical: [1080, 1920],
    factor: 7.5,
  },
];

loadVoices();
syncRenderOptions();
resumeActiveJob();

videoFormat.addEventListener("change", syncRenderOptions);
renderFps.addEventListener("change", syncRenderOptions);
maxMinutes.addEventListener("input", syncRenderOptions);
qualityOptions.querySelectorAll('input[name="render_quality"]').forEach((input) => {
  input.addEventListener("change", syncRenderOptions);
});
fpsOptions.querySelectorAll(".fps-pill").forEach((button) => {
  button.addEventListener("click", () => {
    renderFps.value = button.dataset.fps || "30";
    renderFps.dispatchEvent(new Event("change", { bubbles: true }));
  });
});
voiceProviderOptions.querySelectorAll(".voice-provider-pill").forEach((button) => {
  button.addEventListener("click", () => selectVoiceProvider(button.dataset.provider || "elevenlabs"));
});

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "Drop or choose PDF, TXT, or MD";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearInterval(pollTimer);
  statusReadFailures = 0;
  setBusy(true);
  setStatus("queued", "Uploading source file");
  markStep("pdf");
  updateProgress({
    progress: {
      percent: 0,
      label: "Uploading source file",
      detail: selectedRenderSummary(),
      eta_label: "ETA calculating",
    },
  });
  result.className = "result empty";
  result.innerHTML = `<div class="empty-state"><div class="play-mark"></div><p>Building your walkthrough...</p></div>`;

  const response = await fetch("/api/jobs", {
    method: "POST",
    body: new FormData(form),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Upload failed" }));
    showError(error.detail || "Upload failed");
    setBusy(false);
    return;
  }

  const payload = await response.json();
  startPolling(payload.job_id);
});

async function pollJob(jobId) {
  let response;
  try {
    response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
  } catch (_error) {
    handleStatusReadRetry();
    return;
  }

  if (!response.ok) {
    handleStatusReadRetry();
    return;
  }

  const job = await response.json();
  statusReadFailures = 0;
  setStatus(job.status, job.stage);
  syncSteps(job.stage, job.status);
  updateProgress(job);

  if (job.status === "complete") {
    clearInterval(pollTimer);
    localStorage.removeItem("saffron_active_job");
    activeJobId = "";
    setBusy(false);
    renderResult(jobId, job);
  }

  if (job.status === "failed") {
    clearInterval(pollTimer);
    localStorage.removeItem("saffron_active_job");
    activeJobId = "";
    setBusy(false);
    showError(job.error || "Video generation failed.");
  }
}

function startPolling(jobId) {
  activeJobId = jobId;
  localStorage.setItem("saffron_active_job", jobId);
  clearInterval(pollTimer);
  pollJob(jobId);
  pollTimer = setInterval(() => pollJob(jobId), 1800);
}

function handleStatusReadRetry() {
  statusReadFailures += 1;
  if (statusReadFailures <= 12) {
    setBusy(true);
    setStatus("running", "Reconnecting to render");
    syncSteps("Rendering the video", "running");
    updateProgress({
      progress: {
        percent: currentProgressPercent(),
        label: "Reconnecting to render",
        detail: "Status check is retrying. The render is still being tracked.",
        eta_label: "Retrying",
      },
    });
    return;
  }

  clearInterval(pollTimer);
  localStorage.removeItem("saffron_active_job");
  activeJobId = "";
  showError("Could not read job status. Refresh once and try again.");
  setBusy(false);
}

function currentProgressPercent() {
  const current = Number(String(progressPercent.textContent || "0").replace("%", ""));
  return Number.isFinite(current) ? current : 0;
}

function resumeActiveJob() {
  if (!activeJobId) return;
  setBusy(true);
  setStatus("running", "Reconnecting to render");
  markStep("video");
  updateProgress({
    progress: {
      percent: currentProgressPercent(),
      label: "Reconnecting to render",
      detail: selectedRenderSummary(),
      eta_label: "Retrying",
    },
  });
  result.className = "result empty";
  result.innerHTML = `<div class="empty-state"><div class="play-mark"></div><p>Resuming current render...</p></div>`;
  startPolling(activeJobId);
}

function renderResult(jobId, job) {
  const format = job.summary?.video_format || job.inputs?.video_format || "horizontal";
  const quality = job.summary?.render_quality || job.inputs?.render_quality || "720p";
  const fps = job.summary?.render_fps || job.inputs?.render_fps || 30;
  result.className = format === "vertical" ? "result vertical-result" : "result";
  result.innerHTML = `
    <video controls src="/api/jobs/${jobId}/video"></video>
    <div class="render-summary">${escapeHtml(quality)} - ${escapeHtml(fps)} FPS</div>
    <div class="actions">
      <a class="primary" href="/api/jobs/${jobId}/video" download>Download video</a>
      <a href="/api/jobs/${jobId}/script" download>Download script</a>
    </div>
  `;
}

function showError(message) {
  result.className = "result";
  result.innerHTML = `<div class="error">${escapeHtml(message)}</div>`;
  setStatus("failed", "Failed");
}

function setBusy(isBusy) {
  submit.disabled = isBusy;
  submit.querySelector("span").textContent = isBusy ? "Creating..." : "Create video";
}

function setStatus(status, message) {
  stage.textContent = message || "Working";
  statusPill.className = `pill ${status}`;
  statusPill.textContent = status.charAt(0).toUpperCase() + status.slice(1);
}

function updateProgress(job) {
  const progress = job.progress;
  if (!progress) {
    progressPanel.hidden = true;
    return;
  }

  const percent = Math.max(0, Math.min(Number(progress.percent || 0), 100));
  progressPanel.hidden = false;
  progressLabel.textContent = progress.label || job.stage || "Working";
  progressPercent.textContent = `${Math.round(percent)}%`;
  progressFill.style.width = `${percent}%`;
  progressDetail.textContent = progress.detail || selectedRenderSummary();
  progressEta.textContent = progress.eta_label ? `ETA ${progress.eta_label}` : "ETA calculating";
}

function markStep(activeKey) {
  steps.forEach((step) => {
    step.classList.toggle("active", step.dataset.step === activeKey);
    step.classList.remove("done");
  });
}

function syncSteps(stageText = "", status = "") {
  const text = stageText.toLowerCase();
  let active = "pdf";
  if (text.includes("script")) active = "script";
  if (text.includes("audio") || text.includes("voiceover")) active = "audio";
  if (text.includes("video") || text.includes("composing")) active = "video";

  const order = ["pdf", "script", "audio", "video"];
  const activeIndex = order.indexOf(active);
  steps.forEach((step) => {
    const index = order.indexOf(step.dataset.step);
    step.classList.toggle("active", status !== "complete" && index === activeIndex);
    step.classList.toggle("done", status === "complete" || index < activeIndex);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadVoices() {
  try {
    const response = await fetch("/api/voices");
    if (!response.ok) throw new Error("Voice list failed");
    const payload = await response.json();
    voiceProviderMeta = Object.fromEntries((payload.providers || []).map((provider) => [provider.value, provider]));
    providerVoiceData = payload.provider_voices || {
      elevenlabs: payload.voices || [],
      deepgram: [],
    };
    defaultVoiceModels = payload.defaults || {
      elevenlabs: payload.default_voice_model || "",
      deepgram: "",
    };
    providerPreviewEnabled =
      typeof payload.preview_enabled === "object"
        ? payload.preview_enabled
        : { elevenlabs: Boolean(payload.preview_enabled), deepgram: false };
    selectVoiceProvider(payload.default_provider || "elevenlabs");
  } catch (_error) {
    voiceStatus.textContent = "Voice modules unavailable";
    voiceOptions.innerHTML = `
      <div class="voice-card selected">
        <label class="voice-main">
          <input type="radio" name="voice_model" value="" checked />
          <span>
            <strong>Default voice</strong>
            <em>Voice list could not load</em>
          </span>
        </label>
      </div>
    `;
  }
}

function selectVoiceProvider(provider) {
  activeVoiceProvider = provider === "deepgram" ? "deepgram" : "elevenlabs";
  voiceProviderInput.value = activeVoiceProvider;
  voiceProviderOptions.querySelectorAll(".voice-provider-pill").forEach((button) => {
    button.classList.toggle("selected", button.dataset.provider === activeVoiceProvider);
    const meta = voiceProviderMeta[button.dataset.provider] || {};
    button.classList.toggle("not-configured", meta.configured === false);
  });
  renderVoices(
    providerVoiceData[activeVoiceProvider] || [],
    defaultVoiceModels[activeVoiceProvider] || "",
    Boolean(providerPreviewEnabled[activeVoiceProvider]),
    activeVoiceProvider,
  );
}

function renderVoices(voices, defaultVoiceModel, previewEnabled, provider) {
  if (!voices.length) {
    voiceStatus.textContent = provider === "deepgram" ? "No Deepgram voices found" : "No ElevenLabs voices found";
    voiceOptions.innerHTML = "";
    return;
  }

  const selectedVoiceModel = voices.some((voice) => voice.model === defaultVoiceModel) ? defaultVoiceModel : voices[0].model;
  const providerLabel = provider === "deepgram" ? "Deepgram" : "ElevenLabs";
  const configured = voiceProviderMeta[provider]?.configured !== false;
  if (!configured) {
    voiceStatus.textContent = `${providerLabel} API key needed`;
  } else {
    voiceStatus.textContent = previewEnabled ? `Preview ${providerLabel} voices` : `${providerLabel} preview unavailable`;
  }
  voiceOptions.innerHTML = voices
    .map((voice) => {
      const checked = voice.model === selectedVoiceModel ? "checked" : "";
      const selected = checked ? " selected" : "";
      const recommended = voice.recommended ? `<span class="voice-badge">Recommended</span>` : "";
      const disabled = previewEnabled ? "" : "disabled";
      return `
        <div class="voice-card${selected}" data-model="${escapeHtml(voice.model)}" data-provider="${escapeHtml(provider)}">
          <label class="voice-main">
            <input type="radio" name="voice_model" value="${escapeHtml(voice.model)}" ${checked} />
            <span>
              <strong>${escapeHtml(voice.name)}</strong>
              <em>${escapeHtml(voice.gender)} - ${escapeHtml(voice.tone)}</em>
            </span>
          </label>
          ${recommended}
          <button class="voice-play" type="button" data-model="${escapeHtml(voice.model)}" aria-label="Play ${escapeHtml(voice.name)} preview" title="Play preview" ${disabled}>Play</button>
        </div>
      `;
    })
    .join("");

  voiceOptions.querySelectorAll('input[name="voice_model"]').forEach((input) => {
    input.addEventListener("change", syncVoiceSelection);
  });

  voiceOptions.querySelectorAll(".voice-card").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest(".voice-play")) return;
      const input = card.querySelector('input[name="voice_model"]');
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  voiceOptions.querySelectorAll(".voice-play").forEach((button) => {
    button.addEventListener("click", () => playVoicePreview(button));
  });
}

function syncVoiceSelection() {
  voiceOptions.querySelectorAll(".voice-card").forEach((card) => {
    const input = card.querySelector('input[name="voice_model"]');
    card.classList.toggle("selected", input.checked);
  });
}

async function playVoicePreview(button) {
  const model = button.dataset.model;
  if (!model) return;
  const provider = button.closest(".voice-card")?.dataset.provider || activeVoiceProvider;

  if (activePreviewButton === button && !previewAudio.paused) {
    previewAudio.pause();
    previewAudio.currentTime = 0;
    resetPreviewButton(button);
    return;
  }

  if (activePreviewButton) resetPreviewButton(activePreviewButton);
  activePreviewButton = button;
  button.disabled = true;
  button.textContent = "...";

  previewAudio.pause();
  previewAudio = new Audio(
    `/api/voices/preview?voice_provider=${encodeURIComponent(provider)}&voice_model=${encodeURIComponent(model)}`,
  );
  previewAudio.addEventListener(
    "canplay",
    () => {
      button.disabled = false;
      button.textContent = "Stop";
    },
    { once: true },
  );
  previewAudio.addEventListener("ended", () => resetPreviewButton(button), { once: true });
  previewAudio.addEventListener(
    "error",
    () => {
      resetPreviewButton(button);
      voiceStatus.textContent = "Preview failed";
    },
    { once: true },
  );

  try {
    await previewAudio.play();
  } catch (_error) {
    resetPreviewButton(button);
    voiceStatus.textContent = "Tap again to play";
  }
}

function resetPreviewButton(button) {
  button.disabled = false;
  button.textContent = "Play";
  if (activePreviewButton === button) activePreviewButton = null;
}

function syncRenderOptions() {
  const selectedQuality = getSelectedQuality();
  const fps = Number(renderFps.value || 30);
  const minutes = Number(maxMinutes.value || 3);

  qualityOptions.querySelectorAll(".quality-card").forEach((card) => {
    const input = card.querySelector('input[name="render_quality"]');
    const quality = RENDER_QUALITIES.find((item) => item.value === input.value) || RENDER_QUALITIES[1];
    const title = card.querySelector("strong");
    const speed = card.querySelector(".quality-speed");
    card.classList.toggle("selected", input.checked);
    title.textContent = quality.value;
    speed.textContent = quality.speed;
  });

  fpsOptions.querySelectorAll(".fps-pill").forEach((button) => {
    button.classList.toggle("selected", Number(button.dataset.fps) === fps);
  });

  const selectedEstimate = `About ${formatDuration(estimateRenderSeconds(minutes, selectedQuality.value, fps))}`;
  renderEstimate.textContent = selectedEstimate;
  qualityMode.textContent = selectedQuality.speed;
  renderTimeValue.textContent = selectedEstimate;
}

function getSelectedQuality() {
  const input = qualityOptions.querySelector('input[name="render_quality"]:checked');
  return RENDER_QUALITIES.find((quality) => quality.value === input?.value) || RENDER_QUALITIES[1];
}

function selectedRenderSummary() {
  const quality = getSelectedQuality();
  const fps = Number(renderFps.value || 30);
  return `${quality.value} at ${fps} FPS`;
}

function estimateRenderSeconds(minutes, qualityValue, fps) {
  const quality = RENDER_QUALITIES.find((item) => item.value === qualityValue) || RENDER_QUALITIES[1];
  const safeMinutes = Math.max(1, Math.min(Number(minutes || 3), 6));
  const fpsFactor = fps === 24 ? 0.84 : fps === 60 ? 1.82 : 1;
  return Math.ceil(22 + safeMinutes * 60 * quality.factor * fpsFactor);
}

function formatDuration(seconds) {
  const total = Math.max(1, Math.round(Number(seconds || 0)));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  if (minutes <= 0) return `${remainder}s`;
  if (remainder === 0) return `${minutes}m`;
  return `${minutes}m ${remainder}s`;
}
