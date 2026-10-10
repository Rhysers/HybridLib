# Hybrid Library Appliance
A unified, self-contained library appliance that bridges physical shelf inventory and digital media streaming into a single catalog interface. Hosted entirely within an LXD system container, the appliance runs native Koha ILS alongside containerized digital media services (Kavita and Audiobookshelf), secured with centralized Single Sign-On via Authelia and linked by automated catalog synchronization.

## Overview
Traditional library stacks treat physical inventory and self-hosted media servers as disconnected systems, forcing patrons to switch between separate apps for physical books, eBooks, and audiobooks. This project bridges that gap by using Koha as a single discovery layer.

Automated Python background scripts continuously query Kavita and Audiobookshelf via REST APIs, convert media metadata into standard MARC21 bibliographic records, and inject them into Koha's catalog. In the public catalog, users can check physical shelf availability or click direct "Read Online" and "Listen Online" deep-links into the respective streaming readers behind unified SSO.

## Recommended Use Cases
- **Community, Church, or Private School Libraries**: Ideal for organizations that manage physical lending collections while providing patrons remote access to curated digital eBooks and audiobooks.

- **Homelabs & Power Users**: Designed for self-hosters who want an authentic, standards-compliant Integrated Library System (ILS) without having to manually catalog digital media across disconnected platforms.

- **Portable Self-Contained Deployments**: Suited for environments where portability and disaster recovery matter; packaging the native services and nested Docker containers inside an LXD appliance enables fast snapshots, migrations, and independent media volume mounts.

## Key Features
- Unified OPAC Discovery: Search physical print books, digital epubs, and streaming audiobooks side-by-side from a single search query.
- Hands-Free Catalog Ingestion: Bi-hourly Python bridge scripts automatically create MARC21 records and populate Koha's catalog via REST API without manual librarian data entry.
- Single Sign-On (SSO): Authelia provides centralized OpenID Connect (OIDC) identity management across Koha, Kavita, and Audiobookshelf, enabling Just-in-Time (JIT) account provisioning on first login.
- Controlled Self-Registration: A lightweight Flask web portal allows patrons to register using a pre-shared invite secret and 6-digit email verification before securely writing credentials directly to the identity database.
- Clean Single-Appliance Architecture: Runs Koha natively for full database and indexing performance while nesting Docker containers for media servers inside an isolated LXD boundary.


# Documentation
## Registration Portal Architecture & Workflow

* **Locations & Environment:** The core Flask application is located at `/opt/registration-portal/portal.py` and runs inside a virtual environment at `/opt/registration-portal/.venv/`. The service is managed by the Systemd unit file `/etc/systemd/system/registration-portal.service` and binds locally to `127.0.0.1:5050` before being reverse-proxied by Apache. The HTML form structures are baked directly into the script as a multi-line string variable named `HTML_BASE` rather than existing as standalone HTML files.


* **Initial Workflow:** Patrons enter the registration flow via a banner located on the OPAC or injected into the Authelia login screen. The form requires a pre-shared secret, full name, email, desired username, and password.


* **Validation & Delivery:** The `is_already_registered()` function queries Authelia's database (`users_database.yml`) via `pyyaml` to ensure the email and username do not already exist, preventing YAML collisions. A 6-digit verification code is generated, the user's payload is held temporarily in a Flask client-side encrypted session cookie (`session['reg_data']`), and the code is sent to the user via a local Postfix relay running on `127.0.0.1:25`.


* **Account Creation:** When the user submits the code at the `/verify` route, the portal hashes the provided password into an Argon2id string and appends the final user profile directly into Authelia's `users_database.yml`. Upon their first successful SSO login, Koha, Kavita, and Audiobookshelf provision the accounts Just-in-Time (JIT).



## Python Bridge Scripts (Koha Integration)

* **Locations & Environment:** The Kavita sync script is housed at `/opt/pymarc/kavita/kavita_sync.py` and the Audiobookshelf script at `/opt/pymarc/abs/abs_sync.py`. Both execute using the virtual environment at `/opt/pymarc/.venv` and rely on `pymarc`, `requests`, and `python-dotenv`. Sync outputs are logged to `/var/log/library-sync/kavita_sync.log` and `/var/log/library-sync/abs_sync.log`.


* **Authentication & Fetching:** The scripts authenticate against Kavita via a `POST` request to `/api/Plugin/authenticate` for a JWT, and against Audiobookshelf using a static API Bearer token. They query endpoints (like Kavita's `/api/Library/libraries` and `/api/Series/all-v2`) to pull complete digital libraries containing titles, authors, summaries, and unique internal IDs.


* **Pre-flight Deduplication:** Using basic authentication, the scripts query Koha's `/api/v1/biblios` REST API to check for existing records using deterministic barcodes (e.g., `KAV-{id}`) or item types. This filters out duplicates while permitting physical and digital copies of the same book to coexist.


* **MARC21 Record Construction:** Using `pymarc`, the digital items are structured into formal library formats: Title (245), Author (100), Summary (520), Bibliographic Item Type (942), and physical holding attributes (952) to lock in the library branch and unique dummy barcodes. A direct reading or listening URL is placed into the MARC 856 tag, creating a clickable "Read Online" or "Listen Online" button in Koha.


* **Live Injection:** The compiled records are serialized into MARCXML and sent to Koha via a `POST` request with the `application/marcxml+xml` content type. Both scripts operate unattended via a staggered bi-hourly cron job.



## Manual Update Procedures

* **Docker Media & Auth Stack:** The containers (Authelia, Redis, Kavita, Audiobookshelf) run from `/opt/library-docker/docker-compose.yml`. To update them:


1. Enter the LXD container: `lxc exec library-appliance -- bash`.


2. Navigate to the directory: `cd /opt/library-docker`.


3. Pull and recreate: `docker compose pull` followed by `docker compose up -d --remove-orphans`.


4. Clean unused layers: `docker image prune -f`.




* **Koha (Native APT Package):** Koha runs natively on the OS via the official repository located at `/etc/apt/sources.list.d/koha.list`. To update:


1. Fetch and install packages: `apt update && apt --only-upgrade install koha-common -y`.


2. Run schema updates: `koha-upgrade-schema library`.


3. Restart processes: `koha-plack --restart library`, `koha-zebra --restart library`, and `systemctl restart apache2`.


4. Rebuild the search index: `koha-rebuild-zebra -v -f library`.
