## 1. LXD Container Initialization & Host Storage

* **Initialize LXD Instance:** Create an Ubuntu 24.04 LTS container with nested virtualization permissions enabled so the nested Docker engine can manage cgroups and device nodes:

```bash
lxc init ubuntu:24.04 library-appliance
lxc config set library-appliance security.nesting=true
lxc config set library-appliance security.syscalls.intercept.mknod=true
lxc config set library-appliance security.syscalls.intercept.setxattr=true
lxc start library-appliance
```

* **Attach Host Media Storage:** Pass through host-level media directories into the container at `/mnt/media` to keep large files decoupled from container snapshots:

```bash
lxc config device add library-appliance media-disk disk source=/opt/library-media path=/mnt/media
```

* **Install Container Dependencies:** Access the container shell (`lxc exec library-appliance -- bash`) and install the core Docker runtime and utilities:

```bash
apt update && apt upgrade -y
apt install docker.io docker-compose-v2 curl wget python3-venv python3-pip -y
```

---

## 2. Docker Media & Authentication Stack

* **Directory Layout:** Create directory structures inside the container to isolate persistent volume configurations:

```bash
mkdir -p /opt/library-docker/{abs/config,abs/metadata,kavita/config,authelia/keys,redis}
mkdir -p /mnt/media/{audiobooks,ebooks}
```

* **Compose Configuration:** Deploy `/opt/library-docker/docker-compose.yml` binding all services strictly to loopback addresses (`127.0.0.1`) so Apache can reverse-proxy them:

* **Authelia:** Image `ghcr.io/authelia/authelia:latest` bound to `127.0.0.1:9091:9091`.

* **Redis:** Image `redis:alpine` for internal Authelia state handling.

* **Audiobookshelf:** Image `ghcr.io/advplyr/audiobookshelf:latest` bound to `127.0.0.1:13378:80`, mounting `/mnt/media/audiobooks:/audiobooks`.

* **Kavita:** Image `ghcr.io/kareadita/kavita:latest` bound to `127.0.0.1:5000:5000`, mounting `/mnt/media/ebooks:/data`.

* **Authelia Configuration:** Create `/opt/library-docker/authelia/configuration.yml`:

* Configure file-based authentication pointing to `/config/users_database.yml` and session cookies under the root domain.

* Generate a private RSA key (`openssl genrsa -out /opt/library-docker/authelia/keys/oidc.key 2048`) and declare the JWKS entry under `identity_providers.oidc`.

* Define OIDC clients for `audiobookshelf`, `kavita`, and `koha`, generating pre-hashed client secrets via `docker exec -it library_authelia authelia crypto hash generate --password "<SECRET>"`.

* **Start the Media Services:** Launch the container stack with `docker compose -f /opt/library-docker/docker-compose.yml up -d`.

---

## 3. Native Koha ILS & Apache Reverse Proxy

* **Repository & Package Installation:** Trust the Koha community key and install Koha alongside MariaDB natively via APT:

```bash
mkdir -p /etc/apt/keyrings
wget -qO - https://debian.koha-community.org/koha/gpg.asc | gpg --dearmor -o /etc/apt/keyrings/koha.gpg
echo "deb [signed-by=/etc/apt/keyrings/koha.gpg] http://debian.koha-community.org/koha stable main" > /etc/apt/sources.list.d/koha.list
apt update && apt install -y mariadb-server koha-common
```

* **Enable Apache Modules:** Enable required proxy, Perl execution, and header rewriting modules:

```bash
a2enmod rewrite cgi headers proxy proxy_http proxy_wstunnel deflate
```

* **Instance Creation:** Create the instance database and retrieve the master installation password:

```bash
koha-create --create-db library
xmlstarlet sel -t -v 'yazgfs/config/pass' /etc/koha/sites/library/koha-conf.xml; echo
```

* **Enable Plack & Search Daemons:** Start Koha's high-performance application server and the Zebra search engine:

```bash
koha-plack --enable library && koha-plack --start library
koha-zebra --start library
koha-rebuild-zebra -f -a -b -v library
```

* **VirtualHost Routing (`/etc/apache2/sites-available/`):**
* `library.conf`: Configured to listen on port 80 (or 443 if terminating SSL locally), assigning `ServerName` for the OPAC (`library.domain.org`) and the Staff interface (`staff.library.domain.org`).

* `media.conf`: Configured with `ProxyPass` rules routing `audiobooks.` to `127.0.0.1:13378` (including WebSocket upgrade rules for audio playback), `ebooks.` to `127.0.0.1:5000`, `auth.` to `127.0.0.1:9091`, and `registration.` to `127.0.0.1:5050`.

* Enable configurations and reload: `a2ensite library.conf media.conf && systemctl restart apache2`.

* **Web Installer & OIDC Setup:** Complete the web onboarding at the staff URL using MARC21, and configure Authelia under **Koha administration > Additional parameters > Identity providers** with auto-registration enabled.

---

## 4. Mail Relay, Registration Portal & Bridge Scripts

* **Postfix Local Smart-Host Setup:** Install Postfix to capture container-wide outbound mail queues and relay upstream without exposing external credentials inside application configs:

```bash
apt install -y postfix mailutils postfix-pcre
```

* Set `relayhost = [<UPSTREAM_RELAY_OR_SES>]:25` (or `:587`) in `/etc/postfix/main.cf`.

* Set `inet_interfaces = 127.0.0.1, 172.17.0.1` so both the host and Docker containers can deliver unauthenticated mail.
- Map `/etc/postfix/sender_canonical` (`/.+/ library@domain.org`) to normalize system envelopes, then run `systemctl restart postfix`.
* **Registration Portal Setup:**
* Deploy `/opt/registration-portal/portal.py` inside a virtual environment (`/opt/registration-portal/.venv/`) with `Flask` and `argon2-cffi`.

* Set the shebang to `#!/opt/registration-portal/.venv/bin/python3` and run via systemd unit `/etc/systemd/system/registration-portal.service` bound to port 5050:

```bash
systemctl daemon-reload && systemctl enable --now registration-portal
```

* **API Bridge Scripts Setup:**
* Deploy the Python scripts in `/opt/pymarc/` using a virtual environment containing `pymarc`, `requests`, and `python-dotenv`.

* Set environment variables in `/opt/pymarc/kavita/.env` and `/opt/pymarc/abs/.env` with API keys and Koha REST basic auth credentials.

* Configure root's crontab (`crontab -e`) to execute automated catalog runs on a staggered bi-hourly schedule:

```cron
0 */2 * * * cd /opt/pymarc/kavita && /opt/pymarc/.venv/bin/python kavita_sync.py >> /var/log/library-sync/kavita_sync.log 2>&1
30 */2 * * * cd /opt/pymarc/abs && /opt/pymarc/.venv/bin/python abs_sync.py >> /var/log/library-sync/abs_sync.log 2>&1
```
