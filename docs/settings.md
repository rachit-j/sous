# Provider settings and credential vault

## Scope

On feature/ui-thinking-dots, add an in-app Settings view for AWS Bedrock region, AWS access key ID/secret access key, optional session token, model discovery/custom model ID, default provider/model, and snapshot interval. Interval defaults to10 seconds, accepts integer seconds1–15, and never permits overlapping analyses. Existing cooking controls, recipe snapshots and thinking dots remain intact. User explicitly selected passphrase unlock after server restart. No .env edits or host credential reads are needed for the new flow.

## Security contract

The app remains single-user and localhost-only. The browser sends write-only credentials through same-origin, CSRF-protected JSON requests. The server encrypts the settings payload with authenticated encryption and a passphrase-derived key; random salt and ciphertext persist in the existing Docker data volume, with restrictive file permissions and atomic writes. The passphrase/key never persists in cookies, browser storage, .env or a file alongside ciphertext. The server holds the unlock key only in memory; restarting or explicitly locking requires unlocking again. This protects offline stored data, not a compromised running machine or malicious local process. Forgotten passphrases cannot be recovered.

API responses, logs, observations and session records never include credentials or the passphrase. Safe settings responses expose only region/model, credential-presence flags, lock state and default provider/model. Secret inputs stay empty after load/save. Blank credential fields retain saved values; replacing AWS key ID/secret requires both together. Optional session token has an explicit remove control. Deleting the saved connection removes its encrypted credentials. Unexpected provider errors remain sanitized. Unlock attempts are throttled and request sizes/field lengths bounded.

Saved credentials go explicitly into a Boto3 session per request, never into process environment variables. A configured managed connection that is locked must fail clearly rather than silently use another credential source. Existing environment configuration stays compatible where no managed configuration supersedes it. Locking prevents new managed Bedrock calls; it cannot recall a request already sent to AWS. Manual cooking controls and timers remain available.

## API contract for implementation

All mutations require existing origin/CSRF protection. Settings responses use Cache-Control: no-store. HTTP errors follow the existing `{error: string}` shape, with 400 validation, 403 invalid unlock/CSRF, 409 locked/already initialized/conflict, 429 throttling, and 502 sanitized AWS discovery errors.

- GET /api/settings: `{vault: {initialized, locked}, bedrock: {region, model, has_credentials, has_session_token}, defaults: {provider, model}, sample_interval: 10}`. Locked settings expose no decrypted fields; UI prompts unlock. Nonsecret preferences (defaults and interval) remain available across restarts while the vault is locked. Additional safe source/configuration flags may be additive and documented.
- POST /api/settings/vault: `{passphrase}` initializes an empty vault once and unlocks it. Passphrase minimum12 characters, bounded maximum.
- POST /api/settings/unlock: `{passphrase}` unlocks existing ciphertext; generic wrong-passphrase/damaged-data error.
- POST /api/settings/lock: locks and discards in-memory key/decrypted settings.
- PUT /api/settings/bedrock: `{region, model?, access_key_id?, secret_access_key?, session_token?, clear_session_token?}`. Unlocked vault required. Blank secrets retain existing ones. Credential pair required on first save or replacement. Region required and bounded validated strings. Model may initially be blank so users can save credentials, discover candidates, then select one. Saves without invoking a model.
- DELETE /api/settings/bedrock: removes the stored connection. Unlocked vault required.
- PUT /api/settings/defaults: `{provider, model}` chooses defaults for future sessions. Unlocked vault required. Saved provider/model appears in GET /api/config defaults and corresponding provider default model. Existing sessions retain their selected provider/model.
- PUT /api/settings/preferences: `{sample_interval: integer}` persists the snapshot interval (1–15 seconds inclusive). No vault unlock required for this nonsecret preference; CSRF required. GET /api/config returns the saved interval. UI applies changes to the capture scheduler; previous analysis must finish before another starts. Lower intervals can increase cloud cost; show a concise note.
- GET /api/settings/bedrock/models: explicit discovery using saved, unlocked connection. Returns `{models: [{id, name, kind}], warning?}`. Foundation candidates require IMAGE input, TEXT output, active lifecycle; matching active inference profiles may also be returned. Paginate and deduplicate. Discovery is metadata only, never sends camera images. Model listing does not guarantee invoke permission or Converse compatibility. Custom model/profile IDs remain usable when list permissions fail.

## UI contract

Shared Cooking/Settings navigation switches internal views without reloading the cooking session. Leaving Cooking explicitly pauses guidance/cancels capture so camera images are not submitted while Settings is visible; returning keeps it paused until explicit resume. Setup shows a compact provider/model summary with a Settings link; existing provider/model controls can live in a disclosure for per-session overrides, preserving their IDs and generic recipe behavior. Settings contains initialize/unlock/lock controls, Bedrock connection form, discovered-model picker plus custom ID, and saved default provider/model. Do not imply saving credentials verifies AWS access. No real secrets in screenshots/tests, and no browser localStorage/sessionStorage for settings secrets.

## Verification

Opus implements bounded backend/provider/vault tests first; Luna implements UI against the observed API afterward. Root runs tests and reviews boundaries, then requests independent security review. Verify encrypted persistence and locked restart, wrong unlock/throttle, tampered data, no secret echo/storage/logging, CSRF and locked inference, explicit credential mapping, discovery pagination/filtering/errors, and preserved generic cooking flows. Browser tests use isolated WebKit under /tmp and fake AWS responses. Container restart tests use only the dedicated sous-verify project and synthetic credentials, never the user's normal vault. Live Bedrock requires user-entered credentials; do not read credentials or invoke paid inference automatically.

## Sources

- [Boto3 Bedrock model listing](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock/client/list_foundation_models.html)
- [Boto3 inference profiles](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock/client/list_inference_profiles.html)
- [Cryptography authenticated encryption and password-derived keys](https://cryptography.io/en/latest/fernet/)

See [API contract](api.md), [providers](providers.md), [design](design.md), [Docker](docker.md), [plan](../PLAN.md) and [validation](validation.md).
