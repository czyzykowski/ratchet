from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from core.db import close_pool, get_pool

    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Ratchet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": "Ratchet"})


@app.get("/board")
async def board(request: Request):
    return templates.TemplateResponse("board.html", {"request": request, "title": "Board"})


@app.get("/projects")
async def projects(request: Request):
    return templates.TemplateResponse("projects.html", {"request": request, "title": "Projects"})


@app.get("/blocked")
async def blocked(request: Request):
    return templates.TemplateResponse("blocked.html", {"request": request, "title": "Blocked"})


@app.get("/features")
async def features(request: Request):
    return templates.TemplateResponse("features.html", {"request": request, "title": "Features"})
