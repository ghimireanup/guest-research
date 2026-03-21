"""
Guest Research AI Agent - Flask Backend
Uses Google Gemini AI to research podcast guests and generate interview questions.
Saves output as a local .docx file you can download directly from the browser.
"""

import os
import json
import time
import threading
import queue
import uuid
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from google import genai
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# -------------------------------------------------------------------
# App setup
# -------------------------------------------------------------------

app = Flask(__name__, static_folder="static")
CORS(app)

# Gemini model to use
GEMINI_MODEL = "gemini-2.5-flash"

# Use /tmp on cloud servers (writable), or a local folder in development
DOCS_FOLDER = Path("/tmp/generated_docs") if os.getenv("RAILWAY_ENVIRONMENT") else Path("generated_docs")
DOCS_FOLDER.mkdir(exist_ok=True)

# Gemini client — initialised lazily so a missing key doesn't crash startup
_gemini_client = None

def get_gemini_client():
    """Return a cached Gemini client, creating it on first call."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    return _gemini_client

# -------------------------------------------------------------------
# In-memory session store
# -------------------------------------------------------------------

# Each research run gets a unique session_id with its own message queue
# so the background thread can push updates to the SSE stream.
research_sessions: dict[str, dict] = {}


# -------------------------------------------------------------------
# Helper: Web search via DuckDuckGo
# -------------------------------------------------------------------

def search_web(query: str, max_results: int = 6) -> list[dict]:
    """
    Search DuckDuckGo and return a list of results.
    Each result has: title, href (URL), body (snippet).
    Returns an empty list if the search fails.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        print(f"[search_web] Search failed for '{query}': {e}")
        return []


# -------------------------------------------------------------------
# Helper: Scrape a web page and return its plain text
# -------------------------------------------------------------------

def scrape_page(url: str, max_chars: int = 3000) -> str:
    """
    Fetch a URL and extract readable text from the HTML.
    Strips scripts, styles, and nav noise.
    Returns empty string if the page can't be fetched.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        # Remove noisy tags
        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "button", "iframe"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]

    except Exception as e:
        print(f"[scrape_page] Failed to scrape {url}: {e}")
        return ""


# -------------------------------------------------------------------
# Research Step 1: Gather background info about the guest
# -------------------------------------------------------------------

def research_guest_background(guest_name: str) -> str:
    """
    Searches DuckDuckGo for who the guest is, their company, and journey.
    Returns a combined block of raw text to be synthesised by Gemini.
    """
    search_queries = [
        f"{guest_name} entrepreneur founder biography",
        f"{guest_name} company startup career achievements",
        f"{guest_name} background story business",
    ]

    raw_text_parts = []

    for query in search_queries:
        results = search_web(query, max_results=4)

        for result in results[:3]:
            snippet = result.get("body", "")
            if snippet:
                raw_text_parts.append(f"[SNIPPET] {result.get('title', '')}: {snippet}")

            url = result.get("href", "")
            if url:
                page_text = scrape_page(url, max_chars=2000)
                if page_text:
                    raw_text_parts.append(f"[PAGE: {url}]\n{page_text}")

    # Deduplicate
    seen = set()
    unique_parts = []
    for part in raw_text_parts:
        if part not in seen:
            seen.add(part)
            unique_parts.append(part)

    return "\n\n---\n\n".join(unique_parts) if unique_parts else "No background information found."


# -------------------------------------------------------------------
# Research Step 2: Find past interviews and media appearances
# -------------------------------------------------------------------

def find_guest_interviews(guest_name: str) -> str:
    """
    Searches for podcasts, YouTube interviews, articles, and media appearances.
    Returns combined raw text for Gemini to synthesise.
    """
    search_queries = [
        f"{guest_name} podcast interview",
        f"{guest_name} YouTube interview talk",
        f"{guest_name} featured article press media",
        f'"{guest_name}" podcast guest appearance',
    ]

    raw_text_parts = []

    for query in search_queries:
        results = search_web(query, max_results=5)

        for result in results[:4]:
            title = result.get("title", "")
            url = result.get("href", "")
            snippet = result.get("body", "")
            entry = f"Title: {title}\nURL: {url}\nSnippet: {snippet}"
            raw_text_parts.append(entry)

    return "\n\n---\n\n".join(raw_text_parts) if raw_text_parts else "No interviews found."


# -------------------------------------------------------------------
# Gemini Step 1: Synthesise research into a guest brief
# -------------------------------------------------------------------

def build_context_with_gemini(
    guest_name: str, background_data: str, interview_data: str
) -> str:
    """
    Sends all raw research data to Gemini and asks it to write a structured
    guest brief covering who they are, what they've built, what they've already
    talked about publicly, and what angles haven't been explored yet.
    """
    prompt = f"""You are a professional podcast researcher preparing a guest brief.

Guest Name: {guest_name}

Below is raw research data collected from the web. It may be messy or repetitive — your job is to extract the signal, ignore the noise, and write a clean, insightful brief.

--- RAW BACKGROUND DATA ---
{background_data[:8000]}

--- RAW INTERVIEW / MEDIA DATA ---
{interview_data[:6000]}

Please write a comprehensive guest brief with EXACTLY these sections (use these headings):

## Who They Are
2-3 paragraphs covering their identity, background, and what makes them notable. Be specific — include company names, industries, and notable facts.

## What They've Built
A focused overview of their key companies, products, or projects. Explain why each matters and what problem it solved.

## What They Stand For
Their core beliefs, philosophy, values, and mission. What drives them?

## Past Interviews & Media Appearances
List each interview, podcast, or article you found. Format each as:
- **[Title / Platform]** — [One-sentence summary of what they discussed]

Be thorough. Include at least 5-10 appearances if the data supports it.

## What They've Already Talked About
Identify recurring themes and topics that come up across their past interviews. What are they known for saying?

## Unexplored Angles
Based on what they haven't been asked about, or where past interviewers stayed surface-level, identify 3-5 angles that could produce genuinely fresh insights.

Write in plain, direct language. Be specific and insightful."""

    response = get_gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text


# -------------------------------------------------------------------
# Gemini Step 2: Generate 15 interview questions
# -------------------------------------------------------------------

def generate_questions_with_gemini(guest_name: str, guest_brief: str) -> str:
    """
    Asks Gemini to generate 15 interview questions in three sections:
    - 5 background / journey questions
    - 5 deep insight questions
    - 5 never-been-asked questions based on identified gaps
    """
    prompt = f"""You are a world-class podcast host designing questions for a guest interview.

Guest: {guest_name}

Guest Brief:
{guest_brief[:5000]}

Generate exactly 15 interview questions organised into these three sections:

### Part 1: Background & Journey (5 Questions)
Questions that explore their personal story, pivotal moments, and the path that led them to where they are. Make them feel conversational and designed to surface a great story.

### Part 2: Deep Insights (5 Questions)
Questions that go deep on their expertise, work philosophy, and hard-won lessons. These should reveal thinking that audiences can learn from.

### Part 3: Never-Been-Asked Questions (5 Questions)
Based on the "Unexplored Angles" in the brief, craft 5 questions no interviewer has asked them before. These should feel genuinely surprising and fresh — not generic podcast questions.

Rules:
- Every question must be specific to THIS guest, not generic
- Every question must be open-ended (no yes/no questions)
- Each question should invite a story, not just a fact
- Number each question within its section (1-5)

After the questions, add a short "Interview Notes" section with 2-3 tactical tips for the interviewer based on what you know about this guest."""

    response = get_gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text


# -------------------------------------------------------------------
# Save output as a local .docx file
# -------------------------------------------------------------------

def save_to_docx(guest_name: str, guest_brief: str, questions: str) -> str:
    """
    Creates a nicely formatted Word document (.docx) with the full research output.
    Saves it to the generated_docs/ folder.
    Returns the filename so the frontend can build a download link.
    """
    doc = Document()

    # ── Document title ──────────────────────────────────────────
    today = datetime.now().strftime("%B %d, %Y")
    title_text = f"Guest Research — {guest_name}"

    title = doc.add_heading(title_text, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = doc.add_paragraph(today)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    doc.add_paragraph()  # Spacer

    # ── Guest Brief section ─────────────────────────────────────
    doc.add_heading("GUEST BRIEF", level=1)
    doc.add_paragraph()

    # Parse the markdown-ish brief and add it line by line with basic formatting
    _add_markdown_to_doc(doc, guest_brief)

    doc.add_page_break()

    # ── Interview Questions section ─────────────────────────────
    doc.add_heading("INTERVIEW QUESTIONS", level=1)
    doc.add_paragraph()

    _add_markdown_to_doc(doc, questions)

    # ── Save the file ───────────────────────────────────────────
    # Make the filename safe to use on disk
    safe_name = re.sub(r"[^\w\s-]", "", guest_name).strip().replace(" ", "_")
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"Guest_Research_{safe_name}_{date_str}.docx"
    filepath = DOCS_FOLDER / filename

    doc.save(filepath)
    print(f"[docx] Saved to {filepath}")
    return filename


def _add_markdown_to_doc(doc: Document, text: str):
    """
    Converts simple markdown-like text into Word document paragraphs.
    Handles ## headings, ### sub-headings, **bold**, and bullet lists.
    """
    for line in text.split("\n"):
        line = line.strip()

        if not line:
            doc.add_paragraph()  # Blank line → empty paragraph spacer
            continue

        if line.startswith("## "):
            doc.add_heading(line[3:], level=2)

        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)

        elif line.startswith("- ") or line.startswith("* "):
            # Bullet point — strip the leading marker
            content = line[2:]
            para = doc.add_paragraph(style="List Bullet")
            _add_inline_bold(para, content)

        elif re.match(r"^\d+\.\s", line):
            # Numbered list item
            content = re.sub(r"^\d+\.\s*", "", line)
            para = doc.add_paragraph(style="List Number")
            _add_inline_bold(para, content)

        else:
            # Regular paragraph
            para = doc.add_paragraph()
            _add_inline_bold(para, line)


def _add_inline_bold(para, text: str):
    """
    Splits text on **bold** markers and adds runs with bold formatting applied.
    """
    parts = re.split(r"\*\*(.+?)\*\*", text)
    for i, part in enumerate(parts):
        if not part:
            continue
        run = para.add_run(part)
        run.bold = (i % 2 == 1)   # Odd-indexed parts were inside **...**


# -------------------------------------------------------------------
# Core research orchestrator — runs in a background thread
# -------------------------------------------------------------------

def run_research(session_id: str, guest_name: str):
    """
    Orchestrates the full research pipeline and pushes status/result
    updates into the session queue so the SSE stream forwards them
    to the browser in real time.
    """
    q: queue.Queue = research_sessions[session_id]["queue"]

    def push(data: dict):
        q.put(data)

    try:
        # ── Step 1: Research background ──────────────────────────
        push({"type": "status", "step": 1, "message": f"Searching the web for information about {guest_name}..."})
        background_data = research_guest_background(guest_name)
        push({"type": "status", "step": 1, "message": "Background research complete."})

        # ── Step 2: Find interviews ──────────────────────────────
        push({"type": "status", "step": 2, "message": "Searching for past interviews and media appearances..."})
        interview_data = find_guest_interviews(guest_name)
        push({"type": "status", "step": 2, "message": "Interview search complete."})

        # ── Step 3: Build guest brief with Gemini ────────────────
        push({"type": "status", "step": 3, "message": "Asking Gemini to synthesise everything into a guest brief..."})
        guest_brief = build_context_with_gemini(guest_name, background_data, interview_data)
        push({"type": "section", "section": "brief", "content": guest_brief})

        # ── Step 4: Generate questions with Gemini ───────────────
        push({"type": "status", "step": 4, "message": "Generating 15 interview questions..."})
        questions = generate_questions_with_gemini(guest_name, guest_brief)
        push({"type": "section", "section": "questions", "content": questions})

        # ── Step 5: Save as local .docx ──────────────────────────
        push({"type": "status", "step": 5, "message": "Saving Word document..."})
        filename = save_to_docx(guest_name, guest_brief, questions)
        push({"type": "docx", "filename": filename})

        push({"type": "complete"})

    except Exception as e:
        push({"type": "error", "message": f"Research failed: {str(e)}"})


# -------------------------------------------------------------------
# API Routes
# -------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the main frontend page."""
    return send_from_directory("static", "index.html")


@app.route("/api/research", methods=["POST"])
def start_research():
    """
    Start a research session for a guest.
    Expects JSON: { "guest_name": "Sara Blakely" }
    Returns: { "session_id": "..." }
    """
    data = request.get_json()
    guest_name = (data or {}).get("guest_name", "").strip()

    if not guest_name:
        return jsonify({"error": "guest_name is required"}), 400

    if not os.getenv("GOOGLE_API_KEY"):
        return jsonify({"error": "GOOGLE_API_KEY is not set in .env"}), 500

    session_id = str(uuid.uuid4())
    research_sessions[session_id] = {
        "queue": queue.Queue(),
        "guest_name": guest_name,
        "created_at": time.time(),
    }

    # Run research in a background thread so this request returns immediately
    thread = threading.Thread(
        target=run_research, args=(session_id, guest_name), daemon=True
    )
    thread.start()

    return jsonify({"session_id": session_id})


@app.route("/api/stream/<session_id>")
def stream_research(session_id: str):
    """
    Server-Sent Events stream — the browser connects here and receives
    live updates as each research step completes.
    """
    if session_id not in research_sessions:
        return jsonify({"error": "Session not found"}), 404

    def generate():
        q: queue.Queue = research_sessions[session_id]["queue"]
        while True:
            try:
                data = q.get(timeout=60)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") in ("complete", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/download/<filename>")
def download_file(filename: str):
    """
    Serves a generated .docx file as a browser download.
    The frontend gets the filename from the SSE 'docx' event and
    builds a link to this endpoint.
    """
    # Security: strip any path traversal attempts
    safe_filename = Path(filename).name
    filepath = DOCS_FOLDER / safe_filename

    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404

    return send_file(
        filepath,
        as_attachment=True,
        download_name=safe_filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# -------------------------------------------------------------------
# Start the server
# -------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n🎙️  Guest Research Agent running at http://localhost:{port}\n")
    app.run(debug=True, port=port, threaded=True)
