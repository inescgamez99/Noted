---
name: knowledge-capture
description: >
  Capture client inputs of any kind and consolidate them into a single CSV of
  entities with full context for the OntoForge ontology generator. Inputs are
  optional and combinable: meeting recordings, audio, transcripts, PDFs,
  PPT/Word (→PDF), Excel/CSV, images, and a previous ontology (whose knowledge
  is merged in). Only audio/video needs code (Whisper transcription + frame
  extraction); Claude reads everything else and writes the combined CSV itself.
  Also generates an Accenture-branded Word record per meeting. Trigger phrases: "process meeting", "process capture",
  "transcribe recording", "add to OntoForge", "build ontology input",
  "new recording", "notes only", "procesar reunión", "procesar inputs".
---

# Knowledge Capture Skill

Code is used only for the mechanical steps — Whisper transcription and ffmpeg frame
extraction. Everything else (reading inputs, understanding, writing the combined CSV)
is Claude's own work. Paths come from `config.txt` (see Step 0).

**Workflow:** inputs → understand everything → one combined CSV
- **Phase 1** — gather and prepare inputs (this skill)
- **Phase 2** — Claude writes one combined entity/relationship CSV
- **Phase 3** — OntoForge generates the ontology (downstream)

---

## Inputs (all optional, combinable)

| Input | How it's handled |
|---|---|
| Video (`.mp4`, `.mov`, `.mkv`) | **Always** `process_meeting.py` — Whisper + frame extraction + de-dup |
| Audio (`.mp3`, `.wav`, `.m4a`) | `transcribe.py` — Whisper only (no frames) |
| Text transcript (`.txt`, `.vtt`) | Claude reads directly |
| PDF | Claude reads pages directly |
| PowerPoint / Word | Convert to PDF first, then read as PDF |
| Excel / CSV | Claude reads rows directly |
| Images / screenshots | Claude reads visually |
| Previous ontology (`.ttl`, `.owl`, `.rdf`, `.jsonld`) | Read and merged into the combined CSV |
| Notes only | User dictates; skip to Step 5 |

---

## Step 0 — Load config and setup

### 0a — Config
Read `config.txt` from `~/.claude/skills/knowledge-capture/config.txt`:
```
scripts_path=<absolute path to skill/scripts in the ontoforge-meetings repo>
data_path=<absolute path to your meetings folder>
```
If missing or blank, continue without it — paths from the calling context take priority.

### 0b — Dependencies
```powershell
python -c "import whisper" 2>$null
if ($LASTEXITCODE -ne 0) { pip install openai-whisper }
python -c "import docx" 2>$null
if ($LASTEXITCODE -ne 0) { pip install python-docx }
```

---

## Step 1 — Create the meeting folder & collect inputs

1. Decide the meeting date (ask if not derivable; default today). `date` = `YYYY-MM-DD`.
2. **Meeting ID** = `{YYYYMMDD}-{NN}`: list `{data_path}/` folders starting with `{YYYYMMDD}-`, take highest `{NN}` + 1 (start at `01`).
3. Create `{data_path}/{meeting_id}/inputs/`.
4. Copy every provided file into `{data_path}/{meeting_id}/inputs/`.

All inputs read from `inputs/`; all outputs go into `{data_path}/{meeting_id}/`.

---

## Step 2 — Prepare audio/video inputs

**Video (`.mp4`, `.mov`, `.mkv`) — ALWAYS `process_meeting.py`. No exceptions.**
```powershell
python "{scripts_path}/process_meeting.py" "{data_path}/{meeting_id}/inputs/{file}" --meeting-id {meeting_id} --date {date} --out-dir "{data_path}/{meeting_id}"
```
**Audio only (`.mp3`, `.wav`, `.m4a`) — `transcribe.py`:**
```powershell
python "{scripts_path}/transcribe.py" "{data_path}/{meeting_id}/inputs/{file}"
```
Model auto-selected by duration: `tiny` (<10 min), `base` (10–30 min), `small` (>30 min).
Speaker attribution is done by Claude reading the video frames (name tiles visible in Teams/Zoom recordings).

**PPTX / DOCX** → convert to PDF first (`soffice --headless --convert-to pdf "{file}"`), then treat as PDF.

---

## Step 3 — Read & understand every input (visuals are primary)

For each input, record concrete observations tagged with their source artifact:
- **Frames** — open in order; note the application/system shown, navigation, integrations, data fields.
- **PDF / slides / images** — capture systems, architecture, process steps.
- **Excel / CSV** — columns + sample rows; treat as data source or inventory.
- **Text transcript** — mine for systems mentioned.

Build a running list of every distinct system / tool / data source / process seen or mentioned.

---

## Step 4 — Clean the transcript (Teams/Zoom noise)

Remove without summarising substantive content:
- **Platform notifications:** join/leave, recording notices, waiting room
- **Technical issues:** mute/hear/screen-share/connection problems
- **Social filler:** greetings, goodbyes, thanks, standalone affirmations
- **Overlap/noise:** `[crosstalk]` for unclear simultaneous speech; remove background noise and silences

Normalise punctuation, capitalisation, run-on sentences. Attribute turns (`Name: text`) from
frames/context; mark `[unclear]` — never invent attendees.

---

## Step 5 — Understand systems & relationships (across all inputs)

Synthesise transcript, documents, data, images, and previous ontology into one unified picture:
systems & applications (one-line description), data sources, relationships & integrations
(direction and dependency), processes & workflows, teams/roles/people, documents & artifacts.

**Entity matching:** when the same system appears under different names across inputs (including
the previous ontology), recognise it as one entity and use the same spelling everywhere.

---

## Step 6 — Write the combined CSV (Claude authors this — no script)

Write **one combined CSV** with every entity found across all inputs. Use the Write tool.
Do NOT create relationships — OntoForge builds those from the context you provide.

**Columns:** `meeting_id,source_input,entity,entity_type,description,quote,timestamp`

| Column | What to put |
|---|---|
| `meeting_id` | The meeting ID (e.g. `20260715-01`) — keeps provenance across sessions |
| `source_input` | Filename or label (e.g. `recording.mp4`, `slides.pdf`, `previous_ontology`) |
| `entity` | Name of the system, process, role, dataset, event, document... |
| `entity_type` | System · Application · DataSource · Dataset · Process · Team · Role · Document · Tool · Event |
| `description` | Full rich context — what it does, who uses it, why it matters, any numbers or constraints mentioned |
| `quote` | Literal text from the source (spoken words, PDF text, column headers) — empty if none |
| `timestamp` | MM:SS from the video where this was said/shown — empty for non-video sources |

**Rules:**
- One row per entity — as many rows as needed, no limit
- If the same entity appears in multiple sources, write one row per source (keeps provenance)
- Use the same `entity` spelling everywhere for matched entities
- `description` must be complete — this is the only context OntoForge has, don't summarise

Save as `{data_path}/{meeting_id}/{meeting_id}_ontology_input.csv`.

### 6b — Append to client combined CSV

After saving the per-meeting CSV, ask:
> "Do you want to append this to a client-level combined CSV for OntoForge?
> Existing clients: {list *_combined.csv files in data_path}"

- **Existing client** → append new rows to `{data_path}/{client_name}_combined.csv`
- **New client** → ask for a client name → create `{data_path}/{client_name}_combined.csv`
- **Skip** → per-meeting CSV only

When appending: add all rows from the new meeting CSV. If an entity already exists with the same name, source, and meeting, skip it (deduplicate by `entity` + `source_input` + `meeting_id`). Always keep all rows — never overwrite or summarise existing content.

Confirm: "Appended {N} new entities to `{client_name}_combined.csv` — {total} total rows."
This is the file to upload to OntoForge for the full client picture.

---

## Step 7 — Save a readable record

### 7a — Word document (always generated, Accenture branded)

Check if `python-docx` is installed; if not, install it silently:
```powershell
python -c "import docx" 2>$null
if ($LASTEXITCODE -ne 0) { pip install python-docx }
```

Write `{data_path}/{meeting_id}/{meeting_id}_record.docx` with Accenture brand styling:

**Brand colours:**
- Accenture purple: `RGBColor(0xA1, 0x00, 0xFF)` — use for title bar and section headings
- Black: `RGBColor(0x00, 0x00, 0x00)` — body text
- Light grey: `RGBColor(0xF2, 0xF2, 0xF2)` — metadata table background

**Document structure:**
```
┌─────────────────────────────────────────────┐
│  ▌ Accenture                [purple bar]    │
│    Knowledge Capture — Meeting Record        │
├─────────────────────────────────────────────┤
│  Meeting ID  │  {meeting_id}                │
│  Date        │  {date}                      │  ← grey table
│  Client      │  {project}                   │
│  Attendees   │  {attendees}                 │
│  Sources     │  {input files}               │
├─────────────────────────────────────────────┤
│  SUMMARY                          [purple]  │
│  1–5 paragraphs (adapt to content length):  │
│  what was discussed, decisions made, open   │
│  questions, next steps. Detailed enough to  │
│  replace reading the full transcript.       │
├─────────────────────────────────────────────┤
│  SYSTEMS & RELATIONSHIPS          [purple]  │
│  {bullet list from Step 5}                  │
├─────────────────────────────────────────────┤
│  TRANSCRIPT                       [purple]  │
│  {cleaned speaker-attributed transcript}    │
│  [MM:SS SPEAKER_00]: text...                │
└─────────────────────────────────────────────┘
```

- Title bar: full-width purple rectangle with "Accenture" in white bold 14pt
- Section headings: purple, bold, 12pt, all caps
- Body: Graphik or Arial, 10pt, black
- Metadata table: 2 columns, grey background on label column, no visible border

Confirm: "Record saved as `{meeting_id}_record.docx` — open in Word to review and edit."

### 7b — HTML export (optional)

After saving the Word doc, ask:
> "Do you want an HTML version to share or present? (Enter to skip)"

If yes, generate a clean HTML file with the same content and save as
`{data_path}/{meeting_id}/{meeting_id}_record.html`.

---

## Step 8 — Report

```
Meeting ID          : {meeting_id}
Folder              : {data_path}/{meeting_id}/
CSV (this meeting)  : {meeting_id}_ontology_input.csv
CSV (client total)  : {client_name}_combined.csv  ← upload this to OntoForge
Record (Word)       : {meeting_id}_record.docx
Record (HTML)       : {meeting_id}_record.html  ← if requested
Inputs kept in      : inputs/

Upload {client_name}_combined.csv to OntoForge for the full client ontology.
Open the Word doc to review, edit, or share the meeting record.
```

---

## Rules

- **Phase 2 (CSV) is authored by Claude** — no script, no API.
- **No relationships in the CSV** — OntoForge builds those; Claude only captures entities with full context.
- **One combined CSV per capture** — all inputs in one file; never split per input.
- **Video always → `process_meeting.py`** — frames must always be extracted. No exceptions.
- **Audio only → `transcribe.py`** — `.mp3`, `.wav`, `.m4a` with no screen content.
- **Visuals are a primary source** — capture what's shown, match across inputs.
- **Clean Teams/Zoom noise** — Step 4 patterns only; never summarise.
- **Previous ontology** — fold entities/relationships into the CSV; flag contradictions.
- **Never invent** attendees, systems, or relationships — mark `[unclear]`.
- **Never overwrite** an existing `meeting_id`; **never translate** content.
- `date` always `YYYY-MM-DD`.
