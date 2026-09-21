"""Docker implementation of SandboxExecutor.

This is the ONLY file in the codebase allowed to import `docker`.
"""

import asyncio
import io
import tarfile
import threading
import time
from pathlib import Path

import docker.errors

import docker
from agentbench.sandbox.executor import ExecResult, SandboxHandle
from agentbench.sandbox.policy import SandboxPolicy

# Exit codes GNU coreutils `timeout` uses when it had to stop the command.
_TIMEOUT_EXIT_CODES = {124, 137}
# Extra slack on the host-side deadline, so the in-container timeout is what
# normally fires and the host wait is only a backstop.
_HOST_TIMEOUT_GRACE_S = 15


class DockerSandboxExecutor:
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()
        # Fail fast if the daemon is unreachable, but don't reuse this client
        # across threads: docker-py wraps a requests.Session, which is not
        # documented as thread-safe, and runs execute on a thread pool.
        docker.from_env().ping()
        self._thread_local = threading.local()
        self._has_timeout: dict[str, bool] = {}
        self._probe_lock = threading.Lock()

    @property
    def _client(self) -> docker.DockerClient:
        """One client per worker thread."""
        client = getattr(self._thread_local, "client", None)
        if client is None:
            client = docker.from_env()
            self._thread_local.client = client
        return client

    async def start(self, repo_path: Path, image: str) -> SandboxHandle:
        return await asyncio.to_thread(self._start_sync, repo_path, image)

    def _start_sync(self, repo_path: Path, image: str) -> SandboxHandle:
        workdir = "/repo"
        if not self._image_present(image):
            self._client.images.pull(image)
        container = self._client.containers.create(
            image,
            command="sleep infinity",
            working_dir=workdir,
            mem_limit=self._policy.mem_limit,
            nano_cpus=self._policy.nano_cpus,
            pids_limit=self._policy.pids_limit,
            network_mode=self._policy.network_mode,
            detach=True,
        )
        container.start()
        container.exec_run(["mkdir", "-p", workdir])
        container.put_archive(workdir, _tar_directory(repo_path))
        # repo files keep the host uid after untar; container git runs as root
        # and would refuse the repo as "dubious ownership" without this
        container.exec_run(
            ["git", "config", "--global", "--add", "safe.directory", workdir]
        )
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

    async def exec(
        self, handle: SandboxHandle, command: list[str], timeout_s: int
    ) -> ExecResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._exec_sync, handle, command, timeout_s),
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
        self, handle: SandboxHandle, command: list[str], timeout_s: int
    ) -> ExecResult:
        container = self._client.containers.get(handle.id)
        wrapped = self._with_timeout(container, handle.id, command, timeout_s)
        exit_code, output = container.exec_run(wrapped, workdir=handle.workdir, demux=True)
        stdout, stderr = output
        timed_out = exit_code in _TIMEOUT_EXIT_CODES and wrapped is not command
        return ExecResult(
            exit_code=exit_code,
            stdout=(stdout or b"").decode("utf-8", errors="replace"),
            stderr=(stderr or b"").decode("utf-8", errors="replace")
            + (f"\ntimed out after {timeout_s}s" if timed_out else ""),
            timed_out=timed_out,
        )

    def _with_timeout(
        self, container, container_id: str, command: list[str], timeout_s: int
    ) -> list[str]:
        """Run under coreutils `timeout` so the deadline actually kills it.

        Without this, a host-side timeout returns while the command keeps
        running and holds the container's single CPU for every later exec.
        """
        if not self._timeout_available(container, container_id):
            return command
        return ["timeout", "--signal=KILL", str(timeout_s), *command]

    def _timeout_available(self, container, container_id: str) -> bool:
        """Probe once per container — not every image ships coreutils."""
        with self._probe_lock:
            if container_id not in self._has_timeout:
                code, _ = container.exec_run(["sh", "-c", "command -v timeout"])
                self._has_timeout[container_id] = code == 0
            return self._has_timeout[container_id]

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        result = await self.exec(handle, ["cat", path], timeout_s=30)
        if result.exit_code != 0:
            raise FileNotFoundError(f"{path}: {result.stderr.strip()}")
        return result.stdout

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        await asyncio.to_thread(self._write_file_sync, handle, path, content)

    def _write_file_sync(self, handle: SandboxHandle, path: str, content: str) -> None:
        container = self._client.containers.get(handle.id)
        target = Path(path)
        parent = target.parent if target.is_absolute() else Path(handle.workdir) / target.parent
        container.exec_run(["mkdir", "-p", str(parent)])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=target.name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        container.put_archive(str(parent), buf.getvalue())

    async def teardown(self, handle: SandboxHandle) -> None:
        def _remove() -> None:
            try:
                self._client.containers.get(handle.id).remove(force=True)
            except docker.errors.NotFound:
                pass
            finally:
                self._has_timeout.pop(handle.id, None)

        await asyncio.to_thread(_remove)


def _tar_directory(path: Path) -> bytes:
    """Tar the *contents* of `path` so they extract directly into the workdir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for child in sorted(path.iterdir()):
            tar.add(child, arcname=child.name)
    return buf.getvalue()
