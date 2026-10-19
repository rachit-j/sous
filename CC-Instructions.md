# Claude Code delegation instructions for Codex

## Activation

Read and follow this file when the user asks you to use Claude, get a second opinion, or delegate coding work to Claude Code. Codex remains responsible for the final outcome.

This is a local CLI workflow, not an installed plugin. It requires Codex and an authenticated Claude Code CLI in the same execution environment, with access to the repository. A cloud session cannot assume access to a laptop's Claude installation.

For automatic discovery, add this line to the repository's AGENTS.md:
> Read CC-Instructions.md before delegating to Claude Code.

Do not modify AGENTS.md merely because you read this file; do so when setting up this workflow at the user's request.

## Operating defaults

- Delegate a concrete, bounded task: a second opinion, bug investigation, implementation, or review. Continue useful independent work while Claude runs when your execution tools support it.
- Use your judgment within the user's authorized scope. Do not request confirmation for every invocation or routine edit. An instruction to use this workflow permits ordinary task delegation to Claude.
- Claude receives only the prompt, accessible files, and its own session history. It does not automatically receive this Codex conversation. Supply the context it needs explicitly.
- Respect the user's selected model. Otherwise omit --model and --effort to preserve Claude's configured defaults.
- Prefer one foreground invocation initially. Use your execution tool's running-session support for long calls; do not invent background-job commands.
- Never invoke another coding agent recursively from Claude. Disable automatic cross-agent review loops for delegated runs.
- Treat Claude's output as advice or proposed work. Inspect the result, resolve disagreements, and verify changes before claiming completion.

## Preflight

Run from the intended repository directory:

```bash
command -v claude
claude --version
claude auth status
git status --short
```

For a non-Git project, skip Git commands and record the files you intend to change.

If Claude is missing or unauthenticated, report the specific blocker. Do not read credential files, print secrets, switch authentication providers, or install packages silently. If the user has authorized setup, follow the current official installation guidance; interactive authentication may require the user.

Check claude --help if a flag is rejected. Do not retry the same failing command repeatedly or quietly weaken its permissions.

## Write the task brief

Include these fields in every substantive handoff:

- Goal: the exact question or observable behavior wanted.
- Context: relevant facts, prior investigation, reproduction steps, errors, and important decisions.
- Scope: allowed files or modules; explicitly name user edits that must be preserved.
- Constraints: repository conventions, compatibility, dependencies, and expected behavior.
- Mode: read-only review/investigation OR bounded implementation.
- Acceptance: what evidence or checks establish success.
- Return: findings or changes, file references, checks performed, and unresolved issues.

Tell Claude to read the applicable repository instructions. Summarize relevant instructions in the brief too, including instructions outside Claude's accessible directory.

End the brief with:
> Do only this assigned task. Do not delegate to Codex or launch other coding agents. Do not commit, push, publish, deploy, or contact others. If blocked, describe the blocker and the smallest next step. Distinguish verified facts from hypotheses.

## Read-only review or investigation

Create a private temporary directory for the brief and output. Keep it outside the repository so it does not pollute the diff:

```bash
cc_run_dir="$(mktemp -d)"
cat > "$cc_run_dir/task.txt" <<'CLAUDE_TASK'
Review the relevant code for the task described below.
Mode: read-only. Do not edit any file or execute commands.
Goal: REPLACE WITH THE ACTUAL QUESTION.
Context: REPLACE WITH RELEVANT CONTEXT.
Scope: REPLACE WITH FILES OR MODULES.
Return only actionable findings, with severity, file references,
reasoning, and a suggested fix. State when you found no actionable issue.
Do not delegate to Codex or launch other coding agents.
CLAUDE_TASK

claude -p "Complete the task brief supplied on stdin." \
  --output-format json \
  --tools "Read,Glob,Grep" \
  --allowedTools "Read,Glob,Grep" \
  --disallowedTools "mcp__*" \
  --permission-mode dontAsk \
  --max-turns 20 \
  < "$cc_run_dir/task.txt" \
  > "$cc_run_dir/result.json" \
  2> "$cc_run_dir/stderr.log"
cc_exit_code=$?
```

Replace the placeholders before running. This limits Claude's built-in tools to file inspection and disables MCP tools. Tool restrictions are not an operating-system sandbox, and normal Claude startup can still load configured hooks and customizations.

For a diff review, Codex should capture the intended diff and append it to the brief before invocation. Include both staged and unstaged changes when reviewing the working tree; separately identify relevant untracked files. For branch review, determine the correct base and merge base rather than assuming main. Claude can inspect surrounding source with its read tools.

Do not auto-approve Bash merely to collect a diff; Codex can supply that input itself.

## Bounded implementation

Use the same invocation pattern, with a brief that names the exact files Claude may edit and the acceptance criteria. Replace both tool lists with:

```text
Read,Glob,Grep,Edit,Write
```

Codex runs tests and other commands after inspecting the changes. This provides a useful implementation workflow without giving Claude unrestricted shell execution.

If Claude needs a command to proceed, Codex may run that command itself and return the result in a follow-up. Add narrowly scoped Bash permissions only when necessary within the user's authorization, after checking the installed CLI's permission syntax. Do not use --dangerously-skip-permissions or bypassPermissions as a workaround.

For sequential work, Codex must stop editing the delegated files until Claude finishes. Do not run concurrent writers in the same checkout. If parallel implementation is needed, create an isolated worktree and explicitly transfer any required uncommitted context; a new worktree does not include existing uncommitted changes automatically.

Record the initial Git state. Never use reset, checkout, clean, stash, or an automatic patch reversal to discard changes when recovering from a failed run.

## Read results and resume

Inspect the process exit code, stderr, and the JSON payload. An exit code of zero alone does not prove that the requested task succeeded.

With jq available:

```bash
jq '{session_id, is_error, subtype, result}' "$cc_run_dir/result.json"
```

If jq is unavailable, parse JSON with Python or Node. Do not assume the file is valid JSON after a timeout or crash. Report incomplete output as incomplete.

Save the returned session_id with the task identity, repository path, and output paths in your working notes. For a follow-up, use that exact ID:

```bash
claude --resume "$cc_session_id" \
  -p "Address the follow-up brief supplied on stdin." \
  --output-format json \
  --tools "Read,Glob,Grep" \
  --allowedTools "Read,Glob,Grep" \
  --disallowedTools "mcp__*" \
  --permission-mode dontAsk \
  --max-turns 20 \
  < "$cc_run_dir/followup.txt" \
  > "$cc_run_dir/followup-result.json" \
  2> "$cc_run_dir/followup-stderr.log"
```

Set cc_session_id from the actual result, not from a guessed value. Reapply the appropriate tool lists on every call, including resumed implementation. Avoid --continue when more than one task may exist: it can pick the wrong session.

Use safely quoted files/stdin for long prompts. When invoking from Python or Node, use subprocess argument arrays and an explicit working directory; do not concatenate user content into shell commands.

## Long runs and recovery

- Retain the execution tool's session handle and poll it with reasonable intervals. Keep the user informed during extended work.
- Do not start a duplicate task just because output is quiet.
- If a run reaches its turn limit, inspect the partial work before deciding whether to resume. Increase the limit deliberately only when the task warrants it.
- If interrupted, inspect the working tree before continuing. Changes can exist even when there is no final result.
- Cancel only the process associated with this task using its execution handle. Never use broad process-name killing.
- Keep task artifacts until the result has been reviewed. Do not commit temporary briefs, output logs, or session metadata.

## Finish the task

For review: verify each meaningful finding against the actual code before applying a fix.

For implementation: inspect the diff against the recorded starting state, check scope and user edits, and run focused tests or other appropriate validation. Ask Claude a specific follow-up when a material issue remains; avoid endless reviewer/implementer loops.

Report the outcome briefly: what Claude contributed, what Codex verified, and any unresolved limitation. Do not claim tests ran unless their execution was observed. Complete the user's authorized task rather than stopping after forwarding Claude's response.

## Authentication and startup notes

Use the user's existing Claude Code authentication. Do not add --bare by default: current Claude documentation says bare mode skips subscription OAuth credentials and requires an API key or another supported provider configuration. It also changes which project instructions and customizations load.

The normal commands above retain the user's Claude configuration. If project hooks or plugins delegate back to Codex, resolve that recursion before launching a task. If stronger startup isolation is needed, consult the installed CLI's supported options and preserve the user's authentication choice.

## References

Command syntax checked against official documentation on 2026-10-19. Installed versions may differ.

- [Claude Code programmatic usage](https://code.claude.com/docs/en/headless)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) — optional future bridge; not required by this workflow.

