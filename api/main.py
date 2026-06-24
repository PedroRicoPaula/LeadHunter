import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.db import init_db
from api.routers import companies, pipeline, llm


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("[nexus] DB ready")
    yield


app = FastAPI(title="Nexus OS API", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(pipeline.router)
app.include_router(llm.router)

WEB_DIST = Path(__file__).parent.parent / "web" / "dist"


@app.get("/api/health")
def health():
    return {"status": "ok"}


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="static")
