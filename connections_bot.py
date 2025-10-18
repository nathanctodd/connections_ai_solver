#!/usr/bin/env python3
"""
NYT Connections Bot Solver - created by Nathan Todd
"""

from __future__ import annotations
import argparse
import itertools
import random
import sys
import time
import json
import re
import requests
from openai import OpenAI
from dataclasses import dataclass
from typing import List, Tuple, Set, Dict
from datetime import datetime
import os
try:
    from dotenv import load_dotenv  # optional; if not installed, we just skip
    load_dotenv()
except Exception:
    pass

# Prefer OPENROUTER_API_KEY; fall back to lower-case "api_key" for compatibility
api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("api_key")


from playwright.sync_api import Playwright, sync_playwright

URL = "https://www.nytimes.com/games/connections"

# ----------------------------- Selectors ----------------------------- #

TILE_SELECTOR_CANDIDATES = [
    'label[data-testid="card-label"]',         
    '[data-testid="card-label"]',              
    '[data-testid*="card-label"]',            
    '[data-testid="tile"]',
    '[data-testid*="tile"]',
    'button[role="button"][data-testid^="tile"]',
    'div[role="button"][data-testid^="tile"]',
    '[class*="Tile"] [role="button"]',
    '[role="grid"] [role="button"]',
    '[data-testid*="board"] [role="button"]',
    '[aria-label*="Tiles"] [role="button"]',
]

SUBMIT_SELECTOR_CANDIDATES = [
    '[data-testid="submit-button"]',
    '[data-testid="submit"]',
    'button[data-testid*="submit"]',
    '[data-testid*="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Submit group")',
    'button[aria-label="Submit"]',
]

DISMISS_SELECTORS = [
    '#onetrust-accept-btn-handler',
    'button:has-text("Accept all")',
    'button:has-text("Accept All")',
    'button:has-text("I Accept")',
    'button:has-text("Agree")',
    'button:has-text("Got it")',
    'button:has-text("Close")',
    '[role="dialog"] button[aria-label="Close"]',
    '[data-testid*="modal"] button[aria-label="Close"]',
    'button:has-text("Continue")',
    'button:has-text("Start")',
    'button:has-text("Play")',
]

ROLE_BUTTON_NAMES = ["Accept all", "Accept All", "Accept",
                     "Continue", "Start", "Play", "Got it", "Close"]





# ----------------------------- Logging helpers ----------------------------- #

def log(msg: str) -> None:
    """
    Log a message with a timestamp prefix.
    
    Args:
        msg: The message to log.
    """
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[Bot {ts}] {msg}")


def log_frames(page) -> None:
    """
    Log information about all frames in the page for debugging purposes.
    
    Args:
        page: The Playwright page object to inspect.
    """
    try:
        frs = list(page.frames)
        log(f"Frames detected: {len(frs)}")
        for i, f in enumerate(frs):
            try:
                url = (f.url or "").strip()
            except Exception:
                url = ""
            try:
                rs = f.evaluate("document.readyState")
            except Exception:
                rs = "?"
            try:
                title = f.evaluate("document.title")
            except Exception:
                title = "?"
            log(f"  [#{i}] readyState={rs} url={url} title={title}")
    except Exception as e:
        log(f"Failed to enumerate frames: {e}")


# ----------------------------- Utilities ----------------------------- #

def first_selector(root, selectors: List[str], require_count: int | None = None) -> str | None:
    """
    Return the first selector from a list that matches elements on the page.
    
    Args:
        root: The page or frame to search in.
        selectors: List of CSS selectors to try.
        require_count: Optional minimum number of elements that must match.
        
    Returns:
        The first matching selector string, or None if none match.
    """
    for sel in selectors:
        try:
            loc = root.locator(sel)
            loc.first.wait_for(timeout=3000)
            count = 0
            try:
                count = loc.count()
            except Exception:
                pass
            if require_count is not None and count < require_count:
                log(f"Selector '{sel}' found but count={count} < {require_count}; trying next…")
                continue
            log(f"Using selector: {sel} (count≈{count})")
            return sel
        except Exception:
            log(f"Selector not present yet: {sel}")
            continue
    return None


def click_if_present(root, selector: str, timeout: int = 1200) -> bool:
    """
    Click an element if it's present on the page.
    
    Args:
        root: The page or frame to search in.
        selector: CSS selector for the element to click.
        timeout: Maximum time to wait for the element in milliseconds.
        
    Returns:
        True if the element was found and clicked, False otherwise.
    """
    try:
        el = root.locator(selector).first
        el.wait_for(timeout=timeout)
        el.click()
        return True
    except Exception:
        return False


def dismiss_gates_and_cookies(page) -> None:
    """
    Attempt to dismiss cookie banners, modals, and other gates on the page.
    
    Args:
        page: The Playwright page object.
    """
    log("Dismissing gates/cookies if present…")
    # try on main page
    for sel in DISMISS_SELECTORS:
        if click_if_present(page, sel):
            log(f"Dismissed/Clicked: {sel}")

    log("Trying role buttons…")
    for name in ROLE_BUTTON_NAMES:
        try:
            page.get_by_role("button", name=name).click(timeout=600)
            log(f"Clicked role button: {name}")
        except Exception:
            pass

    log("Checking iframes for gates/cookies…")
    for f in page.frames:
        for sel in DISMISS_SELECTORS:
            if click_if_present(f, sel):
                log(f"(iframe) Dismissed/Clicked: {sel}")
        for name in ROLE_BUTTON_NAMES:
            try:
                f.get_by_role("button", name=name).click(timeout=600)
                log(f"(iframe) Clicked role button: {name}")
            except Exception:
                pass
            
def press_play(page) -> None:
    """
    Attempt to click the Play or Start button to begin the game.
    
    Args:
        page: The Playwright page object.
    """
    log("Attempting to press Play/Start button if present…")
    for name in ["Play", "Start"]:
        try:
            page.get_by_role("button", name=name).click(timeout=1200)
            log(f"Clicked Play/Start button: {name}")
            return
        except Exception:
            pass
    log("No Play/Start button found.")


def find_game_root(page):
    """Return the frame hosting the game if present, else the page."""
    # Prefer frame by URL
    for f in page.frames:
        try:
            u = (f.url or "").lower()
        except Exception:
            u = ""
        if "connections" in u or "/games-assets/" in u:
            log(f"Using iframe as root: {u}")
            return f
    # Fallback: any frame that has tiles
    for f in page.frames:
        for sel in TILE_SELECTOR_CANDIDATES:
            try:
                f.locator(sel).first.is_visible(timeout=400)
                log(f"Found tiles in iframe via selector: {sel}")
                return f
            except Exception:
                pass
    log("No specific iframe matched; using main page as root.")
    return page


def normalize(s: str) -> str:
    """
    Normalize a string by removing special characters and converting to lowercase.
    
    Args:
        s: The string to normalize.
        
    Returns:
        Normalized string with only alphanumeric characters and spaces, lowercased.
    """
    return " ".join("".join(ch for ch in s.strip() if ch.isalnum() or ch.isspace()).split()).lower()


def get_tiles(root, tile_sel: str) -> List[Tuple[str, str]]:
    """
    Get all tile elements and their text content from the game board.
    
    Args:
        root: The page or frame containing the tiles.
        tile_sel: CSS selector for tile elements.
        
    Returns:
        List of tuples containing (original_text, original_text) for each tile.
    """
    tiles = root.locator(tile_sel)
    n = tiles.count()
    out: List[Tuple[str, str]] = []
    for i in range(n):
        t = tiles.nth(i)
        try:
            txt = t.inner_text().strip()
        except Exception:
            txt = ""
        out.append((txt, txt))
    return out


def get_remaining_words(root, tile_sel: str) -> List[str]:
    """
    Get normalized text of all remaining tiles on the board.
    
    Args:
        root: The page or frame containing the tiles.
        tile_sel: CSS selector for tile elements.
        
    Returns:
        List of normalized word strings.
    """
    return [normalize(t[0]) for t in get_tiles(root, tile_sel)]


def click_tile_by_text(root, tile_sel: str, word: str, delay_ms: int = 120) -> bool:
    """
    Click a tile by matching its text content.
    
    Args:
        root: The page or frame containing the tiles.
        tile_sel: CSS selector for tile elements.
        word: The word to search for and click.
        delay_ms: Delay in milliseconds after clicking.
        
    Returns:
        True if the tile was found and clicked, False otherwise.
    """
    target = normalize(word)
    tiles = root.locator(tile_sel)
    n = tiles.count()
    for i in range(n):
        t = tiles.nth(i)
        try:
            txt = normalize(t.inner_text())
        except Exception:
            continue
        if txt == target:
            t.click()
            root.wait_for_timeout(delay_ms)
            return True
    return False


def try_submit(root, submit_sel: str, delay_ms: int = 300) -> bool:
    """
    Attempt to click the submit button.
    
    Args:
        root: The page or frame containing the submit button.
        submit_sel: CSS selector for the submit button.
        delay_ms: Delay in milliseconds after clicking.
        
    Returns:
        True if the submit button was clicked, False otherwise.
    """
    try:
        root.locator(submit_sel).first.click()
        root.wait_for_timeout(delay_ms)
        return True
    except Exception:
        # fallback
        try:
            root.get_by_role("button", name="Submit").click()
            root.wait_for_timeout(delay_ms)
            return True
        except Exception:
            return False
        
        
# ----------------------------- LLM integration ----------------------------- #

def _map_normalized_to_original(words_original: List[str]) -> Dict[str, List[str]]:
    """
    Create a mapping from normalized words to their original forms.
    
    Args:
        words_original: List of original word strings.
        
    Returns:
        Dictionary mapping normalized words to lists of original forms (handles duplicates).
    """
    # multimap: normalized -> list of originals (handles duplicates)
    m: Dict[str, List[str]] = {}
    for w in words_original:
        key = normalize(w)
        m.setdefault(key, []).append(w)
    return m

def _consume_originals(mapping: Dict[str, List[str]], w_norm: str) -> str | None:
    """
    Pop and return one original word form from the mapping.
    
    Args:
        mapping: Dictionary from normalized words to lists of originals.
        w_norm: The normalized word to look up.
        
    Returns:
        One original form of the word, or None if not found.
    """
    arr = mapping.get(w_norm) or []
    if not arr:
        return None
    return arr.pop(0)


# ----------------------------- Heuristics ----------------------------- #

MONTHS = {
    "january","february","march","april","may","june","july",
    "august","september","october","november","december"
}
COLORS = {
    "red","orange","yellow","green","blue","indigo","violet",
    "black","white","gray","grey","brown","pink","purple","cyan","magenta","tan","teal"
}
DAYS = {"monday","tuesday","wednesday","thursday","friday","saturday","sunday"}

def group_by_suffix(words: List[str], k: int) -> List[List[str]]:
    """
    Group words by their last k characters (suffix).
    
    Args:
        words: List of words to group.
        k: Length of suffix to match.
        
    Returns:
        List of groups (each with at least 4 words sharing a suffix).
    """
    buckets: Dict[str,List[str]] = {}
    for w in words:
        if len(w) >= k:
            suf = w[-k:]
            buckets.setdefault(suf, []).append(w)
    return [v for v in buckets.values() if len(v) >= 4]

def group_by_prefix(words: List[str], k: int) -> List[List[str]]:
    """
    Group words by their first k characters (prefix).
    
    Args:
        words: List of words to group.
        k: Length of prefix to match.
        
    Returns:
        List of groups (each with at least 4 words sharing a prefix).
    """
    buckets: Dict[str,List[str]] = {}
    for w in words:
        if len(w) >= k:
            pre = w[:k]
            buckets.setdefault(pre, []).append(w)
    return [v for v in buckets.values() if len(v) >= 4]

def category_groups(words: List[str]) -> List[List[str]]:
    """
    Find groups of words matching known categories (months, colors, days).
    
    Args:
        words: List of words to search.
        
    Returns:
        List of groups (each with 4 words from a known category).
    """
    s = set(words)
    cands = []
    for cat in (MONTHS, COLORS, DAYS):
        hit = list(s & cat)
        if len(hit) >= 4:
            cands.append(hit[:4])
    return cands

def generate_candidate_groups(
    words: List[str],
    priority_groups: List[List[str]] | None = None,
    banned_groups_norm: List[Set[str]] | None = None
) -> List[List[str]]:
    """
    Generate candidate groups of 4 words using various heuristics.
    
    Priority order: priority groups (e.g., from LLM) → category matches → 
    shared suffix/prefix → same length → brute-force sampling.
    
    Args:
        words: List of words to group.
        priority_groups: Optional list of groups to try first (e.g., from LLM).
        banned_groups_norm: Optional list of normalized word sets to avoid.
        
    Returns:
        List of candidate groups (each containing 4 words).
    """
    W = list(words)
    random.shuffle(W)
    seen: Set[Tuple[str,...]] = set()
    out: List[List[str]] = []
    banned_groups_norm = banned_groups_norm or []

    # Start with priority groups (e.g., from LLM), if provided
    if priority_groups:
        for g in priority_groups:
            t = tuple(sorted(map(str.lower, g)))
            if set(map(str.lower, g)).issubset(set(W)) and t not in seen:
                if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                    continue
                seen.add(t)
                out.append(g)

    # Category hits
    for g in category_groups(W):
        t = tuple(sorted(g))
        if t not in seen:
            if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                continue
            seen.add(t)
            out.append(g)

    # Suffix/prefix heuristics (length 4→2)
    for k in (4,3,2):
        for grp in group_by_suffix(W, k):
            g = grp[:4]
            t = tuple(sorted(g))
            if t not in seen:
                if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                    continue
                seen.add(t); out.append(g)
        for grp in group_by_prefix(W, k):
            g = grp[:4]
            t = tuple(sorted(g))
            if t not in seen:
                if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                    continue
                seen.add(t); out.append(g)

    # Same-length groups
    by_len: Dict[int,List[str]] = {}
    for w in W:
        by_len.setdefault(len(w), []).append(w)
    for L, arr in by_len.items():
        if len(arr) >= 4:
            g = arr[:4]
            t = tuple(sorted(g))
            if t not in seen:
                if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                    continue
                seen.add(t); out.append(g)

    # Fallback: sampled brute force (limit to keep mistakes bounded)
    # Try combinations of 4 from remaining; shuffle for variety.
    combos = list(itertools.combinations(W, 4))
    random.shuffle(combos)
    for c in combos[:300]:  # cap to avoid thrashing
        g = list(c)
        t = tuple(sorted(g))
        if t not in seen:
            if any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                continue
            seen.add(t); out.append(g)

    return out

def originals_from_remaining(tiles: List[Tuple[str, str]], remaining_norm: Set[str]) -> List[str]:
    """
    Extract original word forms for the remaining normalized words.
    
    Args:
        tiles: List of (original, _) tuples from get_tiles.
        remaining_norm: Set of normalized words still remaining.
        
    Returns:
        List of original word forms corresponding to remaining_norm.
    """
    out: List[str] = []
    seen: Dict[str, int] = {}
    for original, _ in tiles:
        key = normalize(original)
        if key in remaining_norm:
            # allow duplicates by counting
            cnt = seen.get(key, 0)
            # include this occurrence
            out.append(original)
            seen[key] = cnt + 1
    return out

def openai_refine_groups(words_original: List[str], banned_groups: List[List[str]], api_key: str, model: str = "gpt-4.1", timeout: int = 60) -> List[List[str]]:
    """
    Request refined group proposals from OpenAI, avoiding previously incorrect groups.
    
    Args:
        words_original: List of remaining words in their original form.
        banned_groups: List of previously tried groups that were incorrect.
        api_key: OpenAI API key.
        model: OpenAI model name to use.
        timeout: Request timeout in seconds (unused in current implementation).
        
    Returns:
        List of up to 4 proposed groups, each containing 4 words.
    """
    try:
        client = OpenAI(api_key=api_key)
        words_list = list(words_original)
        banned = banned_groups or []
        prompt = (
            "You are playing the NYT Connections game. You are given the remaining words. "
            "Partition them into groups of four by shared connection. IMPORTANT RULES: "
            "Use only the given words, each word appears in exactly one group. Avoid any of the exact groupings listed as 'DISALLOWED GROUPS'. "
            "Return strict JSON {\"groups\": [[four words], ...]} using the words EXACTLY as shown.\n\n"
            f"REMAINING WORDS: {words_list}\n"
            f"DISALLOWED GROUPS: {banned}\n\n"
            "Return ONLY JSON, no explanation."
        )
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        m = re.search(r"\{[\s\S]*\}", content or "")
        json_text = m.group(0) if m else (content or "")
        parsed = json.loads(json_text)
        groups = parsed.get("groups", [])
        if not isinstance(groups, list):
            return []
        # Validate and map back to originals
        norm_map = _map_normalized_to_original(words_original)
        words_norm_set = {normalize(w) for w in words_original}
        out: List[List[str]] = []
        for g in groups:
            if not isinstance(g, list) or len(g) != 4:
                continue
            mapped: List[str] = []
            ok = True
            for w in g:
                wn = normalize(str(w))
                if wn not in words_norm_set:
                    ok = False; break
                orig = _consume_originals(norm_map, wn)
                if orig is None:
                    ok = False; break
                mapped.append(orig)
            if ok and len(mapped) == 4:
                out.append(mapped)
        return out[:4]
    except Exception as e:
        log(f"OpenAI refine call failed: {e}")
        return []


# ----------------------------- Solver driver ----------------------------- #

@dataclass
class Options:
    """Configuration options for the solver."""
    headless: bool = True
    max_mistakes: int = 4
    dry_run: bool = False
    use_openai: bool = True
    openai_model: str = "gpt-4.1"

def solve(playwright: Playwright, url: str, opt: Options) -> None:
    """
    Main solver function that automates playing the NYT Connections game.
    
    Args:
        playwright: Playwright instance for browser automation.
        url: URL of the Connections game.
        opt: Options object with configuration settings.
    """
    browser = playwright.chromium.launch(headless=opt.headless)
    context = browser.new_context()
    page = context.new_page()

    # generous timeouts
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(45_000)

    log(f"Navigating to: {url}")
    try:
        page.goto(url, wait_until="networkidle", timeout=45_000)
    except Exception:
        log("networkidle timed out, retrying with wait_until='load'…")
        page.goto(url, wait_until="load", timeout=45_000)

    # Dismiss gates/cookies; small scroll to trigger lazy-load
    page.wait_for_timeout(8000)
    print("Initial: checking frames and DOM…")
    # dismiss_gates_and_cookies(page)
    press_play(page)

    log("Post-dismiss: checking frames and DOM…")
    log_frames(page)

    # Light nudge for lazy content
    try:
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(300)
    except Exception:
        pass

    # Determine root and then poll for tiles for up to ~20s before giving up
    root = find_game_root(page)

    log("Polling for tiles (up to 20s)…")
    found_selector = None
    for i in range(20):  # 20 * 1s = 20s
        found_selector = first_selector(root, TILE_SELECTOR_CANDIDATES, require_count=12)
        if found_selector:
            log(f"Tiles became available on attempt {i+1}.")
            break
        page.wait_for_timeout(1000)
        # try dismissing again (pop-ups may reappear)
        dismiss_gates_and_cookies(page)
        log_frames(page)

    if found_selector is None:
        # Save a screenshot for debugging and raise a clearer error
        try:
            page.screenshot(path="connections_debug.png", full_page=True)
            log("Saved screenshot: connections_debug.png")
        except Exception as e:
            log(f"Failed to capture screenshot: {e}")
        raise RuntimeError("Tiles not found after polling — possible new gate, paywall, or UI change.")

    # Use the selector we actually confirmed
    tile_sel = found_selector

    # We already set tile_sel via the polling loop above
    submit_sel = first_selector(root, SUBMIT_SELECTOR_CANDIDATES)

    root.locator(tile_sel).first.wait_for(timeout=20_000)
    try:
        count = root.locator(tile_sel).count()
        log(f"Tile count detected: {count}")
        if count and count < 16:
            log(f"Warning: only {count} tiles found; capturing screenshot…")
            try:
                page.screenshot(path="connections_tiles_partial.png", full_page=True)
                log("Saved screenshot: connections_tiles_partial.png")
            except Exception as e:
                log(f"Failed to capture partial screenshot: {e}")
    except Exception:
        pass

    tiles = get_tiles(root, tile_sel)
    words = [normalize(t[0]) for t in tiles]
    log(f"Words ({len(words)}): {words}")
    
    llm_groups: List[List[str]] = []
    if opt.use_openai:
        if not api_key:
            log("OpenAI API key not found in environment variable 'OPENAI_API_KEY'.")
        else:
            log(f"Querying OpenAI ({opt.openai_model}) for proposed groups…")
            llm_groups = openai_propose_groups([t[0] for t in tiles], api_key=api_key, model=opt.openai_model)
            if llm_groups:
                log(f"OpenAI proposed {len(llm_groups)} groups: {llm_groups}")
            else:
                log("OpenAI returned no usable groups; falling back to heuristics.")

    if len(words) < 16:
        log("Warning: fewer than 16 tiles visible. A modal might still be present.")

    remaining: Set[str] = set(words)
    mistakes = 0
    banned_groups_norm: List[Set[str]] = []

    def refresh_remaining() -> Set[str]:
        return set(get_remaining_words(root, tile_sel))

    # Candidate generator
    candidates = generate_candidate_groups(list(remaining), priority_groups=llm_groups, banned_groups_norm=banned_groups_norm)

    idx = 0
    while True:
        if len(remaining) <= 4:
            # last group is forced—click it
            last = list(remaining)
            log(f"Final forced group: {last}")
            if not opt.dry_run:
                for w in last:
                    click_tile_by_text(root, tile_sel, w)
                try_submit(root, submit_sel)
            break

        # If we've exhausted all candidates, regenerate
        if idx >= len(candidates):
            break
        group = candidates[idx]
        idx += 1

        # skip groups that use words already removed
        if not set(map(str.lower, group)) <= remaining:
            continue

        log(f"Trying group {idx}: {group}")
        if opt.dry_run:
            continue

        # Click all 4 and submit
        before = set(refresh_remaining())
        for w in group:
            click_tile_by_text(root, tile_sel, w)

        submitted = try_submit(root, submit_sel)
        time.sleep(5.0)

        after = set(refresh_remaining())
        if submitted and len(after) == len(before) - 4:
            log(f"✅ Correct group accepted: {group}")
            remaining = after
            # Regenerate candidates for the new board state
            candidates = generate_candidate_groups(list(remaining), priority_groups=None, banned_groups_norm=banned_groups_norm)
            idx = 0
        else:
            log(f"❌ Group rejected: {group}")
            mistakes += 1
            # Track this exact grouping as banned
            banned_groups_norm.append(set(map(str.lower, group)))
            # Deselect all before reconsidering
            try:
                root.get_by_role("button", name="Deselect All").click()
                root.wait_for_timeout(300)
            except Exception:
                for w in group:
                    click_tile_by_text(root, tile_sel, w)
            # Re-query OpenAI with feedback (remaining words and banned groups)
            if opt.use_openai and api_key:
                log("Re-querying OpenAI for refined proposals avoiding incorrect groups…")
                remaining_originals = originals_from_remaining(tiles, remaining)
                refined_groups = openai_refine_groups(remaining_originals, [list(g) for g in banned_groups_norm], api_key=api_key, model=opt.openai_model)
                if refined_groups:
                    log(f"OpenAI refined proposal(s): {refined_groups}")
                    # Prepend refined proposals to candidates (avoid duplicates and banned)
                    new_cands = []
                    for g in refined_groups:
                        if set(map(str.lower, g)) <= remaining and not any(set(map(str.lower, g)) == b for b in banned_groups_norm):
                            new_cands.append(g)
                    # Put refined first, then regenerate the rest with banned filters
                    rest = generate_candidate_groups(list(remaining), priority_groups=None, banned_groups_norm=banned_groups_norm)
                    candidates = new_cands + rest
                    idx = 0
            if mistakes >= opt.max_mistakes:
                log(f"Reached mistake limit ({mistakes}). Stopping.")
                break

    log("Done.")



def main():
    """
    Parse command-line arguments and run the solver.
    
    Returns:
        Exit code from the solver.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--headful", action="store_true", help="show the browser (default headless)")
    ap.add_argument("--dry-run", action="store_true", help="don’t click/submit, just open and read")
    ap.add_argument("--url", default=URL, help="override the game URL")
    ap.add_argument("--openai-model", default="gpt-4.1", help="OpenAI model name (default: gpt-4.1)")
    ap.add_argument("--site-url", default=None,
                    help="Optional site URL for OpenRouter rankings header")
    ap.add_argument("--site-title", default=None,
                    help="Optional site title for OpenRouter rankings header")
    args = ap.parse_args()

    opt = Options(
        headless=not args.headful,
        dry_run=args.dry_run,
        openai_model=args.openai_model,
    )

    with sync_playwright() as pw:
        solve(pw, args.url, opt)
# ----------------------------- LLM integration (OpenRouter) ----------------------------- #

def openai_propose_groups(words_original: List[str], api_key: str, model: str = "gpt-4.1", timeout: int = 60) -> List[List[str]]:
    """
    Use OpenAI API to propose 4 groups of 4 words for NYT Connections.
    
    Args:
        words_original: List of 16 words in their original form.
        api_key: OpenAI API key.
        model: OpenAI model name to use.
        timeout: Request timeout in seconds (unused in current implementation).
        
    Returns:
        List of up to 4 proposed groups, each containing 4 words.
    """
    try:
        client = OpenAI(api_key=api_key)
        words_list = list(words_original)
        prompt = (
            "You are playing the NYT Connections game. You are given exactly 16 words. "
            "Partition them into 4 groups of 4 by shared connection. IMPORTANT RULES: "
            "The four categories get increasingly difficult to match, so start with the easiest ones first. "
            "The easiest ones are often synonyms or simple patterns, while the hardest ones may be more abstract."
            "Use only the given words (no extras), each word appears in exactly one group, "
            "and return strict JSON with the schema {\"groups\": [[four words], ...]} "
            "using the words EXACTLY as shown.\n\n"
            f"WORDS: {words_list}\n\nReturn ONLY JSON, no explanation."
        )

        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )

        content = completion.choices[0].message.content if completion and completion.choices else ""
        m = re.search(r"\{[\s\S]*\}", content or "")
        json_text = m.group(0) if m else (content or "")
        parsed = json.loads(json_text)
        groups = parsed.get("groups", [])
        if not isinstance(groups, list):
            return []

        # Validate and map back to originals
        norm_map = _map_normalized_to_original(words_original)
        words_norm_set = {normalize(w) for w in words_original}
        out: List[List[str]] = []
        for g in groups:
            if not isinstance(g, list) or len(g) != 4:
                continue
            mapped = []
            ok = True
            for w in g:
                wn = normalize(str(w))
                if wn not in words_norm_set:
                    ok = False
                    break
                orig = _consume_originals(norm_map, wn)
                if orig is None:
                    ok = False
                    break
                mapped.append(orig)
            if ok and len(mapped) == 4:
                out.append(mapped)
        return out[:4]
    except Exception as e:
        log(f"OpenAI call failed: {e}")
        return []


if __name__ == "__main__":
    sys.exit(main())