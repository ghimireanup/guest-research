"""
Guest Research AI Agent - Flask Backend
Uses Google Gemini AI to research podcast guests and generate interview questions.
Uses a polling model (no SSE) for maximum compatibility with cloud hosts.
"""

import os
import json
import time
import threading
import uuid
import re
from datetime import datetime

import io
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from google import genai
from docx import Document
from docx.shared import RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------------------
# App setup
# -------------------------------------------------------------------

app = Flask(__name__, static_folder="static")
CORS(app)

GEMINI_MODEL = "gemini-2.5-flash"

# Lazy Gemini client — created on first use so a missing key won't crash startup
_gemini_client = None

def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    return _gemini_client


# -------------------------------------------------------------------
# In-memory session store
# Each session tracks: status messages, completed sections, and whether done.
# The frontend polls /api/status/<session_id> every 2 seconds.
# -------------------------------------------------------------------

research_sessions: dict[str, dict] = {}


def new_session(guest_name: str) -> str:
    session_id = str(uuid.uuid4())
    research_sessions[session_id] = {
        "guest_name": guest_name,
        "status": "Starting research...",
        "step": 0,
        "sections": {},   # filled in as each section completes
        "brief": None,    # stored for docx generation on demand
        "questions": None,
        "done": False,
        "error": None,
        "created_at": time.time(),
    }
    return session_id


def update_session(session_id: str, **kwargs):
    if session_id in research_sessions:
        research_sessions[session_id].update(kwargs)


# -------------------------------------------------------------------
# Web search via DuckDuckGo
# -------------------------------------------------------------------

def search_web(query: str, max_results: int = 6) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"[search] Failed: {e}")
        return []


# -------------------------------------------------------------------
# Scrape a web page for plain text
# -------------------------------------------------------------------

def scrape_page(url: str, max_chars: int = 3000) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))
        return text[:max_chars]
    except Exception as e:
        print(f"[scrape] Failed {url}: {e}")
        return ""


# -------------------------------------------------------------------
# Research Step 1: Guest background
# -------------------------------------------------------------------

def research_guest_background(guest_name: str) -> str:
    queries = [
        f"{guest_name} entrepreneur founder biography",
        f"{guest_name} company startup career achievements",
        f"{guest_name} background story business",
    ]
    parts = []
    for query in queries:
        for result in search_web(query, 4)[:3]:
            if result.get("body"):
                parts.append(f"[SNIPPET] {result.get('title','')}: {result['body']}")
            if result.get("href"):
                page = scrape_page(result["href"], 2000)
                if page:
                    parts.append(f"[PAGE: {result['href']}]\n{page}")
    seen, unique = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p); unique.append(p)
    return "\n\n---\n\n".join(unique) or "No background information found."


# -------------------------------------------------------------------
# Research Step 2: Past interviews
# -------------------------------------------------------------------

def find_guest_interviews(guest_name: str) -> str:
    queries = [
        f"{guest_name} podcast interview",
        f"{guest_name} YouTube interview talk",
        f"{guest_name} featured article press media",
        f'"{guest_name}" podcast guest appearance',
    ]
    parts = []
    for query in queries:
        for result in search_web(query, 5)[:4]:
            parts.append(
                f"Title: {result.get('title','')}\n"
                f"URL: {result.get('href','')}\n"
                f"Snippet: {result.get('body','')}"
            )
    return "\n\n---\n\n".join(parts) or "No interviews found."


# -------------------------------------------------------------------
# Gemini: Build guest brief
# -------------------------------------------------------------------

def build_context_with_gemini(guest_name: str, background: str, interviews: str) -> str:
    prompt = f"""You are a professional podcast researcher preparing a guest brief.

Guest Name: {guest_name}

--- RAW BACKGROUND DATA ---
{background[:8000]}

--- RAW INTERVIEW / MEDIA DATA ---
{interviews[:6000]}

Write a comprehensive guest brief with EXACTLY these sections:

## Who They Are
2-3 paragraphs covering their identity and what makes them notable.

## What They've Built
Key companies, products, or projects and why they matter.

## What They Stand For
Core beliefs, philosophy, and mission.

## Past Interviews & Media Appearances
List each as: - **[Title / Platform]** — [One-sentence summary]

## What They've Already Talked About
Recurring themes across their past interviews.

## Unexplored Angles
3-5 angles no interviewer has explored yet."""

    return get_gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt).text


# -------------------------------------------------------------------
# Gemini: Generate 15 questions
# -------------------------------------------------------------------

def generate_questions_with_gemini(guest_name: str, brief: str) -> str:
    prompt = f"""You are a world-class podcast host designing interview questions.

Guest: {guest_name}

Guest Brief:
{brief[:5000]}

Generate exactly 15 interview questions in three sections:

### Part 1: Background & Journey (5 Questions)
Personal story and pivotal moments.

### Part 2: Deep Insights (5 Questions)
Expertise, work philosophy, hard-won lessons.

### Part 3: Never-Been-Asked Questions (5 Questions)
Based on the Unexplored Angles — questions no interviewer has asked before.

Rules: specific to this guest, open-ended, designed to get a story. Number 1-5 per section.

End with an "Interview Notes" section: 2-3 tactical tips for the interviewer."""

    return get_gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt).text


# -------------------------------------------------------------------
# Build a .docx in memory and return bytes — no disk writes needed
# -------------------------------------------------------------------

def build_docx_bytes(guest_name: str, brief: str, questions: str) -> bytes:
    """Creates a Word document entirely in memory and returns raw bytes."""
    doc = Document()
    today = datetime.now().strftime("%B %d, %Y")

    title = doc.add_heading(f"Guest Research — {guest_name}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph(today)
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    doc.add_paragraph()

    doc.add_heading("GUEST BRIEF", level=1)
    _add_markdown_to_doc(doc, brief)
    doc.add_page_break()
    doc.add_heading("INTERVIEW QUESTIONS", level=1)
    _add_markdown_to_doc(doc, questions)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _add_markdown_to_doc(doc, text: str):
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            doc.add_paragraph()
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith(("- ", "* ")):
            _inline_bold(doc.add_paragraph(style="List Bullet"), line[2:])
        elif re.match(r"^\d+\.\s", line):
            _inline_bold(doc.add_paragraph(style="List Number"), re.sub(r"^\d+\.\s*", "", line))
        else:
            _inline_bold(doc.add_paragraph(), line)


def _inline_bold(para, text: str):
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            para.add_run(part).bold = (i % 2 == 1)


# -------------------------------------------------------------------
# Research pipeline — runs in a background thread
# -------------------------------------------------------------------

def run_research(session_id: str, guest_name: str):
    try:
        update_session(session_id, step=1, status=f"Searching the web for {guest_name}...")
        background = research_guest_background(guest_name)

        update_session(session_id, step=2, status="Finding past interviews and media appearances...")
        interviews = find_guest_interviews(guest_name)

        update_session(session_id, step=3, status="Building guest brief with Gemini AI...")
        brief = build_context_with_gemini(guest_name, background, interviews)
        update_session(session_id, sections={"brief": brief})

        update_session(session_id, step=4, status="Generating 15 interview questions...")
        questions = generate_questions_with_gemini(guest_name, brief)
        research_sessions[session_id]["sections"]["questions"] = questions

        # Store brief and questions in session so /api/download can build the docx on demand
        update_session(session_id, step=5, status="Done!", done=True,
                       brief=brief, questions=questions)

    except Exception as e:
        print(f"[research] Error: {e}")
        update_session(session_id, done=True, error=str(e))


# -------------------------------------------------------------------
# API Routes
# -------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/research", methods=["POST"])
def start_research():
    data = request.get_json()
    guest_name = (data or {}).get("guest_name", "").strip()

    if not guest_name:
        return jsonify({"error": "guest_name is required"}), 400
    if not os.getenv("GOOGLE_API_KEY"):
        return jsonify({"error": "GOOGLE_API_KEY is not set"}), 500

    session_id = new_session(guest_name)
    threading.Thread(target=run_research, args=(session_id, guest_name), daemon=True).start()
    return jsonify({"session_id": session_id})


@app.route("/api/status/<session_id>")
def get_status(session_id: str):
    """
    The frontend polls this every 2 seconds.
    Returns the current step, status message, any completed sections, and done flag.
    """
    sess = research_sessions.get(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    return jsonify({
        "step":     sess["step"],
        "status":   sess["status"],
        "sections": sess["sections"],
        "done":     sess["done"],
        "error":    sess["error"],
        # Tell the frontend a download is ready once brief+questions are stored
        "download_ready": sess["brief"] is not None and sess["questions"] is not None,
    })


@app.route("/api/download/<session_id>")
def download_file(session_id: str):
    """Generate the .docx in memory on demand and stream it to the browser."""
    sess = research_sessions.get(session_id)
    if not sess or not sess.get("brief") or not sess.get("questions"):
        return jsonify({"error": "Research not ready or session expired"}), 404

    docx_bytes = build_docx_bytes(sess["guest_name"], sess["brief"], sess["questions"])

    safe = re.sub(r"[^\w\s-]", "", sess["guest_name"]).strip().replace(" ", "_")
    filename = f"Guest_Research_{safe}_{datetime.now().strftime('%Y-%m-%d')}.docx"

    return send_file(
        io.BytesIO(docx_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# -------------------------------------------------------------------
# Start
# -------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n🎙️  Guest Research Agent running at http://localhost:{port}\n")
    app.run(debug=True, port=port, threaded=True)
