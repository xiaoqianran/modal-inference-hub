use rand::RngCore;
use serde::Serialize;
use std::{
    fs,
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{path::BaseDirectory, AppHandle, Manager, State};

#[derive(Clone, Serialize)]
pub struct AgentInfo {
    running: bool,
    port: Option<u16>,
    session_token: Option<String>,
}

struct AgentProcess {
    child: Child,
    port: u16,
    session_token: String,
    handshake: PathBuf,
}

#[derive(Default)]
pub struct AgentState(Mutex<Option<AgentProcess>>);

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn agent_command(app: &AppHandle) -> Result<Command, String> {
    if let Some(path) = std::env::var_os("MODAL_3D_AGENT_EXECUTABLE") {
        return Ok(Command::new(path));
    }

    #[cfg(debug_assertions)]
    {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .ok_or("invalid project root")?
            .to_path_buf();
        let mut command = Command::new("uv");
        command
            .args(["run", "python", "-m", "agent.server"])
            .current_dir(root);
        Ok(command)
    }

    #[cfg(all(not(debug_assertions), target_os = "windows"))]
    {
        let path = app
            .path()
            .resolve("resources/modal-3d-agent.exe", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?;
        return Ok(Command::new(path));
    }

    #[cfg(all(not(debug_assertions), not(target_os = "windows")))]
    Err("bundled agent executable is only configured for Windows".into())
}

fn stop_process(process: &mut AgentProcess) {
    let _ = process.child.kill();
    let _ = process.child.wait();
    let _ = fs::remove_file(&process.handshake);
}

fn process_info(process: &AgentProcess) -> AgentInfo {
    AgentInfo {
        running: true,
        port: Some(process.port),
        session_token: Some(process.session_token.clone()),
    }
}

#[tauri::command]
pub fn agent_start(app: AppHandle, state: State<'_, AgentState>) -> Result<AgentInfo, String> {
    let mut state = state.0.lock().map_err(|_| "agent state lock failed")?;
    if let Some(process) = state.as_mut() {
        if process
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            return Ok(process_info(process));
        }
        stop_process(process);
        *state = None;
    }

    let session_token = random_token();
    let handshake = std::env::temp_dir().join(format!(
        "modal-3d-agent-{}-{}.port",
        std::process::id(),
        &session_token[..12]
    ));
    let _ = fs::remove_file(&handshake);

    let mut command = agent_command(&app)?;
    command
        .env("MODAL_3D_AGENT_TOKEN", &session_token)
        .env("MODAL_3D_AGENT_HANDSHAKE", &handshake)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Ok(Some((token_id, token_secret))) = crate::credentials::load() {
        command
            .env("MODAL_3D_SAVED_TOKEN_ID", token_id)
            .env("MODAL_3D_SAVED_TOKEN_SECRET", token_secret);
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    let mut child = command.spawn().map_err(|error| error.to_string())?;
    let deadline = Instant::now() + Duration::from_secs(15);
    let port = loop {
        if let Ok(value) = fs::read_to_string(&handshake) {
            break value
                .trim()
                .parse::<u16>()
                .map_err(|_| "invalid agent handshake")?;
        }
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(format!("agent exited during startup: {status}"));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            return Err("agent startup timed out".into());
        }
        thread::sleep(Duration::from_millis(50));
    };
    let _ = fs::remove_file(&handshake);

    let deadline = Instant::now() + Duration::from_secs(5);
    while TcpStream::connect(("127.0.0.1", port)).is_err() {
        if Instant::now() >= deadline {
            let _ = child.kill();
            return Err("agent did not open its local port".into());
        }
        thread::sleep(Duration::from_millis(50));
    }

    let process = AgentProcess {
        child,
        port,
        session_token,
        handshake,
    };
    let info = process_info(&process);
    *state = Some(process);
    Ok(info)
}

#[tauri::command]
pub fn agent_status(state: State<'_, AgentState>) -> Result<AgentInfo, String> {
    let mut state = state.0.lock().map_err(|_| "agent state lock failed")?;
    if let Some(process) = state.as_mut() {
        if process
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            return Ok(process_info(process));
        }
        stop_process(process);
        *state = None;
    }
    Ok(AgentInfo {
        running: false,
        port: None,
        session_token: None,
    })
}

#[tauri::command]
pub fn agent_stop(state: State<'_, AgentState>) -> Result<(), String> {
    let mut state = state.0.lock().map_err(|_| "agent state lock failed")?;
    if let Some(mut process) = state.take() {
        stop_process(&mut process);
    }
    Ok(())
}

pub fn shutdown(state: &AgentState) {
    let process = state.0.lock().ok().and_then(|mut state| state.take());
    if let Some(mut process) = process {
        stop_process(&mut process);
    }
}
