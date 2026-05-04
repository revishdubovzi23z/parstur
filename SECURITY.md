# SECURITY

Operational security notes for `par2`. Skim this before you deploy
anywhere that's not strictly localhost.

## 1. `.env` hygiene

* `.env` is excluded by `.gitignore`. Do not commit it. Do not paste it
  into chat / pull request bodies / screenshots. Do not echo the file
  during a screen share or recorded session.
* Use [.env.example](./.env.example) as a template — copy it to `.env`
  and fill in the values.
* Treat the file as a secret: file permissions `600` on Linux/macOS,
  ACL-protected on Windows. Avoid syncing it via consumer cloud
  storage (Dropbox / Google Drive / iCloud). Prefer a secrets manager
  (1Password, Bitwarden, HashiCorp Vault, AWS SSM Parameter Store, …)
  and have your deploy step write the file at runtime instead of
  storing it on disk.
* Logs **must not** print the values of `REZKA_PASSWORD`, `AUTH_PASS`,
  `AUTH_PASS_HASH`, or any of the API keys. If you add a new env var,
  add it to that "do not log" list mentally.

## 2. Web-UI authentication

* When `AUTH_USER` and either `AUTH_PASS_HASH` or `AUTH_PASS` are set,
  every `/api/*` request and the `/ws` WebSocket require a bearer
  token obtained via `POST /api/login`. Tokens have a sliding 7-day TTL.
* `AUTH_PASS_HASH` (pbkdf2_sha256, 600 000 iterations) is preferred.
  `.env.example` carries a one-liner that generates a hash without
  ever sending the plaintext anywhere.
* `AUTH_PASS` (plaintext) is kept as a fallback for existing installs
  but should be migrated to `AUTH_PASS_HASH` at the next opportunity.
* If `AUTH_USER` is empty the app runs without auth. Only do that on
  a fully private network or behind a VPN/SSH tunnel — there is no
  IP allow-list.

## 3. HDRezka service account

* The `REZKA_EMAIL` / `REZKA_PASSWORD` pair must point at a **separate
  service account** dedicated to par2, not at your personal Rezka
  account.
* Why: par2 stores the credentials in `.env`, then re-uses them on
  every sync. If `.env` ever leaks, you only need to rotate the
  service account and revoke its sessions — your real account stays
  intact.
* Best practice when creating the service account:
  * use a fresh email alias (e.g. Gmail "+par2" trick or a dedicated
    inbox);
  * use a unique, randomly-generated password (not reused anywhere);
  * mirror only the watched/favourite state you actually want par2 to
    see — the account doesn't need to know anything else about you.

## 4. Database / disk artefacts

* `app_data.db` (and the WAL/SHM files next to it) contains your full
  catalog. Treat it like personal data — don't share it casually.
* Logs land in `*_log.txt` files. Rotate / delete them before sharing
  the working directory.

## 5. Reporting issues

If you find a security issue, open a *private* GitHub Security
Advisory on the repo rather than a public issue. If that's not
possible, email the maintainer directly.
