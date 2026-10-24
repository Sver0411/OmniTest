"""Reproducible local system under test; separate from the platform service."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

app = FastAPI(title="OmniTest fixture system")


@app.get("/health")
def health():
    return {"status": "ok", "version": 1}


@app.get("/items")
def items(q: str = "", limit: int = 3):
    values = [
        {"id": i, "name": name}
        for i, name in enumerate(["apple", "banana", "pear"], 1)
        if q in name
    ]
    return {"items": values[:limit], "count": len(values[:limit])}


@app.post("/echo")
async def echo(request: Request):
    return {"body": await request.json(), "header": request.headers.get("x-test")}


@app.get("/text", response_class=PlainTextResponse)
def text():
    return "OmniTest automation fixture"


@app.get("/redirect")
def redirect():
    return RedirectResponse("/health", status_code=302)


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html><head><title>OmniTest Fixture</title></head>
    <body><h1>Automation fixture</h1><label>Name <input id="name" placeholder="Your name"></label>
    <button id="greet" onclick="document.querySelector('#greeting').textContent='Hello, '+document.querySelector('#name').value">Greet</button>
    <p id="greeting" aria-live="polite"></p><button id="toggle" onclick="document.querySelector('#details').hidden=false">Show details</button>
    <p id="details" hidden>Reliable automation</p><a id="next" href="/second">Next page</a>
    <ul id="products"><li>apple</li><li>banana</li><li>pear</li></ul></body></html>"""


@app.get("/second", response_class=HTMLResponse)
def second():
    return "<html><head><title>Second page</title></head><body><h1>Second page</h1></body></html>"
