# GitHub Actions and host Nginx

Sous deploys to one Linux x86-64 server. Host Nginx terminates HTTPS and forwards to the existing loopback port, `127.0.0.1:5050`. The app still uses one container, one Gunicorn worker, eight threads and persistent named storage.

## Workflow

[CI and publish](../.github/workflows/ci.yml) runs on pushes to `main` or `feature/**`, pull requests targeting `main` or `feature/github-actions`, and manual dispatch. It installs Python 3.12 dependencies, runs unit and synthetic localhost WebSocket tests, checks JavaScript syntax, validates Compose and smoke-tests the actual runtime image. Browser, container-persistence and live-provider suites remain opt-in; CI makes no paid model calls and uses no physical camera/microphone.

Successful main runs publish the exact smoke-tested image to `ghcr.io/rachit-j/sous:<full-commit-sha>`. Branch/PR runs never log into GHCR, publish or deploy. Actions uses its built-in `GITHUB_TOKEN`; no registry publishing PAT is needed. Third-party actions are pinned to commit SHAs.

Deployment is disabled until `DEPLOY_ENABLED=true`. The production job serializes deploys, skips superseded main commits, uploads release-specific Compose files and [deploy.sh](../deploy/deploy.sh), and runs the latter over SSH. It pulls the immutable registry digest and waits for `/healthz`. Nginx needs no per-deploy reload because the loopback address is stable.

## Prepare the server once

For an Ubuntu/Debian amd64 server with systemd, [setup-server.sh](../deploy/setup-server.sh) automates dependencies, the deployment user/public key, `/opt/sous/.env`, host Nginx, browser password protection, and Certbot HTTPS/renewal. Point DNS at the server and allow inbound ports80/443 before running. SSH must already work; firewall/SSH-daemon configuration is not changed.

Generate a dedicated key **on your own computer**, keep its private half there for the GitHub secret, and copy only its `.pub` file and the setup script onto the server:

```bash
ssh-keygen -t ed25519 -f ./sous-deploy -N '' -C sous-github-actions
scp ./sous-deploy.pub ./setup-server.sh YOUR_USER@YOUR_SERVER:/tmp/
```

Then on the server:

```bash
sudo bash /tmp/setup-server.sh \
  --domain sous.example.com \
  --email you@example.com \
  --ssh-public-key-file /tmp/sous-deploy.pub
```

The script prompts for a browser-login password and any certificate account/terms consent. Defaults: deployment user `sous-deploy`, browser login `sous`, app port5050. Use `--ssh-host`/`--ssh-port` if the address Actions must SSH to differs from the web domain or port22. It prints the GitHub variables and server public host-key entry; put the matching **private** deployment key into GitHub's `DEPLOY_SSH_KEY` secret, never into chat or Git. Existing `.env` contents and browser password files are retained. If a settings file already exists, ensure its trusted hosts/port match this site. The deployment user gets Docker-group access, which grants control over the host through Docker. The script does not deploy Sous, configure GitHub settings, or log into GHCR. The steps below also document manual setup.

1. Install Docker Engine, a recent Docker Compose v2 with `up --wait`, Nginx, Bash, and `flock` (util-linux). Allow the deployment SSH user to run Docker and write `/opt/sous`. No host Python or repository checkout is needed.
2. Create `/opt/sous/.env`, owned by that user with mode `600`. It must exist, even if most providers are configured later through Settings. Add:

   ```dotenv
   SOUS_PORT=5050
   SOUS_TRUSTED_HOSTS=sous.example.com,localhost,127.0.0.1,[::1]
   ```

   Replace the hostname. Keep loopback hosts for health checks. Put optional provider variables from [.env.example](../.env.example) here, or configure the encrypted vault through the app. Credentials never go into the image, workflow or release files. The production override enables `SOUS_BEHIND_PROXY=1` and secure session cookies.
3. Set up a dedicated SSH deployment key. Add its public key to the deployment user's `authorized_keys`. Verify the server's SSH host-key fingerprint through a trusted channel; save the corresponding `known_hosts` line for GitHub. Do not disable host-key checking. Nonstandard SSH ports use `[hostname]:port` in known_hosts.
4. Ensure the server can pull the GHCR package. New packages may be private even when the repository is public. Either make this package public in GitHub's package settings, or log the deployment user into GHCR once with a `read:packages` credential. Do not store that credential in Git or Actions deployment arguments.
5. Point your domain at the server, issue its TLS certificate, and adapt [the Nginx example](../deploy/nginx.conf.example). The example assumes a certificate already exists. Install it in Nginx's HTTP configuration, run `sudo nginx -t`, then reload Nginx. Match the upstream port if changing `SOUS_PORT`.
6. Configure site access control covering HTTP APIs and WebSockets before exposing Sous. This is a shared single-cook application without user accounts; the vault passphrase is not site authentication. Keep port 5050 bound to loopback. HTTPS is needed for remote browser camera/microphone access.

Nginx overwrites forwarded scheme/IP and preserves Host. ProxyFix trusts exactly one proxy only in deployment mode; `X-Forwarded-Host` is not trusted. Do not enable proxy mode on a directly exposed app port. Extra proxies require deliberate trust configuration.

## GitHub settings

Create an environment named `production`. Set `DEPLOY_ENABLED` as a repository variable; the other settings can be repository or production-environment variables:

| Variable | Value |
| --- | --- |
| `DEPLOY_ENABLED` | `true`, only after server setup is ready |
| `DEPLOY_HOST` | Server DNS name or IPv4 address |
| `DEPLOY_USER` | SSH deployment username |
| `DEPLOY_PORT` | Optional; defaults to `22` |
| `DEPLOY_PATH` | Optional; defaults to `/opt/sous`; absolute path with letters, digits, slash, underscore or hyphen |
| `DEPLOY_PROJECT` | Optional; defaults to `sous`; must match the existing Compose project when retaining data |

`DEPLOY_ENABLED` must be a **repository variable**, since the job condition is evaluated before environment variables become available. Other settings can be production-environment variables. Add production environment secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_SSH_KEY` | Dedicated private SSH key, including its BEGIN/END lines |
| `DEPLOY_KNOWN_HOSTS` | Verified server known_hosts entry |

Publishing requires GitHub Actions/package policies to allow `GITHUB_TOKEN` package writes. This workflow builds `linux/amd64`; ARM servers need a separate image build configuration.

## Persistence, rollback and restarts

The base [Compose file](../compose.yaml) and [production override](../compose.production.yaml) mount the same `sous-instance` volume. Keep `DEPLOY_PROJECT` consistent: for project `sous`, the default volume is `sous_sous-instance`. Before migrating an existing install, inspect its actual Compose project/volume identity and back up its data. Changing the project name can create a fresh volume. Never use `down -v` to deploy.

Each successful release is kept at `/opt/sous/releases/<sha>` and selected by `/opt/sous/current`. A failed health check restores the previous **managed** release when one exists. The first deployment has no managed rollback target; an existing local-only installation needs its own migration backup. Rollback changes the image/configuration, not database contents or schema. Release directories and images are retained for manual cleanup after verification.

There can be a brief interruption while Compose replaces the app. The existing 220-second shutdown grace period remains. Restarting locks the credential vault, pauses monitoring and ends voice streams; unlock Settings and resume afterward.

`/healthz` is a lightweight liveness check without DB/provider calls; a healthy response does not verify TLS, Nginx access control, model credentials or voice connectivity. After initial deployment, check the real HTTPS site, manual controls, camera permission and voice through Nginx.

Until this branch is merged, only branch CI can run. The main-only publication/deployment path activates after merge and server configuration; adding these files does not itself deploy a server.
