import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

from app.services import job_store

router = APIRouter(prefix="/api/projects", tags=["projects"])


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
    return json.loads(cfg_path.read_text(encoding="utf-8"))


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
            projects.append(json.loads(cfg.read_text(encoding="utf-8")))
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

    linkmap_bytes = await linkmap.read()
    brand_bytes = await brand_features.read()

    try:
        json.loads(linkmap_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="linkmap.json 格式错误，请上传有效的JSON文件")

    try:
        json.loads(brand_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="brand_features.json 格式错误，请上传有效的JSON文件")

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
    config["jobs"] = job_store.list_jobs_for_project(project_id)
    return config


@router.put("/{project_id}/linkmap")
async def update_linkmap(project_id: str, linkmap: UploadFile = File(...)):
    _load_config(project_id)
    content = await linkmap.read()
    try:
        json.loads(content)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的JSON文件")
    (_project_dir(project_id) / "linkmap.json").write_bytes(content)
    return {"ok": True}


@router.put("/{project_id}/brand_features")
async def update_brand_features(project_id: str, brand_features: UploadFile = File(...)):
    _load_config(project_id)
    content = await brand_features.read()
    try:
        json.loads(content)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的JSON文件")
    (_project_dir(project_id) / "brand_features.json").write_bytes(content)
    return {"ok": True}


@router.delete("/{project_id}")
def delete_project(project_id: str):
    _load_config(project_id)
    shutil.rmtree(_project_dir(project_id))
    return {"ok": True}
