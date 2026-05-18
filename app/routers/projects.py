import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

from app.services import job_store

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _strip_bom(content: bytes) -> bytes:
    return content[3:] if content.startswith(b'\xef\xbb\xbf') else content


def _validate_linkmap(content: bytes) -> str:
    """Returns error message or empty string if valid."""
    try:
        data = json.loads(content)
    except Exception as e:
        return f"不是合法的 JSON（{e}）"
    if not isinstance(data, dict):
        return "顶层必须是 JSON 对象（{}）"
    if "feature_pages" not in data:
        return "缺少 feature_pages 字段"
    feature_pages = data["feature_pages"]
    if not isinstance(feature_pages, list):
        return "feature_pages 必须是数组"
    if len(feature_pages) == 0:
        return "feature_pages 不能为空，请至少添加一个页面"
    for i, page in enumerate(feature_pages):
        if not isinstance(page, dict):
            return f"feature_pages[{i}] 必须是对象"
        if "url" not in page:
            return f"feature_pages[{i}] 缺少 url 字段"
        if "trigger_topics" not in page or not isinstance(page["trigger_topics"], list):
            return f"feature_pages[{i}] 缺少 trigger_topics 数组"
    return ""


def _validate_brand_features(content: bytes) -> str:
    """Returns error message or empty string if valid."""
    try:
        data = json.loads(content)
    except Exception as e:
        return f"不是合法的 JSON（{e}）"
    if not isinstance(data, dict):
        return "顶层必须是 JSON 对象（{}）"
    if not data.get("brand_name") and not data.get("name"):
        return "缺少 brand_name（或 name）字段"
    if "features" not in data:
        return "缺少 features 字段"
    features = data["features"]
    if not isinstance(features, list):
        return "features 必须是数组"
    if len(features) == 0:
        return "features 不能为空，请至少添加一个功能项"
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            return f"features[{i}] 必须是对象"
        if "name" not in f:
            return f"features[{i}] 缺少 name 字段"
        if "trigger_topics" not in f or not isinstance(f["trigger_topics"], list):
            return f"features[{i}] 缺少 trigger_topics 数组"
    return ""


def _data_dir() -> Path:
    import os
    return Path(os.getenv("DATA_DIR", "./data"))


def _projects_dir() -> Path:
    d = _data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_dir(project_id: str) -> Path:
    return _projects_dir() / project_id


def _load_config(project_id: str) -> dict:
    cfg_path = _project_dir(project_id) / "config.json"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return json.loads(cfg_path.read_text(encoding="utf-8-sig"))


def _save_config(project_id: str, config: dict) -> None:
    cfg_path = _project_dir(project_id) / "config.json"
    cfg_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("")
def list_projects():
    projects = []
    pdir = _projects_dir()
    for d in sorted(pdir.iterdir()):
        cfg = d / "config.json"
        if cfg.exists():
            projects.append(json.loads(cfg.read_text(encoding="utf-8-sig")))
    return projects


@router.post("")
async def create_project(
    name: str = Form(...),
    linkmap: UploadFile = File(...),
    brand_features: UploadFile = File(...),
):
    project_id = str(uuid.uuid4())[:8]
    pdir = _project_dir(project_id)
    pdir.mkdir(parents=True, exist_ok=True)

    linkmap_bytes = _strip_bom(await linkmap.read())
    brand_bytes = _strip_bom(await brand_features.read())

    linkmap_error = _validate_linkmap(linkmap_bytes)
    if linkmap_error:
        raise HTTPException(status_code=400, detail=f"linkmap.json 格式错误：{linkmap_error}")

    brand_error = _validate_brand_features(brand_bytes)
    if brand_error:
        raise HTTPException(status_code=400, detail=f"brand_features.json 格式错误：{brand_error}")

    (pdir / "linkmap.json").write_bytes(linkmap_bytes)
    (pdir / "brand_features.json").write_bytes(brand_bytes)

    config = {"id": project_id, "name": name, "has_linkmap": True, "has_brand_features": True}
    _save_config(project_id, config)

    return config


@router.get("/{project_id}")
def get_project(project_id: str):
    config = _load_config(project_id)
    pdir = _project_dir(project_id)
    config["has_linkmap"] = (pdir / "linkmap.json").exists()
    config["has_brand_features"] = (pdir / "brand_features.json").exists()
    cn_path = pdir / "brand_name_cn.txt"
    config["brand_name_cn"] = cn_path.read_text(encoding="utf-8").strip() if cn_path.exists() else ""
    config["jobs"] = job_store.list_jobs_for_project(project_id)
    return config


@router.put("/{project_id}/brand_name_cn")
async def update_brand_name_cn(project_id: str, brand_name_cn: str = Form(...)):
    _load_config(project_id)
    (_project_dir(project_id) / "brand_name_cn.txt").write_text(brand_name_cn.strip(), encoding="utf-8")
    return {"ok": True}


@router.put("/{project_id}/linkmap")
async def update_linkmap(project_id: str, linkmap: UploadFile = File(...)):
    _load_config(project_id)
    content = _strip_bom(await linkmap.read())
    err = _validate_linkmap(content)
    if err:
        raise HTTPException(status_code=400, detail=f"linkmap.json 格式错误：{err}")
    (_project_dir(project_id) / "linkmap.json").write_bytes(content)
    return {"ok": True}


@router.put("/{project_id}/brand_features")
async def update_brand_features(project_id: str, brand_features: UploadFile = File(...)):
    _load_config(project_id)
    content = await brand_features.read()
    err = _validate_brand_features(content)
    if err:
        raise HTTPException(status_code=400, detail=f"brand_features.json 格式错误：{err}")
    (_project_dir(project_id) / "brand_features.json").write_bytes(content)
    return {"ok": True}


@router.get("/{project_id}/files/{filename}")
def download_project_file(project_id: str, filename: str):
    allowed = {"linkmap.json", "brand_features.json"}
    if filename not in allowed:
        raise HTTPException(status_code=400, detail="不允许下载该文件")
    _load_config(project_id)
    file_path = _project_dir(project_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=str(file_path), filename=filename, media_type="application/json")


@router.delete("/{project_id}")
def delete_project(project_id: str):
    _load_config(project_id)
    shutil.rmtree(_project_dir(project_id))
    return {"ok": True}
