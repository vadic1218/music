# Music Bot

Telegram music bot prepared for Railway deployment.

## Railway

Required variables:

- `BOT_TOKEN`
- `ADMIN_IDS`

Optional variables:

- `YANDEX_MUSIC_TOKEN`
- `DATA_DIR=/data`
- `CACHE_DIR=/data/audio_cache`

Recommended Railway setup:

1. Deploy this repository as a service.
2. Mount a Railway Volume to `/data`.
3. Set the environment variables from `.env.example`.
4. Redeploy the service.

The repository includes:

- `Dockerfile` with `ffmpeg`
- `railway.json` for Docker-based deploys
- runtime storage paths configurable through env vars
