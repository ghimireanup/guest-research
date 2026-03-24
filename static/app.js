/**
 * Guest Research Agent — Frontend JavaScript
 * Handles: link rows, context field, polling, image gallery, results rendering.
 */

window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("guestInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startResearch();
  });
});


// ─────────────────────────────────────────────────────────────────
// Link row management — add/remove URL input rows
// ─────────────────────────────────────────────────────────────────

function addLinkRow() {
  const container = document.getElementById("linksContainer");
  const row = document.createElement("div");
  row.className = "link-row flex items-center gap-2 fade-in";
  row.innerHTML = `
    <div class="relative flex-1">
      <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline text-base">link</span>
      <input type="url" placeholder="https://…" class="field-input pl-9 text-xs" />
    </div>
    <button onclick="removeLinkRow(this)"
      class="shrink-0 w-8 h-9 rounded-lg bg-surface-container flex items-center justify-center hover:bg-red-50 hover:text-red-500 transition-colors"
      title="Remove">
      <span class="material-symbols-outlined text-base">remove</span>
    </button>`;
  container.appendChild(row);
  row.querySelector("input").focus();
}

function removeLinkRow(btn) {
  const row = btn.closest(".link-row");
  // Don't remove if it's the only row
  if (document.querySelectorAll(".link-row").length > 1) {
    row.remove();
  }
}

// Collect all non-empty link values from the rows
function collectLinks() {
  return Array.from(document.querySelectorAll(".link-row input"))
    .map(i => i.value.trim())
    .filter(Boolean);
}


// ─────────────────────────────────────────────────────────────────
// Main entry point
// ─────────────────────────────────────────────────────────────────

async function startResearch() {
  const guestName = document.getElementById("guestInput").value.trim();
  if (!guestName) { document.getElementById("guestInput").focus(); return; }

  const links   = collectLinks();
  const context = document.getElementById("contextInput").value.trim();

  resetUI();

  const btn = document.getElementById("researchBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="material-symbols-outlined text-xl animate-spin">refresh</span> Researching…`;

  document.getElementById("progressSection").classList.remove("hidden");
  document.getElementById("resultsSection").classList.remove("hidden");

  try {
    const res = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guest_name: guestName, links, context }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Failed to start research");
    }

    const { session_id } = await res.json();
    pollStatus(session_id, btn);

  } catch (err) {
    showError(err.message);
    resetButton(btn);
  }
}


// ─────────────────────────────────────────────────────────────────
// Polling — hits /api/status every 2 seconds
// ─────────────────────────────────────────────────────────────────

const _renderedSections = new Set();
let _renderedImageCount = 0;

async function pollStatus(sessionId, btn) {
  let consecutiveErrors = 0;

  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${sessionId}`);
      if (!res.ok) throw new Error("Status check failed");

      const data = await res.json();
      consecutiveErrors = 0;

      if (data.step)   activateStep(data.step);
      if (data.status) setStatus(data.status);

      // Render images as soon as they're available (backend sets them after step 1)
      if (data.images?.length && data.images.length > _renderedImageCount) {
        renderImages(data.images);
        _renderedImageCount = data.images.length;
      }

      // Render sections as they arrive — only once each
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

      if (data.done) {
        clearInterval(interval);
        if (data.error) {
          showError(data.error);
        } else {
          if (data.download_ready) {
            showDocxLink(sessionId);
            markStepDone(5);
          }
          setStatus("Research complete ✓");
        }
        resetButton(btn);
      }

    } catch (err) {
      consecutiveErrors++;
      if (consecutiveErrors >= 5) {
        clearInterval(interval);
        showError("Lost connection to server. Please try again.");
        resetButton(btn);
      }
    }
  }, 2000);
}


// ─────────────────────────────────────────────────────────────────
// Image gallery renderer
// ─────────────────────────────────────────────────────────────────

function renderImages(imageUrls) {
  const section = document.getElementById("imageSection");
  const strip   = document.getElementById("imageStrip");

  // Only add new images (avoid duplicates on repeated polls)
  const existing = new Set(
    Array.from(strip.querySelectorAll("img")).map(i => i.src)
  );

  imageUrls.forEach(url => {
    if (existing.has(url)) return;
    existing.add(url);

    const wrapper = document.createElement("div");
    wrapper.className = "shrink-0 fade-in";

    const img = document.createElement("img");
    img.src = url;
    img.alt = "Guest photo";
    img.className = "h-36 w-36 object-cover rounded-xl border border-outline-variant/20 shadow-sm bg-surface-container";

    // Remove broken images gracefully
    img.onerror = () => wrapper.remove();

    wrapper.appendChild(img);
    strip.appendChild(wrapper);
  });

  if (strip.children.length > 0) {
    section.classList.remove("hidden");
    section.classList.add("fade-in");
  }
}


// ─────────────────────────────────────────────────────────────────
// Progress step helpers
// ─────────────────────────────────────────────────────────────────

const STEP_IDS = ["step1", "step2", "step3", "step4", "step5"];

function activateStep(n) {
  STEP_IDS.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    const num = i + 1;
    if      (num < n)  { el.classList.remove("active"); el.classList.add("done"); }
    else if (num === n){ el.classList.add("active");    el.classList.remove("done"); }
    else               { el.classList.remove("active",  "done"); }
  });
  updateStepLines(n);
}

function markStepDone(n) {
  const el = document.getElementById(`step${n}`);
  if (el) { el.classList.remove("active"); el.classList.add("done"); }
  updateStepLines(n + 1);
}

function updateStepLines(upTo) {
  document.querySelectorAll(".step-line").forEach((line, i) => {
    if (i + 1 < upTo) line.classList.add("done");
    else               line.classList.remove("done");
  });
}

function setStatus(msg) {
  document.getElementById("statusText").textContent = msg;
}


// ─────────────────────────────────────────────────────────────────
// Content rendering — minimal markdown → HTML
// ─────────────────────────────────────────────────────────────────

function markdownToHtml(text) {
  let html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  html = html.replace(/^### (.+)$/gm,  "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm,   "<h2>$1</h2>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+?)\*/g,  "<em>$1</em>");
  // Bullet lists
  html = html.replace(/((?:^- .+\n?)+)/gm, match => {
    const items = match.split("\n").filter(l => l.startsWith("- "))
      .map(l => `<li>${l.slice(2)}</li>`).join("");
    return `<ul>${items}</ul>`;
  });
  // Numbered lists
  html = html.replace(/((?:^\d+\. .+\n?)+)/gm, match => {
    const items = match.split("\n").filter(l => /^\d+\./.test(l))
      .map(l => `<li>${l.replace(/^\d+\.\s*/, "")}</li>`).join("");
    return `<ol>${items}</ol>`;
  });
  // Paragraphs
  html = html.split(/\n{2,}/).map(block => {
    block = block.trim();
    if (!block) return "";
    if (/^<(h[1-6]|ul|ol)/.test(block)) return block;
    return `<p>${block.replace(/\n/g, " ")}</p>`;
  }).join("\n");
  return html;
}

function renderSection(sectionId, contentId, markdown) {
  const section = document.getElementById(sectionId);
  document.getElementById(contentId).innerHTML = markdownToHtml(markdown);
  section.classList.remove("hidden");
  section.classList.add("fade-in");
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showDocxLink(sessionId) {
  const section = document.getElementById("gdocSection");
  document.getElementById("gdocLink").href = `/api/download/${sessionId}`;
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
  btn.innerHTML = `<span class="material-symbols-outlined text-xl" style="font-variation-settings:'FILL' 1;">search</span> Research Guest`;
}

function resetUI() {
  _renderedSections.clear();
  _renderedImageCount = 0;

  ["progressSection","resultsSection","briefSection","questionsSection",
   "gdocSection","errorSection","imageSection"].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.classList.add("hidden"); el.classList.remove("fade-in"); }
  });
  ["briefContent","questionsContent"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  document.getElementById("imageStrip").innerHTML = "";
  STEP_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active","done");
  });
  document.querySelectorAll(".step-line").forEach(l => l.classList.remove("done"));
  setStatus("Starting…");
}
