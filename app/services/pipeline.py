"""Core FAQ generation pipeline (adapted from pipeline_hi3d.py).

All parameters are passed explicitly — no global state, no fixed paths.
"""

import json
import os
import re
import concurrent.futures
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from google import genai
from google.genai import types


logger = logging.getLogger("faq_pipeline")


# ── Prompt builders ──────────────────────────────────────────────────────────

def _build_step1_prompt(brand_features: dict) -> str:
    # Support both naming conventions
    brand_name = brand_features.get("brand_name") or brand_features.get("name", "Our Brand")
    brand_desc = (
        brand_features.get("brand_description")
        or brand_features.get("tagline")
        or "a leading platform"
    )
    writing_context = brand_features.get("writing_context", "Write for a general audience.")
    features = brand_features.get("features", [])

    if features:
        header = "| Feature | What it does (one line) | Trigger when answer mentions... |"
        sep    = "|---------|------------------------|--------------------------------|"
        rows = []
        for f in features:
            trigger = f.get("trigger_when") or ", ".join(f.get("trigger_topics", []))
            rows.append(f"| {f['name']} | {f['description']} | {trigger} |")
        features_table = "\n".join([header, sep] + rows)
        feature_block = f"""{brand_name.upper()} FEATURES REFERENCE:
Use this table to naturally mention ONE relevant feature if it genuinely addresses
a specific pain point in the answer. Do not list features. Do not force-fit.

{features_table}

---
"""
    else:
        feature_block = ""

    return f"""You are an SEO content writer for {brand_name}, {brand_desc}.

Your task is to write a clear, helpful answer to the following FAQ question.

---

{feature_block}
WRITING RULES:

1. FIRST SENTENCE: Directly answer the question in one sentence.
   This is critical for Google Featured Snippets.

2. UNIQUE ANGLE: Identify the ONE specific aspect that makes this question distinct
   from related questions. Build the entire article around that angle only.

3. STRUCTURE: Use H3 subheadings (###) or numbered lists where it aids clarity.

4. TONE: Friendly and practical. {writing_context} Avoid jargon unless explained.

5. LENGTH: 300–500 words. Comprehensive but not padded.

6. SEO: Naturally include related search terms. Do not keyword-stuff.

7. BRAND INTEGRATION: {brand_name} must appear 2–3 times as a practical workflow
   participant, not a promotional footnote. At least one mention should name a
   specific feature; at least one should appear inside a numbered step.

8. OUTPUT FORMAT: Return a JSON object only. No intro, no commentary, no markdown fences.

   {{
     "body": "<full answer in Markdown, no hyperlinks>",
     "seo_title": "<SEO page title, 50–60 chars, primary keyword near front, no brand suffix>",
     "excerpt": "<1–2 sentences, 120–155 chars, active voice, includes primary keyword>",
     "keywords": ["<kw1>", "<kw2>", "<kw3>", "<kw4>"]
   }}

   KEYWORDS: 4–6 terms, all lowercase, no brand names, mix exact-match and long-tail."""


STEP2_SYSTEM_PROMPT = """You are an SEO internal linking specialist.

Given a FAQ answer in Markdown and a link map of available internal pages, insert ONE internal link.

INSTRUCTIONS:
1. Find the single sentence where a link fits most naturally based on trigger_topics.
2. Return JSON only:
   - Match found: {"original": "<exact sentence>", "replacement": "<sentence with [anchor](url)>"}
   - No match: {"no_change": true}

RULES:
- Anchor text must come from words already in the sentence — do not add new words
- Prefer brand name or feature name as anchor over generic verbs
- Do not link the first sentence
- Prefer feature_pages over core_pages
- Return valid JSON only. No explanation, no markdown fences."""


# ── Slug utilities ───────────────────────────────────────────────────────────

def question_to_slug(question: str) -> str:
    slug = question.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = slug[:120]
    return slug


def build_link_map_text(link_map: dict) -> str:
    return json.dumps(link_map, indent=2, ensure_ascii=False)


def get_approved_urls(link_map: dict) -> set[str]:
    urls = [p["url"] for p in link_map.get("core_pages", [])]
    urls += [p["url"] for p in link_map.get("feature_pages", [])]
    return set(urls)


# ── Step 1: Answer generation ────────────────────────────────────────────────

def _generate_one(
    question: str,
    output_dir: Path,
    client: genai.Client,
    step1_prompt: str,
    model_id: str,
) -> tuple[bool, Optional[str]]:
    """Returns (success, error_message)."""
    slug = question_to_slug(question)
    md_path = output_dir / f"{slug}.md"
    meta_path = output_dir / f"{slug}.meta.json"
    tmp_md = output_dir / f"{slug}.md.tmp"
    tmp_meta = output_dir / f"{slug}.meta.json.tmp"

    if md_path.exists() and md_path.stat().st_size > 0 and meta_path.exists() and meta_path.stat().st_size > 0:
        return True, None

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=f"QUESTION: {question}\n\nNow write the answer.",
            config=types.GenerateContentConfig(
                system_instruction=step1_prompt,
                temperature=0.7,
                max_output_tokens=16384,
            ),
        )
        raw = re.sub(r"^```[a-z]*\n?|```$", "", response.text.strip(), flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            body_match = re.search(r'"body"\s*:\s*"(.*?)"\s*,\s*"excerpt"', raw, re.DOTALL)
            if body_match:
                body_text = body_match.group(1).replace('\\"', '"').replace('\\n', '\n')
                parsed = {"body": body_text, "excerpt": "", "keywords": []}
            else:
                return False, f"JSON parse error: {raw[:80]}"

        body = parsed.get("body", "").strip()
        seo_title = parsed.get("seo_title", "").strip()
        excerpt = parsed.get("excerpt", "").strip()
        keywords = parsed.get("keywords", [])

        if not body:
            return False, "empty body"

        tmp_md.write_text(body, encoding="utf-8")
        tmp_meta.write_text(
            json.dumps({"seo_title": seo_title, "excerpt": excerpt, "keywords": keywords}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_md, md_path)
        os.replace(tmp_meta, meta_path)
        return True, None

    except Exception as e:
        for p in (tmp_md, tmp_meta):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        return False, str(e)


# ── Step 2: Internal linking ─────────────────────────────────────────────────

def _insert_link_one(
    md_path: Path,
    client: genai.Client,
    link_map_text: str,
) -> str:
    content = md_path.read_text(encoding="utf-8")
    if "](http" in content:
        return "skip"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"LINK MAP:\n{link_map_text}\n\n---\n\nFAQ ANSWER:\n{content}",
            config=types.GenerateContentConfig(
                system_instruction=STEP2_SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception as e:
        logger.warning("Step2 API error [%s]: %s", md_path.name, e)
        return "error"

    if not response.text:
        return "error"

    raw = re.sub(r"^```[a-z]*\n?|```$", "", response.text.strip(), flags=re.MULTILINE).strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return "error"

    if result.get("no_change"):
        return "nomatch"

    original = result.get("original", "")
    replacement = result.get("replacement", "")
    if not original or not replacement or original not in content:
        return "error"

    md_path.write_text(content.replace(original, replacement, 1), encoding="utf-8")
    return "ok"


# ── Step 3: Validation ───────────────────────────────────────────────────────

def _validate_one(md_path: Path, approved_urls: set[str]) -> list[str]:
    content = md_path.read_text(encoding="utf-8")
    issues: list[str] = []
    if not content.strip():
        return ["empty file"]

    for anchor, url in re.findall(r'\[([^\]]*)\]\(([^)]*)\)', content):
        if not anchor.strip():
            issues.append(f"empty anchor text → {url[:60]}")
        if url.startswith("http") and url not in approved_urls:
            issues.append(f"unapproved URL: {url}")

    levels = [len(m.group(1)) for m in re.finditer(r'^(#{1,6})\s', content, re.MULTILINE)]
    for i in range(1, len(levels)):
        if levels[i] - levels[i - 1] > 1:
            issues.append(f"heading skip H{levels[i-1]}→H{levels[i]}")

    return issues


def run_step3(output_dir: Path, approved_urls: set[str]) -> dict:
    md_files = sorted(f for f in output_dir.glob("*.md") if not f.name.startswith("_"))
    failed = []
    for f in md_files:
        issues = _validate_one(f, approved_urls)
        if issues:
            failed.append({"file": f.name, "issues": issues})

    report = {
        "total": len(md_files),
        "passed": len(md_files) - len(failed),
        "failed": len(failed),
        "failures": failed,
    }
    report_path = output_dir / "_validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# ── Main pipeline runner ─────────────────────────────────────────────────────

def run_pipeline(
    questions: list[str],
    output_dir: Path,
    link_map: dict,
    brand_features: dict,
    api_key: str,
    workers: int,
    steps: list[int],
    progress_callback: Callable[[int, int, int], None],  # (done, total, failed)
) -> None:
    """Run the full pipeline. Writes results to output_dir as it goes.

    Always collects partial results — never raises, logs errors instead.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key)
    step1_prompt = _build_step1_prompt(brand_features)
    link_map_text = build_link_map_text(link_map)
    model_id = brand_features.get("model_id", "gemini-2.5-pro")

    total = len(questions)
    done = 0
    failed = 0

    if 1 in steps:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_generate_one, q, output_dir, client, step1_prompt, model_id): q
                for q in questions
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    success, err = future.result()
                except Exception as e:
                    success, err = False, str(e)
                done += 1
                if not success:
                    failed += 1
                    logger.warning("Step1 FAIL [%s]: %s", futures[future], err)
                progress_callback(done, total, failed)

    if 2 in steps:
        md_files = sorted(f for f in output_dir.glob("*.md") if not f.name.startswith("_"))
        step2_done = 0
        step2_total = len(md_files)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures2 = {executor.submit(_insert_link_one, f, client, link_map_text): f for f in md_files}
            for future in concurrent.futures.as_completed(futures2):
                try:
                    future.result()
                except Exception as e:
                    logger.warning("Step2 exception: %s", e)
                step2_done += 1

    if 3 in steps:
        approved_urls = get_approved_urls(link_map)
        run_step3(output_dir, approved_urls)
