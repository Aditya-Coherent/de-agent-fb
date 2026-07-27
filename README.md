# ME Pipeline

A market estimation pipeline that reads a structured Excel Input Sheet and generates a complete Market Estimation (ME) workbook.

The system is designed to work without manual schema editing. It inspects arbitrary market workbooks, validates them, constructs a runtime taxonomy, applies AI-enabled curve, segmentation, and ASP agents, and assembles a finalized ME workbook.

---

## Project overview

This repository contains a production-style pipeline for generating market forecasts from input workbooks.

Key features:
- Automatic workbook inspection and sheet detection
- Input validation with hard-stop Gate 1 checks
- A runtime taxonomy built directly from the workbook
- Agent-driven growth curves, segmentation drift, and ASP inflation
- A deterministic assembler that produces a full ME workbook
- Gate 3 validation plus optional reference diff reporting
- Output reporting in JSON and Excel formats

The main entry point for the low-level CLI is `run_pipeline.py`. Most people should
use the **web app** instead (see below) — it drives the same pipeline end to end,
starting from just a market name.

---

## Web app (recommended)

A Next.js frontend + FastAPI backend that runs the whole flow — type a market name,
generate its Input Sheet (from the report database + live web evidence), then
generate the ME workbook from it, one click each, no manual file wrangling.

**1. Start the backend** (from the repo root):

```bash
python backend/main.py
```

Runs at `http://127.0.0.1:8000`. Wraps `src/me_engine/generation/*` (the LangGraph
agent fleet) and `run_pipeline.py` behind an HTTP API — no separate pipeline logic
lives here.

**2. Start the frontend** (in a second terminal):

```bash
cd frontend
npm install   # first time only
npm run dev
```

Runs at `http://localhost:3000`. Open it in a browser.

**What it gives you over the CLI:**
- Live streaming progress while the agent fleet runs (not just a spinner)
- One-click "Generate ME Workbook" straight from a generated Input Sheet
- Tabs for regional split, segmentation shares, the "why" behind each number
  (Artifacts), an optional baseline compare against a human Input Sheet, and a
  History tab of past runs (with re-download links for their files)

The web app's one-click ME generation never automatically looks up a reference/
compares against a ground-truth ME workbook — that's Input Sheet in, ME workbook
out, nothing else. For manual accuracy testing against a known-good ME workbook,
see `compare_me_workbooks.py` below or `run_pipeline.py --reference`.

---

## What the pipeline does

1. Reads an Input Sheet workbook.
2. Detects the Data sheet, ASP sheet, segmentation structure, geography names, and product rows.
3. Builds an in-memory runtime taxonomy of dimensions, segments, and priced products.
4. Validates the input using `Gate 1`.
5. Runs the agent fleet and constructs a `DriverSet`.
6. Assembles the final ME workbook with value, ASP, and volume bands.
7. Validates the output using `Gate 3`.
8. Optionally compares against a reference ME workbook and writes a JSON report.

---

## Requirements

Install Python dependencies with:

```bash
pip install -r requirements.txt
```

Current dependency list:
- `httpx>=0.27.0`
- `openpyxl>=3.1.0`

The pipeline also uses `python-dotenv` via `dotenv` if a `.env` file exists.

---

## Environment configuration

The agent layer reads API configuration from the environment or a `.env` file.

Supported variables:
- `OPENROUTER_API_KEY` — recommended default gateway
- `OPENAI_API_KEY` — fallback if OpenRouter is absent
- `ME_CURVE_MODEL` — default model name, e.g. `openai/gpt-4o-mini`
- `ME_LLM_BASE_URL` — OpenRouter/OpenAI base URL
- `ME_LLM_TIMEOUT` — request timeout in seconds
- `ME_ENABLE_WEB_EVIDENCE` — enables evidence agent web search logic when set to `1`

Example `.env`:

```text
OPENROUTER_API_KEY=your_key_here
ME_CURVE_MODEL=openai/gpt-4o-mini
ME_LLM_BASE_URL=https://openrouter.ai/api/v1
ME_LLM_TIMEOUT=60
```

---

## Main CLI

Use `run_pipeline.py` to run the full pipeline.

Example:

```bash
python run_pipeline.py \
  --input "my_inputs/Data File - Global Technical Foam Market.xlsx" \
  --output "my_outputs/ME-Global Technical Foam Market.xlsx" \
  --reference "my_reference/ME - Global Technical Foam Market.xlsx" \
  --save-report "output/report-Global Technical Foam Market.json"
```

Flags:
- `--input` — required input workbook path
- `--output` — required generated ME workbook path
- `--reference` — optional reference ME workbook for Gate 3 diff
- `--template` — optional style template workbook
- `--no-agents` — skip agents and use flat priors only
- `--save-metadata` — save workbook inspection metadata as JSON
- `--save-report` — save a JSON summary report

---

## What the README covers

This README explains:
- how to run the web app (recommended) and the low-level CLI
- what each component does
- how the system validates and assembles output
- file and folder structure
- environment configuration

---

## Input workbook expectations

The pipeline is designed for Excel workbooks with one or more of these conventions:
- a `Data` or `Input Sheet` sheet containing segmentation and geography data
- an `ASP` sheet with product-level ASPs, or a shared Data+ASP sheet
- row 3 geography headers and product rows in column B
- one-year base values for segmentation shares, CAGR, anchor values, and ASPs

The workbook inspector is intentionally flexible and uses content heuristics rather than hardcoded names.

---

## Pipeline architecture

### 1. Workbook inspection

Implemented in `src/me_engine/io/workbook_inspector.py`.

Responsibilities:
- detect the data sheet and ASP sheet
- extract market name, dimensions, segment rows
- extract geography list and column headers
- detect ASP products and product rows
- collect warnings and failures

This module can also write structured metadata to JSON for debugging.

### 2. Runtime taxonomy

Implemented in `src/me_engine/domain/runtime_taxonomy.py`.

The taxonomy is built from the workbook schema at runtime, without depending on stored JSON schemas.
It produces:
- market name
- geography index
- segmentation dimensions
- priced product dimension

### 3. Gate 1 validation

Implemented in `src/me_engine/validation/gate1.py`.

Gate 1 checks:
- `Data` and `ASP` sheets exist and are readable
- market name is present or file stem fallback is used
- every geography has positive CAGR and anchor value
- segment shares approximately sum to 1 for each flat dimension
- each leaf product has positive ASP values

Gate 1 failures halt the pipeline before agent work begins.

### 4. Agent layer

Implemented in `src/me_engine/curve/`.

Agent responsibilities:
- `curve/agent.py` — growth curve decision per geography
- `curve/seg_agent.py` — segmentation drift premiums per dimension
- `curve/asp_agent.py` — ASP inflation rates per product
- `curve/evidence_agent.py` — optional web evidence assistant
- `curve/llm.py` — OpenAI/OpenRouter client wrapper
- `curve/config.py` — environment configuration loader

The agents produce curve, segmentation and ASP drivers for the assembler.

### 5. Input drivers

Implemented in `src/me_engine/io/input_drivers.py`.

This module converts workbook values and agent decisions into a `DriverSet`:
- geography-level forecast curve
- segment-level share paths
- product-level ASP paths

It also handles same-sheet shared Data+ASP layouts by detecting the correct ASP columns.

### 6. Assembler

Implemented in `src/me_engine/assembly/assembler.py`.

The assembler applies deterministic market identities:
- `segment_value = total_value × segment_share`
- `volume = value / asp × 1000`
- `market_share = segment_value / total_value`
- band totals and rows are computed for Value, ASP, and Volume

### 7. Gate 3 validation

Implemented in `src/me_engine/validation/gate3.py`.

Gate 3 includes:
- identity checks on all assembled bands
- no negatives
- volume identity consistency
- CAGR and share invariants
- optional reference diff when `--reference` is provided

The diff summary is written to JSON when `--save-report` is used.

---

## Key files and directories

```
.env                            local env config for API keys (never commit)
requirements.txt                Python dependency list
run_pipeline.py                 main CLI for the full pipeline
compare_me_workbooks.py         manual accuracy check: two ME workbooks, cell-by-cell
generate_synthetic_input.py     CLI: generate an Input Sheet from evidence only
run_pilot.py                    pilot script for avocado oil market testing
test_llm.py                     quick LLM connectivity tester
backend/main.py                 FastAPI backend for the web app
frontend/                       Next.js frontend for the web app
my_inputs/                      human-authored Input Sheet folder (labels/testing)
my_reference/                   reference ME workbook folder (manual accuracy testing)
agentGeneratedInputSheet/       Input Sheets generated by the web app / CLI
agentGeneratedMEWorkbook/       ME workbooks generated by the web app one-click flow
output/                         pipeline metadata, reports, and generation_history.jsonl
src/me_engine/                  pipeline source code
src/me_engine/generation/       the LangGraph agent fleet that generates Input Sheets
```

### Important source modules

- `src/me_engine/io/workbook_inspector.py`
- `src/me_engine/io/input_reader.py`
- `src/me_engine/io/input_drivers.py`
- `src/me_engine/io/me_writer.py`
- `src/me_engine/curve/agent.py`
- `src/me_engine/curve/seg_agent.py`
- `src/me_engine/curve/asp_agent.py`
- `src/me_engine/validation/gate1.py`
- `src/me_engine/validation/gate3.py`
- `src/me_engine/assembly/assembler.py`
- `src/me_engine/domain/runtime_taxonomy.py`

---

## Example workflows

### Run the full pipeline with a reference comparison

```bash
python run_pipeline.py \
  --input "my_inputs/Data File - Global Technical Foam Market.xlsx" \
  --output "my_outputs/ME-Global Technical Foam Market.xlsx" \
  --reference "my_reference/ME - Global Technical Foam Market.xlsx" \
  --save-report "output/report-Global Technical Foam Market.json"
```

### Run without agents

```bash
python run_pipeline.py --input "..." --output "..." --no-agents
```

### Compare two already-built ME workbooks (manual accuracy check)

No regeneration — reads both files directly and reports mean/median/p90/worst
relative error plus the worst individual cells. Use this whenever you want to
check a generated ME workbook against a known-good reference:

```bash
python compare_me_workbooks.py \
  --generated "agentGeneratedMEWorkbook/ME - Global Process Analyzer Market.xlsx" \
  --reference "my_reference/ME-Global Process Analyzer Market.xlsx" \
  --save-report "output/compare-me-process-analyzer.json"
```

### Test LLM connectivity

```bash
python test_llm.py
```

### Run the pilot evaluation script

```bash
python run_pilot.py
```

---

## Notes on current behavior

- The pipeline is designed to preserve existing working markets while making input detection more robust.
- The workbook inspector now supports shared Data+ASP layouts and nonstandard ASP sheet names.
- Market names are read from `C1` on the Data sheet. If `C1` is blank, the filename stem is used instead.
- A failed `Gate 1` means the workbook is not ready for agent processing.

---

## Tips for GitHub

- Commit `README.md`, `requirements.txt`, and your source files.
- Do not commit `.env` if it contains secrets.
- If you want to share sample data, add only sanitized example workbooks, not API keys.

---

## License

Add your license text here if needed.
