# Music Bot

Telegram music bot prepared for Railway deployment.

## Railway

Required variables:

- `BOT_TOKEN`
- `ADMIN_IDS`

Optional variables:

- `YANDEX_MUSIC_TOKEN`
- `VK_LOGIN`
- `VK_PASSWORD`
- `VK_ACCESS_TOKEN`
- `DATA_DIR=/data`
- `CACHE_DIR=/data/audio_cache`

Recommended Railway setup:

1. Deploy this repository as a service.
2. Mount a Railway Volume to `/data`.
3. Set the environment variables from `.env.example`.
4. Redeploy the service.

## VK Music

VK search works through one dedicated technical VK account that belongs to the bot.

- Set `VK_LOGIN` and `VK_PASSWORD` for that account
- `VK_ACCESS_TOKEN` can be left empty unless you explicitly use it
- After deploy, VK appears as a third source next to Yandex and YouTube
- Use `/search_vk <query>` for VK-only search or plain text for combined search

The repository includes:

- `Dockerfile` with `ffmpeg`
- `railway.json` for Docker-based deploys
- runtime storage paths configurable through env vars
