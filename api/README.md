# dorf api

FastAPI shim between the Godot desktop pet and the Discord bot's Redis queues.

## What it does

- `POST /api/process_query` — pushes `{unique_id, message}` onto the bot's
  `response_queue` (the same queue Discord `!derf` commands use). Returns
  `{"unique_id": ...}`.
- `POST /api/fetch_response` — polls `response:{unique_id}` for up to 125s,
  deletes the key on success, and returns `{"response": ...}` or HTTP 504.
- `GET /api/health` — liveness + Redis ping (unauthenticated).

## Configuration (api/.env, loaded relative to this file — not CWD)

| Var | Required | Notes |
|---|---|---|
| `REDIS_HOST` | no (default `localhost`) | |
| `REDIS_PORT` | no (default `6379`) | |
| `REDIS_PASSWORD` | **yes** against the deployed Redis (`client/docker-compose.yml` sets `--requirepass`) | |
| `REDIS_DB` | no (default `0`) | |
| `DORF_API_TOKEN` | recommended | Shared secret; the Godot client must set the same value in `DORF_API_TOKEN` and it is sent as the `X-Dorf-Token` header. Unset disables the check. |

## Run

```sh
just api            # foreground, port 8000
```

or as a systemd user unit:

```sh
just install-services
systemctl --user enable --now dorf-api.service
```

Install deps first: `cd api && poetry install` (requires Python 3.13).
