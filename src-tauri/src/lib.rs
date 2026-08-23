mod agent;
mod credentials;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(agent::AgentState::default())
        .invoke_handler(tauri::generate_handler![
            agent::agent_start,
            agent::agent_status,
            agent::agent_stop,
            credentials::credentials_status,
            credentials::credentials_save,
            credentials::credentials_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building modal-3D Client");

    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            agent::shutdown(&handle.state::<agent::AgentState>());
        }
    });
}
