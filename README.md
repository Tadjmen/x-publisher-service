# x-publisher-service

FastAPI service for posting to X/Twitter with normal web-session cookies.

This is a public-ready X web-session publisher service with:

- Post one tweet
- Post a reply thread
- Upload image/GIF/video media
- Attach up to 4 media files to the first tweet in a tweet/thread
- Delete a tweet by id
- Optional proxy support
- Health check and outbound IP check
- No paid X API dependency

## 1. Install

```bash
git clone https://github.com/Tadjmen/x-publisher-service.git
cd x-publisher-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
git clone https://github.com/Tadjmen/x-publisher-service.git
cd x-publisher-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in:

```env
SERVICE_ACCESS_TOKEN=change-me
X_AUTH_TOKEN=your_auth_token_cookie
X_CT0=your_ct0_cookie
X_WEB_BEARER_TOKEN=your_x_web_bearer_token
```

Optional:

```env
PROXY_URL=
BROWSER=chrome136
MAX_TWEET_CHARS=250
MAX_MEDIA_DOWNLOAD_MB=50
ALLOW_LOCAL_MEDIA_PATHS=false
```

### Cookie notes

- `X_AUTH_TOKEN`: value of the `auth_token` cookie from your logged-in X account.
- `X_CT0`: value of the `ct0` cookie from the same X session.
- `X_WEB_BEARER_TOKEN`: value of the `authorization: Bearer ...` header used by normal x.com web requests.
- Copy the values for `auth_token` and `ct0`. Cookies generally last around 12 months before needing rotation.
- Keep `.env` private. It is ignored by git.

## 3. Run

```bash
uvicorn execution.main:app --host 0.0.0.0 --port 8000
```

Open:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

## 4. Service access token

State-changing endpoints require a private service password for your self-hosted HTTP service. This is not related to X Developer access and does not use the paid X API.

```http
Authorization: Bearer your_SERVICE_ACCESS_TOKEN
```

## 5. Post one tweet

```bash
curl -X POST "http://127.0.0.1:8000/tweet" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  -d '{"text":"Hello from x-publisher-service"}'
```

Response:

```json
{
  "success": true,
  "tweet_id": "1234567890123456789",
  "tweet_ids": ["1234567890123456789"]
}
```

## 6. Post with image/video URL

```bash
curl -X POST "http://127.0.0.1:8000/tweet" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  -d '{
    "text": "Tweet with media",
    "media_urls": ["https://example.com/image.png"]
  }'
```

Supported extensions:

- Images: `.jpg`, `.jpeg`, `.png`, `.webp`
- GIF: `.gif`
- Video: `.mp4`, `.mov`, `.m4v`

X may reject media that is too small, too large, corrupted, or in an unsupported codec.

## 7. Post with local media path

Local file paths are disabled by default for safer public deployments.

Enable only when this API is private/internal:

```env
ALLOW_LOCAL_MEDIA_PATHS=true
```

Then:

```bash
curl -X POST "http://127.0.0.1:8000/tweet" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  -d '{
    "text": "Tweet with local media",
    "media_paths": ["/home/ubuntu/image.png"]
  }'
```

Windows JSON example:

```json
{
  "text": "Tweet with local media",
  "media_paths": ["C:/Users/Z/Pictures/image.png"]
}
```

## 8. Post a thread

```bash
curl -X POST "http://127.0.0.1:8000/thread" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  -d '{
    "tweets": [
      "Tweet 1: headline",
      "Tweet 2: context",
      "Tweet 3: detail"
    ]
  }'
```

Media is attached to the first tweet in the thread:

```json
{
  "tweets": ["Headline", "Follow-up"],
  "media_urls": ["https://example.com/chart.mp4"]
}
```

## 9. Delete a tweet

```bash
curl -X POST "http://127.0.0.1:8000/delete" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer change-me" \
  -d '{"tweet_id":"1234567890123456789"}'
```

Response:

```json
{
  "success": true,
  "deleted": true,
  "tweet_id": "1234567890123456789"
}
```

## 10. Health and IP check

Health does not require authentication:

```bash
curl "http://127.0.0.1:8000/health"
```

Outbound IP check requires the service access token:

```bash
curl "http://127.0.0.1:8000/ip" -H "Authorization: Bearer change-me"
```

## 11. Ubuntu systemd service

Example path:

```bash
/home/ubuntu/x-publisher-service
```

Create `/etc/systemd/system/x-publisher-service.service`:

```ini
[Unit]
Description=x-publisher-service
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/x-publisher-service
EnvironmentFile=/home/ubuntu/x-publisher-service/.env
ExecStart=/home/ubuntu/x-publisher-service/.venv/bin/uvicorn execution.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable x-publisher-service
sudo systemctl start x-publisher-service
sudo systemctl status x-publisher-service
```

Logs:

```bash
journalctl -u x-publisher-service -f
```

## 12. Error messages

Common normalized errors:

- `AUTH_EXPIRED`: cookie/session invalid or expired
- `ACCOUNT_LOCKED`
- `ACCOUNT_SUSPENDED`
- `RATE_LIMIT`
- `DUPLICATE_TWEET`
- `AUTOMATION_DETECTED`
- `X_DAILY_LIMIT`
- `MEDIA_UPLOAD_<status>`
- `MEDIA_PROCESSING_FAILED`

## 13. Development

```bash
pip install -e ".[dev]"
python -m compileall execution tests
pytest
```

## 14. Notes

- The service stores no cookies and writes no X session files.
- Remote media is downloaded to a temporary folder and deleted after the request finishes.
- Tweet text is compacted to `MAX_TWEET_CHARS` before posting.
- X web internals can change. This service dynamically discovers GraphQL operation ids and uses fallbacks when discovery fails.

## License

MIT


