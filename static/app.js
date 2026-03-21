/**
 * Guest Research Agent — Frontend JavaScript
 * Uses polling (/api/status every 2s) instead of SSE for cloud compatibility.
 */

window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("guestInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startResearch();
  });
});


// ─────────────────────────────────────────────────────────────────
// Main entry point
// ─────────────────────────────────────────────────────────────────

async function startResearch() {
  const input = document.getElementById("guestInput");
  const guestName = input.value.trim();
  if (!guestName) { input.focus(); return; }

  resetUI();

  const btn = document.getElementById("researchBtn");
  btn.disabled = true;
  btn.textContent = "Researching…";

  document.getElementById("progressSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.remove("hidden");

  try {
    // Ask the backend to start a research session
    const res = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guest_name: guestName }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Failed to start research");
    }

    const { session_id } = await res.json();

    // Start polling for progress
    pollStatus(session_id, btn);

  } catch (err) {
    showError(err.message);
    resetButton(btn);
  }
}


// ─────────────────────────────────────────────────────────────────
// Polling — check /api/status every 2 seconds
// ─────────────────────────────────────────────────────────────────

// Track which sections we've already rendered so we don't re-render on every poll
const _renderedSections = new Set();

async function pollStatus(sessionId, btn) {
  let consecutiveErrors = 0;

  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${sessionId}`);
      if (!res.ok) throw new Error("Status check failed");

      const data = await res.json();
      consecutiveErrors = 0;

      // Update progress step indicator
      if (data.step) activateStep(data.step);

      // Update status text
      if (data.status) setStatus(data.status);

      // Render sections as they arrive (only once each)
      if (data.sections?.brief && !_renderedSections.has("brief")) {
        _renderedSections.add("brief");
        renderSection("briefSection", "briefContent", data.sections.brief);
        markStepDone(3);
      }
      if (data.sections?.questions && !_renderedSections.has("questions")) {
        _renderedSections.add("questions");
        renderSection("questionsSection", "questionsContent", data.sections.questions);
        markStepDone(4);
      }

      // Check if done
      if (data.done) {
        clearInterval(interval);

        if (data.error) {
          showError(data.error);
        } else {
          if (data.docx_filename) {
            showDocxLink(data.docx_filename);
            markStepDone(5);
          }
          setStatus("Research complete! ✓");
        }
        resetButton(btn);
      }

    } catch (err) {
      consecutiveErrors++;
      // Give up after 5 consecutive failures
      if (consecutiveErrors >= 5) {
        clearInterval(interval);
        showError("Lost connection to server. Please try again.");
        resetButton(btn);
      }
    }
  }, 2000); // Poll every 2 seconds
}


// ─────────────────────────────────────────────────────────────────
// Progress step helpers
// ─────────────────────────────────────────────────────────────────

const STEP_IDS = ["step1", "step2", "step3", "step4", "step5"];

function activateStep(stepNumber) {
  STEP_IDS.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    const num = i + 1;
    if (num < stepNumber)      { el.classList.remove("active"); el.classList.add("done"); }
    else if (num === stepNumber){ el.classList.add("active"); el.classList.remove("done"); }
    else                        { el.classList.remove("active", "done"); }
  });
  updateStepLines(stepNumber);
}

function markStepDone(stepNumber) {
  const el = document.getElementById(`step${stepNumber}`);
  if (el) { el.classList.remove("active"); el.classList.add("done"); }
  updateStepLines(stepNumber + 1);
}

function updateStepLines(upToStep) {
  document.querySelectorAll(".step-line").forEach((line, i) => {
    if (i + 1 < upToStep) line.classList.add("done");
    else line.classList.remove("done");
  });
}

function setStatus(message) {
  document.getElementById("statusText").textContent = message;
}


// ─────────────────────────────────────────────────────────────────
// Content rendering
// ─────────────────────────────────────────────────────────────────

function markdownToHtml(text) {
  let html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+?)\*/g, "<em>$1</em>");
  html = html.replace(/((?:^- .+\n?)+)/gm, (match) => {
    const items = match.split("\n").filter(l => l.startsWith("- "))
      .map(l => `<li>${l.slice(2)}</li>`).join("\n");
    return `<ul>${items}</ul>`;
  });
  html = html.replace(/((?:^\d+\. .+\n?)+)/gm, (match) => {
    const items = match.split("\n").filter(l => /^\d+\./.test(l))
      .map(l => `<li>${l.replace(/^\d+\.\s*/, "")}</li>`).join("\n");
    return `<ol>${items}</ol>`;
  });
  html = html.split(/\n{2,}/).map(block => {
    block = block.trim();
    if (!block) return "";
    if (/^<(h[1-6]|ul|ol)/.test(block)) return block;
    return `<p>${block.replace(/\n/g, " ")}</p>`;
  }).join("\n");
  return html;
}

function renderSection(sectionId, contentId, markdownText) {
  const section = document.getElementById(sectionId);
  document.getElementById(contentId).innerHTML = markdownToHtml(markdownText);
  section.classList.remove("hidden");
  section.classList.add("fade-in");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showDocxLink(filename) {
  const section = document.getElementById("gdocSection");
  const link = document.getElementById("gdocLink");
  link.href = `/api/download/${encodeURIComponent(filename)}`;
  link.textContent = `Download ${filename} →`;
  section.classList.remove("hidden");
  section.classList.add("fade-in");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showError(message) {
  document.getElementById("errorText").textContent = message;
  document.getElementById("errorSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.remove("hidden");
}


// ─────────────────────────────────────────────────────────────────
// UI reset
// ─────────────────────────────────────────────────────────────────

function resetButton(btn) {
  btn.disabled = false;
  btn.textContent = "Research Guest";
}

function resetUI() {
  _renderedSections.clear();
  ["progressSection", "resultsSection", "briefSection", "questionsSection",
   "gdocSection", "errorSection"].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.classList.add("hidden"); el.classList.remove("fade-in"); }
  });
  ["briefContent", "questionsContent"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  STEP_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active", "done");
  });
  document.querySelectorAll(".step-line").forEach(l => l.classList.remove("done"));
  setStatus("Starting research…");
}
