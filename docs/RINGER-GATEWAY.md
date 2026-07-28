# Use Ringer inside Codex CLI

Ringer can sit between Codex CLI and the model provider for text-answer turns.
You keep typing in Codex CLI.
Ringer extracts the exact newest user message and leaves the old conversation
out of its new model request.

This gateway is one token-saving option. It does not replace the token-saver
skill or the habits described in that skill.

## What happens to a request

1. Codex CLI sends its normal request to Ringer on your Mac.
2. Ringer keeps the newest user message exactly as written.
3. Ringer ignores the older chat, old tool results, and the full tool list.
4. Ringer selects only relevant passages from the files you chose.
5. If local code or a previously saved, explicitly reviewed answer can do the
   work, Ringer returns the answer without calling a model.
6. If a model is needed, Ringer makes one call with the small packet and newest
   request. It does not retry a failed call.
7. The answer appears in the same Codex CLI window.

Ringer binds to `127.0.0.1` by default. It does not print request bodies,
authorization headers, or provider error bodies.

## Important current limit

The first version handles text-answer turns. It deliberately does not forward the
large tool list or old tool transcript. This makes it useful for research,
summaries, editing, classification, and other answer-producing turns. It is not
yet a drop-in replacement for a long Codex session that must
keep calling shell, browser, or file-editing tools.

Codex CLI compatibility is verified with the real client. The Anthropic
request shape has unit tests, but real Claude Code is not compatible yet:
Claude Code retries after the current local response. Do not use this gateway
as a Claude Code plan-limit fix. It also does not intercept the consumer Claude
web or desktop app.

## Start in local-only mode

Local-only mode proves that code recipes and exact accepted answers need no
provider call.

```bash
cd "/path/to/ringer"
python3 local_gateway.py
```

The server listens at `http://127.0.0.1:8790`.

Try the local calculator without any provider key:

```bash
curl -s http://127.0.0.1:8790/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"ringer-local","input":"Calculate 144 / 12"}'
```

The response header `X-Ringer-Route: local_code` means no model was called.
Ringer remembers deterministic code answers, so repeating the exact request
returns `X-Ringer-Route: accepted_cache`, also with no model call.

## Save one reviewed answer for exact reuse

Ringer never accepts a model answer automatically. After you review an answer,
put the exact request and approved answer in two text files:

```bash
python3 local_gateway.py \
  --accept-request-file /path/to/request.txt \
  --accept-answer-file /path/to/reviewed-answer.txt
```

Ringer saves that answer against the exact request and the exact source packet.
The next identical request returns the saved answer with no upstream call. A
changed request or changed selected source does not match.

## Add an OpenAI-compatible upstream

Set all four values. Ringer fails closed if any value is missing.

```bash
export RINGER_OPENAI_BASE_URL="https://api.openai.com/v1"
export RINGER_OPENAI_API_KEY="YOUR_PROVIDER_KEY"
export RINGER_OPENAI_CHEAP_MODEL="YOUR_CHEAP_MODEL"
export RINGER_OPENAI_STRONG_MODEL="YOUR_STRONG_MODEL"
python3 local_gateway.py
```

These settings create direct API calls. They do not promise to use a Codex
subscription allowance. Check how the credential is billed before using this
as a plan-limit fix.

### Point Codex CLI at Ringer

Add this to your user-level `~/.codex/config.toml`:

```toml
model = "ringer-local"
model_provider = "ringer"

[model_providers.ringer]
name = "Ringer local gateway"
base_url = "http://127.0.0.1:8790/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
```

Codex still owns the window. Ringer owns the decision about whether the next
request needs a provider call.

## Add an Anthropic upstream

```bash
export RINGER_ANTHROPIC_BASE_URL="https://api.anthropic.com/v1"
export RINGER_ANTHROPIC_API_KEY="YOUR_PROVIDER_KEY"
export RINGER_ANTHROPIC_CHEAP_MODEL="YOUR_CHEAP_MODEL"
export RINGER_ANTHROPIC_STRONG_MODEL="YOUR_STRONG_MODEL"
python3 local_gateway.py
```

The following is the intended configuration, not a working Claude Code setup
yet:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8790"
claude
```

This adapter implements unit-tested `/v1/messages` and
`/v1/messages/count_tokens` response shapes. The real Claude Code streaming
and retry contract still needs work. Provider keys stay in the Ringer process
and are not written to its database.

## Give Ringer source files

Ringer never treats the entire old conversation as a source. Tell it which
files or folders it may search:

```bash
export RINGER_GATEWAY_SOURCES="/path/to/project:/path/to/notes.md"
export RINGER_GATEWAY_STATE_FILES="/path/to/accepted-state.md"
python3 local_gateway.py
```

Use `:` between paths on macOS and Linux. Ringer reads the files locally and
sends only selected passages. The default packet limit is 16,000 bytes.

## Set hard limits

```bash
export RINGER_GATEWAY_MAX_PACKET_BYTES=16000
export RINGER_GATEWAY_MAX_FRESH_INPUT_TOKENS=12000
export RINGER_GATEWAY_MAX_REUSED_INPUT_TOKENS=5000
export RINGER_GATEWAY_MAX_OUTPUT_TOKENS=4000
```

The gateway always allows at most one upstream call per request. A missing
provider, oversized packet, provider error, or token-limit failure stops the
request. Ringer does not try another model after a failure.

## What gets counted

When a provider reports usage, Ringer records:

- fresh input tokens;
- reused or cached input tokens;
- cache-write input tokens when the provider reports them;
- output tokens;
- reasoning tokens when the provider reports them.

Local code and explicitly saved exact answers record zero upstream calls.
Ringer stores the counts and route in its local SQLite database. It does not
store provider keys there. It does not automatically save a model answer as
accepted.
