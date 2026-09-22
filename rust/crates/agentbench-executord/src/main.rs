//! AgentBench sandbox executor, speaking the stdio protocol.
//!
//! stdin carries one JSON request per line; stdout carries the handshake,
//! responses and stream events, one per line, and **nothing else**. All
//! logging goes to stderr — a stray println here corrupts the stream and the
//! client's parse fails somewhere unrelated.
//!
//! See `docs/PROTOCOL.md`. Conformance is `tests/conformance/` in the Python
//! repo, run against this binary.

use std::sync::Arc;

use agentbench_protocol::{
    ErrorCode, ExecParams, Handshake, Method, ReadParams, ReadResult, Request, Response,
    SandboxPolicy, StartParams, StartResult, StreamEvent, TeardownParams, WriteParams,
    PROTOCOL_VERSION,
};
use agentbench_sandbox::{DockerBackend, SandboxError};
use base64::Engine as _;
use clap::Parser;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, Mutex};

#[derive(Parser)]
#[command(name = "agentbench-executord", version, about)]
struct Cli {
    /// Print the protocol version this binary implements and exit.
    #[arg(long)]
    protocol_version: bool,
}

/// stdout is a shared resource: responses and stream events interleave, so
/// every write goes through one lock and ends with a newline.
struct Out(Mutex<tokio::io::Stdout>);

impl Out {
    async fn send(&self, value: &impl serde::Serialize) {
        let mut line = match serde_json::to_vec(value) {
            Ok(line) => line,
            Err(err) => {
                tracing::error!(%err, "failed to serialize a protocol message");
                return;
            }
        };
        line.push(b'\n');
        let mut stdout = self.0.lock().await;
        if let Err(err) = stdout.write_all(&line).await {
            tracing::error!(%err, "failed to write to stdout");
            return;
        }
        let _ = stdout.flush().await;
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    if cli.protocol_version {
        println!("{PROTOCOL_VERSION}");
        return Ok(());
    }

    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_env("AGENTBENCH_LOG")
                .unwrap_or_else(|_| "warn".into()),
        )
        .init();

    let backend = Arc::new(DockerBackend::connect(SandboxPolicy::default()).await?);
    let out = Arc::new(Out(Mutex::new(tokio::io::stdout())));

    out.send(&Handshake {
        protocol_version: PROTOCOL_VERSION.to_string(),
        backend: "docker".to_string(),
        capabilities: vec!["stream".to_string()],
    })
    .await;

    let serving = serve(Arc::clone(&backend), Arc::clone(&out));
    tokio::select! {
        result = serving => result?,
        _ = shutdown_signal() => {
            tracing::info!("signalled; reaping containers");
        }
    }

    // Containers outlive this process unless something removes them. The
    // Python executor cannot guarantee this if it is killed; owning them in
    // one process is the point of the daemon.
    backend.reap_all().await;
    Ok(())
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = match signal(SignalKind::terminate()) {
            Ok(term) => term,
            Err(_) => return std::future::pending().await,
        };
        tokio::select! {
            _ = term.recv() => {}
            _ = tokio::signal::ctrl_c() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

async fn serve(backend: Arc<DockerBackend>, out: Arc<Out>) -> anyhow::Result<()> {
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    let mut inflight = tokio::task::JoinSet::new();

    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        let request: Request = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(err) => {
                // No id to answer with, so this can only be logged.
                tracing::error!(%err, "unparseable request");
                continue;
            }
        };
        // Requests are handled concurrently: the client issues parallel execs
        // and matches responses by id.
        inflight.spawn(handle(Arc::clone(&backend), Arc::clone(&out), request));
    }

    while inflight.join_next().await.is_some() {}
    Ok(())
}

async fn handle(backend: Arc<DockerBackend>, out: Arc<Out>, request: Request) {
    let id = request.id;
    let response = match dispatch(&backend, &out, &request).await {
        Ok(result) => Response::ok(id, result),
        Err(err) => Response::failed(id, code_for(&err), err.to_string()),
    };
    out.send(&response).await;
}

fn code_for(err: &SandboxError) -> ErrorCode {
    match err {
        SandboxError::NotFound(_) => ErrorCode::NotFound,
        SandboxError::InvalidRequest(_) => ErrorCode::InvalidRequest,
        SandboxError::Backend(_) | SandboxError::Other(_) => ErrorCode::BackendError,
    }
}

fn bad_params(err: serde_json::Error) -> SandboxError {
    SandboxError::InvalidRequest(err.to_string())
}

async fn dispatch(
    backend: &DockerBackend,
    out: &Arc<Out>,
    request: &Request,
) -> Result<serde_json::Value, SandboxError> {
    match request.method {
        Method::Start => {
            let params: StartParams =
                serde_json::from_value(request.params.clone()).map_err(bad_params)?;
            let handle = backend
                .start(&params.repo, &params.image, params.policy.as_ref())
                .await?;
            to_value(StartResult { handle })
        }
        Method::Exec => {
            let params: ExecParams =
                serde_json::from_value(request.params.clone()).map_err(bad_params)?;
            let sink = params
                .stream
                .then(|| spawn_stream(Arc::clone(out), request.id));
            let result = backend
                .exec(&params.handle, &params.command, params.timeout_s, sink)
                .await?;
            to_value(result)
        }
        Method::ReadBytes => {
            let params: ReadParams =
                serde_json::from_value(request.params.clone()).map_err(bad_params)?;
            let content = backend.read_bytes(&params.handle, &params.path).await?;
            to_value(ReadResult {
                content_b64: base64::engine::general_purpose::STANDARD.encode(content),
            })
        }
        Method::WriteBytes => {
            let params: WriteParams =
                serde_json::from_value(request.params.clone()).map_err(bad_params)?;
            let content = base64::engine::general_purpose::STANDARD
                .decode(&params.content_b64)
                .map_err(|err| SandboxError::InvalidRequest(format!("bad base64: {err}")))?;
            backend
                .write_bytes(&params.handle, &params.path, &content)
                .await?;
            Ok(serde_json::json!({}))
        }
        Method::Teardown => {
            let params: TeardownParams =
                serde_json::from_value(request.params.clone()).map_err(bad_params)?;
            backend.teardown(&params.handle).await?;
            Ok(serde_json::json!({}))
        }
    }
}

/// Forward output chunks as stream events until the exec drops the sender.
///
/// Events must all be written before the response, which holds because the
/// backend drops the sender when the exec finishes and this task drains the
/// channel; the response is only written after `exec` returns.
fn spawn_stream(out: Arc<Out>, request_id: i64) -> agentbench_sandbox::OutputSender {
    let (tx, mut rx) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Some((kind, data)) = rx.recv().await {
            out.send(&StreamEvent {
                stream: request_id,
                kind,
                data,
            })
            .await;
        }
    });
    tx
}

fn to_value(value: impl serde::Serialize) -> Result<serde_json::Value, SandboxError> {
    serde_json::to_value(value).map_err(|err| SandboxError::Other(err.to_string()))
}
