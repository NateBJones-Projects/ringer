use std::{env, fs, path::PathBuf};

fn main() {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let repo_dir = manifest_dir.parent().expect("hud has a parent repo");
    let dashboard_dir = repo_dir.join("dashboard");
    let dist_dir = manifest_dir.join("dist");
    // Direct cargo builds must embed the same frontend as the Tauri sync hook.
    let sources = [
        (dashboard_dir.join("ringside.html"), "index.html"),
        (dashboard_dir.join("ringside.css"), "ringside.css"),
        (dashboard_dir.join("ringside.js"), "ringside.js"),
        (
            dashboard_dir.join("assets/ringside-mark.svg"),
            "assets/ringside-mark.svg",
        ),
        (
            dashboard_dir.join("assets/ringside-live.svg"),
            "assets/ringside-live.svg",
        ),
        (
            dashboard_dir.join("assets/ringside-attention.svg"),
            "assets/ringside-attention.svg",
        ),
        (manifest_dir.join("frontend/hud.js"), "hud.js"),
    ];
    for (source, target) in sources {
        println!("cargo:rerun-if-changed={}", source.display());
        let destination = dist_dir.join(target);
        fs::create_dir_all(destination.parent().expect("asset has parent"))
            .expect("create dist dir");
        fs::copy(source, destination).expect("copy Ringside frontend");
    }

    tauri_build::build();
}
