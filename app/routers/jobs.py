import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from app.services import job_store, pipeline, excel_io
from app.routers.projects import _project_dir, _load_config

router = APIRouter(prefix="/api", tags=["jobs"])


def _job_dir(project_id: str, job_id: str) -> Path:
    return _project_dir(project_id) / "jobs" / job_id


def _run_job(job_id: str, project_id: str, questions: list[str], questions_map: dict, steps: list[int], workers: int) -> None:
    pdir = _project_dir(project_id)
    jdir = _job_dir(project_id, job_id)
    output_dir = jdir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    link_map = json.loads((pdir / "linkmap.json").read_text(encoding="utf-8"))
    brand_features = json.loads((pdir / "brand_features.json").read_text(encoding="utf-8"))
    api_key = os.getenv("GOOGLE_CLOUD_API_KEY", "")

    job_store.update_job(job_id, status="running")

    def progress_callback(done: int, total: int, failed: int) -> None:
        job_store.update_job(job_id, success_count=done - failed, failed_count=failed)

    try:
        pipeline.run_pipeline(
            questions=questions,
            output_dir=output_dir,
            link_map=link_map,
            brand_features=brand_features,
            api_key=api_key,
            workers=workers,
            steps=steps,
            progress_callback=progress_callback,
        )
    except Exception as e:
        job_store.update_job(job_id, error=str(e))
    finally:
        # Always collect whatever results exist
        df = excel_io.collect_results(output_dir, questions_map)
        result_path = jdir / "result.xlsx"
        has_results = False
        if not df.empty:
            excel_io.save_result_excel(df, result_path)
            has_results = True

        job = job_store.get_job(job_id)
        if job:
            if job.failed_count > 0 and job.success_count > 0:
                status = "partial"
            elif job.success_count == 0:
                status = "failed"
            else:
                status = "completed"
        else:
            status = "completed"

        job_store.update_job(
            job_id,
            status=status,
            completed_at=time.time(),
            has_results=has_results,
        )


@router.post("/projects/{project_id}/jobs")
async def create_job(
    project_id: str,
    excel: UploadFile = File(...),
    workers: int = Form(50),
    steps: str = Form("1,2,3"),
):
    _load_config(project_id)
    pdir = _project_dir(project_id)

    if not (pdir / "linkmap.json").exists() or not (pdir / "brand_features.json").exists():
        raise HTTPException(status_code=400, detail="请先完成项目配置（上传linkmap和brand_features）")

    excel_bytes = await excel.read()
    try:
        questions = excel_io.read_questions(excel_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Excel读取失败: {e}")

    if not questions:
        raise HTTPException(status_code=400, detail="Excel中未找到有效问题")

    step_list = [int(s.strip()) for s in steps.split(",") if s.strip().isdigit()]
    job_id = str(uuid.uuid4())[:12]
    jdir = _job_dir(project_id, job_id)
    jdir.mkdir(parents=True, exist_ok=True)

    # Save input Excel and questions map
    (jdir / "input.xlsx").write_bytes(excel_bytes)
    questions_map = {pipeline.question_to_slug(q): q for q in questions}
    (jdir / "questions_map.json").write_text(
        json.dumps(questions_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    job_store.create_job(job_id, project_id, total=len(questions))

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, project_id, questions, questions_map, step_list, workers),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "total": len(questions)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    async def event_generator():
        while True:
            job = job_store.get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                break
            yield f"data: {json.dumps(job.to_dict())}\n\n"
            if job.status in ("completed", "partial", "failed"):
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/download")
def download_result(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result_path = _job_dir(job.project_id, job_id) / "result.xlsx"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="结果文件尚未生成")

    return FileResponse(
        path=str(result_path),
        filename=f"faq_result_{job_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
