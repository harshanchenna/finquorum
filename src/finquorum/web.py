"""Tiny local web page for the human-only approve/reject step: one list of
pending proposals with approve/reject buttons. No auth beyond "runs on
localhost" — this is a POC, not a hardened deployment."""

import json

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from finquorum.db import get_conn
from finquorum import proposals

app = FastAPI(title="finquorum")
templates = Jinja2Templates(directory=str(__file__.rsplit("/", 1)[0] + "/templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with get_conn() as conn:
        pending = proposals.list_proposals(conn, status="pending")
        decided = [
            p for p in proposals.list_proposals(conn) if p.status != "pending"
        ][-20:]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "pending": pending,
            "decided": decided,
            "dumps": lambda o: json.dumps(o, indent=2),
        },
    )


@app.post("/approve/{proposal_id}", response_class=HTMLResponse)
def approve(request: Request, proposal_id: int):
    with get_conn() as conn:
        proposals.approve_proposal(conn, proposal_id, decided_by="web-ui")
    return index(request)


@app.post("/reject/{proposal_id}", response_class=HTMLResponse)
def reject(request: Request, proposal_id: int):
    with get_conn() as conn:
        proposals.reject_proposal(conn, proposal_id, decided_by="web-ui")
    return index(request)


@app.post("/revert/{proposal_id}", response_class=HTMLResponse)
def revert(request: Request, proposal_id: int):
    with get_conn() as conn:
        proposals.revert_proposal(conn, proposal_id, decided_by="web-ui")
    return index(request)
