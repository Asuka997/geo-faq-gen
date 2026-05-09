import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import projects, jobs
from app.routers.projects import _projects_dir

load_dotenv()

app = FastAPI(title="FAQ Generator")

app.include_router(projects.router)
app.include_router(jobs.router)

# Templates endpoint
from fastapi.responses import FileResponse
from fastapi import APIRouter

templates_router = APIRouter(prefix="/api/templates", tags=["templates"])

TEMPLATES_DIR = Path(__file__).parent / "templates"


@templates_router.get("/{name}")
def download_template(name: str):
    allowed = {"linkmap_template.json", "brand_features_template.json"}
    if name not in allowed:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Template not found")
    return FileResponse(path=str(TEMPLATES_DIR / name), filename=name, media_type="application/json")


app.include_router(templates_router)

# Serve static files last (catches all remaining routes for SPA)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
