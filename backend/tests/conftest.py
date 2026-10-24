import socket
import threading
import time

import pytest
import uvicorn
from omnitest.adapters.base import Registry
from omnitest.config import Settings
from omnitest.db import Database
from omnitest.demo_app import app
from omnitest.services.orchestrator import Orchestrator
from omnitest.services.resources import Resources


@pytest.fixture
def stack(tmp_path):
    settings = Settings(data_dir=tmp_path)
    db = Database(settings.database_url)
    db.migrate()
    registry = Registry()
    resources = Resources(db, registry)
    orchestrator = Orchestrator(db, resources, settings, registry)
    yield settings, db, registry, resources, orchestrator
    db.engine.dispose()


@pytest.fixture(scope="session")
def fixture_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("Fixture server failed to start")
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
