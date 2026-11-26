#!/usr/bin/env bash
# Prepare a Debian/Ubuntu amd64 server for Sous; does not deploy the app.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: sudo bash setup-server.sh --domain sous.example.com --email you@example.com \
  --ssh-public-key-file ./sous-deploy.pub [--deploy-user sous-deploy] \
  [--auth-user sous] [--ssh-host server.example.com] [--ssh-port 22]

Requires Ubuntu/Debian with systemd, domain DNS pointing at this server,
and public ports 80/443 reachable for certificate issuance. SSH must already work.
Installs Docker/Compose when absent, Nginx, Certbot and site password protection.
Prompts for a site password and any certificate terms. Existing .env and passwords
are preserved. Does not change firewall/SSH settings or start/update Sous.
HELP
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
domain=''
email=''
public_key_file=''
ssh_host=''
deploy_user=sous-deploy
auth_user=sous
ssh_port=22
while (( $# )); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --domain|--email|--ssh-public-key-file|--deploy-user|--auth-user|--ssh-host|--ssh-port)
      (( $# >= 2 )) || die "Missing value for $1"
      case "$1" in
        --domain) domain=$2 ;;
        --email) email=$2 ;;
        --ssh-public-key-file) public_key_file=$2 ;;
        --deploy-user) deploy_user=$2 ;;
        --auth-user) auth_user=$2 ;;
        --ssh-host) ssh_host=$2 ;;
        --ssh-port) ssh_port=$2 ;;
      esac
      shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ $EUID == 0 ]] || die 'Run with sudo.'
[[ "$domain" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}$ ]] || die 'Supply a lowercase DNS hostname with --domain.'
(( ${#domain} <= 253 )) || die 'Domain is too long.'
IFS=. read -r -a domain_labels <<< "$domain"
for label in "${domain_labels[@]}"; do
  if ! [[ "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || (( ${#label} > 63 )); then
    die 'Invalid domain label.'
  fi
done
[[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || die 'Supply --email for the certificate account.'
[[ "$deploy_user" =~ ^[a-z_][a-z0-9_-]*$ && "$deploy_user" != root ]] || die 'Choose a non-root deployment username.'
[[ "$auth_user" =~ ^[a-zA-Z0-9_-]+$ ]] || die 'Invalid site authentication username.'
ssh_host=${ssh_host:-$domain}
[[ "$ssh_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || die 'SSH host must be a hostname or IPv4 address.'
if ! [[ "$ssh_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$ssh_port < 1 || 10#$ssh_port > 65535 )); then
  die 'Invalid SSH port.'
fi
[[ -f "$public_key_file" ]] || die 'Supply --ssh-public-key-file pointing to a single OpenSSH public key (.pub).'
mapfile -t public_key_lines < "$public_key_file"
(( ${#public_key_lines[@]} == 1 )) || die 'Public-key file must contain exactly one line.'
public_key=${public_key_lines[0]}
[[ "$public_key" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[^[:space:]]+|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)[[:space:]] ]] || die 'Expected an OpenSSH public key, not a private key.'

# shellcheck source=/dev/null
source /etc/os-release
[[ "$ID" == ubuntu || "$ID" == debian ]] || die 'Supported systems: Ubuntu or Debian.'
[[ $(dpkg --print-architecture) == amd64 ]] || die 'The current Sous workflow builds amd64 images.'
[[ -d /run/systemd/system ]] || die 'This script requires a systemd server.'
codename=${VERSION_CODENAME:?OS release codename missing}
apt-get update
apt-get install -y ca-certificates curl nginx certbot apache2-utils openssh-client util-linux
ssh-keygen -lf "$public_key_file" > /dev/null

if ! command -v docker > /dev/null; then
  # Use Docker's signed apt repository; do not remove conflicting existing packages.
  install -m 755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  if [[ ! -f /etc/apt/sources.list.d/docker.sources && ! -f /etc/apt/sources.list.d/docker.list ]]; then
    cat > /etc/apt/sources.list.d/docker.sources <<DOCKER
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $codename
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER
  fi
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version > /dev/null || die 'Docker is installed but Compose v2 is missing. Install its Compose plugin, then rerun.'
systemctl enable --now docker nginx

if ! id "$deploy_user" > /dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
usermod -aG docker "$deploy_user"
deploy_home=$(getent passwd "$deploy_user" | cut -d: -f6)
deploy_group=$(id -gn "$deploy_user")
[[ -d "$deploy_home" && ! -L "$deploy_home/.ssh" ]] || die 'Deployment home must exist and .ssh must not be a symlink.'
install -d -m 700 -o "$deploy_user" -g "$deploy_group" "$deploy_home/.ssh"
authorized="$deploy_home/.ssh/authorized_keys"
[[ ! -L "$authorized" ]] || die 'authorized_keys must not be a symlink.'
touch "$authorized"
if ! grep -qxF -- "$public_key" "$authorized"; then
  printf '\n%s\n' "$public_key" >> "$authorized"
fi
chown "$deploy_user:$deploy_group" "$authorized"
chmod 600 "$authorized"

install -d -m 750 -o "$deploy_user" -g "$deploy_group" /opt/sous /opt/sous/releases
[[ ! -L /opt/sous/.env ]] || die '/opt/sous/.env must not be a symlink.'
if [[ ! -e /opt/sous/.env ]]; then
  printf 'SOUS_PORT=5050\nSOUS_TRUSTED_HOSTS=%s,localhost,127.0.0.1,[::1]\n' "$domain" > /opt/sous/.env
  chown "$deploy_user:$deploy_group" /opt/sous/.env
  chmod 600 /opt/sous/.env
else
  chown "$deploy_user:$deploy_group" /opt/sous/.env
  chmod 600 /opt/sous/.env
  printf 'Preserving /opt/sous/.env; ensure SOUS_PORT=5050 and trusted hosts include %s and loopback hosts.\n' "$domain"
fi

password_file="/etc/nginx/sous-$domain.htpasswd"
if [[ ! -f "$password_file" ]]; then
  printf 'Create the browser login password for %s (not an AWS/vault password).\n' "$auth_user"
  htpasswd -c "$password_file" "$auth_user"
  chown root:www-data "$password_file"
  chmod 640 "$password_file"
fi

install -d -m 755 /var/www/sous-acme /etc/nginx/sites-available /etc/nginx/sites-enabled
site="/etc/nginx/sites-available/sous-$domain"
enabled="/etc/nginx/sites-enabled/sous-$domain"
marker='# Managed by Sous setup-server.sh.'
[[ ! -L "$site" ]] || die 'The managed Nginx site must not be a symlink.'
if [[ -e "$site" ]] && [[ $(head -n 1 "$site") != "$marker" ]]; then
  die "Refusing to replace unmanaged Nginx configuration: $site"
fi
if [[ -e "$enabled" || -L "$enabled" ]]; then
  [[ -L "$enabled" && $(readlink "$enabled") == "$site" ]] || die "Unexpected enabled Nginx site: $enabled"
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

apply_site() {
  local candidate=$1 had_site=false had_link=false
  if [[ -f "$site" ]]; then cp -p "$site" "$work/previous-site"; had_site=true; fi
  if [[ -L "$enabled" ]]; then had_link=true; fi
  install -m 644 "$candidate" "$site"
  ln -sfn "$site" "$enabled"
  if nginx -t && systemctl reload nginx; then return 0; fi
  if $had_site; then cp -p "$work/previous-site" "$site"; else rm -f "$site"; fi
  if ! $had_link; then rm -f "$enabled"; fi
  nginx -t && systemctl reload nginx || true
  die 'Nginx validation/reload failed; restored the previous site configuration.'
}

cert_dir="/etc/letsencrypt/live/$domain"
if [[ ! -f "$cert_dir/fullchain.pem" || ! -f "$cert_dir/privkey.pem" ]]; then
  cat > "$work/bootstrap" <<NGINX
$marker
server {
    listen 80;
    server_name $domain;
    location ^~ /.well-known/acme-challenge/ { root /var/www/sous-acme; }
    location / { return 503; }
}
NGINX
  apply_site "$work/bootstrap"
fi

# Certbot prompts for terms/account consent when needed. Keep port 80 reachable
# for future webroot renewals. No credentials are printed or embedded in arguments.
certbot certonly --webroot --webroot-path /var/www/sous-acme \
  --cert-name "$domain" --domain "$domain" --email "$email" --keep-until-expiring

connection_variable="sous_connection_${domain//./_}"
connection_variable=${connection_variable//-/_}
cat > "$work/https-site" <<NGINX
$marker
map \$http_upgrade \$$connection_variable {
    default upgrade;
    '' close;
}
server {
    listen 80;
    server_name $domain;
    location ^~ /.well-known/acme-challenge/ { root /var/www/sous-acme; }
    location / { return 301 https://$domain\$request_uri; }
}
server {
    listen 443 ssl;
    server_name $domain;
    ssl_certificate $cert_dir/fullchain.pem;
    ssl_certificate_key $cert_dir/privkey.pem;
    client_max_body_size 2m;
    auth_basic "Sous";
    auth_basic_user_file $password_file;
    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$$connection_variable;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }
}
NGINX
apply_site "$work/https-site"
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/sous-nginx-reload <<'HOOK'
#!/bin/sh
nginx -t && systemctl reload nginx
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/sous-nginx-reload
systemctl enable --now certbot.timer

printf '\nServer configuration ready. GitHub repository variables:\n'
printf 'DEPLOY_HOST=%s\nDEPLOY_USER=%s\nDEPLOY_PORT=%s\nDEPLOY_PATH=/opt/sous\n' "$ssh_host" "$deploy_user" "$ssh_port"
printf '\nSet DEPLOY_PROJECT to the existing Compose project, or sous for a new install.\n'
printf 'Add the matching private deployment key to the production DEPLOY_SSH_KEY secret.\n'
printf 'DEPLOY_KNOWN_HOSTS (obtained directly on this server):\n'
known_host=$ssh_host
if [[ "$ssh_port" != 22 ]]; then known_host="[$ssh_host]:$ssh_port"; fi
[[ -f /etc/ssh/ssh_host_ed25519_key.pub ]] || die 'Find the actual sshd host key to populate DEPLOY_KNOWN_HOSTS.'
awk -v host="$known_host" '{ print host " " $1 " " $2 }' /etc/ssh/ssh_host_ed25519_key.pub
printf '\nEnsure this user can pull GHCR images; then set repository DEPLOY_ENABLED=true.\n'
printf 'HTTPS is configured at https://%s. A 502 is expected until Sous is deployed.\n' "$domain"
