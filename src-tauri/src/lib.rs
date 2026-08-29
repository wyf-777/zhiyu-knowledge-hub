use std::{net::TcpListener, sync::Mutex, time::Duration};

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use uuid::Uuid;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeInfo {
    phase: String,
    api_running: bool,
    base_url: Option<String>,
    token: Option<String>,
    service_version: Option<String>,
    protocol_version: Option<String>,
    message: String,
}

impl Default for RuntimeInfo {
    fn default() -> Self {
        Self {
            phase: "starting".into(),
            api_running: false,
            base_url: None,
            token: None,
            service_version: None,
            protocol_version: None,
            message: "正在启动本地知识库服务".into(),
        }
    }
}

#[derive(serde::Deserialize)]
struct HealthResponse {
    version: String,
    protocol_version: String,
}

#[derive(Default)]
struct RuntimeState {
    info: Mutex<RuntimeInfo>,
    child: Mutex<Option<CommandChild>>,
}

fn set_runtime_info(app: &AppHandle, next: RuntimeInfo) {
    let state = app.state::<RuntimeState>();
    if let Ok(mut info) = state.info.lock() {
        *info = next;
    };
}

fn stop_sidecar(app: &AppHandle) {
    let state = app.state::<RuntimeState>();
    let current = state
        .info
        .lock()
        .map(|info| info.clone())
        .unwrap_or_default();
    if let (Some(base_url), Some(token)) = (current.base_url, current.token) {
        let shutdown_request = std::thread::spawn(move || {
            reqwest::blocking::Client::builder()
                .timeout(Duration::from_millis(700))
                .build()
                .and_then(|client| {
                    client
                        .post(format!("{base_url}/api/v1/runtime/shutdown"))
                        .bearer_auth(token)
                        .send()
                })
        });
        let _ = shutdown_request.join();
        std::thread::sleep(Duration::from_millis(300));
    }
    if let Ok(mut child_slot) = state.child.lock() {
        if let Some(child) = child_slot.take() {
            let _ = child.kill();
        }
    };
}

async fn start_sidecar(app: AppHandle) -> Result<RuntimeInfo, String> {
    stop_sidecar(&app);
    set_runtime_info(&app, RuntimeInfo::default());

    let port = TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map_err(|error| format!("无法分配本地端口：{error}"))?
        .port();
    let token = Uuid::new_v4().simple().to_string();
    let base_url = format!("http://127.0.0.1:{port}");
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法确定应用数据目录：{error}"))?;
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("无法创建应用数据目录：{error}"))?;
    let port_argument = port.to_string();
    let data_dir_argument = data_dir.to_string_lossy().to_string();

    let sidecar = app
        .shell()
        .sidecar("knowledge-api")
        .map_err(|error| format!("无法创建本地服务进程：{error}"))?
        .args([
            "--port",
            &port_argument,
            "--token",
            &token,
            "--data-dir",
            &data_dir_argument,
        ]);

    let (mut events, child) = sidecar
        .spawn()
        .map_err(|error| format!("无法启动本地服务：{error}"))?;

    {
        let state = app.state::<RuntimeState>();
        let mut child_slot = state
            .child
            .lock()
            .map_err(|_| "本地服务状态锁定失败".to_string())?;
        *child_slot = Some(child);
    }

    let event_app = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if let CommandEvent::Terminated(payload) = event {
                let current = runtime_status_inner(&event_app);
                if current.phase != "stopping" {
                    set_runtime_info(
                        &event_app,
                        RuntimeInfo {
                            phase: "stopped".into(),
                            api_running: false,
                            base_url: None,
                            token: None,
                            service_version: current.service_version,
                            protocol_version: current.protocol_version,
                            message: format!("本地服务已退出（状态码 {:?}）", payload.code),
                        },
                    );
                }
                break;
            }
        }
    });

    let client = reqwest::Client::builder()
        .timeout(Duration::from_millis(700))
        .build()
        .map_err(|error| format!("无法创建健康检查客户端：{error}"))?;

    for _ in 0..50 {
        let response = client
            .get(format!("{base_url}/health"))
            .bearer_auth(&token)
            .send()
            .await;
        if let Ok(response) = response {
            if response.status().is_success() {
                if let Ok(health) = response.json::<HealthResponse>().await {
                    let info = RuntimeInfo {
                        phase: "running".into(),
                        api_running: true,
                        base_url: Some(base_url),
                        token: Some(token),
                        service_version: Some(health.version),
                        protocol_version: Some(health.protocol_version),
                        message: "本地知识库服务运行正常".into(),
                    };
                    set_runtime_info(&app, info.clone());
                    return Ok(info);
                }
            }
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    stop_sidecar(&app);
    let message = "本地服务启动超时，请重试".to_string();
    set_runtime_info(
        &app,
        RuntimeInfo {
            phase: "error".into(),
            message: message.clone(),
            ..RuntimeInfo::default()
        },
    );
    Err(message)
}

fn runtime_status_inner(app: &AppHandle) -> RuntimeInfo {
    let state = app.state::<RuntimeState>();
    state
        .info
        .lock()
        .map(|info| info.clone())
        .unwrap_or_else(|_| RuntimeInfo {
            phase: "error".into(),
            message: "无法读取本地服务状态".into(),
            ..RuntimeInfo::default()
        })
}

#[tauri::command]
fn runtime_status(state: State<'_, RuntimeState>) -> RuntimeInfo {
    state
        .info
        .lock()
        .map(|info| info.clone())
        .unwrap_or_else(|_| RuntimeInfo {
            phase: "error".into(),
            message: "无法读取本地服务状态".into(),
            ..RuntimeInfo::default()
        })
}

#[tauri::command]
async fn restart_knowledge_api(app: AppHandle) -> Result<RuntimeInfo, String> {
    start_sidecar(app).await
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            restart_knowledge_api
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = start_sidecar(handle.clone()).await {
                    let current = runtime_status_inner(&handle);
                    if current.phase != "error" {
                        set_runtime_info(
                            &handle,
                            RuntimeInfo {
                                phase: "error".into(),
                                message: error,
                                ..RuntimeInfo::default()
                            },
                        );
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Zhiyu desktop application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_sidecar(app_handle);
        }
    });
}
