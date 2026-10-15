#!/usr/bin/env python3
"""Verify a UI-only image locally; no remote access or production containers.

Test containers have no external network, credentials or persistent data mounts.
"""
import argparse
import json
from pathlib import Path
import subprocess


ISOLATION = [
    '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges', '--memory', '512m', '--cpus', '1',
    '--pids-limit', '128',
]

FILE_HASHES = '''from pathlib import Path
import hashlib,json
out={}
for root in ['/app/app','/maps','/app/static']:
    for p in sorted(Path(root).rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts:
            out[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps(out))
'''

HTTP_SMOKE = '''import hashlib,json,signal,subprocess,time,urllib.request
p=subprocess.Popen(['python','-B','-m','uvicorn','app.main:app',
    '--host','127.0.0.1','--port','8000'],stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,text=True)
result={}
try:
    for _ in range(200):
        if p.poll() is not None: raise RuntimeError('isolated application exited early')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=1) as r:
                health=json.load(r)
            break
        except OSError: time.sleep(.05)
    else: raise RuntimeError('isolated HTTP startup timed out')
    assert health['robot_connected'] is False, 'a test must never have a robot'
    for e in EXPECTED:
        path='/' if e['path']=='index.html' else '/'+e['path']
        with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=5) as r:
            assert r.status==200
            assert hashlib.sha256(r.read()).hexdigest()==e['sha256'], path
    result={'http_assets':len(EXPECTED),'robot_connected':False}
finally:
    if p.poll() is None: p.send_signal(signal.SIGTERM)
    try: logs=p.communicate(timeout=10)[0]
    except subprocess.TimeoutExpired:
        p.kill();logs=p.communicate()[0]
        raise RuntimeError('isolated application shutdown timed out')
    # Uvicorn re-raises a captured signal after graceful cleanup. A -SIGTERM
    # return is expected only when the complete application shutdown is logged.
    assert p.returncode in (0,-signal.SIGTERM), logs[-2000:]
    assert 'Application shutdown complete.' in logs, logs[-2000:]
    assert 'Finished server process' in logs, logs[-2000:]
    result.update(application_shutdown_complete=True,signal_exit=p.returncode)
print(json.dumps(result))
'''


def inspect(image):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]


def hashes(image):
    command = ['docker', 'run', *ISOLATION, '--entrypoint', 'python', image, '-B', '-c', FILE_HASHES]
    return json.loads(subprocess.check_output(command, timeout=60))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    base = inspect(manifest['base_image'])
    candidate = inspect(manifest['image_id'])
    assert base['Id'] == manifest['base_image']
    assert candidate['Id'] == manifest['image_id']
    assert candidate['RootFS']['Layers'][:len(base['RootFS']['Layers'])] == base['RootFS']['Layers']
    for key in ('Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir'):
        assert base['Config'].get(key) == candidate['Config'].get(key), key
    labels = candidate['Config']['Labels']
    assert labels['org.opencontainers.image.revision'] == manifest['source_revision']
    assert labels['io.patrolbot.ui-only'] == 'true'
    assert labels['io.patrolbot.frontend-tree-sha256'] == manifest['static_tree_sha256']

    old = hashes(base['Id'])
    new = hashes(candidate['Id'])
    backend = {p: h for p, h in old.items() if not p.startswith('/app/static/')}
    assert backend == {p: h for p, h in new.items() if not p.startswith('/app/static/')}
    expected = {'/app/static/' + entry['path']: entry['sha256'] for entry in manifest['files']}
    assert expected == {p: h for p, h in new.items() if p.startswith('/app/static/')}
    program = 'EXPECTED=' + repr(manifest['files']) + '\n' + HTTP_SMOKE
    command = [
        'docker', 'run', '-i', *ISOLATION,
        '--tmpfs', '/tmp:rw,nosuid,size=64m', '--tmpfs', '/data:rw,nosuid,size=64m',
        '--env', 'PATROLBOT_ENVIRONMENT=development', '--env', 'PATROLBOT_AUTH_MODE=local',
        '--env', 'PATROLBOT_DATABASE_PATH=/data/ui-smoke.sqlite3',
        '--entrypoint', 'python', candidate['Id'], '-B', '-',
    ]
    result = subprocess.run(command, input=program, text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    smoke = json.loads(result.stdout)
    print(json.dumps(dict(passed=True, image=candidate['Id'],
                         unchanged_backend_map_files=len(backend), static_files=len(expected),
                         smoke=smoke, deployed=False), indent=2))


if __name__ == '__main__':
    main()
