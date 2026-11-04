/* Native-only transport and window chrome for the shared Ringside frontend. */
(() => {
  const tauri = window.__TAURI__;
  if (!tauri) return;
  const invoke = tauri.core.invoke;
  let listener = null;
  window.ringsideNative = {
    serverURL: invoke("hud_server_url"),
    readLibrary: () => invoke("read_artifact_library"),
    readArtifact: (path) => invoke("read_artifact_html", { path }),
    artifactURL: (path) => tauri.core.convertFileSrc(path),
    readLog: (path) => invoke("read_worker_log", { path }),
    resize: (width, height) => invoke("resize_main_window", { width, height }),
    hide: () => invoke("hide_window"),
    onRuns: (callback) => {
      listener = callback;
    },
  };
  tauri.event.listen("ringer-runs", (event) => {
    if (Array.isArray(event.payload)) listener?.(event.payload);
  });
  // Drag only the chrome; selecting text, scrolling previews and clicking tasks are normal.
  document.addEventListener("mousedown", (event) => {
    if (
      event.button !== 0 ||
      !event.target.closest("[data-tauri-drag-region]") ||
      event.target.closest("button,input,select,a")
    )
      return;
    tauri.window
      .getCurrentWindow()
      .startDragging()
      .catch(() => {});
  });
})();
