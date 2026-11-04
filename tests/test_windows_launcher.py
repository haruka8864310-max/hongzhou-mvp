import os
from pathlib import Path
import subprocess

import pytest


def test_windows_batch_has_safe_line_endings():
    content=(Path(__file__).resolve().parents[1]/'start_windows.bat').read_bytes()
    assert content.isascii()
    assert b'\r\n' in content
    assert b'\n' not in content.replace(b'\r\n',b'')
    assert b'run_local.py %*' in content


@pytest.mark.skipif(os.name!='nt',reason='Windows cmd.exe acceptance')
def test_actual_batch_parses_and_runs_python_launcher_help():
    project=Path(__file__).resolve().parents[1]
    result=subprocess.run(['cmd.exe','/d','/c','start_windows.bat --help'],cwd=project,
        capture_output=True,timeout=20)
    output=result.stdout.decode('utf-8',errors='replace')+result.stderr.decode('utf-8',errors='replace')
    assert result.returncode==0,ascii(output)
    assert '--no-browser' in output
    assert '--port' in output
    assert 'not recognized' not in output.lower()
