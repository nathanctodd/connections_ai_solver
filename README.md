# Connections Bot

Automates the **NYT Connections** daily puzzle by opening the game in a real browser (via Playwright), dismissing cookie and start modals, reading the 16 tiles, and attempting solutions by proposing 4-word groups, submitting them, and adapting based on results. It combines:
- Robust DOM/iframe navigation and selector fallbacks,
- Heuristic grouping (prefix/suffix/category patterns),
- Optional LLM proposals and refinement (OpenAI API; OpenRouter-compatible key).

> Use responsibly and at your own risk. The NYT site may change at any time and could break automated selectors. Read the NYT Terms of Service before automating gameplay.

---

## Table of Contents

- [Features](#features)
- [How it Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Playwright Browser Setup](#playwright-browser-setup)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [Examples](#examples)
- [Logging, Screenshots, and Debugging](#logging-screenshots-and-debugging)
- [Heuristics & LLM Integration](#heuristics--llm-integration)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Development Notes](#development-notes)
- [Security Notes](#security-notes)
- [License](#license)

---

## Features

- **Browser automation** with Playwright (Chromium) to open and control the game page.
- **Gate/cookie handling** across main document and iframes (common consent dialogs and “Play/Start” buttons).
- **Resilient selectors** with multiple fallbacks for tiles and submit buttons.
- **Tile parsing** with normalization (case/spacing/punctuation handling).
- **Submit & feedback loop** that re-plans based on acceptance/rejection of proposed groups.
- **Heuristic solver**: category detection (months, colors, days), prefix/suffix buckets, same-length grouping, sampled combinations.
- **LLM solver (optional)**:
  - Initial proposals for 4 groups of 4.
  - Iterative refinement that avoids previously rejected (banned) groups.
  - Uses `OpenAI` Python SDK with an API key read from environment.
- **Dry-run mode** for safe testing (collects words without clicking/solving).
- **Headful/headless** operation.

---

## How it Works

1. **Navigate** to `https://www.nytimes.com/games/connections`.
2. **Dismiss** common cookie/gate/start modals in main DOM and iframes.
3. **Locate** the game root (iframe/page) and find tile & submit selectors using multiple candidate selectors.
4. **Extract** the visible tile texts, normalize, and compute candidate groups.
5. **Propose solutions**:
   - If enabled and an API key is present, query the LLM for groupings.
   - Otherwise, rely on heuristics.
6. **Attempt & adapt**:
   - Click 4 tiles, submit, check if 4 tiles disappeared.
   - On success: regenerate candidates for remaining words.
   - On failure: record the banned group, deselect, optionally re-query the LLM with feedback, and continue.
7. **Stop** when:
   - There is a final forced group,
   - The configured mistake limit is reached, or
   - No more candidates are viable.

---

## Requirements

- **Python** 3.9+
- **System dependencies** for Playwright (headless Chromium); install via Playwright tooling below.
- **Python packages**:
  - `playwright`
  - `openai` (>=1.0)
  - `requests`
  - `python-dotenv` (optional; for local `.env`)
- **(Optional) API key** for LLM assistance: OpenAI or OpenRouter-compatible key.

---

## Installation

```bash
# 1) Create & activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2) Install Python dependencies
pip install playwright openai requests python-dotenv
