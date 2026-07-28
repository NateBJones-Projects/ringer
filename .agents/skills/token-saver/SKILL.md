---
name: token-saver
description: Reduce token use while working inside Codex or Claude. Use when a user hits plan limits, works in a long chat, asks questions across large files, wants to lower AI cost, or wants an existing workflow to use fewer tokens. Prefer local code, select only relevant passages, continue from the accepted result plus the new change, load tools only when needed, choose the least expensive capable model, keep answers to the requested length, limit repairs, and count every model call.
---

# Token saver

## Know what this skill can and cannot save

Apply these rules to the work that follows.

This skill cannot erase the input already sent to start the current Codex or
Claude turn. The model call that loaded this skill has already begun. Use the
Ringer gateway when the request must be reduced or redirected before the main
model sees it.

The skill is still useful without that gateway. It can prevent the current
model from loading whole files, opening unused tools, repeating the transcript
in worker prompts, calling an expensive model for simple work, producing an
unasked-for essay, or entering a costly retry loop.

Ringer is optional. Use the strategies below directly inside Codex or Claude
when no gateway is installed.

## Keep the human's normal workflow

Do this work yourself. Do not ask the human to run these scripts, choose the
source files, create a state file, summarize the old chat, start a new chat, or
learn Ringer.

For each request:

1. Decide whether it continues the current result or starts unrelated work.
2. Find likely sources from attached paths, named files, the current project,
   and local search results. Use `rg --files` and `rg -l` with the meaningful
   words from the request when the source is not obvious.
3. Run the passage selector yourself. With no explicit source, give it the
   narrowest useful project root and let it select locally.
4. When the user accepts a result or asks for a change that keeps the rest,
   save the current result automatically under `.token-saver/` in the working
   project. Pick a short task-specific filename. Do not save secrets.
5. Build follow-up work from that saved result plus the latest requested
   change. Do not ask the human to restate the work.
6. Replace the saved result after the next version is accepted. Keep one
   current result, not a growing history.

If the user rejects a result, do not promote it to accepted state. If there is
no accepted result yet, select the minimum source material and complete the
request normally.

## Use these strategies in order

Resolve `/absolute/path/to/token-saver` from the active skill location that
Codex or Claude provides. Do not ask the human for that path.

1. **Try local code before another model.** Use `rg`, `jq`, a parser, a
   formatter, a test, a database query, or a short deterministic script for
   exact and repeatable work. Examples include counting, sorting, finding
   exact text, extracting known fields, calculating, converting formats,
   comparing files, and validating output. Do not ask a model to imitate a
   command.

2. **Read only the passages needed for the request.** Search first. Do not
   load every file or paste a whole transcript merely because it might contain
   the answer. For large text sources, run this yourself:

   ```bash
   python3 /absolute/path/to/token-saver/scripts/select_context.py \
     --request "What did we decide about Wednesday?" \
     --source /absolute/path/to/transcript.txt \
     --max-packet-bytes 12000 \
     --output /tmp/context-packet.txt \
     --report /tmp/context-packet-report.json
   ```

   When no source was named, use `--root` with the narrowest likely directory:

   ```bash
   python3 /absolute/path/to/token-saver/scripts/select_context.py \
     --request-file /tmp/current-request.txt \
     --root /absolute/path/to/project \
     --max-packet-bytes 12000 \
     --output /tmp/context-packet.txt
   ```

   Send the resulting packet to the model instead of the full source. If the
   report says needed information was skipped or the packet lacks the answer,
   select a second bounded passage. Do not fall back to loading everything.

3. **Continue from the accepted result, not the whole conversation.** Save
   the version the user accepted yourself:

   ```bash
   python3 /absolute/path/to/token-saver/scripts/state_delta.py save \
     --state /absolute/path/to/current-state.json \
     --accepted-file /absolute/path/to/accepted-result.md
   ```

   For the next change, make a prompt from that result and the new request:

   ```bash
   python3 /absolute/path/to/token-saver/scripts/state_delta.py packet \
     --state /absolute/path/to/current-state.json \
     --change-file /absolute/path/to/new-change.txt \
     --max-packet-bytes 16000 \
     --output /tmp/change-packet.txt
   ```

   Replace the saved result after the user accepts a new version. Do not add
   the transcript, rejected drafts, reasoning, or old tool output to this
   state file.

4. **Load tools only when the job needs them.** Do not inspect every
   connector, schema, skill, or tool description at the start. Load the one
   tool needed for the next action. Stop loading tools when the answer can be
   completed from local files or selected passages.

5. **Use the least expensive capable path.**

   - Return a saved result when the same answer is already available.
   - Use local code for exact work.
   - Use a smaller or cheaper model for bounded extraction, classification,
     formatting, and simple rewrites when the host supports model choice.
   - Use a fresh strong worker for difficult judgment. Give it the current
     request, the selected passages, and the accepted result only.
   - Keep the work in the current strong model only when it already has useful
     context that would cost more to rebuild than to reuse.

   Do not call a model merely to decide which model to call.

6. **Make discounted reuse work when background truly must repeat.** Keep the
   shared background exactly unchanged and put the new assignment after it.
   This can qualify the repeated part for a provider's lower reused-input
   price. Remove irrelevant background first. Discounted input is still
   input, so do not use reuse as an excuse to carry a large chat forever.

7. **Match the answer to the request.** If the user asks for a sentence,
   return a sentence. If no length is given, answer directly and stop when the
   job is complete. Do not add a process diary, repeated summary, or
   unrequested options.

8. **Allow one bounded repair.** Give a failed check and the smallest needed
   source to one repair attempt. Never retry a token-limit or usage-limit
   failure. Stop, reduce the input, choose a cheaper path, or wait for the
   limit to reset.

## Count the whole job

Count every model call used for planning, selecting, answering, checking, and
repairing. Record:

- fresh input tokens;
- reused or cached input tokens;
- output tokens;
- number of model calls;
- number of retries; and
- model used for each call.

Add the calls together. Moving tokens from Codex to Claude, a worker, or a
smaller model is not a token saving unless the combined total falls. If a host
does not report a number, write `unavailable`; do not invent it.

Compare the full job with the normal path. Keep the change only when the
answer still works and the combined token use falls.
