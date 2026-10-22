"""Exercise the actual Windows kernel memory limit using small synthetic workers."""
import os
import subprocess
import sys

import pytest

from markus_dispatcher.core.limited_process import run_limited, WindowsMemoryJob, ResourceLimitError

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Job Objects')


def test_limited_process_captures_output_and_peak():
    result = run_limited([sys.executable, '-c', 'import sys; print(sys.stdin.read().upper())'],
                         input=b'synthetic text', timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == b'SYNTHETIC TEXT'
    assert 0 < result.peak_memory_bytes < 2*1024**3


def test_kernel_refuses_allocation_over_memory_limit():
    script = 'try:\n data = bytearray(256*1024**2)\n print("UNEXPECTED_ALLOCATION")\nexcept MemoryError:\n print("MEMORY_LIMIT_CONFIRMED")'
    result = run_limited([sys.executable, '-c', script], memory_bytes=64*1024**2, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == b'MEMORY_LIMIT_CONFIRMED'
    assert 0 < result.peak_memory_bytes <= 64*1024**2


def test_timeout_kills_worker():
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_limited([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=.2)
    assert error.value.cmd == 'local OCR'
    assert error.value.output is None


def test_limit_failure_does_not_run_child(tmp_path, monkeypatch):
    marker = tmp_path/'should-not-run.txt'
    def fail(*args):
        raise ResourceLimitError('synthetic setup failure')
    monkeypatch.setattr(WindowsMemoryJob, 'assign_and_resume', fail)
    script = 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("unexpected")'
    with pytest.raises(ResourceLimitError):
        run_limited([sys.executable, '-c', script, str(marker)], timeout=5)
    assert not marker.exists()
