import json
import threading
from datetime import datetime, timezone

from ringer import ArtifactConfig, StateWriter


def test_state_writer_flush_serializes_atomic_replacement(tmp_path):
    state_dir = tmp_path / "state"
    writer = StateWriter(
        run_id="concurrent-flush",
        run_name="concurrent-flush",
        identity="test",
        state_dir=state_dir,
        engines={},
        started_at=datetime.now(timezone.utc),
        runtimes=[],
        lock=threading.RLock(),
        artifact=ArtifactConfig(
            enabled=False,
            out_template=str(state_dir / "artifacts" / "{run_id}.html"),
            report_template=str(state_dir / "artifacts" / "{run_id}-report.html"),
            index_out=state_dir / "artifacts" / "index.html",
        ),
    )
    barrier = threading.Barrier(8)
    errors = []

    def flush_repeatedly():
        try:
            barrier.wait()
            for _ in range(10):
                writer.flush()
        except Exception as exc:  # captured so the parent thread can assert
            errors.append(exc)

    threads = [threading.Thread(target=flush_repeatedly) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert json.loads(writer.path.read_text())["run_id"] == "concurrent-flush"
