# dorfv2

things whacky things to get running on amd

poetry run pip3 install torch --no-deps --force-reinstall --index-url https://download.pytorch.org/whl/rocm6.2.4

after doing the poetry install

## Local services

`docker-compose.yml` defines Redis on `127.0.0.1:6379` and the ROCm
Whisper server on `127.0.0.1:9191`. Whisper uses host networking and an
explicit server port. Its model is stored in `models/ggml-base.en.bin`.

Run `just model` once to download the model, then `just up` to start the
containers, STT worker, bot, and API. `just whisper-up` starts or updates
Whisper alone and migrates the previous standalone `derf-whisper` container
to Compose. `just whisper-down` stops it; `just check` checks the stack.
`just redis-up` and `just redis-down` manage Redis alone.

The bot sends audio through Redis to `whisper_worker.py`, which defaults to
`http://127.0.0.1:9191/inference`. `WHISPER_URL` can override that endpoint.
Restart an existing STT worker after changing the URL; the Discord bot
itself does not need a restart for this port change.
