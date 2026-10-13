"""Asynchronous process execution with streaming output.

Everything the app shells out to goes through :class:`JobRunner`. The
guarantees it provides are what keep the UI responsive:

* no call ever blocks the event loop -- subprocesses are spawned with
  ``asyncio.create_subprocess_exec`` and drained by dedicated pump coroutines
* output is delivered line by line through a callback, so the UI can batch
  writes instead of repainting per line
* interactive ``[y/N]``-style prompts are answered automatically on stdin,
  so nothing ever waits for a human at the terminal
* mutating commands are serialised behind a lock because ``sdkmanager``
  takes its own repository lock and concurrent runs fail confusingly
* every job is cancellable, including its process group
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

try:  # POSIX only; Windows has no PTY support
    import pty
    import termios
except ImportError:  # pragma: no cover - Windows
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

#: Matches the confirmation prompts the Android tools print, e.g.
#: ``Accept? (y/N):``, ``[no]``, ``[y/N]``, ``[yes/no]``.
PROMPT_RE = re.compile(
    r"\[\s*y\s*/\s*n\s*\]"
    r"|\[\s*n\s*o\s*\]"
    r"|\[\s*yes\s*/\s*no\s*\]"
    r"|\(\s*y\s*/\s*n\s*\)",
    re.IGNORECASE,
)

LineCallback = Callable[[str, str], None]
AnswerCallback = Callable[[], "Awaitable[None]"]

#: Bytes pulled from a stream per read.
_CHUNK = 4096

#: ANSI control sequences (colours, cursor moves) emitted by TTY-aware tools.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from tool output."""
    return _ANSI_RE.sub("", text)


class _LineSplitter:
    """Split a byte stream into lines, treating CR and LF as terminators.

    Bare ``\\r`` counts as a terminator because the SDK tools repaint progress
    bars in place; downstream parsers collapse those redraws.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._after_cr = False

    def feed(self, text: str) -> list[str]:
        self._buffer += text.replace("\r\n", "\n")
        out: list[str] = []
        start = 0
        last_sep: str | None = None
        for index, char in enumerate(self._buffer):
            if char == "\r" or char == "\n":
                out.append(self._buffer[start:index].rstrip())
                start = index + 1
                last_sep = char
        if last_sep is not None:
            self._after_cr = last_sep == "\r"
        self._buffer = self._buffer[start:]
        return out

    @property
    def pending(self) -> str:
        """Text after the last terminator, not yet emitted."""
        return self._buffer

    @property
    def after_cr(self) -> bool:
        """True when ``pending`` is a line being repainted in place."""
        return self._after_cr


async def pump_stream(
    stream: asyncio.StreamReader | None,
    emit: Callable[[str], None],
    answer: AnswerCallback | None = None,
) -> None:
    """Drain ``stream`` line by line without ever blocking on a newline.

    The Android tools print confirmation prompts *without* a trailing newline
    (``Accept? (y/N): ``) and then block on stdin, so a plain ``readline()``
    would never surface them and the job would hang. We therefore read in
    chunks and inspect the trailing partial line for a prompt.
    """
    if stream is None:
        return

    splitter = _LineSplitter()
    last_prompt = ""

    while True:
        chunk = await stream.read(_CHUNK)
        if not chunk:
            break
        for line in splitter.feed(chunk.decode("utf-8", errors="replace")):
            if line:
                emit(line)
            if answer and PROMPT_RE.search(line):
                await answer()
        # A prompt may be sitting in the partial tail with no newline yet.
        if answer and splitter.pending and PROMPT_RE.search(splitter.pending):
            if splitter.pending != last_prompt:
                last_prompt = splitter.pending
                await answer()

    if splitter.pending:
        emit(splitter.pending.rstrip())


async def pump_fd(
    fd: int,
    emit: Callable[[str], None],
    answer: AnswerCallback | None = None,
) -> None:
    """Drain a pseudo-terminal master ``fd`` line by line.

    Tools like the ``android`` CLI block-buffer their output when stdout is a
    pipe and only flush on exit, so a long install shows nothing at all. On a
    PTY they line-buffer and print progress, which is why mutation jobs use
    this pump instead of :func:`pump_stream`.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def readable() -> None:
        try:
            data = os.read(fd, _CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if data:
            queue.put_nowait(data)
        else:
            with contextlib.suppress(Exception):
                loop.remove_reader(fd)
            queue.put_nowait(None)

    loop.add_reader(fd, readable)
    splitter = _LineSplitter()
    last_prompt = ""
    last_current = ""

    try:
        while True:
            data = await queue.get()
            if data is None:
                break
            text = strip_ansi(data.decode("utf-8", errors="replace"))
            for line in splitter.feed(text):
                if line:
                    emit(line)
                    last_current = ""
                if answer and PROMPT_RE.search(line):
                    await answer()
            if answer and splitter.pending and PROMPT_RE.search(splitter.pending):
                if splitter.pending != last_prompt:
                    last_prompt = splitter.pending
                    await answer()
            # The android CLI writes its download URL as "\r<url>..." with no
            # newline, repainting a single line. Emit that live line as it
            # changes so a long install is not silent.
            current = splitter.pending
            if splitter.after_cr and current and current != last_current:
                last_current = current
                emit(current)
        if splitter.pending and splitter.pending != last_current:
            emit(splitter.pending.rstrip())
    finally:
        with contextlib.suppress(Exception):
            loop.remove_reader(fd)


def _quiet_pty(slave_fd: int) -> None:
    """Stop the terminal echoing our auto-answers back into the log."""
    if termios is None:
        return
    try:
        attrs = termios.tcgetattr(slave_fd)
    except termios.error:  # pragma: no cover - defensive
        return
    attrs[3] &= ~termios.ECHO
    with contextlib.suppress(termios.error):  # pragma: no cover - defensive
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass(slots=True)
class JobLine:
    text: str
    stream: str  # "out" | "err" | "info"
    at: float


@dataclass(slots=True)
class Job:
    """A single tracked process invocation."""

    id: int
    label: str
    argv: list[str]
    mutation: bool = False
    status: JobStatus = JobStatus.PENDING
    started_at: float = 0.0
    ended_at: float | None = None
    returncode: int | None = None
    error: str | None = None
    lines: deque[JobLine] = field(default_factory=lambda: deque(maxlen=5000))
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def ok(self) -> bool:
        return self.status is JobStatus.DONE

    @property
    def duration(self) -> float:
        if not self.started_at:
            return 0.0
        return (self.ended_at or time.monotonic()) - self.started_at

    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def stdout_text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.stream == "out")

    def write(self, text: str, stream: str) -> None:
        self.lines.append(JobLine(text=text, stream=stream, at=time.monotonic()))


@dataclass(slots=True)
class CaptureResult:
    """Outcome of a fire-and-read call used for list/query commands."""

    argv: list[str]
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return self.stdout if self.stdout.strip() else self.stderr


class JobRunner:
    """Spawns and supervises SDK subprocesses."""

    def __init__(
        self,
        *,
        max_lines: int = 5000,
        history: int = 100,
        on_start: Callable[[Job], None] | None = None,
        on_line: LineCallback | None = None,
        on_finish: Callable[[Job], None] | None = None,
    ) -> None:
        self._max_lines = max_lines
        self._history: deque[Job] = deque(maxlen=history)
        self._counter = 0
        self._mutation_lock = asyncio.Lock()
        self._on_start = on_start
        self._on_line = on_line
        self._on_finish = on_finish
        self._active: dict[int, Job] = {}

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @property
    def jobs(self) -> list[Job]:
        """Recent jobs, newest last."""
        return list(self._history)

    @property
    def active_jobs(self) -> list[Job]:
        return [j for j in self._active.values() if not j.status.terminal]

    @property
    def busy(self) -> bool:
        return any(j.mutation for j in self.active_jobs)

    # ------------------------------------------------------------------ #
    # Running
    # ------------------------------------------------------------------ #

    async def start(
        self,
        argv: Sequence[str],
        *,
        label: str = "",
        mutation: bool = False,
        answer_text: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_line: LineCallback | None = None,
        timeout: float | None = None,
        use_pty: bool = False,
    ) -> Job:
        """Spawn ``argv`` in the background and return immediately.

        The returned job is already tracked; use ``await runner.wait(job)`` to
        await completion. ``use_pty`` runs the child on a pseudo-terminal so
        tools that block-buffer when piped (the ``android`` CLI) emit output
        live instead of all at once on exit.
        """
        argv = [str(a) for a in argv]
        self._counter += 1
        job = Job(
            id=self._counter,
            label=label or os.path.basename(argv[0]),
            argv=argv,
            mutation=mutation,
        )
        job.lines = deque(maxlen=self._max_lines)
        self._history.append(job)
        self._active[job.id] = job

        if self._on_start:
            self._on_start(job)

        job.status = JobStatus.RUNNING
        job.started_at = time.monotonic()
        command_line = "$ " + " ".join(argv)
        job.write(command_line, "info")
        emit = on_line or self._on_line
        if emit:
            emit(command_line, "info")

        coro = self._supervise(
            job,
            answer_text=answer_text,
            cwd=cwd,
            env=env,
            on_line=on_line,
            timeout=timeout,
            use_pty=use_pty,
        )
        # Fire and forget; completion is signalled through job._done.
        asyncio.create_task(coro, name=f"job-{job.id}")
        return job

    async def _supervise(
        self,
        job: Job,
        *,
        answer_text: str | None,
        cwd: str | None,
        env: dict[str, str] | None,
        on_line: LineCallback | None,
        timeout: float | None,
        use_pty: bool = False,
    ) -> None:
        acquired = False
        use_pty = use_pty and pty is not None
        try:
            if job.mutation:
                # sdkmanager serialises internally; running two at once just
                # produces confusing failures, so queue them.
                await self._mutation_lock.acquire()
                acquired = True
                job.write("(queued behind another SDK operation)", "info")

            process_env = dict(os.environ)
            if env:
                process_env.update(env)

            master_fd = -1
            slave_fd = -1
            if use_pty:
                master_fd, slave_fd = pty.openpty()
                _quiet_pty(slave_fd)
                stdio = {"stdin": slave_fd, "stdout": slave_fd, "stderr": slave_fd}
            else:
                stdio = {
                    "stdin": asyncio.subprocess.PIPE,
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.PIPE,
                }

            try:
                process = await asyncio.create_subprocess_exec(
                    *job.argv,
                    cwd=cwd,
                    env=process_env,
                    start_new_session=True,
                    **stdio,
                )
            except (OSError, ValueError) as exc:
                if use_pty:
                    os.close(master_fd)
                    os.close(slave_fd)
                job.error = str(exc)
                job.status = JobStatus.FAILED
                job.write(f"failed to start: {exc}", "err")
                return
            if use_pty:
                os.close(slave_fd)
            job.process = process

            emit = on_line or self._on_line

            async def answer() -> None:
                payload = answer_text or "y\n"
                if use_pty:
                    await self._answer_fd(master_fd, payload)
                else:
                    await self._answer_prompt(job, payload)

            def sink(kind: str) -> Callable[[str], None]:
                def _sink(line: str) -> None:
                    job.write(line, kind)
                    if emit:
                        emit(line, kind)

                return _sink

            def answer_cb() -> Awaitable[None] | None:
                return answer if answer_text is not None else None

            if use_pty:
                pumps = [
                    asyncio.create_task(
                        pump_fd(master_fd, sink("out"), answer_cb()),
                        name=f"job-{job.id}-pty",
                    )
                ]
            else:
                pumps = [
                    asyncio.create_task(
                        pump_stream(process.stdout, sink("out"), answer_cb()),
                        name=f"job-{job.id}-stdout",
                    ),
                    asyncio.create_task(
                        pump_stream(process.stderr, sink("err"), answer_cb()),
                        name=f"job-{job.id}-stderr",
                    ),
                ]

            try:
                job.returncode = await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                job.error = f"timed out after {timeout:.0f}s"
                await self._terminate(process)
                job.status = JobStatus.FAILED

            await asyncio.gather(*pumps, return_exceptions=True)
            if use_pty:
                with contextlib.suppress(OSError):
                    os.close(master_fd)

            if job.status is JobStatus.RUNNING:
                if job.returncode == 0:
                    job.status = JobStatus.DONE
                else:
                    job.status = JobStatus.FAILED
        except asyncio.CancelledError:
            await self._terminate(job.process)
            job.status = JobStatus.CANCELLED
            raise
        except Exception as exc:  # pragma: no cover - defensive
            job.error = str(exc)
            job.status = JobStatus.FAILED
            job.write(f"runner error: {exc}", "err")
        finally:
            if acquired:
                self._mutation_lock.release()
            job.ended_at = time.monotonic()
            self._active.pop(job.id, None)
            job._done.set()
            if self._on_finish:
                self._on_finish(job)

    async def _answer_prompt(self, job: Job, answer_text: str) -> None:
        process = job.process
        if process is None or process.stdin is None or process.stdin.is_closing():
            return
        payload = answer_text if answer_text.endswith("\n") else answer_text + "\n"
        with contextlib.suppress(ProcessLookupError, ConnectionResetError, BrokenPipeError):
            process.stdin.write(payload.encode())
            await process.stdin.drain()

    @staticmethod
    async def _answer_fd(fd: int, answer_text: str) -> None:
        """Write an auto-answer to a PTY master fd."""
        payload = answer_text if answer_text.endswith("\n") else answer_text + "\n"
        with contextlib.suppress(OSError):
            os.write(fd, payload.encode())

    async def wait(self, job: Job, timeout: float | None = None) -> Job:
        """Block until ``job`` finishes."""
        if timeout is None:
            await job._done.wait()
        else:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(job._done.wait(), timeout)
        return job

    async def cancel(self, job: Job) -> None:
        """Terminate a running job and its process group."""
        if job.status.terminal:
            return
        job.write("cancelling…", "info")
        await self._terminate(job.process)
        job.status = JobStatus.CANCELLED

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process | None) -> None:
        if process is None or process.returncode is not None:
            return
        # Kill the whole group: the Android tools spawn helper processes.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(process.pid), sig)
            with contextlib.suppress(ProcessLookupError, ProcessLookupError):
                process.terminate() if sig == signal.SIGTERM else process.kill()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=3)
            if process.returncode is not None:
                return

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    async def capture(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = 120,
        answer_text: str | None = None,
        on_line: LineCallback | None = None,
    ) -> CaptureResult:
        """Run a command to completion and return its output.

        Used for fast read-only queries (``list avd``, ``--list``) where the
        caller wants a parsed result rather than a live job.
        """
        argv = [str(a) for a in argv]
        process_env = dict(os.environ)
        if env:
            process_env.update(env)

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=process_env,
            )
        except (OSError, ValueError) as exc:
            return CaptureResult(argv=argv, stdout="", stderr=str(exc), returncode=127)

        async def answer() -> None:
            if process.stdin is not None and not process.stdin.is_closing():
                payload = answer_text if answer_text.endswith("\n") else answer_text + "\n"
                with contextlib.suppress(Exception):
                    process.stdin.write(payload.encode())
                    await process.stdin.drain()

        collected: dict[str, list[str]] = {"out": [], "err": []}

        def sink(kind: str) -> Callable[[str], None]:
            def _sink(line: str) -> None:
                collected[kind].append(line)
                if on_line:
                    on_line(line, kind)

            return _sink

        answer_cb = answer if answer_text is not None else None

        async def drain(reader: asyncio.StreamReader | None, kind: str) -> None:
            await pump_stream(reader, sink(kind), answer_cb)

        try:
            await asyncio.wait_for(
                asyncio.gather(drain(process.stdout, "out"), drain(process.stderr, "err")),
                timeout=timeout,
            )
            returncode = await process.wait()
            timed_out = False
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()
            await process.wait()
            returncode = -1
            timed_out = True

        return CaptureResult(
            argv=argv,
            stdout="\n".join(collected["out"]),
            stderr="\n".join(collected["err"]),
            returncode=returncode,
            timed_out=timed_out,
        )

    async def cancel_all(self) -> None:
        for job in list(self.active_jobs):
            await self.cancel(job)

    async def shutdown(self) -> None:
        await self.cancel_all()


def quote_display(argv: Iterable[str]) -> str:
    """Render an argv for the log pane."""
    parts = []
    for arg in argv:
        parts.append(f'"{arg}"' if " " in arg else arg)
    return " ".join(parts)