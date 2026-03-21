/**
 * Guest Research Agent — Frontend JavaScript
 *
 * Flow:
 *  1. User types a guest name and clicks "Research Guest"
 *  2. We POST to /api/research → get back a session_id
 *  3. We open an SSE connection to /api/stream/<session_id>
 *  4. As events arrive we update the progress steps and render content
 *  5. When complete we show a link to the saved Google Doc (if available)
 */

// ─────────────────────────────────────────────────────────────────
// Check Google auth status on page load
// ─────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  // Allow pressing Enter in the input to trigger research
  document.getElementById("guestInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startResearch();
  });
});

// ─────────────────────────────────────────────────────────────────
// Main entry point — called when the button is clicked
// ─────────────────────────────────────────────────────────────────

async function startResearch() {
  const input = document.getElementById("guestInput");
  const guestName = input.value.trim();

  if (!guestName) {
    input.focus();
    return;
  }

  // Reset UI from any previous run
  resetUI();

  // Disable the button while researching
  const btn = document.getElementById("researchBtn");
  btn.disabled = true;
  btn.textContent = "Researching…";

  // Show the progress section
  document.getElementById("progressSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.remove("hidden");

  try {
    // Step 1: Ask the backend to start a research session
    const startRes = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guest_name: guestName }),
    });

    if (!startRes.ok) {
      const err = await startRes.json();
      throw new Error(err.error || "Failed to start research");
    }

    const { session_id } = await startRes.json();

    // Step 2: Open a Server-Sent Events stream to get live updates
    listenToStream(session_id, guestName, btn);

  } catch (err) {
    showError(err.message);
    resetButton(btn);
  }
}


// ─────────────────────────────────────────────────────────────────
// SSE stream listener
// ─────────────────────────────────────────────────────────────────

/**
 * Connect to the SSE endpoint and handle each event type as it arrives.
 * Updates the progress indicators and renders content sections in real time.
 */
function listenToStream(sessionId, guestName, btn) {
  const evtSource = new EventSource(`/api/stream/${sessionId}`);

  evtSource.onmessage = (event) => {
    // All our events are JSON
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return; // Ignore malformed events (e.g. heartbeat comments handled natively)
    }

    switch (data.type) {

      // ── Progress status update ─────────────────────────────
      case "status":
        setStatus(data.message);
        if (data.step) activateStep(data.step);
        break;

      // ── A content section is ready ─────────────────────────
      case "section":
        if (data.section === "brief") {
          renderSection("briefSection", "briefContent", data.content);
          markStepDone(3);
        } else if (data.section === "questions") {
          renderSection("questionsSection", "questionsContent", data.content);
          markStepDone(4);
        }
        break;

      // ── Local .docx saved ─────────────────────────────────
      case "docx":
        showDocxLink(data.filename);
        markStepDone(5);
        break;

      // ── Research complete ──────────────────────────────────
      case "complete":
        setStatus("Research complete! ✓");
        resetButton(btn);
        evtSource.close();
        break;

      // ── Something went wrong ───────────────────────────────
      case "error":
        showError(data.message);
        resetButton(btn);
        evtSource.close();
        break;
    }
  };

  evtSource.onerror = () => {
    // The SSE connection dropped (normal after "complete" event fires,
    // but handle unexpected drops gracefully too)
    evtSource.close();
    resetButton(btn);
  };
}


// ─────────────────────────────────────────────────────────────────
// Progress step helpers
// ─────────────────────────────────────────────────────────────────

/**
 * Map step numbers to DOM element IDs.
 * Steps: 1=Research, 2=Interviews, 3=Context, 4=Questions, 5=Saving
 */
const STEP_IDS = ["step1", "step2", "step3", "step4", "step5"];

/** Highlight a step as currently active (pulsing dot). */
function activateStep(stepNumber) {
  STEP_IDS.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    const num = i + 1;
    if (num < stepNumber) {
      el.classList.remove("active");
      el.classList.add("done");
    } else if (num === stepNumber) {
      el.classList.add("active");
      el.classList.remove("done");
    } else {
      el.classList.remove("active", "done");
    }
  });

  // Also colour the connector lines between completed steps
  updateStepLines(stepNumber);
}

/** Mark a step as done (green dot). */
function markStepDone(stepNumber) {
  const el = document.getElementById(`step${stepNumber}`);
  if (el) {
    el.classList.remove("active");
    el.classList.add("done");
  }
  updateStepLines(stepNumber + 1);
}

/** Colour the connector lines up to the current step. */
function updateStepLines(upToStep) {
  const lines = document.querySelectorAll(".step-line");
  lines.forEach((line, i) => {
    if (i + 1 < upToStep) line.classList.add("done");
    else line.classList.remove("done");
  });
}

/** Update the status text below the progress steps. */
function setStatus(message) {
  document.getElementById("statusText").textContent = message;
}


// ─────────────────────────────────────────────────────────────────
// Content rendering
// ─────────────────────────────────────────────────────────────────

/**
 * Render a markdown-like text string into a container element.
 * We do a lightweight conversion: ## headings, **bold**, lists, paragraphs.
 * No external markdown library needed — keeps dependencies minimal.
 */
function markdownToHtml(text) {
  // Escape HTML entities first to prevent XSS
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Headers: ### H3 and ## H2
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");

  // Bold: **text**
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Italic: *text*  (only single asterisks not already consumed by bold)
  html = html.replace(/\*([^*]+?)\*/g, "<em>$1</em>");

  // Unordered lists: lines starting with "- "
  // Wrap consecutive list items in <ul>
  html = html.replace(/((?:^- .+\n?)+)/gm, (match) => {
    const items = match
      .split("\n")
      .filter((l) => l.startsWith("- "))
      .map((l) => `<li>${l.slice(2)}</li>`)
      .join("\n");
    return `<ul>${items}</ul>`;
  });

  // Ordered lists: lines starting with "1. " etc.
  html = html.replace(/((?:^\d+\. .+\n?)+)/gm, (match) => {
    const items = match
      .split("\n")
      .filter((l) => /^\d+\./.test(l))
      .map((l) => `<li>${l.replace(/^\d+\.\s*/, "")}</li>`)
      .join("\n");
    return `<ol>${items}</ol>`;
  });

  // Paragraphs: blank-line separated blocks that aren't already HTML tags
  html = html
    .split(/\n{2,}/)
    .map((block) => {
      block = block.trim();
      if (!block) return "";
      // If already a block-level tag, don't wrap in <p>
      if (/^<(h[1-6]|ul|ol|li|blockquote|pre)/.test(block)) return block;
      return `<p>${block.replace(/\n/g, " ")}</p>`;
    })
    .join("\n");

  return html;
}

/**
 * Show a result section and populate it with rendered content.
 * Adds a fade-in animation.
 */
function renderSection(sectionId, contentId, markdownText) {
  const section = document.getElementById(sectionId);
  const content = document.getElementById(contentId);

  content.innerHTML = markdownToHtml(markdownText);
  section.classList.remove("hidden");
  section.classList.add("fade-in");

  // Smooth-scroll so the new section is visible
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Show the Word doc download card. */
function showDocxLink(filename) {
  const section = document.getElementById("gdocSection");
  const link = document.getElementById("gdocLink");

  link.href = `/api/download/${encodeURIComponent(filename)}`;
  link.textContent = `Download ${filename} →`;
  section.classList.remove("hidden");
  section.classList.add("fade-in");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Show an error card. */
function showError(message) {
  const section = document.getElementById("errorSection");
  document.getElementById("errorText").textContent = message;
  section.classList.remove("hidden");
  document.getElementById("resultsSection").classList.remove("hidden");
}


// ─────────────────────────────────────────────────────────────────
// UI reset helpers
// ─────────────────────────────────────────────────────────────────

/** Re-enable the Research button and restore its label. */
function resetButton(btn) {
  btn.disabled = false;
  btn.textContent = "Research Guest";
}

/** Clear all result sections and progress state from a previous run. */
function resetUI() {
  // Hide all result / error sections
  [
    "progressSection", "resultsSection",
    "briefSection", "questionsSection",
    "gdocSection", "errorSection",
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.add("hidden");
      el.classList.remove("fade-in");
    }
  });

  // Clear content
  ["briefContent", "questionsContent"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });

  // Reset all step indicators
  STEP_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active", "done");
  });
  document.querySelectorAll(".step-line").forEach((l) => l.classList.remove("done"));

  // Reset status text
  setStatus("Starting research…");
}
