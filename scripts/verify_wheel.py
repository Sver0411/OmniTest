"""Install a built wheel in a clean environment and verify it outside the repository."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SMOKE = r"""
import json,sys,socket,threading,time
from pathlib import Path
import omnitest
import uvicorn
from fastapi.testclient import TestClient
from omnitest.api.app import create_app
from omnitest.config import Settings
from omnitest.demo_app import app as fixture
from omnitest.sdk import ADAPTER_API_VERSION, validate_adapter
from omnitest_json_document import JsonDocumentAdapter

assert not Path(omnitest.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert ADAPTER_API_VERSION == 1
validate_adapter(JsonDocumentAdapter)
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(fixture,log_level='error'))
thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
deadline=time.monotonic()+10
while not server.started:
    if time.monotonic()>deadline: raise RuntimeError('Fixture startup failed')
    time.sleep(.02)
try:
    with TestClient(create_app(Settings(data_dir=Path('installed-data')))) as client:
        assert client.get('/api/ready').status_code == 200
        assert client.get('/api/health').json()['version'] == omnitest.__version__
        html=client.get('/').text
        assert 'OmniTest' in html and '/assets/' in html
        import re
        asset=re.search(r'src="([^"]+\.js)"',html)[1]
        assert client.get(asset).status_code == 200
        project=client.post('/api/resources/projects',json={'body':{'name':'Installed wheel'}}).json()['id']
        target=client.post('/api/resources/targets',json={'project_id':project,'body':{'name':'HTTP fixture','kind':'http','authorized':True,'config':{'base_url':f'http://127.0.0.1:{port}'}}}).json()['id']
        case=client.post('/api/resources/cases',json={'project_id':project,'body':{'name':'Health','adapter':'api','target':target,'steps':[{'action':'request','config':{'path':'/health'},'assertions':[{'path':'$.status_code','op':'eq','expected':200}]}]}}).json()['id']
        run=client.post('/api/runs',json={'project_id':project,'case_ids':[case]}).json()['id']
        for _ in range(200):
            report=client.get('/api/runs/'+run).json()
            if report['status'] not in {'PENDING','RUNNING'}:break
            time.sleep(.03)
        assert report['status']=='PASSED',report
        assert client.get('/api/audit').json()
        assert 'json-document' in {row['id'] for row in client.get('/api/adapters').json()['items']}
    print(json.dumps({'version':omnitest.__version__,'package':str(Path(omnitest.__file__).resolve()),'ui':'PASSED','migrations':'PASSED','execution':'1 PASSED','sdk_plugin':'PASSED'}))
finally:
    server.should_exit=True;thread.join(10);sock.close()
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel")
    parser.add_argument("--output", default="data/phase5/wheel-summary.json")
    args = parser.parse_args()
    wheel = Path(args.wheel).resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if not any(name.startswith("omnitest/static/assets/") for name in names):
            raise RuntimeError("Wheel does not contain the production UI")
        if any(name.startswith("tests/") for name in names):
            raise RuntimeError("Development tests must not be distributed in the platform wheel")
    with tempfile.TemporaryDirectory(prefix="omnitest-installed-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        constraints = work / "constraints.txt"
        constraints.write_text(
            "\n".join(
                line
                for line in (ROOT / "requirements-dev.lock").read_text().splitlines()
                if re.fullmatch(r"[a-zA-Z0-9_.-]+==[^\s]+", line)
            ),
            encoding="utf-8",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("OMNITEST_") and key != "PYTHONPATH"
        }
        env["PYTHONUTF8"] = "1"

        def run(*argv, cwd=work):
            result = subprocess.run(
                [str(python), *map(str, argv)],
                cwd=cwd,
                env=env,
                capture_output=True,
                encoding="utf-8",
                timeout=300,
            )
            if result.returncode:
                raise RuntimeError(result.stdout[-5000:] + result.stderr[-5000:])
            return result.stdout

        run(
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-c",
            constraints,
            str(wheel) + "[grpc]",
        )
        run(
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            ROOT / "examples/plugins/json_document",
        )
        smoke = work / "installed_smoke.py"
        smoke.write_text(SMOKE, encoding="utf-8")
        result = run("-I", smoke, ROOT)
        summary = json.loads(result.strip().splitlines()[-1])
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
