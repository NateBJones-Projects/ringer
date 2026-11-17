import { expect, mock, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'
import { attemptCells, cellWidth, config, fit, layout, parseRuns, sanitize, selectRun } from '../hooks/core'
import type { Run } from '../types'
// Captured response: api-runs-empty.json (the test sandbox cannot import JSON or read files).
const emptyText = "{\n  \"active\": {},\n  \"runs\": [],\n  \"update\": null\n}"
const empty = JSON.parse(emptyText)
// Captured response: api-runs-live.json (the test sandbox cannot import JSON or read files).
const liveText = "{\n  \"active\": {\n    \"referee-fixture-fleet-20261117T120657Z-p87166\": {\n      \"identity\": \"fixture-capture\",\n      \"pid\": 87166,\n      \"run_name\": \"referee-fixture-fleet\",\n      \"started_at\": \"2026-11-17T12:06:57.044012+00:00\",\n      \"workdir\": \"/tmp/ringer-fixtures/wd1\"\n    }\n  },\n  \"runs\": [\n    {\n      \"artifact_path\": null,\n      \"dashboard_port\": null,\n      \"elapsed_s\": 9.5,\n      \"fail\": 1,\n      \"finished\": false,\n      \"identity\": \"fixture-capture\",\n      \"live_path\": null,\n      \"max_parallel\": 3,\n      \"pass\": 3,\n      \"pid\": 87166,\n      \"port\": null,\n      \"report_path\": null,\n      \"report_ready\": false,\n      \"run_id\": \"referee-fixture-fleet-20261117T120657Z-p87166\",\n      \"run_name\": \"referee-fixture-fleet\",\n      \"started_at\": \"2026-11-17T12:06:57.044012+00:00\",\n      \"state\": \"live\",\n      \"summary\": null,\n      \"tasks\": [\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"grep -q hello out.txt || { echo FAIL: out.txt missing hello; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-fast/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 0.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-fast\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-fast/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.089991+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-fast\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: simulated failure\",\n          \"attempts\": 2,\n          \"check\": \"test -f never.txt || { echo FAIL: never.txt was not created; exit 1; }\",\n          \"check_output_tail\": \"FAIL: never.txt was not created\",\n          \"check_returncode\": 1,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 0.1,\n          \"engine\": \"mock\",\n          \"key\": \"fails-twice\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/fails-twice/worker.log\",\n          \"log_tail\": [\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"\",\n            \"[ringer.py] attempt 2 started 2026-11-17T12:06:57.126725+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL\",\n            \"\",\n            \"Previous attempt failed: [ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py '\\\"'\\\"'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL'\\\"'\\\"' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FAIL\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FAIL\",\n          \"status\": \"fail\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/fails-twice\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"FAIL\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 25; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": null,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 9.5,\n          \"engine\": \"mock\",\n          \"key\": \"slow-check\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/slow-check/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.095687+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"verifying\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/slow-check\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": null,\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 8; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/queued-after/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 8.0,\n          \"engine\": \"mock\",\n          \"key\": \"queued-after\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/queued-after/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.124173+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/queued-after\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 3; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-later/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 3.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-later\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-later/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.154192+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-later\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        }\n      ],\n      \"tokens\": 0,\n      \"totals\": {\n        \"done\": 4,\n        \"fail\": 1,\n        \"pass\": 3,\n        \"running\": 1,\n        \"tokens\": 0\n      }\n    }\n  ],\n  \"update\": null\n}"
const live = JSON.parse(liveText)
// Captured response: api-runs-killed.json (the test sandbox cannot import JSON or read files).
const killedText = "{\n  \"active\": {\n    \"referee-fixture-killed-20261117T120724Z-p87227\": {\n      \"identity\": \"fixture-capture\",\n      \"pid\": 87227,\n      \"run_name\": \"referee-fixture-killed\",\n      \"started_at\": \"2026-11-17T12:07:24.463159+00:00\",\n      \"workdir\": \"/tmp/ringer-fixtures/wd2\"\n    }\n  },\n  \"runs\": [\n    {\n      \"artifact_path\": null,\n      \"dashboard_port\": null,\n      \"elapsed_s\": 7.4,\n      \"fail\": 0,\n      \"finished\": false,\n      \"identity\": \"fixture-capture\",\n      \"live_path\": null,\n      \"max_parallel\": 2,\n      \"pass\": 1,\n      \"pid\": 87227,\n      \"port\": null,\n      \"report_path\": null,\n      \"report_ready\": false,\n      \"run_id\": \"referee-fixture-killed-20261117T120724Z-p87227\",\n      \"run_name\": \"referee-fixture-killed\",\n      \"started_at\": \"2026-11-17T12:07:24.463159+00:00\",\n      \"state\": \"live\",\n      \"summary\": null,\n      \"tasks\": [\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 120; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": null,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 7.4,\n          \"engine\": \"mock\",\n          \"key\": \"long-check\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd2/long-check/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:07:24.512139+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"verifying\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd2/long-check\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": null,\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-killed-20261117T120724Z-p87227/quick-pass/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 0.0,\n          \"engine\": \"mock\",\n          \"key\": \"quick-pass\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd2/quick-pass/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:07:24.515543+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd2/quick-pass\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        }\n      ],\n      \"tokens\": 0,\n      \"totals\": {\n        \"done\": 1,\n        \"fail\": 0,\n        \"pass\": 1,\n        \"running\": 1,\n        \"tokens\": 0\n      }\n    },\n    {\n      \"artifact_path\": null,\n      \"dashboard_port\": null,\n      \"elapsed_s\": 25.0,\n      \"fail\": 1,\n      \"finished\": true,\n      \"identity\": \"fixture-capture\",\n      \"live_path\": null,\n      \"max_parallel\": 3,\n      \"pass\": 4,\n      \"pid\": 87166,\n      \"port\": null,\n      \"report_path\": null,\n      \"report_ready\": false,\n      \"run_id\": \"referee-fixture-fleet-20261117T120657Z-p87166\",\n      \"run_name\": \"referee-fixture-fleet\",\n      \"started_at\": \"2026-11-17T12:06:57.044012+00:00\",\n      \"state\": \"finished\",\n      \"summary\": {\n        \"fail\": 1,\n        \"pass\": 4,\n        \"tokens\": 0\n      },\n      \"tasks\": [\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"grep -q hello out.txt || { echo FAIL: out.txt missing hello; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-fast/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 0.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-fast\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-fast/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.089991+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-fast\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: simulated failure\",\n          \"attempts\": 2,\n          \"check\": \"test -f never.txt || { echo FAIL: never.txt was not created; exit 1; }\",\n          \"check_output_tail\": \"FAIL: never.txt was not created\",\n          \"check_returncode\": 1,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 0.1,\n          \"engine\": \"mock\",\n          \"key\": \"fails-twice\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/fails-twice/worker.log\",\n          \"log_tail\": [\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"\",\n            \"[ringer.py] attempt 2 started 2026-11-17T12:06:57.126725+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL\",\n            \"\",\n            \"Previous attempt failed: [ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py '\\\"'\\\"'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL'\\\"'\\\"' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FAIL\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FAIL\",\n          \"status\": \"fail\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/fails-twice\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"FAIL\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 25; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/slow-check/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 25.0,\n          \"engine\": \"mock\",\n          \"key\": \"slow-check\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/slow-check/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.095687+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/slow-check\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 8; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/queued-after/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 8.0,\n          \"engine\": \"mock\",\n          \"key\": \"queued-after\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/queued-after/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.124173+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/queued-after\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 3; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-later/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 3.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-later\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-later/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.154192+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-later\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        }\n      ],\n      \"tokens\": 0,\n      \"totals\": {\n        \"done\": 5,\n        \"fail\": 1,\n        \"pass\": 4,\n        \"running\": 0,\n        \"tokens\": 0\n      }\n    }\n  ],\n  \"update\": null\n}"
const killed = JSON.parse(killedText)
// Captured response: api-runs-finished.json (the test sandbox cannot import JSON or read files).
const finishedText = "{\n  \"active\": {},\n  \"runs\": [\n    {\n      \"artifact_path\": null,\n      \"dashboard_port\": null,\n      \"elapsed_s\": 25.0,\n      \"fail\": 1,\n      \"finished\": true,\n      \"identity\": \"fixture-capture\",\n      \"live_path\": null,\n      \"max_parallel\": 3,\n      \"pass\": 4,\n      \"pid\": 87166,\n      \"port\": null,\n      \"report_path\": null,\n      \"report_ready\": false,\n      \"run_id\": \"referee-fixture-fleet-20261117T120657Z-p87166\",\n      \"run_name\": \"referee-fixture-fleet\",\n      \"started_at\": \"2026-11-17T12:06:57.044012+00:00\",\n      \"state\": \"finished\",\n      \"summary\": {\n        \"fail\": 1,\n        \"pass\": 4,\n        \"tokens\": 0\n      },\n      \"tasks\": [\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"grep -q hello out.txt || { echo FAIL: out.txt missing hello; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-fast/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 0.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-fast\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-fast/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.089991+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-fast\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: simulated failure\",\n          \"attempts\": 2,\n          \"check\": \"test -f never.txt || { echo FAIL: never.txt was not created; exit 1; }\",\n          \"check_output_tail\": \"FAIL: never.txt was not created\",\n          \"check_returncode\": 1,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 0.1,\n          \"engine\": \"mock\",\n          \"key\": \"fails-twice\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/fails-twice/worker.log\",\n          \"log_tail\": [\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"\",\n            \"[ringer.py] attempt 2 started 2026-11-17T12:06:57.126725+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL\",\n            \"\",\n            \"Previous attempt failed: [ringer.py] attempt 1 started 2026-11-17T12:06:57.093231+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py '\\\"'\\\"'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FAIL'\\\"'\\\"' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 1 exited rc=1\",\n            \"FAIL: never.txt was not created. Fix it.' < /dev/null\",\n            \"mock-worker: simulated failure\",\n            \"[ringer.py] attempt 2 exited rc=1\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FAIL\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FAIL\",\n          \"status\": \"fail\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/fails-twice\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"FAIL\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 25; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/slow-check/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 25.0,\n          \"engine\": \"mock\",\n          \"key\": \"slow-check\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/slow-check/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.095687+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/slow-check\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 8; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/queued-after/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 8.0,\n          \"engine\": \"mock\",\n          \"key\": \"queued-after\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/queued-after/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.124173+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/queued-after\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        },\n        {\n          \"activity\": \"mock-worker: wrote 1 file(s): out.txt\",\n          \"attempts\": 1,\n          \"check\": \"sleep 3; grep -q hello out.txt || { echo FAIL; exit 1; }\",\n          \"check_output_tail\": \"\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [\n            {\n              \"bytes\": 6,\n              \"name\": \"out.txt\",\n              \"path\": \"/tmp/ringer-fixtures/state/artifacts/deliverables/referee-fixture-fleet-20261117T120657Z-p87166/passes-later/out.txt\"\n            }\n          ],\n          \"elapsed_s\": 3.0,\n          \"engine\": \"mock\",\n          \"key\": \"passes-later\",\n          \"log_path\": \"/tmp/ringer-fixtures/wd1/passes-later/worker.log\",\n          \"log_tail\": [\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"[ringer.py] attempt 1 started 2026-11-17T12:06:57.154192+00:00\",\n            \"[ringer.py] engine: mock\",\n            \"[ringer.py] command: /usr/bin/python3 /opt/ringer/engines/mock_worker.py 'You are the deterministic mock worker. Write only the file in this block.\",\n            \"MOCK_FILE: out.txt\",\n            \"hello\",\n            \"MOCK_END' < /dev/null\",\n            \"mock-worker: wrote 1 file(s): out.txt\",\n            \"[ringer.py] attempt 1 exited rc=0\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"You are the deterministic mock worker. Write only the file in this block.\\nMOCK_FILE: out.txt\\nhello\\nMOCK_END\",\n          \"spec_short\": \"You are the deterministic mock worker. Write only the file in this block. MOCK_FILE: out.txt hello MOCK_END\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-fixtures/wd1/passes-later\",\n          \"timeout_s\": 900,\n          \"tokens\": null,\n          \"verdict\": \"PASS\",\n          \"verified\": \"\"\n        }\n      ],\n      \"tokens\": 0,\n      \"totals\": {\n        \"done\": 5,\n        \"fail\": 1,\n        \"pass\": 4,\n        \"running\": 0,\n        \"tokens\": 0\n      }\n    }\n  ],\n  \"update\": null\n}"
const finished = JSON.parse(finishedText)
// Captured response: api-runs-codex-finished.json (the test sandbox cannot import JSON or read files).
const codexText = "{\n  \"active\": {},\n  \"runs\": [\n    {\n      \"artifact_path\": \"/tmp/ringer-example/artifact_path\",\n      \"dashboard_port\": 8787,\n      \"elapsed_s\": 406.7,\n      \"fail\": 1,\n      \"finished\": true,\n      \"identity\": \"orchestrator\",\n      \"lane-2\": 8787,\n      \"live_path\": \"/tmp/ringer-example/live_path\",\n      \"max_parallel\": 2,\n      \"pass\": 1,\n      \"pid\": 59116,\n      \"report_path\": \"/tmp/ringer-example/report_path\",\n      \"report_ready\": true,\n      \"run_id\": \"codex-example-20261116T075318Z-p59116\",\n      \"run_name\": \"codex-example\",\n      \"started_at\": \"2026-11-16T07:53:18.522756+00:00\",\n      \"state\": \"finished\",\n      \"summary\": {\n        \"fail\": 1,\n        \"pass\": 1,\n        \"tokens\": 159372\n      },\n      \"tasks\": [\n        {\n          \"activity\": \"<activity scrubbed>\",\n          \"attempts\": 2,\n          \"check\": \"<check scrubbed>\",\n          \"check_output_tail\": \"/tmp/ringer-example/check_output_tail\",\n          \"check_returncode\": 1,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 382.6,\n          \"engine\": \"codex\",\n          \"key\": \"lane-1\",\n          \"log_path\": \"/tmp/ringer-example/log_path\",\n          \"log_tail\": [\n            \"<log_tail scrubbed>\",\n            \"<log_tail scrubbed>\",\n            \"<log_tail scrubbed>\"\n          ],\n          \"log_tail_full\": [\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"gpt-6-astra\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"<spec scrubbed>\",\n          \"spec_short\": \"<spec_short scrubbed>\",\n          \"status\": \"fail\",\n          \"taskdir\": \"/tmp/ringer-example/taskdir\",\n          \"timeout_s\": 3600,\n          \"tokens\": 78607,\n          \"verdict\": \"FAIL\",\n          \"verified\": \"<verified scrubbed>\"\n        },\n        {\n          \"activity\": \"<activity scrubbed>\",\n          \"attempts\": 1,\n          \"check\": \"<check scrubbed>\",\n          \"check_output_tail\": \"<check_output_tail scrubbed>\",\n          \"check_returncode\": 0,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 406.7,\n          \"engine\": \"codex\",\n          \"key\": \"lane-2\",\n          \"log_path\": \"/tmp/ringer-example/log_path\",\n          \"log_tail\": [\n            \"<log_tail scrubbed>\",\n            \"<log_tail scrubbed>\",\n            \"<log_tail scrubbed>\"\n          ],\n          \"log_tail_full\": [\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"/tmp/ringer-example/log_tail_full\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\"\n          ],\n          \"max_attempts\": 2,\n          \"model\": \"gpt-6-astra\",\n          \"report_paths\": {},\n          \"setup_error\": null,\n          \"spec\": \"<spec scrubbed>\",\n          \"spec_short\": \"<spec_short scrubbed>\",\n          \"status\": \"pass\",\n          \"taskdir\": \"/tmp/ringer-example/taskdir\",\n          \"timeout_s\": 4500,\n          \"tokens\": 80765,\n          \"verdict\": \"PASS\",\n          \"verified\": \"<verified scrubbed>\"\n        }\n      ],\n      \"tokens\": 159372,\n      \"totals\": {\n        \"done\": 2,\n        \"fail\": 1,\n        \"pass\": 1,\n        \"running\": 0,\n        \"tokens\": 159372\n      }\n    }\n  ],\n  \"update\": null\n}"
const codex = JSON.parse(codexText)

// Captured response: api-runs-legacy.json (the test sandbox cannot import JSON or read files).
const legacyText = "{\n  \"active\": {},\n  \"runs\": [\n    {\n      \"artifact_path\": \"/tmp/ringer-example/artifact_path\",\n      \"dashboard_port\": 8787,\n      \"elapsed_s\": 324.5,\n      \"fail\": 1,\n      \"finished\": true,\n      \"identity\": \"orchestrator\",\n      \"live_path\": \"/tmp/ringer-example/live_path\",\n      \"max_parallel\": 1,\n      \"pass\": 0,\n      \"pid\": 50709,\n      \"port\": 8787,\n      \"report_path\": \"/tmp/ringer-example/report_path\",\n      \"report_ready\": true,\n      \"run_id\": \"legacy-example-20260705T120000Z-p4242\",\n      \"run_name\": \"legacy-example\",\n      \"started_at\": \"2026-07-30T18:13:47.467471+00:00\",\n      \"state\": \"finished\",\n      \"summary\": {\n        \"fail\": 1,\n        \"pass\": 0,\n        \"tokens\": 111553\n      },\n      \"tasks\": [\n        {\n          \"activity\": \"<activity scrubbed>\",\n          \"attempts\": 2,\n          \"check\": \"<check scrubbed>\",\n          \"check_output_tail\": \"<check_output_tail scrubbed>\",\n          \"check_returncode\": 1,\n          \"check_timed_out\": false,\n          \"children\": 0,\n          \"deliverable_notes\": [],\n          \"deliverables\": [],\n          \"elapsed_s\": 324.5,\n          \"engine\": \"codex\",\n          \"key\": \"lane-1\",\n          \"log_path\": \"/tmp/ringer-example/log_path\",\n          \"log_tail\": [\n            \"\",\n            \"<log_tail scrubbed>\",\n            \"<log_tail scrubbed>\"\n          ],\n          \"log_tail_full\": [\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\",\n            \"\",\n            \"<log_tail_full scrubbed>\",\n            \"<log_tail_full scrubbed>\"\n          ],\n          \"model\": \"\",\n          \"report_paths\": {},\n          \"spec\": \"<spec scrubbed>\",\n          \"spec_short\": \"<spec_short scrubbed>\",\n          \"status\": \"fail\",\n          \"taskdir\": \"/tmp/ringer-example/taskdir\",\n          \"timeout_s\": 3000,\n          \"tokens\": 111553,\n          \"verdict\": \"FAIL\",\n          \"verified\": \"<verified scrubbed>\"\n        }\n      ],\n      \"tokens\": 111553,\n      \"totals\": {\n        \"done\": 1,\n        \"fail\": 1,\n        \"pass\": 0,\n        \"running\": 0,\n        \"tokens\": 111553\n      }\n    }\n  ],\n  \"update\": null\n}"

type Engine = Parameters<TestBody>[0]
type On = Parameters<TestBody>[1]
const NOW = Date.parse('2026-11-17T12:08:00Z')
const CFG = config({})
const PANE = {
  plugin: 'referee', component: 'Pane', requestId: 'referee', surface: 'terminal',
  props: { title: 'Referee', isFocused: false, bodyColumns: 90, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

function textOf(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (!node || typeof node !== 'object') return ''
  return ((node as { children?: unknown[] }).children ?? []).map(textOf).join('')
}
function rowsOf(node: unknown): string[] {
  return ((node as { children?: unknown[] }).children ?? []).filter(Boolean).map(textOf)
}
async function setup($: Engine, on: On, data: unknown = liveText, now = NOW, initialFailure = false) {
  const clock = mock.clock(on, { now })
  let payload = typeof data === 'string' ? data : JSON.stringify(data)
  let reject = initialFailure
  let open = false
  let requests = 0
  let status: string | undefined
  let requestUrl = ''
  on('command.register', () => ({ value: { command: 'referee' } }))
  on('ui.open', () => { open = true; return { value: { isPlaced: true } } })
  on('ui.close', () => { open = false; return { value: undefined } })
  on('ui.panes', () => ({ value: open ? [{ id: 'referee', title: 'Referee', isShown: true, isFocused: false, isPlaced: true }] : [] }))
  on('ui.status', (_, e) => { status = e.text; return { value: undefined } })
  on('http.fetch', (_, e) => {
    requests++
    requestUrl = e.url
    if (reject) throw new Error('ECONNREFUSED')
    return { value: { ok: true, status: 200, headers: {}, text: payload } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  return { clock, fail: () => { reject = true }, replace: (next: unknown) => { payload = JSON.stringify(next); reject = false },
    requests: () => requests, status: () => status, url: () => requestUrl }
}
async function drawn($: Engine) {
  const ui = await $.ui.mount(PANE)
  const tree = await ui.drawn()
  await ui.unmount()
  return rowsOf(tree).join('\n')
}

for (const [name, fixture, expected, now] of [
  ['empty', emptyText, 'no live run', NOW],
  ['live', liveText, 'LIVE · referee-fixture-fleet', NOW],
  ['killed', killedText, 'LIVE · referee-fixture-killed', NOW],
  ['finished', finishedText, 'last run · FINISHED', NOW],
  ['codex finished', codexText, 'tokens 159,372', Date.parse('2026-11-16T08:01:00Z')],
] as const) {
  test(`${name} fixture reaches the pane through HTTP`, async ($, on) => {
    await setup($, on, fixture, now)
    const text = await drawn($)
    expect(text).toContain(expected)
    if (name === 'codex finished') expect(text).toContain('gpt-6-astra')
    if (name === 'finished') expect(text).toContain('Receipt')
  })
}

test('failed attempts remain two red cells; verifying is not a pass', async ($, on) => {
  await setup($, on)
  const ui = await $.ui.mount(PANE)
  const failed = await ui.find({ type: 'Text', text: /fails-twice fail/ })
  expect(failed?.text).toContain('■■')
  const run = parseRuns(JSON.stringify(live)).runs[0]!
  expect(attemptCells(run.tasks.find(t => t.key === 'fails-twice')!, false)).toEqual([
    { text: '■', color: 'error' }, { text: '■', color: 'error' },
  ])
  expect((await ui.find({ type: 'Text', text: /slow-check/ }))?.text).toContain('verifying □')
  expect(rowsOf(await ui.drawn()).join('\n')).toContain('FAIL: never.txt was not created')
  await ui.unmount()
})

test('a dead orchestrator is STOPPED in error colour, never running', async ($, on) => {
  const data = JSON.parse(JSON.stringify(live))
  delete data.active[data.runs[0].run_id]
  await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('STOPPED')
  expect(text).toContain('saved: verifying')
  expect(text).not.toContain('running')
  const lines = layout({ ...data, lastSuccess: NOW, error: null }, NOW, CFG, 'dock', 90)
  expect(lines[0]![0]!.color).toBe('error')
})

test('unknown tokens are a dash and missing models leave the engine alone', async ($, on) => {
  const data = JSON.parse(JSON.stringify(live))
  delete data.runs[0].tasks[0].model
  data.runs[0].tokens = null
  await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('tokens -')
  expect(text).not.toContain('tokens 0')
  expect(text).toContain('mock 1/2')
  expect(text).not.toContain('mock/')
})

test('unreachable Ringside gives the port and the start command', async ($, on) => {
  const world = await setup($, on, empty)
  world.fail()
  await world.clock.advance(2000)
  const text = await drawn($)
  expect(text).toContain('Ringside not reachable on :8700')
  expect(text).toContain('./ringer.py hud --no-open')
})

test('untrusted names, keys, models, statuses and check output are sanitized', async ($, on) => {
  const data = JSON.parse(JSON.stringify(live))
  data.runs[0].run_name = '\u001b[31munsafe\u0007'
  data.runs[0].tasks[1].key = '\u001b]8;;https://evil.test\u0007bad\u001b]8;;\u0007'
  data.runs[0].tasks[1].model = '\u001b[32mmodel\u0007'
  data.runs[0].tasks[1].check_output_tail = 'first line\n\u001b[31mcheck\u0007\n  \n'
  await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('unsafe')
  expect(text).not.toContain('\u001b')
  expect(text).not.toContain('\u0007')
  expect(text).not.toContain('evil.test')
  expect(text).toContain('bad: check')
  expect(text).not.toContain('first line')
})

for (const [name, fixture, now] of [
  ['empty', emptyText, NOW], ['live', liveText, NOW], ['killed', killedText, NOW],
  ['finished', finishedText, NOW], ['codex', codexText, Date.parse('2026-11-16T08:01:00Z')],
] as const) {
  test(`${name}: every placement, surface and width fits`, async ($, on) => {
    await setup($, on, fixture, now)
    for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
      for (const placement of ['dock', 'inline', 'widget'] as const) {
        for (const bodyColumns of [24, 40, 60, 90]) {
          const ui = await $.ui.mount({ ...PANE, surface, props: { ...PANE.props, placement, bodyColumns } })
          const rows = rowsOf(await ui.drawn())
          expect(rows.length).toBeGreaterThan(0)
          for (const row of rows) expect(cellWidth(row) <= bodyColumns).toBe(true)
          if (placement === 'inline') expect(rows.length <= 8).toBe(true)
          if (placement === 'widget') expect(rows.length).toBe(1)
          await ui.unmount()
        }
      }
    }
  })
}

test('newest active live run wins; completed runs expire after thirty minutes', () => {
  const data = parseRuns(liveText)
  const fleet = data.runs[0]!
  const newer = { ...fleet, run_id: 'newer', started_at: '2026-11-17T12:07:50Z' }
  const multiple = { runs: [fleet, newer], active: { ...data.active, newer: {} } }
  expect(selectRun(multiple, NOW)).toEqual({ run: newer, more: 1, last: false })
  expect(layout({ ...multiple, lastSuccess: NOW, error: null }, NOW, CFG, 'dock', 90).map(line => line.map(p => p.text).join('')).join('\n')).toContain('+1 more live')
  const done = parseRuns(finishedText)
  const ended = Date.parse(done.runs[0]!.started_at!) + done.runs[0]!.elapsed_s! * 1000
  expect(selectRun(done, ended + 1800000).last).toBe(true)
  expect(selectRun(done, ended + 1800001).run).toBeUndefined()
  expect(selectRun({ ...multiple, runs: [...done.runs, ...multiple.runs] }, NOW).run?.run_id).toBe('newer')
})

test('stale data keeps its verdicts and is marked after three poll intervals', async ($, on) => {
  const world = await setup($, on)
  world.fail()
  await world.clock.advance(6000)
  expect(await drawn($)).not.toContain('STALE')
  await world.clock.advance(2000)
  expect(await drawn($)).toContain('STALE')
  expect(await drawn($)).toContain('fails-twice fail')
  expect(world.status()).toContain('STALE')
  world.replace(live)
  await world.clock.advance(2000)
  expect(await drawn($)).not.toContain('STALE')
})

test('status line clears when the run becomes idle', async ($, on) => {
  const world = await setup($, on)
  expect(world.status()).toContain('referee · LIVE')
  world.replace(empty)
  await world.clock.advance(2000)
  expect(world.status()).toBeUndefined()
})

test('closed pane with status disabled stops fetching; commands open and summarize', { options: { statusLine: false, openOnStart: false, port: 8765, pollMs: 1000 } }, async ($, on) => {
  const world = await setup($, on)
  expect(world.requests()).toBe(0)
  await $.command.run({ command: 'referee', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 140 }, args: 'open' })
  expect(world.url()).toBe('http://127.0.0.1:8765/api/runs')
  expect((await $.command.run({ command: 'referee', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 140 }, args: 'status' })).text).toContain('referee-fixture-fleet')
  await world.clock.advance(1000)
  expect(world.requests()).toBe(2)
  await $.command.run({ command: 'referee', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 140 }, args: 'close' })
  await world.clock.advance(3000)
  expect(world.requests()).toBe(2)
  expect(world.status()).toBeUndefined()
})

test('unknown task status is literal, never derived from worker exit code', async ($, on) => {
  const data = JSON.parse(JSON.stringify(live))
  data.runs[0].tasks[0].status = 'review-needed'
  await setup($, on, data)
  expect(await drawn($)).toContain('passes-fast review-needed')
})

test('invalid response contract is visible and never replaced by guessed liveness', async ($, on) => {
  const data = JSON.parse(JSON.stringify(live))
  delete data.active
  await setup($, on, data)
  expect(await drawn($)).toContain('Ringside response missing required fields')
  expect(await drawn($)).not.toContain('LIVE')
})

test('wide Unicode text fits; unknown placement is one glance line', () => {
  expect(sanitize('\u009b31mred\u0007')).toBe('red')
  const run: Run = { ...parseRuns(JSON.stringify(live)).runs[0]!, run_name: '漢字🙂'.repeat(50) }
  const snapshot = { runs: [run], active: live.active, lastSuccess: NOW, error: null }
  for (const columns of [24, 40, 60, 90]) {
    for (const line of layout(snapshot, NOW, CFG, 'dock', columns)) expect(cellWidth(line.map(p => p.text).join('')) <= columns).toBe(true)
    expect(layout(snapshot, NOW, CFG, 'future', columns).length).toBe(1)
    expect(cellWidth(fit([{ text: '漢字🙂'.repeat(50) }], columns).map(p => p.text).join('')) <= columns).toBe(true)
  }
})

test('inline prioritizes failed and active lanes and counts omitted lanes', () => {
  const run = parseRuns(JSON.stringify(live)).runs[0]!
  run.tasks = [...run.tasks, ...Array.from({ length: 10 }, (_, i) => ({ ...run.tasks[0]!, key: `extra-${i}` }))]
  const rows = layout({ runs: [run], active: live.active, lastSuccess: NOW, error: null }, NOW, CFG, 'inline', 90).map(line => line.map(p => p.text).join(''))
  expect(rows.length).toBe(8)
  expect(rows[1]).toContain('fails-twice')
  expect(rows[2]).toContain('slow-check')
  expect(rows[7]).toBe('+9 more')
})

for (const bodyRows of [3, 4, 6]) {
  test(`inline accounts for every lane within ${bodyRows} body rows`, async ($, on) => {
    const world = await setup($, on, liveText)
    const data = parseRuns(liveText)
    for (const stale of [false, true]) {
      if (stale) {
        world.fail()
        await world.clock.advance(8000)
      }
      const ui = await $.ui.mount({ ...PANE, props: {
        ...PANE.props, placement: 'inline', bodyColumns: 80,
        scroll: { offset: 0, bodyRows },
      } })
      const rows = rowsOf(await ui.drawn())
      const expected = layout({ ...data, lastSuccess: NOW, error: stale ? 'unreachable' : null },
        NOW + (stale ? 8000 : 0), CFG, 'inline', 80, bodyRows)
        .map(line => line.map(part => part.text).join(''))
      expect(rows).toEqual(expected)
      expect(rows.length <= bodyRows).toBe(true)
      const drawn = data.runs[0]!.tasks.filter(task => rows.some(row => row.startsWith(`+ ${task.key} `) ||
        row.startsWith(`x ${task.key} `) || row.startsWith(`· ${task.key} `))).length
      const more = rows[rows.length - 1]?.match(/^\+(\d+) more$/)
      expect(drawn + Number(more?.[1] ?? 0)).toBe(data.runs[0]!.tasks.length)
      await ui.unmount()
    }
  })
}

test('six lanes in six body rows reserve the last row for two omitted lanes', () => {
  const data = parseRuns(liveText)
  const run = data.runs[0]!
  run.tasks = Array.from({ length: 6 }, (_, i) => ({ ...run.tasks[0]!, key: `lane-${i}` }))
  const rows = layout({ ...data, lastSuccess: NOW, error: null }, NOW, CFG, 'inline', 80, 6)
    .map(line => line.map(part => part.text).join(''))
  expect(rows.length).toBe(6)
  expect(rows.filter(row => row.startsWith('+ lane-')).length).toBe(4)
  expect(rows[5]).toBe('+2 more')
})

test('tiny inline bodies keep the omitted count visible even with cached errors', () => {
  const data = parseRuns(liveText)
  const snapshot = { ...data, lastSuccess: NOW, error: 'unreachable' as const }
  for (const bodyRows of [0, 1, 2]) {
    const rows = layout(snapshot, NOW + 8000, CFG, 'inline', 80, bodyRows)
      .map(line => line.map(part => part.text).join(''))
    expect(rows.length).toBe(bodyRows)
    if (bodyRows) expect(rows[bodyRows - 1]).toBe(`+${data.runs[0]!.tasks.length} more`)
  }
})


test('an unreachable first poll shows recovery instructions', async ($, on) => {
  await setup($, on, emptyText, NOW, true)
  const text = await drawn($)
  expect(text).toContain('Ringside not reachable on :8700')
  expect(text).toContain('./ringer.py hud --no-open')
})

test('clear keeps polling; exiting cancels the timer and clears status', async ($, on) => {
  const world = await setup($, on)
  const end = { sessionId: 'test-session', resume: { id: 'test-session' } }
  await $.session.end({ ...end, reason: 'clear' })
  await world.clock.advance(2000)
  expect(world.requests()).toBe(2)
  await $.session.end({ ...end, reason: 'prompt_input_exit' })
  await world.clock.advance(4000)
  expect(world.requests()).toBe(2)
  expect(world.status()).toBeUndefined()
})

test('a died run is STOPPED even when listed as active', async ($, on) => {
  const data = JSON.parse(liveText)
  data.runs[0].state = 'died'
  await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('STOPPED')
  expect(text).toContain('saved: verifying')
  expect(text).not.toContain('running')
})

test('a status-looking state cannot bypass active membership', () => {
  const data = parseRuns(liveText)
  data.runs[0]!.state = 'LIVE'
  expect(selectRun(data, NOW).run).toBeUndefined()
})


test('legacy fixture draws attempts without a maximum or a contract error', async ($, on) => {
  const run = JSON.parse(legacyText).runs[0]
  await setup($, on, legacyText, Date.parse(run.started_at) + run.elapsed_s * 1000)
  const text = await drawn($)
  expect(text).toContain('legacy-example')
  expect(text).toContain('lane-1 fail ■■ codex 2 5:24 tok 111,553')
  expect(text).not.toContain('2/')
  expect(text).not.toContain('Ringside response missing required fields')
})

test('a malformed run beside a live run adds a warning without hiding the live run', async ($, on) => {
  const data = { ...live, runs: [{ run_id: 'broken', tasks: [] }, ...live.runs] }
  const world = await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('LIVE · referee-fixture-fleet')
  expect(text).toContain('1 unreadable runs skipped')
  expect(text).not.toContain('Ringside response missing required fields')
  const lines = layout({ ...parseRuns(JSON.stringify(data)), lastSuccess: NOW, error: null }, NOW, CFG, 'dock', 90)
  expect(lines.flat().find(p => p.text === '1 unreadable runs skipped')?.color).toBe('warning')
  world.replace(live)
  await world.clock.advance(2000)
  expect(await drawn($)).not.toContain('unreadable runs skipped')
})

test('an old stuck-live run behind a newer finished run leaves the pane idle', async ($, on) => {
  const old = { ...live.runs[0], started_at: '2026-07-05T12:00:00Z' }
  const data = { runs: [...JSON.parse(finishedText).runs, old], active: {} }
  const later = NOW + 3600000
  expect(selectRun(parseRuns(JSON.stringify(data)), later).run).toBeUndefined()
  await setup($, on, data, later)
  expect(await drawn($)).toContain('no live run')
  expect(await drawn($)).not.toContain('STOPPED')
})

test('a stuck-live run first in server order draws STOPPED regardless of start time', async ($, on) => {
  const old = { ...live.runs[0], started_at: '2026-07-05T12:00:00Z' }
  const data = { runs: [old, ...JSON.parse(finishedText).runs], active: {} }
  const later = NOW + 3600000
  expect(selectRun(parseRuns(JSON.stringify(data)), later).run?.run_id).toBe(old.run_id)
  await setup($, on, data, later)
  expect(await drawn($)).toContain('STOPPED · referee-fixture-fleet')
})

test('absent optional fields draw dashes, engine alone, and no excerpt', async ($, on) => {
  const data = JSON.parse(liveText)
  const run = data.runs[0]
  delete run.elapsed_s
  delete run.tokens
  delete run.started_at
  run.tasks = [{ key: 'minimal', status: 'fail', engine: 'codex', attempts: 2 }]
  await setup($, on, data)
  const text = await drawn($)
  expect(text).toContain('LIVE · referee-fixture-fleet')
  expect(text).toContain('- · 0 pass 1 fail 0 running · tokens -')
  expect(text).toContain('minimal fail ■■ codex 2 - tok -')
  expect(text).not.toContain('minimal:')
  expect(text).not.toContain('undefined')
  expect(text).not.toContain('NaN')
})

test('only required run and task fields determine unreadable runs', () => {
  const minimal = { run_id: 'minimal', run_name: 'minimal', state: 'live', tasks: [{ key: 'task', status: 'fail' }] }
  const bad = [null, {}, { ...minimal, run_id: 1 }, { ...minimal, run_name: null },
    { ...minimal, state: null }, { ...minimal, tasks: {} },
    { ...minimal, tasks: [null] }, { ...minimal, tasks: [{ key: 'task' }] },
    { ...minimal, tasks: [{ key: 1, status: 'pass' }] }]
  const data = parseRuns(JSON.stringify({ active: { minimal: {} }, runs: [...bad, minimal] }))
  expect(data.runs.length).toBe(1)
  expect(data.skippedRuns).toBe(bad.length)
  const text = layout({ ...data, lastSuccess: NOW, error: null }, NOW, CFG, 'dock', 90).map(line => line.map(p => p.text).join('')).join('\n')
  expect(text).toContain('task fail  - - - tok -')
  expect(text).not.toContain('NaN')
})

for (const body of ['not JSON', '{}', '{"runs":[]}', '{"active":{},"runs":{}}', '{"active":[],"runs":[]}']) {
  test(`invalid response envelope shows the contract error: ${body}`, async ($, on) => {
    await setup($, on, body)
    expect(await drawn($)).toContain('Ringside response missing required fields')
  })
}
