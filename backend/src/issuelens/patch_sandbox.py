import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from issuelens.investigation import InvestigationTools


@dataclass(frozen=True)
class SandboxLimits:
    cpus: str
    memory_bytes: int
    output_bytes: int
    pids: int
    timeout_seconds: int


@dataclass(frozen=True)
class PatchSandboxManifest:
    case_id: str
    repository: str
    snapshot_commit: str
    image: str
    allowed_changed_files: frozenset[str]
    patches: dict[str, Path]
    tests: dict[str, tuple[str, ...]]
    reproduction_test_id: str
    reproduction_failure_marker: str
    limits: SandboxLimits


def load_patch_sandbox_manifest(path: Path) -> PatchSandboxManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {
        "schema_version",
        "case_id",
        "repository",
        "snapshot_commit",
        "image",
        "allowed_changed_files",
        "patches",
        "tests",
        "reproduction",
        "limits",
    } or data["schema_version"] != "1.0":
        raise ValueError("patch sandbox manifest has an unexpected schema")
    limits = data["limits"]
    if set(limits) != {
        "cpus", "memory_bytes", "output_bytes", "pids", "timeout_seconds"
    }:
        raise ValueError("patch sandbox limits have an unexpected schema")
    if (
        limits["cpus"] != "1"
        or limits["memory_bytes"] != 536_870_912
        or limits["output_bytes"] != 1_048_576
        or limits["pids"] != 256
        or limits["timeout_seconds"] != 300
    ):
        raise ValueError("patch sandbox limits must match the approved safety profile")
    manifest_root = path.resolve().parent
    patches: dict[str, Path] = {}
    for patch_id, relative in data["patches"].items():
        candidate = (manifest_root / relative).resolve()
        if not candidate.is_relative_to(manifest_root) or not candidate.is_file():
            raise ValueError("patch file is outside the manifest directory")
        patches[patch_id] = candidate
    tests = {
        test_id: tuple(command)
        for test_id, command in data["tests"].items()
        if isinstance(command, list)
        and command
        and all(isinstance(argument, str) and argument for argument in command)
    }
    if len(tests) != len(data["tests"]):
        raise ValueError("test commands must be non-empty argv arrays")
    reproduction = data["reproduction"]
    if (
        not isinstance(reproduction, dict)
        or set(reproduction) != {"test_id", "failure_marker"}
        or reproduction["test_id"] not in tests
        or not isinstance(reproduction["failure_marker"], str)
        or not reproduction["failure_marker"]
    ):
        raise ValueError("reproduction must name an approved test and failure marker")
    allowed_changed_files = frozenset(data["allowed_changed_files"])
    if not allowed_changed_files or any(
        not isinstance(item, str)
        or not item
        or Path(item).is_absolute()
        or ".." in Path(item).parts
        for item in allowed_changed_files
    ):
        raise ValueError("allowed changed files must be repository-relative paths")
    return PatchSandboxManifest(
        case_id=data["case_id"],
        repository=data["repository"],
        snapshot_commit=data["snapshot_commit"],
        image=data["image"],
        allowed_changed_files=allowed_changed_files,
        patches=patches,
        tests=tests,
        reproduction_test_id=reproduction["test_id"],
        reproduction_failure_marker=reproduction["failure_marker"],
        limits=SandboxLimits(**limits),
    )


class PatchSandboxTools:
    def __init__(
        self,
        *,
        manifest: PatchSandboxManifest,
        source_repository: Path,
        workspace_root: Path,
        retrieval_tools: InvestigationTools,
    ) -> None:
        self.manifest = manifest
        self.source_repository = source_repository.resolve()
        self.workspace_root = workspace_root.resolve()
        cache_root = (self.workspace_root / ".retrieval-cache").resolve()
        if not self.source_repository.is_relative_to(cache_root):
            raise ValueError("source repository must be inside the managed worktree cache")
        self.retrieval_tools = retrieval_tools
        self.started_ns = time.perf_counter_ns()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._worktree: Path | None = None
        self._created = False
        self._cleaned = False
        self._finished: dict[str, Any] | None = None
        self._executions: list[dict[str, Any]] = []
        self._patch: dict[str, Any] = {"status": "not_run"}
        self._source_before = self._fingerprint_source()

    def _remaining_seconds(self) -> float:
        elapsed = (time.perf_counter_ns() - self.started_ns) / 1_000_000_000
        remaining = self.manifest.limits.timeout_seconds - elapsed
        if remaining <= 0:
            raise TimeoutError("patch validation exceeded its five-minute deadline")
        return remaining

    @staticmethod
    def _git(path: Path, *arguments: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(path), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )

    def _fingerprint_source(self) -> dict[str, str]:
        head = self._git(self.source_repository, "rev-parse", "HEAD").stdout.strip()
        if head != self.manifest.snapshot_commit:
            raise ValueError("source repository is not at the approved snapshot commit")
        status = self._git(
            self.source_repository, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        index = self._git(self.source_repository, "ls-files", "-s").stdout
        return {
            "head": head,
            "status": status,
            "index_sha256": hashlib.sha256(index.encode()).hexdigest(),
        }

    def _ensure_worktree(self) -> Path:
        if self._worktree is not None:
            return self._worktree
        cache_root = self.workspace_root / ".retrieval-cache"
        cache_root.mkdir(exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="issuelens-patch-", dir=cache_root
        )
        worktree = Path(self._temporary.name) / "worktree"
        subprocess.run(
            [
                "git", "clone", "--no-hardlinks", "--no-checkout",
                str(self.source_repository), str(worktree),
            ],
            check=True,
            capture_output=True,
            timeout=self._remaining_seconds(),
        )
        self._git(
            worktree,
            "checkout",
            "--detach",
            self.manifest.snapshot_commit,
            timeout=self._remaining_seconds(),
        )
        self._worktree = worktree
        self._created = True
        return worktree

    def invoke(self, name: str, arguments: dict[str, object]) -> object:
        if name not in {"run_tests", "submit_patch"}:
            return self.retrieval_tools.invoke(name, arguments)
        if name == "run_tests":
            test_id = arguments.get("test_id")
            if not isinstance(test_id, str) or test_id not in self.manifest.tests:
                raise PermissionError("test identifier is not approved by the manifest")
            status = self._run_test(test_id)
            if test_id == self.manifest.reproduction_test_id and self._patch["status"] == "not_run":
                first = self._executions[-1]
                second_status = self._run_test(test_id)
                second = self._executions[-1]
                if not (
                    status == second_status == "failed"
                    and self.manifest.reproduction_failure_marker in first["output"]
                    and first["output"] == second["output"]
                ):
                    status = "error"
            return {"passed": status == "passed", "status": status}
        patch_id = arguments.get("patch_id")
        if not isinstance(patch_id, str) or patch_id not in self.manifest.patches:
            raise PermissionError("patch identifier is not approved by the manifest")
        return {"accepted": self._apply_patch(patch_id)}

    def _apply_patch(self, patch_id: str) -> bool:
        worktree = self._ensure_worktree()
        patch = self.manifest.patches[patch_id]
        try:
            self._git(
                worktree, "apply", "--check", str(patch), timeout=self._remaining_seconds()
            )
            self._git(worktree, "apply", str(patch), timeout=self._remaining_seconds())
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, TimeoutError):
            self._patch = {"status": "rejected", "patch_id": patch_id}
            return False
        changed = set(
            self._git(worktree, "diff", "--name-only").stdout.splitlines()
        )
        if not changed or not changed <= self.manifest.allowed_changed_files:
            self._patch = {
                "status": "rejected",
                "patch_id": patch_id,
                "changed_files": sorted(changed),
            }
            return False
        self._patch = {
            "status": "applied",
            "patch_id": patch_id,
            "changed_files": sorted(changed),
        }
        return True

    def _run_test(self, test_id: str) -> str:
        worktree = self._ensure_worktree()
        limits = self.manifest.limits
        name = f"issuelens-{uuid4().hex}"
        mount = f"type=bind,source={worktree},target=/workspace"
        created = subprocess.run(
            [
                "docker", "create", "--name", name,
                "--network", "none",
                "--cpus", limits.cpus,
                "--memory", str(limits.memory_bytes),
                "--memory-swap", str(limits.memory_bytes),
                "--pids-limit", str(limits.pids),
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--mount", mount,
                "--workdir", "/workspace",
                self.manifest.image,
                *self.manifest.tests[test_id],
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=self._remaining_seconds(),
        )
        container_id = created.stdout.strip()
        started = time.perf_counter_ns()
        status = "failed"
        return_code: int | None = None
        output = b""
        limits_verified = False
        try:
            inspected = json.loads(subprocess.run(
                ["docker", "inspect", container_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=self._remaining_seconds(),
            ).stdout)[0]
            host = inspected["HostConfig"]
            mounts = inspected["Mounts"]
            limits_verified = (
                host["NetworkMode"] == "none"
                and host["NanoCpus"] == 1_000_000_000
                and host["Memory"] == limits.memory_bytes
                and host["MemorySwap"] == limits.memory_bytes
                and host["PidsLimit"] == limits.pids
                and host["ReadonlyRootfs"] is True
                and len(mounts) == 1
                and Path(mounts[0]["Source"]).resolve() == worktree.resolve()
                and mounts[0]["Destination"] == "/workspace"
                and mounts[0]["RW"] is True
            )
            if not limits_verified:
                raise RuntimeError("docker did not apply the approved sandbox limits")
            process = subprocess.Popen(
                ["docker", "start", "--attach", container_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=2)

            def read_output() -> None:
                assert process.stdout is not None
                while chunk := process.stdout.read(65_536):
                    chunks.put(chunk)
                chunks.put(None)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                while True:
                    try:
                        chunk = chunks.get(timeout=self._remaining_seconds())
                    except queue.Empty:
                        status = "timed_out"
                        break
                    if chunk is None:
                        break
                    remaining = limits.output_bytes - len(output)
                    output += chunk[:remaining]
                    if len(chunk) > remaining or len(output) >= limits.output_bytes:
                        status = "output_limited"
                        break
                if status not in {"timed_out", "output_limited"}:
                    process.wait(timeout=self._remaining_seconds())
                    inspected_exit = json.loads(subprocess.run(
                        ["docker", "inspect", container_id],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=self._remaining_seconds(),
                    ).stdout)[0]
                    return_code = inspected_exit["State"]["ExitCode"]
                    status = "passed" if return_code == 0 else "failed"
            except (TimeoutError, subprocess.TimeoutExpired):
                status = "timed_out"
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=30)
        finally:
            subprocess.run(
                ["docker", "rm", "--force", container_id],
                capture_output=True,
                timeout=30,
            )
        self._executions.append({
            "test_id": test_id,
            "status": status,
            "exit_code": return_code,
            "duration_ns": time.perf_counter_ns() - started,
            "output": output.decode("utf-8", errors="replace"),
            "output_bytes": len(output),
            "limits_verified": limits_verified,
        })
        return status

    def finish_patch_validation(self) -> dict[str, Any]:
        if self._finished is not None:
            return self._finished
        diff = ""
        if self._worktree is not None:
            diff = self._git(self._worktree, "diff", "--binary").stdout
        if self._temporary is not None:
            temporary_path = Path(self._temporary.name)
            self._temporary.cleanup()
            self._cleaned = not temporary_path.exists()
        source_after = self._fingerprint_source()
        self._finished = {
            "source_repository": {
                "before": self._source_before,
                "after": source_after,
                "unchanged": source_after == self._source_before,
            },
            "worktree": {"created": self._created, "cleaned": self._cleaned},
            "executions": self._executions,
            "patch": {
                **self._patch,
                "diff": diff,
                "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            },
            "limits": {
                "network": "none",
                "cpus": self.manifest.limits.cpus,
                "memory_bytes": self.manifest.limits.memory_bytes,
                "memory_swap_bytes": self.manifest.limits.memory_bytes,
                "output_bytes": self.manifest.limits.output_bytes,
                "pids": self.manifest.limits.pids,
                "timeout_seconds": self.manifest.limits.timeout_seconds,
            },
        }
        return self._finished
