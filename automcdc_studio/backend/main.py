from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.api import environments, testcases, runs, coverage, settings, requirements


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="AutoMCDC Studio", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(environments.router, prefix="/api/environments", tags=["environments"])
app.include_router(testcases.router,   prefix="/api/testcases",   tags=["testcases"])
app.include_router(runs.router,        prefix="/api/runs",         tags=["runs"])
app.include_router(coverage.router,    prefix="/api/coverage",     tags=["coverage"])
app.include_router(settings.router,     prefix="/api/settings",      tags=["settings"])
app.include_router(requirements.router, prefix="/api/requirements",  tags=["requirements"])

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
