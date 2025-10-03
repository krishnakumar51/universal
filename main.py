import asyncio
import uuid
import json
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, Browser
from langgraph.errors import GraphRecursionError

from agent.state import AgentState
from agent.graph import create_graph, set_push_status_update
from config.settings import SCREENSHOTS_DIR, RESULTS_DIR, STATIC_DIR, VIEWPORT_SIZE
from browser.utils import get_current_timestamp

# IMPROVE: Add basic logging
import logging
logging.basicConfig(level=logging.INFO)

# --- CRITICAL FIX: Restore the robust import for undetected-playwright ---
try:
    from undetected_playwright.sync import Tarnished
except ImportError:
    try:
        from undetected_playwright import Tarnished
    except ImportError:
        logging.warning("undetected_playwright not found. Stealth features disabled.")
        Tarnished = None

LLMProvider = str

app = FastAPI(title="Universal Web Agent")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

JOB_QUEUES = {}
JOB_RESULTS = {}

def push_status(job_id: str, msg: str, details: dict = None):
    q = JOB_QUEUES.get(job_id)
    if q:
        entry = {"ts": get_current_timestamp(), "msg": msg}
        if details: entry["details"] = details
        try: q.put_nowait(entry)
        except asyncio.QueueFull: logging.warning(f"Queue full for job {job_id}.")

set_push_status_update(push_status)

class SearchRequest(BaseModel):
    url: str
    query: str
    llm_provider: LLMProvider = "anthropic"
    stealth: bool = False  # NEW: Default to False (normal Playwright)

agent_graph = create_graph()

def run_job(job_id: str, payload: dict):
    push_status(job_id, "job_initiated")
    browser: Browser = None
    final_state_dict = {}
    stealth_enabled = payload.get("stealth", False)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORT_SIZE,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            # UPDATED: Apply stealth only if requested and available
            if stealth_enabled:
                if Tarnished:
                    Tarnished.apply_stealth(context)
                    logging.info(f"Stealth mode enabled for job {job_id}")
                else:
                    logging.warning(f"Stealth requested but undetected_playwright not available for job {job_id}. Using normal mode.")
                    stealth_enabled = False  # Fallback to normal
            
            page = context.new_page()
            page.goto(payload["url"], wait_until='domcontentloaded', timeout=90000)
            
            page.wait_for_selector("body", timeout=15000)
            page.wait_for_timeout(5000)
            
            push_status(job_id, "job_started", {"provider": payload["llm_provider"], "query": payload["query"], "stealth": stealth_enabled})
            
            job_artifacts_dir = SCREENSHOTS_DIR / job_id
            job_artifacts_dir.mkdir(exist_ok=True)
            
            initial_state = AgentState(
                job_id=job_id, query=payload["query"], url=page.url, provider=payload["llm_provider"],
                plan_details={}, current_task="", page_content="", modified_html_for_action="",
                results=[], generated_credentials={}, screenshots=[], job_artifacts_dir=job_artifacts_dir,
                step=1, max_steps=40, history=[], execution_summary=[], last_action={},
                last_action_outcome="", retry_count=0, last_error="", research_summary=""
            )

            config = {"configurable": {"page": page}, "recursion_limit": 100}
            
            final_state_dict = agent_graph.invoke(initial_state, config=config)

    except (Exception, GraphRecursionError) as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        tb_str = traceback.format_exc()
        logging.error(tb_str)
        push_status(job_id, "job_failed", {"error": error_message, "trace": tb_str})
        final_state_dict["error"] = error_message
    finally:
        result_data = {
            "job_id": job_id,
            "results": final_state_dict.get("results", []),
            "screenshots": final_state_dict.get("screenshots", []),
            "execution_summary": final_state_dict.get("execution_summary", ["Job did not complete."]),
            "error": final_state_dict.get("error")
        }
        
        results_file = RESULTS_DIR / f"{job_id}.json"
        with open(results_file, "w") as f:
            json.dump(result_data, f, indent=2)

        JOB_RESULTS[job_id] = result_data
        push_status(job_id, "job_done" if not result_data.get("error") else "job_failed")
        if browser: browser.close()

@app.post("/search")
async def start_search(req: SearchRequest):
    job_id = str(uuid.uuid4())
    JOB_QUEUES[job_id] = asyncio.Queue()
    push_status(job_id, "job_queued")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_job, job_id, req.dict())
    return {"job_id": job_id, "stream_url": f"/stream/{job_id}", "result_url": f"/result/{job_id}"}

@app.get("/stream/{job_id}")
async def stream_status(job_id: str):
    q = JOB_QUEUES.get(job_id)
    if not q: raise HTTPException(status_code=404, detail="Job not found")
    async def event_generator():
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=120)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg["msg"] in ("job_done", "job_failed"): break
            except asyncio.TimeoutError: yield ": keep-alive\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/result/{job_id}")
async def get_result(job_id: str):
    result = JOB_RESULTS.get(job_id)
    if not result:
        result_file = RESULTS_DIR / f"{job_id}.json"
        if result_file.exists():
            with open(result_file, "r") as f: return JSONResponse(json.load(f))
        return JSONResponse({"status": "pending"}, status_code=202)
    return JSONResponse(result)

@app.get("/")
async def client_ui():
    return FileResponse(STATIC_DIR / "test_client.html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)