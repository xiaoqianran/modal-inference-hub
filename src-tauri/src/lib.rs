use rand::RngCore;
use serde::Serialize;
use std::{
    fs,
    io::Write,
    net::TcpStream,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{async_runtime, Manager};

#[derive(Clone, Serialize)]
struct AgentInfo {
    running: bool,
    port: Option<u16>,
    session_token: Option<String>,
}

struct HubProcess {
    child: Child,
    port: u16,
    session_token: String,
    handshake: PathBuf,
}

#[derive(Default)]
struct HubState(Mutex<Option<HubProcess>>);

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn data_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("MODAL_HUB_DATA_DIR") {
        return Ok(PathBuf::from(path));
    }
    let current = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位 Hub 数据目录：{error}"))?;
    #[cfg(target_os = "windows")]
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let legacy = PathBuf::from(local).join("modal-3D-client");
        // 继续使用旧目录，保证升级不会移动或丢失原 Project/Artifact 历史。
        if legacy.join("projects.sqlite3").is_file() || legacy.join("experiments.sqlite3").is_file()
        {
            return Ok(legacy);
        }
    }
    Ok(current)
}

#[cfg(debug_assertions)]
fn hub_command(_app: &tauri::AppHandle) -> Result<Command, String> {
    if let Some(path) = std::env::var_os("MODAL_HUB_EXECUTABLE") {
        return Ok(Command::new(path));
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("Hub 项目根目录无效")?
        .to_path_buf();
    let mut command = Command::new("uv");
    command
        .args(["run", "python", "-m", "agent.server"])
        .current_dir(root);
    Ok(command)
}

#[cfg(not(debug_assertions))]
fn hub_command(app: &tauri::AppHandle) -> Result<Command, String> {
    if let Some(path) = std::env::var_os("MODAL_HUB_EXECUTABLE") {
        return Ok(Command::new(path));
    }
    let target = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位 Hub 资源目录：{error}"))?
        .join("binaries")
        .join("modal-inference-hub-agent-x86_64-pc-windows-msvc")
        .join("modal-inference-hub-agent-x86_64-pc-windows-msvc.exe");
    target
        .is_file()
        .then(|| Command::new(target))
        .ok_or_else(|| "在应用资源中找不到 Hub Agent".into())
}

fn terminate(child: &mut Child) {
    #[cfg(target_os = "windows")]
    if matches!(child.try_wait(), Ok(None)) {
        use std::os::windows::process::CommandExt;
        let mut command = Command::new("taskkill");
        command
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x08000000);
        let _ = command.status();
    }
    #[cfg(not(target_os = "windows"))]
    let _ = child.kill();
    let _ = child.wait();
}

fn stop(process: &mut HubProcess) {
    terminate(&mut process.child);
    let _ = fs::remove_file(&process.handshake);
}

fn info(process: &HubProcess) -> AgentInfo {
    AgentInfo {
        running: true,
        port: Some(process.port),
        session_token: Some(process.session_token.clone()),
    }
}

fn startup_error(child: &mut Child, handshake: &PathBuf, log: &PathBuf, message: &str) -> String {
    terminate(child);
    let _ = fs::remove_file(handshake);
    let detail = fs::read_to_string(log).unwrap_or_default();
    let tail: String = detail
        .chars()
        .rev()
        .take(3000)
        .collect::<String>()
        .chars()
        .rev()
        .collect();
    if tail.trim().is_empty() {
        message.into()
    } else {
        format!("{message}\nHub 日志：\n{tail}")
    }
}

fn start_blocking(app: &tauri::AppHandle) -> Result<AgentInfo, String> {
    let state = app.state::<HubState>();
    let mut guard = state.0.lock().map_err(|_| "无法锁定 Hub 进程状态")?;
    if let Some(process) = guard.as_mut() {
        if matches!(process.child.try_wait(), Ok(None)) {
            return Ok(info(process));
        }
        stop(process);
        *guard = None;
    }

    let session_token = random_token();
    let data = data_dir(app)?;
    fs::create_dir_all(&data).map_err(|error| format!("无法创建 Hub 数据目录：{error}"))?;
    let handshake = std::env::temp_dir().join(format!(
        "modal-inference-hub-{}-{}.port",
        std::process::id(),
        &session_token[..12]
    ));
    let log = data.join("hub.log");
    let _ = fs::remove_file(&handshake);
    let mut log_file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
        .map_err(|error| format!("无法创建 Hub 日志：{error}"))?;
    let _ = writeln!(
        log_file,
        "[desktop] starting Hub pid={}",
        std::process::id()
    );
    let stdout = log_file
        .try_clone()
        .map_err(|error| format!("无法打开 Hub 日志：{error}"))?;
    let mut command = hub_command(app)?;
    command
        .env("MODAL_HUB_DATA_DIR", &data)
        .env("MODAL_HUB_SESSION_TOKEN", &session_token)
        .env("MODAL_HUB_HANDSHAKE", &handshake)
        .env("MODAL_HUB_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(log_file));
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动 Hub Agent：{error}"))?;

    let deadline = Instant::now() + Duration::from_secs(30);
    let port = loop {
        if let Ok(raw) = fs::read_to_string(&handshake) {
            break raw
                .trim()
                .parse::<u16>()
                .map_err(|_| startup_error(&mut child, &handshake, &log, "Hub 握手无效"))?;
        }
        if !matches!(child.try_wait(), Ok(None)) {
            return Err(startup_error(
                &mut child,
                &handshake,
                &log,
                "Hub Agent 在启动时退出",
            ));
        }
        if Instant::now() >= deadline {
            return Err(startup_error(
                &mut child,
                &handshake,
                &log,
                "Hub Agent 启动超时",
            ));
        }
        thread::sleep(Duration::from_millis(50));
    };
    let _ = fs::remove_file(&handshake);
    let connect_deadline = Instant::now() + Duration::from_secs(5);
    while TcpStream::connect(("127.0.0.1", port)).is_err() {
        if Instant::now() >= connect_deadline {
            return Err(startup_error(
                &mut child,
                &handshake,
                &log,
                "Hub Agent 未监听端口",
            ));
        }
        thread::sleep(Duration::from_millis(25));
    }
    let process = HubProcess {
        child,
        port,
        session_token,
        handshake,
    };
    let result = info(&process);
    *guard = Some(process);
    Ok(result)
}

#[tauri::command]
async fn agent_start(app: tauri::AppHandle) -> Result<AgentInfo, String> {
    async_runtime::spawn_blocking(move || start_blocking(&app))
        .await
        .map_err(|error| format!("Hub 后台任务异常：{error}"))?
}

#[tauri::command]
async fn agent_status(app: tauri::AppHandle) -> Result<AgentInfo, String> {
    async_runtime::spawn_blocking(move || {
        let state = app.state::<HubState>();
        let mut guard = state.0.lock().map_err(|_| "无法锁定 Hub 进程状态")?;
        if let Some(process) = guard.as_mut() {
            if matches!(process.child.try_wait(), Ok(None)) {
                return Ok(info(process));
            }
            stop(process);
            *guard = None;
        }
        Ok(AgentInfo {
            running: false,
            port: None,
            session_token: None,
        })
    })
    .await
    .map_err(|error| format!("Hub 后台任务异常：{error}"))?
}

#[tauri::command]
async fn agent_stop(app: tauri::AppHandle) -> Result<(), String> {
    async_runtime::spawn_blocking(move || {
        let state = app.state::<HubState>();
        let process = state.0.lock().map_err(|_| "无法锁定 Hub 进程状态")?.take();
        if let Some(mut process) = process {
            stop(&mut process);
        }
        Ok(())
    })
    .await
    .map_err(|error| format!("Hub 后台任务异常：{error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(HubState::default())
        .invoke_handler(tauri::generate_handler![
            agent_start,
            agent_status,
            agent_stop
        ])
        .build(tauri::generate_context!())
        .expect("构建 Modal Inference Hub 失败");
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Ok(mut guard) = handle.state::<HubState>().0.lock() {
                if let Some(mut process) = guard.take() {
                    stop(&mut process);
                }
            }
        }
    });
}
