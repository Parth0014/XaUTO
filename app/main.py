import asyncio
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import HTMLResponse

from app.database import get_db_client, init_indexes
from app.routes.analytics import router as analytics_router
from app.routes.browser import router as browser_router
from app.routes.embeddings import router as embeddings_router
from app.routes.generator import router as generator_router
from app.routes.maintenance import router as maintenance_router
from app.routes.pipeline import router as pipeline_router
from app.routes.posting import router as posting_router
from app.routes.retrieval import router as retrieval_router
from app.routes.scraper import router as scraper_router
from app.routes.scoring import router as scoring_router
from app.routes.trends import router as trends_router
from app.routes.events import router as events_router
from app.services.event_broadcaster import make_event, publish_sync, set_event_loop
from app.scheduler.jobs import start_scheduler
from app.scheduler.jobs import stop_scheduler
from app.services.vector_store_service import qdrant_health

app = FastAPI()

cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allowed_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.middleware("http")
async def request_event_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/analytics", "/scrape", "/pipeline", "/score", "/post", "/generate")):
        try:
            publish_sync(
                make_event(
                    "request",
                    {
                        "method": request.method,
                        "path": path,
                        "status_code": response.status_code,
                    },
                )
            )
        except Exception:
            pass
    return response


@app.on_event("startup")
async def startup_event():
    try:
        set_event_loop(asyncio.get_running_loop())
    except RuntimeError:
        set_event_loop(None)
    init_indexes(get_db_client())
    start_scheduler()


@app.on_event("shutdown")
def shutdown_event():

    stop_scheduler()


app.include_router(scraper_router)
app.include_router(browser_router)
app.include_router(analytics_router)
app.include_router(embeddings_router)
app.include_router(generator_router)
app.include_router(pipeline_router)
app.include_router(posting_router)
app.include_router(retrieval_router)
app.include_router(maintenance_router)
app.include_router(scoring_router)
app.include_router(trends_router)
app.include_router(events_router)


@app.get("/", response_class=HTMLResponse)
def home():

    return """
    <!DOCTYPE html>
    <html>

    <head>
        <title>X AI System</title>

        <style>

            body{
                background:#0f172a;
                color:white;
                font-family:Arial;
                display:flex;
                justify-content:center;
                align-items:center;
                height:100vh;
                flex-direction:column;
            }

            h1{
                margin-bottom:30px;
            }

            button{
                padding:15px 30px;
                font-size:18px;
                border:none;
                border-radius:10px;
                cursor:pointer;
                background:#2563eb;
                color:white;
            }

            button:hover{
                background:#1d4ed8;
            }

            #status{
                margin-top:20px;
                font-size:18px;
            }

        </style>

    </head>

    <body>

        <h1>X AI Scraper System</h1>

        <button onclick="startScraper()">
            Start X Scraper
        </button>

        <div id="status"></div>

        <script>

            async function startScraper(){

                document.getElementById("status").innerHTML =
                    "Scraper Running...";

                const response = await fetch('/scrape/x');

                const data = await response.json();

                document.getElementById("status").innerHTML =
                    data.message;
            }

        </script>

    </body>

    </html>
    """


@app.get("/healthz")
def health_check():
    qdrant = qdrant_health()
    status = "ok" if qdrant.get("available") or not qdrant.get("configured") else "degraded"
    return {
        "status": status,
        "qdrant": qdrant,
    }
