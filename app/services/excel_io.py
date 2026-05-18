import json
from pathlib import Path
import pandas as pd


def read_questions(xlsx_bytes: bytes) -> list[str]:
    """Read questions from uploaded Excel bytes. First column, no header assumed."""
    import io
    df = pd.read_excel(io.BytesIO(xlsx_bytes), engine="openpyxl", header=None)
    col = df.columns[0]
    return (
        df[col]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s != ""]
        .drop_duplicates()
        .tolist()
    )


def _question_to_seo_title(question: str) -> str:
    q = question.strip()
    if q and q[-1] in ".?!":
        q = q[:-1]
    return q[:70]


def collect_results(output_dir: Path, questions_map: dict[str, str], english: bool = False) -> pd.DataFrame:
    """Collect generated content into a DataFrame.

    english=True: read from .en.md / .en.meta.json backups (pre-translation English).
    english=False (default): read from .md / .meta.json (current, may be Chinese).
    questions_map: {slug: original_question}
    """
    rows = []
    if english:
        md_files = sorted(f for f in output_dir.glob("*.en.md") if not f.name.startswith("_"))
    else:
        md_files = sorted(f for f in output_dir.glob("*.md")
                          if not f.name.startswith("_") and not f.name.endswith(".en.md"))

    # Load Chinese question translations if available (only used for zh output)
    questions_zh: dict[str, str] = {}
    if not english:
        zh_path = output_dir / "_questions_zh.json"
        if zh_path.exists():
            try:
                questions_zh = json.loads(zh_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    for md_file in md_files:
        slug = md_file.name.replace(".en.md", "").replace(".md", "")
        question = (
            questions_zh.get(slug)
            or questions_map.get(slug, slug.replace("-", " ").capitalize())
        )
        answer = md_file.read_text(encoding="utf-8").strip()
        if english:
            meta_file = output_dir / f"{slug}.en.meta.json"
            if not meta_file.exists():
                meta_file = output_dir / f"{slug}.meta.json"
        else:
            meta_file = output_dir / f"{slug}.meta.json"
        meta = {}
        if meta_file.exists() and meta_file.stat().st_size > 0:
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        seo_title = meta.get("seo_title") or _question_to_seo_title(question)
        rows.append({
            "question": question,
            "slug": slug,
            "seo_title": seo_title,
            "answer": answer,
            "description": meta.get("description") or meta.get("excerpt", ""),
            "keywords": ", ".join(meta.get("keywords", [])),
        })
    return pd.DataFrame(rows, columns=["question", "slug", "seo_title", "answer", "description", "keywords"])


def save_result_excel(df: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="FAQ")
