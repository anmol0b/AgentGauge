"""Docker implementation of SandboxExecutor.

This is the ONLY file in the codebase allowed to import `docker`.
"""

import asyncio
import io
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import docker.errors
from docker.client import DockerClient, from_env

from agentgauge.sandbox.image import LOCAL_IMAGE
from agentgauge.sandbox.policy import SandboxPolicy
from agentgauge.sandbox.protocol import (
    ExecResult,
    OutputChunk,
    OutputSink,
    RepoSource,
    SandboxHandle,
)

# Exit codes GNU coreutils `timeout` uses when it had to stop the command.
_TIMEOUT_EXIT_CODES = {124, 137}
# Extra slack on the host-side deadline, so the in-container timeout is what
# normally fires and the host wait is only a backstop.
_HOST_TIMEOUT_GRACE_S = 15


class ImageUnavailable(RuntimeError):
    """Docker is unreachable, or the sandbox image is absent and cannot be pulled."""


def ensure_image(image: str) -> None:
    """Make sure `image` is present, pulling it once if not.

    Done once before a benchmark starts, so a missing image is one clear error
    rather than every run failing on its own with a registry message.
    """
    try:
        client = from_env()
        client.ping()
    except docker.errors.DockerException as err:
        raise ImageUnavailable(
            f"cannot reach the Docker daemon. Is Docker running? ({err})"
        ) from err
    try:
        client.images.get(image)
        return
    except docker.errors.ImageNotFound:
        pass
    print(f"pulling sandbox image {image} (once; later runs reuse it)")
    try:
        client.images.pull(image)
    except docker.errors.DockerException as err:
        reason = getattr(err, "explanation", None) or err
        raise ImageUnavailable(
            f"cannot pull the sandbox image {image}: {reason}\n"
            "Every task runs inside it. To build it here instead, from the Dockerfile "
            f"in this package:\n  agentgauge image build\nthen run with --image {LOCAL_IMAGE}"
        ) from err


class DockerSandboxExecutor:
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()
        # Fail fast if the daemon is unreachable, but don't reuse this client
        # across threads: docker-py wraps a requests.Session, which is not
        # documented as thread-safe, and execs run on a thread pool.
        from_env().ping()
        self._thread_local = threading.local()
        self._has_timeout: dict[str, bool] = {}
        self._probe_lock = threading.Lock()

    @property
    def _client(self) -> DockerClient:
        """One client per worker thread."""
        client: DockerClient | None = getattr(self._thread_local, "client", None)
        if client is None:
            client = from_env()
            self._thread_local.client = client
        return client

    # --- lifecycle --------------------------------------------------------

    async def start(
        self,
        repo: RepoSource,
        image: str,
        policy: SandboxPolicy | None = None,
    ) -> SandboxHandle:
        return await asyncio.to_thread(self._start_sync, repo, image, policy or self._policy)

    def _start_sync(self, repo: RepoSource, image: str, policy: SandboxPolicy) -> SandboxHandle:
        workdir = "/repo"
        if not self._image_present(image):
            self._client.images.pull(image)
        container = self._client.containers.create(
            image,
            command="sleep infinity",
            working_dir=workdir,
            mem_limit=policy.mem_limit,
            nano_cpus=policy.nano_cpus,
            pids_limit=policy.pids_limit,
            network_mode=policy.network_mode,
            detach=True,
        )
        container.start()
        container.exec_run(["mkdir", "-p", workdir])
        container.put_archive(workdir, _tar_directory(Path(repo.path)))
        # repo files keep the host uid after untar; container git runs as root
        # and would refuse the repo as "dubious ownership" without this
        container.exec_run(["git", "config", "--global", "--add", "safe.directory", workdir])
        return SandboxHandle(id=container.id, workdir=workdir)

    def _image_present(self, image: str) -> bool:
        # Docker Desktop occasionally 404s a present image under host load;
        # retry once before concluding the image is missing.
        for attempt in range(2):
            try:
                self._client.images.get(image)
                return True
            except docker.errors.ImageNotFound:
                if attempt == 1:
                    return False
                time.sleep(1)
        return False

    async def teardown(self, handle: SandboxHandle) -> None:
        def _remove() -> None:
            try:
                self._client.containers.get(handle.id).remove(force=True)
            except docker.errors.NotFound:
                pass  # already gone: teardown is idempotent
            finally:
                self._has_timeout.pop(handle.id, None)

        await asyncio.to_thread(_remove)

    # --- exec -------------------------------------------------------------

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout_s: int,
        on_output: OutputSink | None = None,
    ) -> ExecResult:
        sink: OutputSink | None = None
        if on_output is not None:
            # Output arrives on a worker thread; hop back to the event loop so
            # the caller's sink runs where the caller expects it to.
            loop = asyncio.get_running_loop()

            def sink(chunk: OutputChunk) -> None:
                loop.call_soon_threadsafe(on_output, chunk)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._exec_sync, handle, command, timeout_s, sink),
                timeout=timeout_s + _HOST_TIMEOUT_GRACE_S,
            )
        except TimeoutError:
            # The in-container timeout should have fired first. Reaching here
            # means it could not (no coreutils `timeout` in the image, or a
            # wedged daemon); the container is force-removed at teardown,
            # which is what finally kills the process.
            return ExecResult(
                exit_code=-1,
                stdout="",
                stderr=f"timed out after {timeout_s}s (not killed in container)",
                timed_out=True,
            )

    def _exec_sync(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout_s: int,
        sink: OutputSink | None,
    ) -> ExecResult:
        client = self._client
        container = client.containers.get(handle.id)
        wrapped = self._with_timeout(container, handle.id, command, timeout_s)

        # The low-level API is used rather than exec_run because streaming and
        # the exit code cannot be had together from the high-level helper: with
        # stream=True it reports None for the code, so the exec is inspected
        # afterwards by id.
        exec_id = client.api.exec_create(handle.id, wrapped, workdir=handle.workdir)["Id"]
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        for out, err in client.api.exec_start(exec_id, stream=True, demux=True):
            for raw, kind, parts in (
                (out, "stdout", stdout_parts),
                (err, "stderr", stderr_parts),
            ):
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="replace")
                if sink is not None:
                    sink(OutputChunk(kind=kind, data=text))  # type: ignore[arg-type]
                else:
                    parts.append(text)  # only buffer when nobody is consuming

        exit_code = client.api.exec_inspect(exec_id).get("ExitCode")
        timed_out = exit_code in _TIMEOUT_EXIT_CODES and wrapped is not command
        return ExecResult(
            exit_code=-1 if exit_code is None else exit_code,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts) + (f"\ntimed out after {timeout_s}s" if timed_out else ""),
            timed_out=timed_out,
        )

    def _with_timeout(
        self, container: Any, container_id: str, command: list[str], timeout_s: int
    ) -> list[str]:
        """Run under coreutils `timeout` so the deadline actually kills it.

        Docker's Engine API cannot kill a running exec (moby#35703), so without
        this a host-side timeout returns while the command keeps running and
        holds the container's single CPU for every later exec.
        """
        if not self._timeout_available(container, container_id):
            return command
        return ["timeout", "--signal=KILL", str(timeout_s), *command]

    def _timeout_available(self, container: Any, container_id: str) -> bool:
        """Probe once per container — not every image ships coreutils."""
        with self._probe_lock:
            if container_id not in self._has_timeout:
                code, _ = container.exec_run(["sh", "-c", "command -v timeout"])
                self._has_timeout[container_id] = code == 0
            return self._has_timeout[container_id]

    # --- files ------------------------------------------------------------

    async def read_bytes(self, handle: SandboxHandle, path: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes_sync, handle, path)

    def _read_bytes_sync(self, handle: SandboxHandle, path: str) -> bytes:
        container = self._client.containers.get(handle.id)
        try:
            stream, _stat = container.get_archive(_absolute(handle, path))
        except docker.errors.NotFound as exc:
            raise FileNotFoundError(path) from exc

        # get_archive returns a tar of the requested path, which is the only
        # binary-safe way to read a file out: `cat` would force a decode.
        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
            member = tar.next()
            if member is None or not member.isfile():
                raise FileNotFoundError(path)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise FileNotFoundError(path)
            return extracted.read()

    async def write_bytes(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        await asyncio.to_thread(self._write_bytes_sync, handle, path, content)

    def _write_bytes_sync(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        container = self._client.containers.get(handle.id)
        target = Path(_absolute(handle, path))
        container.exec_run(["mkdir", "-p", str(target.parent)])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=target.name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        container.put_archive(str(target.parent), buf.getvalue())

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return (await self.read_bytes(handle, path)).decode("utf-8", errors="replace")

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await self.write_bytes(handle, path, content.encode("utf-8"))


def _absolute(handle: SandboxHandle, path: str) -> str:
    target = Path(path)
    return str(target if target.is_absolute() else Path(handle.workdir) / target)


def _tar_directory(path: Path) -> bytes:
    """Tar the *contents* of `path` so they extract directly into the workdir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for child in sorted(path.iterdir()):
            tar.add(child, arcname=child.name)
    return buf.getvalue()
