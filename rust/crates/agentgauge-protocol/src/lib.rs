//! Wire types for the AgentGauge sandbox executor protocol.
//!
//! These mirror `agentgauge/sandbox/protocol.py` field for field. The Python
//! client parses what this crate emits, so a rename on either side breaks the
//! pair silently — `docs/PROTOCOL.md` is the shared specification, and
//! `tests/conformance/` is what proves the two agree.

use serde::{Deserialize, Serialize};

/// 2.0 replaced newline-delimited framing with length-prefixed framing.
/// Bumped when a change would break an executor built against the old shape.
pub const PROTOCOL_VERSION: &str = "2.0";

fn default_workdir() -> String {
    "/repo".to_string()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxHandle {
    pub id: String,
    #[serde(default = "default_workdir")]
    pub workdir: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecResult {
    pub exit_code: i64,
    pub stdout: String,
    pub stderr: String,
    #[serde(default)]
    pub timed_out: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputKind {
    Stdout,
    Stderr,
}

/// A repository the executor can read directly from the host filesystem.
///
/// Tagged so a remote executor can add a variant without a version bump.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RepoSource {
    HostPath { path: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxPolicy {
    #[serde(default = "default_mem_limit")]
    pub mem_limit: String,
    #[serde(default = "default_nano_cpus")]
    pub nano_cpus: i64,
    #[serde(default = "default_pids_limit")]
    pub pids_limit: i64,
    #[serde(default = "default_network_mode")]
    pub network_mode: String,
}

fn default_mem_limit() -> String {
    "2g".to_string()
}
fn default_nano_cpus() -> i64 {
    1_000_000_000
}
fn default_pids_limit() -> i64 {
    256
}
fn default_network_mode() -> String {
    "none".to_string()
}

impl Default for SandboxPolicy {
    fn default() -> Self {
        Self {
            mem_limit: default_mem_limit(),
            nano_cpus: default_nano_cpus(),
            pids_limit: default_pids_limit(),
            network_mode: default_network_mode(),
        }
    }
}

// --- method params ------------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct StartParams {
    pub repo: RepoSource,
    pub image: String,
    /// Omitted means the executor's configured default, not "no limits".
    #[serde(default)]
    pub policy: Option<SandboxPolicy>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StartResult {
    pub handle: SandboxHandle,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ExecParams {
    pub handle: SandboxHandle,
    pub command: Vec<String>,
    pub timeout_s: u64,
    #[serde(default)]
    pub stream: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ReadParams {
    pub handle: SandboxHandle,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReadResult {
    pub content_b64: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct WriteParams {
    pub handle: SandboxHandle,
    pub path: String,
    pub content_b64: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TeardownParams {
    pub handle: SandboxHandle,
}

// --- envelopes ----------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Method {
    Start,
    Exec,
    ReadBytes,
    WriteBytes,
    Teardown,
}

/// Part of the contract: the client maps `NotFound` onto FileNotFoundError,
/// so an executor that invents its own string turns a detectable condition
/// into an opaque failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    NotFound,
    InvalidRequest,
    Unsupported,
    BackendError,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ErrorObject {
    pub code: ErrorCode,
    pub message: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Request {
    pub id: i64,
    pub method: Method,
    pub params: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Response {
    pub id: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorObject>,
}

impl Response {
    pub fn ok(id: i64, result: serde_json::Value) -> Self {
        Self {
            id,
            result: Some(result),
            error: None,
        }
    }

    pub fn failed(id: i64, code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            id,
            result: None,
            error: Some(ErrorObject {
                code,
                message: message.into(),
            }),
        }
    }
}

/// Unsolicited output for an in-flight request.
///
/// Deliberately carries no `id` field: a response is matched on `id`, so an
/// event that had one could be mistaken for the request completing.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamEvent {
    pub stream: i64,
    pub kind: OutputKind,
    pub data: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Handshake {
    pub protocol_version: String,
    pub backend: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_source_is_tagged() {
        let json = serde_json::to_value(RepoSource::HostPath {
            path: "/tmp/r".into(),
        })
        .unwrap();
        assert_eq!(json["kind"], "host_path");
        assert_eq!(json["path"], "/tmp/r");
    }

    #[test]
    fn handle_defaults_to_the_repo_workdir() {
        let handle: SandboxHandle = serde_json::from_str(r#"{"id":"abc"}"#).unwrap();
        assert_eq!(handle.workdir, "/repo");
    }

    #[test]
    fn policy_defaults_match_the_python_side() {
        let policy = SandboxPolicy::default();
        assert_eq!(policy.mem_limit, "2g");
        assert_eq!(policy.nano_cpus, 1_000_000_000);
        assert_eq!(policy.pids_limit, 256);
        assert_eq!(policy.network_mode, "none"); // sandboxes have no egress
    }

    #[test]
    fn exec_params_default_to_not_streaming() {
        let params: ExecParams =
            serde_json::from_str(r#"{"handle":{"id":"c"},"command":["true"],"timeout_s":30}"#)
                .unwrap();
        assert!(!params.stream);
    }

    #[test]
    fn methods_use_the_wire_spelling() {
        assert_eq!(
            serde_json::to_value(Method::ReadBytes).unwrap(),
            "read_bytes"
        );
        assert_eq!(
            serde_json::to_value(ErrorCode::NotFound).unwrap(),
            "not_found"
        );
    }

    #[test]
    fn stream_events_carry_no_id() {
        let event = StreamEvent {
            stream: 3,
            kind: OutputKind::Stdout,
            data: "x".into(),
        };
        let json = serde_json::to_value(&event).unwrap();
        assert!(json.get("id").is_none());
        assert_eq!(json["stream"], 3);
    }

    #[test]
    fn a_successful_response_omits_the_error_field() {
        let json = serde_json::to_value(Response::ok(1, serde_json::json!({}))).unwrap();
        assert!(json.get("error").is_none());
    }
}
