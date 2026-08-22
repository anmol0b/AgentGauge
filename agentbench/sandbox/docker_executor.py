"""Docker implementation of SandboxExecutor.

This is the ONLY file in the codebase allowed to import `docker`.
"""

import asyncio
import io
import tarfile
import time
from pathlib import Path

import docker
import docker.errors

from agentbench.sandbox.executor import ExecResult, SandboxHandle
from agentbench.sandbox.policy import SandboxPolicy


class DockerSandboxExecutor:
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self._policy = policy or SandboxPolicy()
        self._client = docker.from_env()

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
                asyncio.to_thread(self._exec_sync, handle, command), timeout=timeout_s
            )
        except TimeoutError:
            # v1: we don't kill the in-container process; the caller treats a
            # timeout as a failed command and the run usually ends here.
            return ExecResult(
                exit_code=-1, stdout="", stderr=f"timed out after {timeout_s}s", timed_out=True
            )

    def _exec_sync(self, handle: SandboxHandle, command: list[str]) -> ExecResult:
        container = self._client.containers.get(handle.id)
        exit_code, output = container.exec_run(command, workdir=handle.workdir, demux=True)
        stdout, stderr = output
        return ExecResult(
            exit_code=exit_code,
            stdout=(stdout or b"").decode("utf-8", errors="replace"),
            stderr=(stderr or b"").decode("utf-8", errors="replace"),
        )

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

        await asyncio.to_thread(_remove)


def _tar_directory(path: Path) -> bytes:
    """Tar the *contents* of `path` so they extract directly into the workdir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for child in sorted(path.iterdir()):
            tar.add(child, arcname=child.name)
    return buf.getvalue()
