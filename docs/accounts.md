# Accounts and private data

V2 serves a small trusted group on one private instance. The first visitor on a new instance creates the administrator. Administrators create additional usernames/passwords in Settings; public signup and email delivery are outside this release. Complete setup locally before making an instance remotely reachable.

Usernames are case-insensitive, stored lowercase, and contain3–32 ASCII letters, digits, underscores or hyphens. Passwords contain12–1024 characters and are stored using Werkzeug's supported password hashing. Login uses a revocable server-side token with a12-hour lifetime. Login/logout/password changes rotate CSRF state; cookies are HTTP-only and SameSite Lax. Failed logins are throttled.

Each account owns its cooking sessions, preferences, personal recipes and AWS connection. Another account's IDs return the same not-found response as unknown IDs. Shared global recipes are visible to everyone; global editing is an administrator action. Administrators cannot use a special API override to read another user's personal data or credentials.

## AWS vault

Your account password and AWS vault passphrase are separate. Initialize your vault in Settings, enter your own AWS connection and select a model. The server stores authenticated ciphertext under users/<server-user-id>/settings.vault in the data volume. The passphrase/key remains in server memory only, never beside ciphertext or in browser storage. Restart/logout locks the vault; unlock again explicitly. Forgotten vault passphrases are not recoverable through a password reset.

Account-facing cloud calls never fall back to environment AWS keys or another account's connection. Ollama remains an optional instance-local shared service. One conversational voice stream is available instance-wide; busy responses reveal no other cook's identity.

## Password changes and resets

Change your password in Settings using the current password. Administrators can reset an account password for the trusted group. Changing/resetting a password revokes affected sessions and locks the vault; it does not rewrite the independent encrypted vault. A stolen old cookie must not become valid after logout/reset.

### Local owner recovery

If no administrator can sign in, an operator with access to the Docker host can use the local `reset-password` command. It creates a random password in a new file with0600 permissions, resets only the named account and revokes all its sign-in sessions. It never displays the password in terminal output. There is no unauthenticated HTTP recovery endpoint.

Stop the app while recovering so the running process releases unlocked vaults and pending work. Keep the named volume. From the project root, choose an unused recovery-container name:

```sh
recovery_dir="$(mktemp -d)"
docker compose stop sous
docker compose run --name sous-account-recovery --no-deps sous flask --app app reset-password YOUR_USERNAME --password-file /tmp/new-password.txt
docker cp sous-account-recovery:/tmp/new-password.txt "$recovery_dir/password.txt"
docker rm sous-account-recovery
docker compose up -d sous
```

Open the private `password.txt` file locally, sign in, then choose your own password in Settings and delete the temporary file. Never commit or share it. Existing output files are refused; unknown usernames do not create accounts. This recovers sign-in access only; your AWS vault still requires its original passphrase.

## Existing V1 data

V2 adds ownership without assigning old single-user records to the first new account. Existing sessions/events/observations and the original settings.vault stay on disk but are not exposed through account APIs. Create your account and save your connection anew. This avoids silently granting legacy data to whoever completes setup. A deliberate import flow is separate future work; retain the data volume and its backup.

## Deployment boundary

Compose remains bound to localhost. Account support alone does not make the service ready for public hosting. A future remote installation needs an explicit hostname/proxy policy, HTTPS, Secure cookies and operational account recovery. No hosting, native iOS project or TestFlight submission is performed by this branch.

See [V2 specification](v2.md), [API contracts](v2-api.md), [settings](settings.md), and [Docker](docker.md).
