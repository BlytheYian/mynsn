"""MCDC Lite 進入點：給 MC/DC 新手用的簡化前端 + 薄後端。

本服務本身不做任何測試生成邏輯，只負責：
  1. 把新手需要填的表單（函式、參數型別、值域）自動預填
  2. 呼叫既有的 ifl_api 做真正的解析／MC/DC 測試生成
  3. 把回應整理成白話摘要，技術細節收在 /result 的 "raw" 欄位

啟動方式（需先啟動 ifl_api）：
    uvicorn ifl_api.main:app --port 8100 &
    uvicorn mynsn.backend.main:app --reload --port 8200

環境變數從專案根目錄的 .env 讀取（IFL_API_BASE_URL、GITHUB_OAUTH_CLIENT_ID、
GITHUB_OAUTH_CLIENT_SECRET、GITHUB_OAUTH_REDIRECT_URI），不用手動 export。
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# 一定要在 import routes 之前載入：routes.py／ifl_client.py 是在模組載入當下
# 就用 os.environ.get(...) 讀取設定值，載入太晚就讀不到。
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import storage  # noqa: E402
from .environments_routes import router as environments_router  # noqa: E402
from .environments_routes import runs_router  # noqa: E402
from .routes import router  # noqa: E402

app = FastAPI(title="MCDC Lite", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(environments_router)
app.include_router(runs_router)


@app.on_event("startup")
def _init_storage() -> None:
    storage.init_db()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# 靜態前端一定要最後掛載：StaticFiles(html=True) 會攔截所有未匹配到上面路由的路徑。
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
