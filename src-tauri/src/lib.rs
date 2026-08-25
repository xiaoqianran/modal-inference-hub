mod agent_handoff;
mod credentials;
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
use tauri_plugin_dialog::DialogExt;
#[derive(Clone, Serialize)]
struct AgentInfo {
    running: bool,
    port: Option<u16>,
    session_token: Option<String>,
}

struct AgentProcess {
    child: Child,
    port: u16,
    session_token: String,
    handshake: PathBuf,
    log: PathBuf,
}

#[derive(Default)]
struct AgentState(Mutex<Option<AgentProcess>>);

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[derive(Serialize)]
struct AppDiagnostics {
    version: String,
    data_dir: String,
    agent_log: Option<String>,
}

#[tauri::command]
fn choose_local_sam_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    Ok(app
        .dialog()
        .file()
        .set_title("选择 Local SAM 存储目录")
        .blocking_pick_folder()
        .map(|path| path.to_string()))
}

#[tauri::command]
async fn app_diagnostics(app: tauri::AppHandle) -> Result<AppDiagnostics, String> {
    async_runtime::spawn_blocking(move || {
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("无法定位客户端数据目录：{error}"))?;
        let state = app.state::<AgentState>();
        let agent_log = state
            .0
            .lock()
            .map_err(|_| "无法锁定本地代理状态")?
            .as_ref()
            .map(|process| process.log.to_string_lossy().into_owned());
        Ok(AppDiagnostics {
            version: app.package_info().version.to_string(),
            data_dir: data_dir.to_string_lossy().into_owned(),
            agent_log,
        })
    })
    .await
    .map_err(|error| format!("桌面后台任务异常退出：{error}"))?
}

#[tauri::command]
fn reveal_app_data(app: tauri::AppHandle) -> Result<(), String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位客户端数据目录：{error}"))?;
    fs::create_dir_all(&data_dir).map_err(|error| format!("无法创建客户端数据目录：{error}"))?;
    #[cfg(target_os = "windows")]
    Command::new("explorer.exe")
        .arg(&data_dir)
        .spawn()
        .map_err(|error| format!("无法打开客户端数据目录：{error}"))?;
    #[cfg(not(target_os = "windows"))]
    return Err("当前只支持在 Windows 中打开数据目录".into());
    Ok(())
}

fn agent_command(app: &tauri::AppHandle) -> Result<Command, String> {
    if let Some(path) = std::env::var_os("MODAL_3D_AGENT_EXECUTABLE") {
        return Ok(Command::new(path));
    }

    #[cfg(target_os = "windows")]
    {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|error| format!("无法定位客户端资源目录：{error}"))?;
        let bundled_agent = resource_dir
            .join("binaries")
            .join("modal-3d-agent-x86_64-pc-windows-msvc")
            .join("modal-3d-agent-x86_64-pc-windows-msvc.exe");
        if bundled_agent.is_file() {
            return Ok(Command::new(bundled_agent));
        }
    }

    #[cfg(debug_assertions)]
    {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .ok_or("项目根目录无效")?
            .to_path_buf();
        let mut command = Command::new("uv");
        command
            .args(["run", "python", "-m", "agent.server"])
            .current_dir(root);
        Ok(command)
    }

    #[cfg(not(debug_assertions))]
    Err("在客户端资源目录中找不到已捆绑的本地代理".into())
}

fn terminate_child(child: &mut Child) {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;

        if matches!(child.try_wait(), Ok(None)) {
            let mut taskkill = Command::new("taskkill");
            taskkill
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(0x08000000);
            let terminated = taskkill.spawn().ok().is_some_and(|mut taskkill| {
                let deadline = Instant::now() + Duration::from_secs(5);
                loop {
                    match taskkill.try_wait() {
                        Ok(Some(status)) => break status.success(),
                        Ok(None) if Instant::now() < deadline => {
                            thread::sleep(Duration::from_millis(25));
                        }
                        _ => {
                            let _ = taskkill.kill();
                            break false;
                        }
                    }
                }
            });
            if !terminated {
                let _ = child.kill();
            }
        }
    }

    #[cfg(not(target_os = "windows"))]
    let _ = child.kill();

    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        if !matches!(child.try_wait(), Ok(None)) {
            return;
        }
        thread::sleep(Duration::from_millis(25));
    }
    let _ = child.kill();
}

fn stop_process(process: &mut AgentProcess) {
    terminate_child(&mut process.child);
    let _ = fs::remove_file(&process.handshake);
    let _ = credentials::clear_agent_handoff(&process.session_token);
}

fn startup_failure(
    child: &mut Child,
    handshake: &PathBuf,
    log: &PathBuf,
    message: impl Into<String>,
) -> String {
    terminate_child(child);
    let _ = fs::remove_file(handshake);

    let message = message.into();
    let detail = fs::read_to_string(log).ok().and_then(|contents| {
        let trimmed = contents.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(
                trimmed
                    .chars()
                    .rev()
                    .take(4000)
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect::<String>(),
            )
        }
    });
    match detail {
        Some(detail) => format!("{message}\n本地代理日志：\n{detail}"),
        None => message,
    }
}

fn process_info(process: &AgentProcess) -> AgentInfo {
    AgentInfo {
        running: true,
        port: Some(process.port),
        session_token: Some(process.session_token.clone()),
    }
}

fn agent_start_blocking(app: &tauri::AppHandle) -> Result<AgentInfo, String> {
    let state = app.state::<AgentState>();
    let mut state = state.0.lock().map_err(|_| "无法锁定本地代理状态")?;
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
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位客户端数据目录：{error}"))?;
    fs::create_dir_all(&data_dir).map_err(|error| format!("无法创建客户端数据目录：{error}"))?;
    let handshake = std::env::temp_dir().join(format!(
        "modal-3d-agent-{}-{}.port",
        std::process::id(),
        &session_token[..12]
    ));
    let log = data_dir.join("agent.log");
    let _ = fs::remove_file(&handshake);

    let mut command = agent_command(app)?;
    command.env("MODAL_3D_AGENT_DATA_DIR", &data_dir);
    if let Ok(Some((token_id, token_secret))) = credentials::load() {
        command
            .env("MODAL_3D_SAVED_TOKEN_ID", token_id)
            .env("MODAL_3D_SAVED_TOKEN_SECRET", token_secret);
    }
    let mut log_file = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
        .map_err(|error| format!("无法创建本地代理启动日志：{error}"))?;
    let _ = writeln!(
        log_file,
        "[desktop] starting local agent pid={}",
        std::process::id()
    );
    let log_stdout = log_file
        .try_clone()
        .map_err(|error| format!("无法打开本地代理启动日志：{error}"))?;
    command
        .env("MODAL_3D_AGENT_TOKEN", &session_token)
        .env("MODAL_3D_AGENT_HANDSHAKE", &handshake)
        .env("MODAL_3D_AGENT_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_stdout))
        .stderr(Stdio::from(log_file));

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    let mut child = command.spawn().map_err(|error| {
        let _ = fs::remove_file(&handshake);
        format!("无法启动本地代理：{error}")
    })?;
    let deadline = Instant::now() + Duration::from_secs(30);
    let port = loop {
        if let Ok(value) = fs::read_to_string(&handshake) {
            match value.trim().parse::<u16>() {
                Ok(port) => break port,
                Err(_) => {
                    return Err(startup_failure(
                        &mut child,
                        &handshake,
                        &log,
                        "本地代理返回了无效的启动握手信息",
                    ));
                }
            }
        }
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(startup_failure(
                &mut child,
                &handshake,
                &log,
                format!("本地代理在启动过程中退出：{status}"),
            ));
        }
        if Instant::now() >= deadline {
            return Err(startup_failure(
                &mut child,
                &handshake,
                &log,
                "本地代理启动超时",
            ));
        }
        thread::sleep(Duration::from_millis(50));
    };
    let _ = fs::remove_file(&handshake);

    let deadline = Instant::now() + Duration::from_secs(5);
    while TcpStream::connect(("127.0.0.1", port)).is_err() {
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(startup_failure(
                &mut child,
                &handshake,
                &log,
                format!("本地代理在监听端口前退出：{status}"),
            ));
        }
        if Instant::now() >= deadline {
            return Err(startup_failure(
                &mut child,
                &handshake,
                &log,
                "本地代理未能监听本机端口",
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }

    let _ =
        credentials::publish_agent_handoff(port, child.id(), std::process::id(), &session_token);

    let process = AgentProcess {
        child,
        port,
        session_token,
        handshake,
        log,
    };
    let info = process_info(&process);
    *state = Some(process);
    Ok(info)
}

#[tauri::command]
async fn agent_start(app: tauri::AppHandle) -> Result<AgentInfo, String> {
    async_runtime::spawn_blocking(move || agent_start_blocking(&app))
        .await
        .map_err(|error| format!("桌面后台任务异常退出：{error}"))?
}

#[tauri::command]
async fn agent_status(app: tauri::AppHandle) -> Result<AgentInfo, String> {
    async_runtime::spawn_blocking(move || agent_status_blocking(&app))
        .await
        .map_err(|error| format!("桌面后台任务异常退出：{error}"))?
}

fn agent_status_blocking(app: &tauri::AppHandle) -> Result<AgentInfo, String> {
    let state = app.state::<AgentState>();
    let mut state = state.0.lock().map_err(|_| "无法锁定本地代理状态")?;
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
async fn export_save(
    app: tauri::AppHandle,
    export_id: String,
    suggested_name: String,
) -> Result<Option<String>, String> {
    if export_id.len() != 32 || !export_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("无效的导出 ID".into());
    }

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位客户端数据目录：{error}"))?;
    let source = data_dir.join("exports").join(format!("{export_id}.glb"));
    if !source.is_file() {
        return Err("导出缓存不存在，请重新生成导出文件".into());
    }

    let suggested = PathBuf::from(suggested_name);
    let filename = suggested
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .unwrap_or("modal-3d.glb");
    let selected = app
        .dialog()
        .file()
        .add_filter("glTF Binary", &["glb"])
        .set_file_name(filename)
        .blocking_save_file();
    let Some(selected) = selected else {
        let _ = fs::remove_file(&source);
        return Ok(None);
    };
    let mut target = selected
        .into_path()
        .map_err(|error| format!("无法读取保存路径：{error}"))?;
    if target
        .extension()
        .and_then(|extension| extension.to_str())
        .map_or(true, |extension| !extension.eq_ignore_ascii_case("glb"))
    {
        target.set_extension("glb");
    }

    let expected = fs::metadata(&source)
        .map_err(|error| format!("无法读取导出缓存：{error}"))?
        .len();
    let copied = fs::copy(&source, &target).map_err(|error| format!("保存 GLB 失败：{error}"))?;
    if copied != expected {
        let _ = fs::remove_file(&target);
        return Err(format!(
            "GLB 写入不完整：预期 {expected} 字节，实际 {copied} 字节"
        ));
    }
    let _ = fs::remove_file(&source);
    Ok(Some(target.to_string_lossy().into_owned()))
}

#[tauri::command]
async fn agent_stop(app: tauri::AppHandle) -> Result<(), String> {
    async_runtime::spawn_blocking(move || agent_stop_blocking(&app))
        .await
        .map_err(|error| format!("桌面后台任务异常退出：{error}"))?
}

fn agent_stop_blocking(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<AgentState>();
    let mut state = state.0.lock().map_err(|_| "无法锁定本地代理状态")?;
    if let Some(mut process) = state.take() {
        stop_process(&mut process);
    }
    Ok(())
}
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AgentState::default())
        .invoke_handler(tauri::generate_handler![
            agent_start,
            agent_status,
            agent_stop,
            choose_local_sam_directory,
            app_diagnostics,
            reveal_app_data,
            export_save,
            credentials::credentials_status,
            credentials::credentials_save,
            credentials::credentials_clear
        ])
        .build(tauri::generate_context!())
        .expect("构建 modal-3D 客户端失败");

    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            let state = handle.state::<AgentState>();
            let process = state.0.lock().ok().and_then(|mut state| state.take());
            if let Some(mut process) = process {
                stop_process(&mut process);
            }
        }
    });
}
