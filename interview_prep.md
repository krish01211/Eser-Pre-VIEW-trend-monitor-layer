# Interview Preparation Guide: Pre-VIEW Audit Trend Monitor

This document is designed to help you explain the **Pre-VIEW Audit Trend Monitor** project to your interviewers at Takazono. It breaks down the narrative, the architecture, and the specific technical decisions you made so you can speak with confidence and depth.

---

## 1. The Origin Story: "Why I Built This"

**The Setup:** 
"When I was researching Takazono for this role, I looked closely at your recent product releases to understand your technical trajectory. I noticed the **Eser packaging machine** and the newly added **Pre-VIEW AI audit system** (co-developed with Hitachi). Pre-VIEW is fantastic—it uses image recognition to automatically verify pills per-batch, reducing the psychological burden on pharmacists."

**The Gap (The "Aha!" Moment):** 
"However, based on public materials, Pre-VIEW evaluates *individual batches in isolation*. If a camera lens slowly gets dirty, or a cassette's mechanical alignment slightly shifts over a month, a per-batch system won't notice anything is wrong until a batch finally drops below the passing threshold and triggers a hard failure. It is reactive."

**The Solution:**
"Because the job description emphasized a blend of Software Engineering, Data Science, and AI, I decided to build a prototype of a **companion analytics layer**. This layer sits alongside the existing Pre-VIEW system, ingests its audit logs in real-time, and analyzes confidence trends *across* batches. The goal is to detect statistical drift and alert operators to degrading machine health **before** an audit actually fails. It turns a reactive safety system into a proactive maintenance system."

---

## 2. System Architecture: "What I Built"

"To prove the concept without needing actual machine hardware, I built a fully local, synthetic simulation using a multi-threaded Python backend and a live HTML5 dashboard."

### The Four Layers:
1. **The Audit Engine (Producer)**
   - A Python thread simulating the physical machine. It generates 1 synthetic audit record per second for 5 different cassettes. 
   - It outputs physically realistic data (baseline ~96% confidence with Gaussian noise).
   - It supports real-time **anomaly injection** (e.g., simulating a slowly dirtying lens on Cassette 3).
2. **The Persistence Layer (SQLite)**
   - An append-only SQLite database. It acts as a ring buffer/telemetry log connecting the engine to the analytics layer.
3. **The Trend Analyzer (Consumer & Data Science Layer)**
   - A separate Python thread running every 2 seconds. It queries the SQLite database to perform feature engineering (calculating rolling averages and z-scores) and writes the results to JSON files.
4. **The Dashboard (Frontend & HTTP Bridge)**
   - A zero-dependency Python HTTP server (`http.server`) serving a vanilla HTML/CSS/JS dashboard.
   - The frontend uses JavaScript `fetch` to poll the JSON files every 500ms, updating a live HTML5 Canvas chart and a scrolling data feed.

---

## 3. Code Technicalities: "How I Built It" (Talking Points)

Interviewers will want to know that you understand *why* you wrote the code the way you did. Use these technical highlights:

### A. Thread Safety & SQLite WAL Mode
"Because I had a producer thread (Audit Engine) writing 5 records a second, and a consumer thread (Trend Analyzer) reading the database every 2 seconds, I had to manage database concurrency carefully. I configured SQLite to use **WAL (Write-Ahead Logging) mode** (`PRAGMA journal_mode=WAL`). This allows simultaneous readers and writers without locking the entire database, preventing `OperationalError` lockups."

### B. Offloading Math to SQL Window Functions
"Instead of loading thousands of records into Python memory to calculate the rolling averages and drift, I pushed that computation down to the database layer using advanced SQL. I used **SQL Window Functions** (`AVG() OVER (PARTITION BY cassette_id ORDER BY id ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)`). This is vastly more efficient and keeps the Python layer lightweight."

### C. Atomic JSON Writes (Preventing Frontend Crashes)
"The trend analyzer outputs its results to JSON files (`feed.json`, `trend.json`). Because the frontend polls these files every 500ms, there was a risk that the server might serve a partially written JSON file, crashing the frontend parser. To solve this, I implemented **Atomic Writes**: the Python script writes to a temporary file (`trend.json.tmp`) and then uses `os.rename()` to instantly swap it. The dashboard only ever sees complete files."

### D. Frontend Polling & Cache-Busting
"For the dashboard, I wanted a real-time feel without the overhead of WebSockets. I used a 500ms Javascript polling interval via `fetch()`. To ensure the browser didn't aggressively cache the JSON payloads, I appended a dynamic timestamp query parameter to every request (`/api/feed?_=${Date.now()}`). I also configured the Python backend to strip query parameters before routing, ensuring clean 404-free delivery."

### E. Primary Key Tracking vs. Batch IDs
"Initially, I tracked new data in the frontend using the `batch_id`. However, I realized that if the backend engine restarted (resetting `batch_id` to 1), the frontend would freeze because it thought the new batch IDs were 'older' than what it had already seen. I refactored the database schema to use an auto-incrementing `id` primary key and updated both the SQL queries and Javascript to track the `id`. This made the system resilient to engine restarts."

---

## 4. The Demo Walkthrough

When showing the project, guide them through these three phases:

### Phase 1: Normal Operation
* **Show:** The baseline state.
* **Say:** "Here you can see the system operating normally. Five cassettes are dispensing, the confidence scores are holding steady around 96%, and the live audit stream is green. The chart shows a stable horizontal trend."

### Phase 2: Instant Hard Fault
* **Action:** Click "Inject Pill Mismatch".
* **Say:** "This simulates a traditional error—a wrong pill drops. The confidence plummets to 40%, the system flags a hard failure (mismatch), and the dashboard flashes red. This is exactly what the current Pre-VIEW system catches perfectly."

### Phase 3: The "Star of the Show" (Early Warning)
* **Action:** Click "Reset All", wait 5 seconds, then click "Simulate Lens Degradation (C3)".
* **Say:** "Now let's simulate a subtle mechanical degradation, like a slowly dirtying camera lens on Cassette 3. Watch the chart. The individual batches are *still passing*—they are above the 85% threshold, so a traditional per-batch auditor would say everything is fine. But our analytics layer detects the downward drift. The trend line visibly bends, and the cassette status changes to a warning 'DEGRADING' state. We have caught the failure *before* it happened, allowing maintenance to clean the lens during scheduled downtime rather than stopping production."
