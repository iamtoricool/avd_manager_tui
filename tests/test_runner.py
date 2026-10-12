"""Tests for the async job runner.

These use real subprocesses (``/bin/sh`` scripts) rather than mocks so the
streaming, prompting and cancellation behaviour is genuinely exercised.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from avdmanager import runner as r

# `asyncio_mode = "auto"` in pyproject means every async test is collected as a
# coroutine, so no explicit marker is needed here.


# --------------------------------------------------------------------------- #
# Prompt detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line", "matches"),
    [
        ("Accept? (y/N):", True),
        ("Do you wish to create a custom hardware profile? [no] [yes]:", True),
        ("[y/N]", True),
        ("[Y/n]", True),
        ("[yes/no]", True),
        ("Do you want to continue? (y/N)", True),
        ("Loading... 50%", False),
        ("[Y] Failed to install", False),
        ("Warning: something happened", False),
    ],
)
def test_prompt_regex(line, matches):
    assert bool(r.PROMPT_RE.search(line)) is matches


# --------------------------------------------------------------------------- #
# capture()
# --------------------------------------------------------------------------- #


async def test_capture_reads_stdout():
    result = await r.JobRunner().capture([sys.executable, "-c", "print('hello')"])
    assert result.ok
    assert result.stdout == "hello"


async def test_capture_reports_failure():
    result = await r.JobRunner().capture([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert not result.ok
    assert result.returncode == 3


async def test_capture_merges_stderr_into_output():
    result = await r.JobRunner().capture(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom')"]
    )
    assert "boom" in result.output


async def test_capture_times_out():
    result = await r.JobRunner().capture(
        [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.5
    )
    assert result.timed_out
    assert not result.ok


async def test_capture_handles_missing_binary():
    result = await r.JobRunner().capture(["definitely-not-a-real-binary-xyz"])
    assert result.returncode == 127
    assert not result.ok


async def test_capture_streams_lines_to_callback():
    seen: list[tuple[str, str]] = []
    script = "import sys\nfor i in range(3): print(f'line {i}')\n"
    await r.JobRunner().capture(
        [sys.executable, "-c", script], on_line=lambda t, k: seen.append((t, k))
    )
    assert [t for t, _ in seen] == ["line 0", "line 1", "line 2"]
    assert {k for _, k in seen} == {"out"}


async def test_capture_auto_confirms_prompts():
    """A tool that blocks forever unless stdin is fed must not hang us."""
    script = (
        "import sys\n"
        "sys.stdout.write('Accept? (y/N): ')\n"
        "sys.stdout.flush()\n"
        "answer = sys.stdin.readline().strip()\n"
        "sys.stdout.write(f'got:{answer}')\n"
    )
    runner = r.JobRunner()
    result = await runner.capture(
        [sys.executable, "-c", script], answer_text="y", timeout=10
    )
    assert result.ok
    assert "got:y" in result.stdout


# --------------------------------------------------------------------------- #
# start() / wait() / streaming
# --------------------------------------------------------------------------- #


async def test_start_returns_immediately_and_completes():
    runner = r.JobRunner()
    job = await runner.start(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('done')"], label="slow"
    )
    assert job.status is r.JobStatus.RUNNING  # returns before completion

    await runner.wait(job)
    assert job.status is r.JobStatus.DONE
    assert "done" in job.stdout_text()
    assert job.duration >= 0.2


async def test_job_records_command_line():
    runner = r.JobRunner()
    job = await runner.start([sys.executable, "-c", "pass"])
    await runner.wait(job)
    assert any(line.text.startswith("$ ") for line in job.lines)


async def test_job_failure_is_reported():
    runner = r.JobRunner()
    job = await runner.start([sys.executable, "-c", "import sys; sys.exit(2)"])
    await runner.wait(job)
    assert job.status is r.JobStatus.FAILED
    assert job.returncode == 2
    assert not job.ok


async def test_missing_binary_becomes_failed_job():
    runner = r.JobRunner()
    job = await runner.start(["not-a-real-binary-abc"])
    await runner.wait(job)
    assert job.status is r.JobStatus.FAILED
    assert job.error


async def test_line_callback_fires_during_execution():
    lines: list[str] = []
    runner = r.JobRunner(on_line=lambda text, kind: lines.append(text))
    job = await runner.start([sys.executable, "-c", "print('streamed')"])
    await runner.wait(job)
    assert "streamed" in lines


async def test_stderr_is_tagged():
    runner = r.JobRunner()
    job = await runner.start(
        [sys.executable, "-c", "import sys; sys.stderr.write('bad'); print('good')"]
    )
    await runner.wait(job)
    assert any(line.stream == "err" and line.text == "bad" for line in job.lines)
    assert any(line.stream == "out" and line.text == "good" for line in job.lines)


async def test_line_buffer_is_bounded():
    runner = r.JobRunner(max_lines=50)
    job = await runner.start([sys.executable, "-c", "for i in range(500): print(i)"])
    await runner.wait(job)
    assert len(job.lines) <= 50


async def test_job_auto_confirms_prompt():
    runner = r.JobRunner()
    script = (
        "import sys\n"
        "for i in range(3):\n"
        "    sys.stdout.write(f'[y/N] q{i} '); sys.stdout.flush()\n"
        "    sys.stdin.readline()\n"
        "print('all answered')\n"
    )
    job = await runner.start([sys.executable, "-c", script], answer_text="y")
    await runner.wait(job, timeout=10)
    assert job.ok
    assert "all answered" in job.stdout_text()


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


async def test_cancel_stops_a_running_job():
    runner = r.JobRunner()
    job = await runner.start([sys.executable, "-c", "import time; time.sleep(30)"])
    await asyncio.sleep(0.15)

    await runner.cancel(job)
    await asyncio.wait_for(job._done.wait(), timeout=10)

    assert job.status is r.JobStatus.CANCELLED
    assert job.status.terminal


async def test_cancel_kills_child_processes():
    """The tools spawn helpers; killing only the parent would leak them."""
    runner = r.JobRunner()
    job = await runner.start(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "time.sleep(60)\n",
        ]
    )
    await asyncio.sleep(0.3)
    await runner.cancel(job)
    await asyncio.wait_for(job._done.wait(), timeout=10)
    assert job.status is r.JobStatus.CANCELLED
    assert job.process is not None
    assert job.process.returncode is not None


async def test_cancel_is_a_noop_for_finished_jobs():
    runner = r.JobRunner()
    job = await runner.start([sys.executable, "-c", "pass"])
    await runner.wait(job)
    status = job.status
    await runner.cancel(job)
    assert job.status is status


# --------------------------------------------------------------------------- #
# Serialisation and bookkeeping
# --------------------------------------------------------------------------- #


async def test_mutations_are_serialised():
    """sdkmanager takes a repository lock; overlapping runs must not happen."""
    runner = r.JobRunner()
    order: list[str] = []

    script = (
        "import sys, time\n"
        "print('start', sys.argv[1])\n"
        "time.sleep(0.3)\n"
        "print('end', sys.argv[1])\n"
    )

    def watch(text: str, kind: str) -> None:
        if text.startswith("start "):
            order.append(f"start:{text.split()[-1]}")

    job_a = await runner.start(
        [sys.executable, "-c", script, "a"], mutation=True, on_line=watch
    )
    job_b = await runner.start(
        [sys.executable, "-c", script, "b"], mutation=True, on_line=watch
    )
    assert runner.busy

    await asyncio.gather(runner.wait(job_a), runner.wait(job_b))

    assert job_a.ok and job_b.ok
    # a finished before b started
    assert order == ["start:a", "start:b"]
    assert not runner.busy


async def test_reads_are_not_blocked_by_a_running_mutation():
    runner = r.JobRunner()
    slow = await runner.start([sys.executable, "-c", "import time; time.sleep(0.5)"], mutation=True)
    fast = await runner.start([sys.executable, "-c", "print('fast')"])
    await asyncio.wait_for(runner.wait(fast), timeout=0.4)
    assert fast.ok
    await runner.wait(slow)


async def test_history_and_active_tracking():
    runner = r.JobRunner(history=5)
    for _ in range(7):
        job = await runner.start([sys.executable, "-c", "pass"])
        await runner.wait(job)

    assert len(runner.jobs) == 5
    assert runner.active_jobs == []


async def test_active_jobs_lists_running_only():
    runner = r.JobRunner()
    running = await runner.start([sys.executable, "-c", "import time; time.sleep(0.4)"])
    done = await runner.start([sys.executable, "-c", "pass"])
    await runner.wait(done)

    assert running in runner.active_jobs
    assert done not in runner.active_jobs


async def test_shutdown_cancels_everything():
    runner = r.JobRunner()
    job = await runner.start([sys.executable, "-c", "import time; time.sleep(30)"])
    await asyncio.sleep(0.2)
    await runner.shutdown()
    await asyncio.wait_for(job._done.wait(), timeout=10)
    assert job.status is r.JobStatus.CANCELLED


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #


def test_quote_display():
    assert r.quote_display(["sdkmanager", "--list"]) == "sdkmanager --list"
    assert r.quote_display(["avdmanager", "create", "-n", "My AVD"]) == (
        'avdmanager create -n "My AVD"'
    )


def test_job_status_terminal_flag():
    assert r.JobStatus.DONE.terminal
    assert r.JobStatus.FAILED.terminal
    assert r.JobStatus.CANCELLED.terminal
    assert not r.JobStatus.RUNNING.terminal


def test_capture_result_output_prefers_stdout():
    result = r.CaptureResult(argv=["x"], stdout="out", stderr="err", returncode=0)
    assert result.output == "out"
    empty = r.CaptureResult(argv=["x"], stdout="", stderr="err", returncode=0)
    assert empty.output == "err"