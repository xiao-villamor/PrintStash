"""Resource reports include native children launched from an executor thread."""

import os
import selectors
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from tests.fakes.process_metrics import sample_processes


class TestSampleProcesses:
    def test_samples_workers_owned_by_an_executor_thread(
        self,
    ):
        with ThreadPoolExecutor(max_workers=1) as executor:
            child = executor.submit(
                subprocess.Popen,
                [
                    sys.executable,
                    "-u",
                    "-c",
                    "import sys; print('ready'); sys.stdin.read()",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).result(timeout=5)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    assert selector.select(timeout=5)
                assert child.stdout.readline() == b"ready\n"
                observations = {}
                sample_processes(observations)
                assert str(os.getpid()) in observations
                assert observations[str(child.pid)]["peak_sampled_rss_bytes"] > 0
                assert observations[str(child.pid)]["cpu_seconds"] >= 0
            finally:
                child.communicate(input=b"", timeout=5)
