//! Docker-backed sandbox executor.
//!
//! Behaviour is defined by `tests/conformance/` in the Python repo, not by
//! this file. The properties that matter and are easy to get wrong: a timeout
//! must actually terminate the command and leave the sandbox usable, teardown
//! must be idempotent, and file contents must survive as bytes.

use std::collections::{HashMap, HashSet};
use std::io::Read;

use agentgauge_protocol::{ExecResult, OutputKind, RepoSource, SandboxHandle, SandboxPolicy};
use bollard::body_full;
use bollard::models::{ContainerCreateBody, ExecConfig, HostConfig};
use bollard::query_parameters::{
    CreateContainerOptionsBuilder, CreateImageOptionsBuilder, DownloadFromContainerOptionsBuilder,
    RemoveContainerOptionsBuilder, StartContainerOptionsBuilder, UploadToContainerOptionsBuilder,
};
use bollard::Docker;
use futures_util::StreamExt;
use tokio::sync::{mpsc, Mutex};

/// Exit codes GNU coreutils `timeout` uses when it had to stop the command.
const TIMEOUT_EXIT_CODES: [i64; 2] = [124, 137];
/// Slack on the host-side deadline, so the in-container timeout fires first
/// and this is only a backstop.
const HOST_TIMEOUT_GRACE_S: u64 = 15;

const WORKDIR: &str = "/repo";

pub type OutputSender = mpsc::UnboundedSender<(OutputKind, String)>;

#[derive(Debug, thiserror::Error)]
pub enum SandboxError {
    #[error("no such file: {0}")]
    NotFound(String),
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error("docker: {0}")]
    Backend(#[from] bollard::errors::Error),
    #[error("{0}")]
    Other(String),
}

pub type Result<T> = std::result::Result<T, SandboxError>;

pub struct DockerBackend {
    docker: Docker,
    default_policy: SandboxPolicy,
    /// Not every image ships coreutils; probe once per container.
    timeout_probe: Mutex<HashMap<String, bool>>,
    /// Every container this process created, so they can be reaped on exit.
    /// A killed client used to leave these behind.
    owned: Mutex<HashSet<String>>,
}

impl DockerBackend {
    pub async fn connect(default_policy: SandboxPolicy) -> Result<Self> {
        let docker = Docker::connect_with_local_defaults()?;
        docker.ping().await?;
        Ok(Self {
            docker,
            default_policy,
            timeout_probe: Mutex::new(HashMap::new()),
            owned: Mutex::new(HashSet::new()),
        })
    }

    // --- lifecycle --------------------------------------------------------

    pub async fn start(
        &self,
        repo: &RepoSource,
        image: &str,
        policy: Option<&SandboxPolicy>,
    ) -> Result<SandboxHandle> {
        let policy = policy.unwrap_or(&self.default_policy);
        self.ensure_image(image).await?;

        let host_config = HostConfig {
            memory: Some(parse_memory(&policy.mem_limit)?),
            nano_cpus: Some(policy.nano_cpus),
            pids_limit: Some(policy.pids_limit),
            network_mode: Some(policy.network_mode.clone()),
            ..Default::default()
        };
        let body = ContainerCreateBody {
            image: Some(image.to_string()),
            // the container only has to outlive the run; the commands come
            // through exec
            cmd: Some(vec!["sleep".into(), "infinity".into()]),
            working_dir: Some(WORKDIR.to_string()),
            host_config: Some(host_config),
            ..Default::default()
        };

        let created = self
            .docker
            .create_container(Some(CreateContainerOptionsBuilder::default().build()), body)
            .await?;
        let id = created.id;
        self.owned.lock().await.insert(id.clone());

        self.docker
            .start_container(&id, Some(StartContainerOptionsBuilder::default().build()))
            .await?;

        let handle = SandboxHandle {
            id: id.clone(),
            workdir: WORKDIR.to_string(),
        };
        self.raw_exec(&handle, &["mkdir".into(), "-p".into(), WORKDIR.into()])
            .await?;

        let RepoSource::HostPath { path } = repo;
        let archive = tar_directory(std::path::Path::new(path))?;
        self.docker
            .upload_to_container(
                &id,
                Some(
                    UploadToContainerOptionsBuilder::default()
                        .path(WORKDIR)
                        .build(),
                ),
                body_full(archive.into()),
            )
            .await?;

        // repo files keep the host uid after untar; container git runs as root
        // and would refuse the repo as "dubious ownership" without this
        self.raw_exec(
            &handle,
            &[
                "git".into(),
                "config".into(),
                "--global".into(),
                "--add".into(),
                "safe.directory".into(),
                WORKDIR.into(),
            ],
        )
        .await?;

        Ok(handle)
    }

    async fn ensure_image(&self, image: &str) -> Result<()> {
        if self.docker.inspect_image(image).await.is_ok() {
            return Ok(());
        }
        let options = CreateImageOptionsBuilder::default()
            .from_image(image)
            .build();
        let mut stream = self.docker.create_image(Some(options), None, None);
        while let Some(item) = stream.next().await {
            item?;
        }
        Ok(())
    }

    pub async fn teardown(&self, handle: &SandboxHandle) -> Result<()> {
        let options = RemoveContainerOptionsBuilder::default().force(true).build();
        match self
            .docker
            .remove_container(&handle.id, Some(options))
            .await
        {
            Ok(()) => {}
            // already gone: teardown is idempotent
            Err(bollard::errors::Error::DockerResponseServerError {
                status_code: 404, ..
            }) => {}
            Err(err) => return Err(err.into()),
        }
        self.timeout_probe.lock().await.remove(&handle.id);
        self.owned.lock().await.remove(&handle.id);
        Ok(())
    }

    /// Remove every container this process still owns. Called on shutdown so
    /// a signalled executor does not leave containers running.
    pub async fn reap_all(&self) {
        let ids: Vec<String> = self.owned.lock().await.iter().cloned().collect();
        for id in ids {
            let handle = SandboxHandle {
                id,
                workdir: WORKDIR.to_string(),
            };
            if let Err(err) = self.teardown(&handle).await {
                tracing::warn!(%err, "failed to reap container");
            }
        }
    }

    // --- exec -------------------------------------------------------------

    /// Internal exec with no timeout wrapping, for setup steps.
    async fn raw_exec(&self, handle: &SandboxHandle, command: &[String]) -> Result<ExecResult> {
        self.run(handle, command, None).await
    }

    pub async fn exec(
        &self,
        handle: &SandboxHandle,
        command: &[String],
        timeout_s: u64,
        sink: Option<OutputSender>,
    ) -> Result<ExecResult> {
        let wrapped = self.with_timeout(handle, command, timeout_s).await;
        let wrapped_applied = wrapped.len() != command.len();

        let deadline = std::time::Duration::from_secs(timeout_s + HOST_TIMEOUT_GRACE_S);
        let outcome = tokio::time::timeout(deadline, self.run(handle, &wrapped, sink)).await;

        match outcome {
            Ok(result) => {
                let mut result = result?;
                if wrapped_applied && TIMEOUT_EXIT_CODES.contains(&result.exit_code) {
                    result.timed_out = true;
                    result
                        .stderr
                        .push_str(&format!("\ntimed out after {timeout_s}s"));
                }
                Ok(result)
            }
            // The in-container timeout should have fired first. Reaching here
            // means it could not; the container is removed at teardown, which
            // is what finally kills the process.
            Err(_) => Ok(ExecResult {
                exit_code: -1,
                stdout: String::new(),
                stderr: format!("timed out after {timeout_s}s (not killed in container)"),
                timed_out: true,
            }),
        }
    }

    async fn run(
        &self,
        handle: &SandboxHandle,
        command: &[String],
        sink: Option<OutputSender>,
    ) -> Result<ExecResult> {
        let config = ExecConfig {
            cmd: Some(command.to_vec()),
            working_dir: Some(handle.workdir.clone()),
            attach_stdout: Some(true),
            attach_stderr: Some(true),
            ..Default::default()
        };
        let exec = self.docker.create_exec(&handle.id, config).await?;

        let mut stdout = String::new();
        let mut stderr = String::new();
        if let bollard::exec::StartExecResults::Attached { mut output, .. } =
            self.docker.start_exec(&exec.id, None).await?
        {
            while let Some(chunk) = output.next().await {
                let (kind, bytes) = match chunk? {
                    bollard::container::LogOutput::StdOut { message } => {
                        (OutputKind::Stdout, message)
                    }
                    bollard::container::LogOutput::StdErr { message } => {
                        (OutputKind::Stderr, message)
                    }
                    other => (OutputKind::Stdout, other.into_bytes()),
                };
                let text = String::from_utf8_lossy(&bytes).into_owned();
                match &sink {
                    // only buffer when nobody is consuming the stream
                    Some(tx) => {
                        let _ = tx.send((kind, text));
                    }
                    None if kind == OutputKind::Stdout => stdout.push_str(&text),
                    None => stderr.push_str(&text),
                }
            }
        }

        let exit_code = self
            .docker
            .inspect_exec(&exec.id)
            .await?
            .exit_code
            .unwrap_or(-1);
        Ok(ExecResult {
            exit_code,
            stdout,
            stderr,
            timed_out: false,
        })
    }

    /// Wrap under coreutils `timeout` so the deadline actually kills it.
    ///
    /// Docker's Engine API cannot kill a running exec (moby#35703), so without
    /// this a host-side timeout returns while the command keeps running and
    /// holds the container's single CPU for every later call.
    async fn with_timeout(
        &self,
        handle: &SandboxHandle,
        command: &[String],
        timeout_s: u64,
    ) -> Vec<String> {
        if !self.timeout_available(handle).await {
            return command.to_vec();
        }
        let mut wrapped = vec![
            "timeout".to_string(),
            "--signal=KILL".to_string(),
            timeout_s.to_string(),
        ];
        wrapped.extend_from_slice(command);
        wrapped
    }

    async fn timeout_available(&self, handle: &SandboxHandle) -> bool {
        if let Some(known) = self.timeout_probe.lock().await.get(&handle.id) {
            return *known;
        }
        let probe = self
            .run(
                handle,
                &["sh".into(), "-c".into(), "command -v timeout".into()],
                None,
            )
            .await;
        let available = matches!(probe, Ok(result) if result.exit_code == 0);
        self.timeout_probe
            .lock()
            .await
            .insert(handle.id.clone(), available);
        available
    }

    // --- files ------------------------------------------------------------

    pub async fn read_bytes(&self, handle: &SandboxHandle, path: &str) -> Result<Vec<u8>> {
        let absolute = absolute(handle, path);
        let options = DownloadFromContainerOptionsBuilder::default()
            .path(&absolute)
            .build();
        let mut stream = self
            .docker
            .download_from_container(&handle.id, Some(options));

        let mut archive = Vec::new();
        while let Some(chunk) = stream.next().await {
            match chunk {
                Ok(bytes) => archive.extend_from_slice(&bytes),
                Err(bollard::errors::Error::DockerResponseServerError {
                    status_code: 404, ..
                }) => return Err(SandboxError::NotFound(path.to_string())),
                Err(err) => return Err(err.into()),
            }
        }

        // The archive endpoint is the only binary-safe way to read a file out;
        // `cat` would force a decode.
        let mut tar = tar::Archive::new(std::io::Cursor::new(archive));
        let mut entries = tar
            .entries()
            .map_err(|err| SandboxError::Other(format!("unreadable archive: {err}")))?;
        let Some(entry) = entries.next() else {
            return Err(SandboxError::NotFound(path.to_string()));
        };
        let mut entry = entry.map_err(|err| SandboxError::Other(err.to_string()))?;
        if entry.header().entry_type().is_dir() {
            return Err(SandboxError::NotFound(path.to_string()));
        }
        let mut content = Vec::new();
        entry
            .read_to_end(&mut content)
            .map_err(|err| SandboxError::Other(err.to_string()))?;
        Ok(content)
    }

    pub async fn write_bytes(
        &self,
        handle: &SandboxHandle,
        path: &str,
        content: &[u8],
    ) -> Result<()> {
        let absolute = absolute(handle, path);
        let (parent, name) = split_parent(&absolute)?;

        self.raw_exec(handle, &["mkdir".into(), "-p".into(), parent.clone()])
            .await?;

        let mut builder = tar::Builder::new(Vec::new());
        let mut header = tar::Header::new_gnu();
        header.set_size(content.len() as u64);
        header.set_mode(0o644);
        header.set_cksum();
        builder
            .append_data(&mut header, &name, content)
            .map_err(|err| SandboxError::Other(err.to_string()))?;
        let archive = builder
            .into_inner()
            .map_err(|err| SandboxError::Other(err.to_string()))?;

        self.docker
            .upload_to_container(
                &handle.id,
                Some(
                    UploadToContainerOptionsBuilder::default()
                        .path(&parent)
                        .build(),
                ),
                body_full(archive.into()),
            )
            .await?;
        Ok(())
    }
}

fn absolute(handle: &SandboxHandle, path: &str) -> String {
    if path.starts_with('/') {
        path.to_string()
    } else {
        format!("{}/{}", handle.workdir.trim_end_matches('/'), path)
    }
}

fn split_parent(absolute: &str) -> Result<(String, String)> {
    let path = std::path::Path::new(absolute);
    let parent = path
        .parent()
        .and_then(|p| p.to_str())
        .filter(|p| !p.is_empty())
        .unwrap_or("/")
        .to_string();
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| SandboxError::InvalidRequest(format!("not a file path: {absolute}")))?
        .to_string();
    Ok((parent, name))
}

/// Parse a docker-style memory limit ("2g", "512m", "1048576").
fn parse_memory(limit: &str) -> Result<i64> {
    let trimmed = limit.trim().to_lowercase();
    let (digits, multiplier) = match trimmed.chars().last() {
        Some('k') => (&trimmed[..trimmed.len() - 1], 1024),
        Some('m') => (&trimmed[..trimmed.len() - 1], 1024 * 1024),
        Some('g') => (&trimmed[..trimmed.len() - 1], 1024 * 1024 * 1024),
        Some('b') => (&trimmed[..trimmed.len() - 1], 1),
        _ => (trimmed.as_str(), 1),
    };
    digits
        .trim()
        .parse::<i64>()
        .map(|value| value * multiplier)
        .map_err(|_| SandboxError::InvalidRequest(format!("bad mem_limit: {limit}")))
}

/// Tar the *contents* of `path` so they extract directly into the workdir.
fn tar_directory(path: &std::path::Path) -> Result<Vec<u8>> {
    let mut builder = tar::Builder::new(Vec::new());
    builder.follow_symlinks(false);
    let mut children: Vec<_> = std::fs::read_dir(path)
        .map_err(|err| SandboxError::InvalidRequest(format!("{}: {err}", path.display())))?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .collect();
    children.sort();

    for child in children {
        let name = child
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| SandboxError::Other("unreadable filename".into()))?
            .to_string();
        if child.is_dir() {
            builder.append_dir_all(&name, &child)
        } else {
            builder.append_path_with_name(&child, &name)
        }
        .map_err(|err| SandboxError::Other(err.to_string()))?;
    }
    builder
        .into_inner()
        .map_err(|err| SandboxError::Other(err.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_limits_parse_like_docker() {
        assert_eq!(parse_memory("2g").unwrap(), 2 * 1024 * 1024 * 1024);
        assert_eq!(parse_memory("512m").unwrap(), 512 * 1024 * 1024);
        assert_eq!(parse_memory("1024").unwrap(), 1024);
        assert!(parse_memory("lots").is_err());
    }

    #[test]
    fn relative_paths_resolve_against_the_workdir() {
        let handle = SandboxHandle {
            id: "c".into(),
            workdir: "/repo".into(),
        };
        assert_eq!(absolute(&handle, "calc.py"), "/repo/calc.py");
        assert_eq!(absolute(&handle, "pkg/mod.py"), "/repo/pkg/mod.py");
        // absolute paths are left alone: the junit report is written to /tmp
        assert_eq!(absolute(&handle, "/tmp/junit.xml"), "/tmp/junit.xml");
    }

    #[test]
    fn parent_and_name_split() {
        let (parent, name) = split_parent("/repo/a/b/c.py").unwrap();
        assert_eq!(parent, "/repo/a/b");
        assert_eq!(name, "c.py");
    }
}
