use chrono::{DateTime, Utc};
use serde_json::{Map, Number, Value};
use std::{
    cmp::Ordering,
    env, fs,
    path::{Path, PathBuf},
    sync::Mutex,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, Runtime, State, WebviewWindow,
    WindowEvent,
};

mod names {
    pub const TOOL_NAME: &str = "ringer";
    pub const PRODUCT_NAME: &str = "Ringside";
    pub const BUNDLE_IDENTIFIER: &str = "com.jonedwards.ringside";
    pub const CONFIG_DIR_NAME: &str = TOOL_NAME;
    pub const CONFIG_FILE_NAME: &str = "config.toml";
    pub const ENV_VAR_PREFIX: &str = "RINGER";
    pub const STATE_DIR_NAME: &str = ".ringer";
    pub const MAIN_WINDOW_LABEL: &str = "main";
    pub const TRAY_ID: &str = "main-tray";
    pub const MENU_TOGGLE_ID: &str = "toggle";
    pub const MENU_VERSION_ID: &str = "version";
    pub const MENU_QUIT_ID: &str = "quit";
    pub const RUNS_EVENT: &str = "ringer-runs";
}

const DEFAULT_WIDTH: f64 = 360.0;
const DEFAULT_HEIGHT: f64 = 420.0;
const MIN_WIDTH: f64 = 280.0;
const MIN_HEIGHT: f64 = 220.0;
const MINI_STRIP_HEIGHT: f64 = 34.0;

#[derive(Default)]
struct LayoutState {
    collapsed: bool,
    expanded_size: Option<LogicalSize<f64>>,
    expanded_position: Option<LogicalPosition<f64>>,
}

#[tauri::command]
fn hide_window<R: Runtime>(window: WebviewWindow<R>) -> Result<(), String> {
    window.hide().map_err(|err| err.to_string())
}

#[tauri::command]
fn toggle_collapse<R: Runtime>(
    window: WebviewWindow<R>,
    layout: State<'_, Mutex<LayoutState>>,
) -> Result<bool, String> {
    let scale = window.scale_factor().map_err(|err| err.to_string())?;
    let mut layout = layout
        .lock()
        .map_err(|_| "layout lock poisoned".to_string())?;

    if layout.collapsed {
        let target_size = layout
            .expanded_size
            .unwrap_or(LogicalSize::new(DEFAULT_WIDTH, DEFAULT_HEIGHT));
        window
            .set_min_size(Some(LogicalSize::new(MIN_WIDTH, MIN_HEIGHT)))
            .map_err(|err| err.to_string())?;
        window
            .set_size(target_size)
            .map_err(|err| err.to_string())?;
        if let Some(position) = layout.expanded_position {
            window
                .set_position(position)
                .map_err(|err| err.to_string())?;
        }
        layout.collapsed = false;
        return Ok(false);
    }

    let size = window
        .outer_size()
        .map_err(|err| err.to_string())?
        .to_logical::<f64>(scale);
    let position = window
        .outer_position()
        .map_err(|err| err.to_string())?
        .to_logical::<f64>(scale);
    layout.expanded_size = Some(size);
    layout.expanded_position = Some(position);

    let strip_size = LogicalSize::new(size.width.max(MIN_WIDTH), MINI_STRIP_HEIGHT);
    window
        .set_min_size(Some(LogicalSize::new(MIN_WIDTH, MINI_STRIP_HEIGHT)))
        .map_err(|err| err.to_string())?;
    window.set_size(strip_size).map_err(|err| err.to_string())?;
    layout.collapsed = true;
    Ok(true)
}

/// Feature 3 (selectable views): resize to a named preset from the frontend's view-mode
/// toolbar, without fighting the mini-strip `toggle_collapse` bookkeeping.
#[tauri::command]
fn resize_main_window<R: Runtime>(
    window: WebviewWindow<R>,
    layout: State<'_, Mutex<LayoutState>>,
    width: f64,
    height: f64,
) -> Result<(), String> {
    let clamped_width = width.max(MIN_WIDTH);
    let clamped_height = height.max(MIN_HEIGHT);
    window
        .set_min_size(Some(LogicalSize::new(MIN_WIDTH, MIN_HEIGHT)))
        .map_err(|err| err.to_string())?;
    window
        .set_size(LogicalSize::new(clamped_width, clamped_height))
        .map_err(|err| err.to_string())?;
    let mut layout = layout
        .lock()
        .map_err(|_| "layout lock poisoned".to_string())?;
    layout.collapsed = false;
    layout.expanded_size = Some(LogicalSize::new(clamped_width, clamped_height));
    Ok(())
}

/// Feature 2 (embedded live artifact): read a Tier 0 HTML artifact ringer.py rendered to disk,
/// so the frontend can embed it via `<iframe srcdoc>` without relaxing the webview CSP or
/// touching the Tauri `asset:` protocol scope. Restricted to paths currently advertised by
/// ringer run state (never an arbitrary path from the frontend).
#[tauri::command]
fn read_artifact_html(path: String) -> Result<String, String> {
    let state_dir = load_state_dir();
    let requested = expand_path(&path);
    let canonical_state_dir = state_dir
        .canonicalize()
        .map_err(|err| format!("state dir unavailable: {err}"))?;
    let canonical_requested = requested
        .canonicalize()
        .map_err(|err| format!("artifact not found: {err}"))?;
    if !is_advertised_artifact_path(&canonical_state_dir, &canonical_requested) {
        return Err(
            "refusing to read an artifact path not advertised by ringer state".to_string(),
        );
    }
    fs::read_to_string(canonical_requested).map_err(|err| err.to_string())
}

fn is_advertised_artifact_path(canonical_state_dir: &Path, canonical_requested: &Path) -> bool {
    let canonical_runs_dir = match canonical_state_dir.join("runs").canonicalize() {
        Ok(path) if path.starts_with(canonical_state_dir) => path,
        _ => return false,
    };
    let entries = match fs::read_dir(&canonical_runs_dir) {
        Ok(entries) => entries,
        Err(_) => return false,
    };

    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
            continue;
        }
        let Ok(canonical_run_path) = path.canonicalize() else {
            continue;
        };
        if !canonical_run_path.starts_with(&canonical_runs_dir) {
            continue;
        }
        let Ok(data) = fs::read_to_string(canonical_run_path) else {
            continue;
        };
        let Ok(run) = serde_json::from_str::<Value>(&data) else {
            continue;
        };
        let Some(artifact_path) = run
            .get("artifact_path")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|path| !path.is_empty())
        else {
            continue;
        };
        let Ok(canonical_artifact_path) = expand_path(artifact_path).canonicalize() else {
            continue;
        };
        if canonical_artifact_path == canonical_requested {
            return true;
        }
    }

    false
}

/// Feature 5 (settings panel): plain JSON file, not UserDefaults (this is Tauri, not Swift),
/// so the same file could later be read by a Tier 0 HTML artifact for matching theme. Lives
/// alongside ringer's own state under `<state_dir>/ringside-settings.json`.
fn settings_path() -> PathBuf {
    load_state_dir().join("ringside-settings.json")
}

#[tauri::command]
fn load_settings() -> Result<Value, String> {
    let path = settings_path();
    match fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text).map_err(|err| err.to_string()),
        Err(_) => Ok(Value::Object(Map::new())),
    }
}

#[tauri::command]
fn save_settings(settings: Value) -> Result<(), String> {
    let path = settings_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let text = serde_json::to_string_pretty(&settings).map_err(|err| err.to_string())?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, text).map_err(|err| err.to_string())?;
    fs::rename(&tmp, &path).map_err(|err| err.to_string())?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(Mutex::new(LayoutState::default()))
        .invoke_handler(tauri::generate_handler![
            hide_window,
            toggle_collapse,
            resize_main_window,
            read_artifact_html,
            load_settings,
            save_settings
        ])
        .setup(|app| {
            debug_assert_eq!(names::BUNDLE_IDENTIFIER, "com.jonedwards.ringside");
            configure_main_window(app.handle());
            build_tray(app)?;
            start_state_poller(app.handle().clone());
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            names::MENU_TOGGLE_ID => toggle_main_window(app),
            names::MENU_QUIT_ID => app.exit(0),
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}

fn configure_main_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window(names::MAIN_WINDOW_LABEL) else {
        return;
    };

    let close_window = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = close_window.hide();
        }
    });
}

fn build_tray(app: &mut tauri::App) -> tauri::Result<()> {
    let version_label = format!("{} v{}", names::PRODUCT_NAME, env!("CARGO_PKG_VERSION"));
    let version = MenuItem::with_id(
        app,
        names::MENU_VERSION_ID,
        version_label.as_str(),
        false,
        None::<&str>,
    )?;
    let toggle = MenuItem::with_id(
        app,
        names::MENU_TOGGLE_ID,
        "Show/Hide HUD",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, names::MENU_QUIT_ID, "Quit", true, Some("q"))?;
    let version_separator = PredefinedMenuItem::separator(app)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[&version, &version_separator, &toggle, &separator, &quit],
    )?;
    let icon = Image::from_bytes(include_bytes!("../icons/32x32.png"))?;

    TrayIconBuilder::with_id(names::TRAY_ID)
        .tooltip(names::PRODUCT_NAME)
        .icon(icon)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                toggle_main_window(&tray.app_handle());
            }
        })
        .build(app)?;

    Ok(())
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(names::MAIN_WINDOW_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn toggle_main_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window(names::MAIN_WINDOW_LABEL) else {
        return;
    };
    if window.is_visible().unwrap_or(false) {
        let _ = window.hide();
    } else {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn start_state_poller(app: AppHandle) {
    let state_dir = load_state_dir();
    thread::spawn(move || loop {
        let runs = scan_runs(&state_dir);
        let _ = app.emit(names::RUNS_EVENT, runs);
        thread::sleep(Duration::from_secs(1));
    });
}

fn scan_runs(state_dir: &Path) -> Vec<Value> {
    let runs_dir = state_dir.join("runs");
    let now = SystemTime::now();
    let mut runs: Vec<SortableRun> = match fs::read_dir(runs_dir) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("json"))
            .filter_map(|path| parse_run(&path, now))
            .collect(),
        Err(_) => Vec::new(),
    };

    runs.sort_by(|left, right| {
        left.rank.cmp(&right.rank).then_with(|| {
            right
                .sort_ts
                .partial_cmp(&left.sort_ts)
                .unwrap_or(Ordering::Equal)
        })
    });
    runs.into_iter().map(|run| run.payload).collect()
}

struct SortableRun {
    rank: u8,
    sort_ts: f64,
    payload: Value,
}

fn parse_run(path: &Path, now: SystemTime) -> Option<SortableRun> {
    let metadata = fs::metadata(path).ok()?;
    let modified_at = metadata.modified().ok()?;
    let age = now
        .duration_since(modified_at)
        .unwrap_or_default()
        .as_secs_f64();
    let data = fs::read_to_string(path).ok()?;
    let mut payload: Value = serde_json::from_str(&data).ok()?;

    let object = payload.as_object_mut()?;
    let finished = bool_value(object.get("finished")).unwrap_or(false);
    let pid = int_value(object.get("pid"));
    let pid_alive = pid.map(process_is_alive).unwrap_or(false);

    let (state, rank) = if finished {
        if age > 60.0 {
            return None;
        }
        ("finished", 1)
    } else if pid_alive && age <= 30.0 {
        ("live", 0)
    } else {
        if age > 300.0 {
            return None;
        }
        ("died", 2)
    };

    let modified_ts = unix_ts(modified_at);
    let started_at = string_value(object.get("started_at"));
    let sort_ts = started_at
        .as_deref()
        .and_then(parse_datetime_ts)
        .unwrap_or(modified_ts);
    let task_elapsed = max_task_elapsed(object);
    let elapsed_s = if state == "live" {
        started_at
            .as_deref()
            .and_then(parse_datetime_ts)
            .map(|started_ts| (unix_ts(now) - started_ts).max(task_elapsed))
            .unwrap_or(task_elapsed)
    } else {
        task_elapsed
    };
    let (pass, fail, tokens) = counts(object);

    ensure_string(object, "run_id", fallback_run_id(path));
    ensure_string(object, "run_name", names::TOOL_NAME.to_string());
    ensure_string(object, "identity", "unknown".to_string());
    ensure_array(object, "tasks");
    object.insert("state".to_string(), Value::String(state.to_string()));
    object.insert("finished".to_string(), Value::Bool(finished));
    object.insert("mtime".to_string(), number(modified_ts));
    object.insert("elapsed_s".to_string(), number(elapsed_s));
    object.insert("pass".to_string(), Value::Number(Number::from(pass)));
    object.insert("fail".to_string(), Value::Number(Number::from(fail)));
    object.insert("tokens".to_string(), Value::Number(Number::from(tokens)));
    if !object.contains_key("pid") {
        object.insert("pid".to_string(), Value::Null);
    }
    if !object.contains_key("port") {
        object.insert("port".to_string(), Value::Null);
    }

    Some(SortableRun {
        rank,
        sort_ts,
        payload,
    })
}

fn load_state_dir() -> PathBuf {
    let config_path = config_path();
    if let Ok(data) = fs::read_to_string(config_path) {
        if let Ok(value) = data.parse::<toml::Value>() {
            if let Some(state_dir) = value.get("state_dir").and_then(|item| item.as_str()) {
                return expand_path(state_dir);
            }
        }
    }
    home_dir().join(names::STATE_DIR_NAME)
}

fn config_path() -> PathBuf {
    let env_key = format!("{}_CONFIG", names::ENV_VAR_PREFIX);
    if let Some(path) = env::var_os(env_key) {
        return expand_path(&path.to_string_lossy());
    }

    if let Some(config_home) = env::var_os("XDG_CONFIG_HOME") {
        return PathBuf::from(config_home)
            .join(names::CONFIG_DIR_NAME)
            .join(names::CONFIG_FILE_NAME);
    }

    home_dir()
        .join(".config")
        .join(names::CONFIG_DIR_NAME)
        .join(names::CONFIG_FILE_NAME)
}

fn expand_path(raw: &str) -> PathBuf {
    if raw == "~" {
        return home_dir();
    }
    if let Some(rest) = raw.strip_prefix("~/") {
        return home_dir().join(rest);
    }
    PathBuf::from(raw).components().collect()
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn ensure_string(object: &mut Map<String, Value>, key: &str, fallback: String) {
    let value = object
        .get(key)
        .and_then(|value| string_value(Some(value)))
        .filter(|text| !text.trim().is_empty())
        .unwrap_or(fallback);
    object.insert(key.to_string(), Value::String(value));
}

fn ensure_array(object: &mut Map<String, Value>, key: &str) {
    if !object.get(key).is_some_and(Value::is_array) {
        object.insert(key.to_string(), Value::Array(Vec::new()));
    }
}

fn max_task_elapsed(object: &Map<String, Value>) -> f64 {
    let task_elapsed = object
        .get("tasks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|task| task.as_object())
        .filter_map(|task| double_value(task.get("elapsed_s")))
        .fold(0.0, f64::max);
    double_value(object.get("elapsed_s"))
        .unwrap_or(0.0)
        .max(task_elapsed)
}

fn counts(object: &Map<String, Value>) -> (u64, u64, u64) {
    let summary = object.get("summary").and_then(Value::as_object);
    let totals = object.get("totals").and_then(Value::as_object);
    (
        lookup_count(object, summary, totals, "pass"),
        lookup_count(object, summary, totals, "fail"),
        lookup_count(object, summary, totals, "tokens"),
    )
}

fn lookup_count(
    object: &Map<String, Value>,
    summary: Option<&Map<String, Value>>,
    totals: Option<&Map<String, Value>>,
    key: &str,
) -> u64 {
    int_value(object.get(key))
        .or_else(|| summary.and_then(|summary| int_value(summary.get(key))))
        .or_else(|| totals.and_then(|totals| int_value(totals.get(key))))
        .unwrap_or(0)
        .max(0) as u64
}

fn fallback_run_id(path: &Path) -> String {
    path.file_stem()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .unwrap_or(names::TOOL_NAME)
        .to_string()
}

fn string_value(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(text) => Some(text.trim().to_string()),
        Value::Number(number) => Some(number.to_string()),
        Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
    }
}

fn bool_value(value: Option<&Value>) -> Option<bool> {
    match value? {
        Value::Bool(flag) => Some(*flag),
        Value::String(text) => match text.trim().to_ascii_lowercase().as_str() {
            "true" | "1" | "yes" => Some(true),
            "false" | "0" | "no" => Some(false),
            _ => None,
        },
        Value::Number(number) => number.as_i64().map(|raw| raw != 0),
        _ => None,
    }
}

fn int_value(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_u64().map(|raw| raw as i64)),
        Value::String(text) => text.trim().parse::<i64>().ok(),
        _ => None,
    }
}

fn double_value(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(number) => number.as_f64(),
        Value::String(text) => text.trim().parse::<f64>().ok(),
        _ => None,
    }
}

fn parse_datetime_ts(raw: &str) -> Option<f64> {
    DateTime::parse_from_rfc3339(raw)
        .map(|datetime| datetime.with_timezone(&Utc).timestamp_millis() as f64 / 1000.0)
        .ok()
}

fn unix_ts(time: SystemTime) -> f64 {
    time.duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn number(value: f64) -> Value {
    Number::from_f64(value)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

#[cfg(unix)]
fn process_is_alive(pid: i64) -> bool {
    if pid <= 0 {
        return false;
    }
    unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
}

#[cfg(windows)]
fn process_is_alive(pid: i64) -> bool {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };

    if pid <= 0 {
        return false;
    }
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid as u32);
        if handle.is_null() {
            return false;
        }
        let _ = CloseHandle(handle);
        true
    }
}
