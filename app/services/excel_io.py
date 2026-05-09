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


def collect_results(output_dir: Path, questions_map: dict[str, str]) -> pd.DataFrame:
    """Collect all generated .md files into a DataFrame.

    questions_map: {slug: original_question}
    """
    rows = []
    for md_file in sorted(output_dir.glob("*.md")):
        if md_file.name.startswith("_"):
            continue
        slug = md_file.stem
        question = questions_map.get(slug, slug.replace("-", " ").capitalize())
        answer = md_file.read_text(encoding="utf-8").strip()
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
            "excerpt": meta.get("excerpt", ""),
            "keywords": ", ".join(meta.get("keywords", [])),
        })
    return pd.DataFrame(rows, columns=["question", "slug", "seo_title", "answer", "excerpt", "keywords"])


def save_result_excel(df: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="FAQ")
