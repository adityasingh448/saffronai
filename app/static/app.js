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

let pollTimer = null;
let previewAudio = new Audio();
let activePreviewButton = null;

loadVoices();

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "Drop or choose report PDF";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearInterval(pollTimer);
  setBusy(true);
  setStatus("queued", "Uploading report");
  markStep("pdf");
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
  pollJob(payload.job_id);
  pollTimer = setInterval(() => pollJob(payload.job_id), 1800);
});

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    clearInterval(pollTimer);
    showError("Could not read job status.");
    setBusy(false);
    return;
  }

  const job = await response.json();
  setStatus(job.status, job.stage);
  syncSteps(job.stage, job.status);

  if (job.status === "complete") {
    clearInterval(pollTimer);
    setBusy(false);
    renderResult(jobId, job);
  }

  if (job.status === "failed") {
    clearInterval(pollTimer);
    setBusy(false);
    showError(job.error || "Video generation failed.");
  }
}

function renderResult(jobId, job) {
  const format = job.summary?.video_format || job.inputs?.video_format || "horizontal";
  result.className = format === "vertical" ? "result vertical-result" : "result";
  result.innerHTML = `
    <video controls src="/api/jobs/${jobId}/video"></video>
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
    renderVoices(payload.voices || [], payload.default_voice_model, payload.preview_enabled);
  } catch (_error) {
    voiceStatus.textContent = "Default voice";
    voiceOptions.innerHTML = `
      <div class="voice-card selected">
        <label class="voice-main">
          <input type="radio" name="voice_model" value="aura-2-arcas-en" checked />
          <span>
            <strong>Arcas</strong>
            <em>Male · Natural, smooth, clear</em>
          </span>
        </label>
      </div>
    `;
  }
}

function renderVoices(voices, defaultVoiceModel, previewEnabled) {
  if (!voices.length) {
    voiceStatus.textContent = "Default voice";
    return;
  }

  voiceStatus.textContent = previewEnabled ? "Tap play to preview" : "Preview unavailable";
  voiceOptions.innerHTML = voices
    .map((voice) => {
      const checked = voice.model === defaultVoiceModel ? "checked" : "";
      const selected = checked ? " selected" : "";
      const recommended = voice.recommended ? `<span class="voice-badge">Recommended</span>` : "";
      const disabled = previewEnabled ? "" : "disabled";
      return `
        <div class="voice-card${selected}" data-model="${escapeHtml(voice.model)}">
          <label class="voice-main">
            <input type="radio" name="voice_model" value="${escapeHtml(voice.model)}" ${checked} />
            <span>
              <strong>${escapeHtml(voice.name)}</strong>
              <em>${escapeHtml(voice.gender)} · ${escapeHtml(voice.tone)}</em>
            </span>
          </label>
          ${recommended}
          <button class="voice-play" type="button" data-model="${escapeHtml(voice.model)}" aria-label="Play ${escapeHtml(voice.name)} preview" title="Play preview" ${disabled}>▶</button>
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

  if (activePreviewButton === button && !previewAudio.paused) {
    previewAudio.pause();
    previewAudio.currentTime = 0;
    resetPreviewButton(button);
    return;
  }

  if (activePreviewButton) resetPreviewButton(activePreviewButton);
  activePreviewButton = button;
  button.disabled = true;
  button.textContent = "…";

  previewAudio.pause();
  previewAudio = new Audio(`/api/voices/preview?voice_model=${encodeURIComponent(model)}`);
  previewAudio.addEventListener("canplay", () => {
    button.disabled = false;
    button.textContent = "■";
  }, { once: true });
  previewAudio.addEventListener("ended", () => resetPreviewButton(button), { once: true });
  previewAudio.addEventListener("error", () => {
    resetPreviewButton(button);
    voiceStatus.textContent = "Preview failed";
  }, { once: true });

  try {
    await previewAudio.play();
  } catch (_error) {
    resetPreviewButton(button);
    voiceStatus.textContent = "Tap again to play";
  }
}

function resetPreviewButton(button) {
  button.disabled = false;
  button.textContent = "▶";
  if (activePreviewButton === button) activePreviewButton = null;
}
