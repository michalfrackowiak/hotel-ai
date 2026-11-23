# Server deployment

The Linux server runs Django, Gunicorn, SQLite, Piper, and Vosk. It stores room rates, availability, reservations, and configuration in a Docker volume. Django uses its built-in parser for the demo. The browser captures microphone replies, sends each utterance to the hotel server over HTTPS, and plays back Piper's WAV replies. Vosk transcribes the utterances in memory using separate small English and Polish models. [Vosk models](https://alphacephei.com/vosk/models)

The server has two ARM Neoverse N1 cores, 11.6 GiB of RAM, and a 96 GiB root filesystem. Docker Engine and the Compose plugin were already present. Piper uses CPU inference with one worker and a one-CPU, 1 GiB container limit. Django serves the UI and admin assets with debug disabled. The browser session and SSH tunnel can be closed while the server keeps its reservations and continues running.

## Speech models

The deployed Piper voices are Joe for English and Darkman for Polish. Each voice has about 63 MB of model weights. The build downloads the public weights and their model cards at a fixed revision and verifies their checksums. The Piper runtime is GPL-3.0-or-later and both pinned cards document CC0 training data. [Piper source](https://github.com/OHF-Voice/piper1-gpl), [voice selection and licensing](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/COMMERCIAL_USE.md)

| Engine or model | Weight size | Languages relevant here |
| --- | ---: | --- |
| Kokoro-82M (stock checkpoint) | 327 MB | English. Its published voices omit Polish. [Weights](https://huggingface.co/hexgrad/Kokoro-82M/tree/main), [voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) |
| Piper Joe and Darkman | 63 MB each | English and Polish in one CPU runtime |
| KittenTTS Nano 0.8 INT8 | About 28 MB including voice presets | English. [Model card](https://huggingface.co/KittenML/kitten-tts-nano-0.8-int8) |
| Matcha-TTS PL Edge v2 INT8 | About 25 MB for the selected encoder, decoder, and vocoder | Polish. [Model card](https://huggingface.co/machinekind/Matcha-TTS-PL-Edge-v2) |
| eSpeak NG | About 8.5 MB for the Debian ARM64 speech-data download | English and Polish. It uses formant synthesis and has a more synthetic sound. [eSpeak NG](https://github.com/espeak-ng/espeak-ng), [Debian package](https://packages.debian.org/trixie/espeak-ng-data) |

Piper gives both languages one neural speech engine. The smaller KittenTTS and Matcha-TTS models would require separate runtimes. If Piper is unavailable, reception uses the browser voice or continues in text.

## Start the server

Install [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/ubuntu/) if needed. Place a checkout of the project on the server and run these commands from that directory. Use `sudo docker` where Docker requires administrator access. Keep `.env` readable only by your account and Docker administrators. On a new installation, generate independent values for `DJANGO_SECRET_KEY` and `BOOKING_API_KEY` with `openssl rand -hex 32`. Set `HOTEL_PORT=8080` in `.env`, or choose a free port. To seed an administrator, set `HOTEL_DEMO_ADMIN_USERNAME` and `HOTEL_DEMO_ADMIN_PASSWORD` in the same file before running `seed_demo`.

```sh
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml -f compose.asr.yaml up --build -d --wait
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml exec web python manage.py seed_demo
python3 scripts/smoke_piper.py http://127.0.0.1:8080 --asr
```

Use the same Compose project name on updates so that the stack keeps its `hotel-ai_hotel-data` volume. The initial deployment seeds the demo inventory in a fresh SQLite database.

## Connect from the MacBook

Run this from the MacBook with the private key that corresponds to the server account. Substitute the path to your key, your SSH account, and your server address.

```sh
ssh -i ~/.ssh/your-server-key -o ExitOnForwardFailure=yes -N \
  -L 127.0.0.1:8080:127.0.0.1:8080 ubuntu@your-server
```

Leave the terminal open and visit [reception](http://127.0.0.1:8080/) or [Django admin](http://127.0.0.1:8080/admin/). If port 8080 is busy on the Mac, change the first port in `-L` to `18080` and open `http://127.0.0.1:18080/`. If the server uses a different `HOTEL_PORT`, use it in server smoke checks and for the last port in `-L`. Django listens only on loopback, and Piper listens only on its internal Docker network. The production HTTPS site also exposes the Django login at `/admin/login/`.

## Publish the reception

The Nginx configuration exposes the guest page, chat and audio routes, and the authentication-protected Django admin over HTTPS. It limits login requests to five per minute per client address (with a burst allowance) and serves admin stylesheets only through the HTTPS configuration. The booking API and phone callbacks remain behind their private or separately configured listeners. The bootstrap HTTP configuration does not expose the admin login. Browsers require a secure context for microphone access. Use the SSH tunnel for voice booking while the HTTPS setup is in progress, and use fictional guest details in the HTTP demo. [Browser secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)

Set `DJANGO_ALLOWED_HOSTS=92.5.19.10` in the server `.env`, preserving any existing entries. Use your public IP in the Nginx `server_name`, the HTTPS redirect, and `DJANGO_ALLOWED_HOSTS` for another deployment. Recreate the Django container to apply the environment change. The [guest route configuration](../deploy/nginx/hotel-ai-reception.inc) points to loopback port 8080. Update the upstream URLs if your stack uses a different `HOTEL_PORT`. Releases with separate reception views require the updated guest route snippet for `/chat/` and `/voice/`. Install the snippet with the application release, run `sudo nginx -t` before reloading, and check both public routes in English and Polish.

Allow inbound TCP ports 80 and 443 in the cloud and host firewalls. On a dedicated server with Ubuntu's default Nginx site enabled, disable its symlink at `/etc/nginx/sites-enabled/default`. Keep port 8080 private for Django and the SSH tunnel.

### Bootstrap HTTP

```sh
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml -f compose.asr.yaml up -d --wait
sudo apt install nginx
sudo install -d /var/lib/hotel-ai-acme /etc/nginx/snippets
sudo install -m 0644 deploy/nginx/hotel-ai-reception.inc /etc/nginx/snippets/hotel-ai-reception.inc
sudo install -m 0644 deploy/nginx/hotel-ai-http.conf /etc/nginx/conf.d/hotel-ai.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

From a separate machine, confirm `curl -fsS http://92.5.19.10/healthz/` succeeds before requesting a certificate.

### Enable HTTPS for browser voice

Install Certbot 5.4 or later. This server uses the official Certbot snap (`sudo snap install --classic certbot`), which provides its own renewal timer. The certificate covers the public IP directly and is valid for just over six days, so automatic renewal and reload are required. Install the certificate with the name `hotel-ai`, which matches the paths in the [HTTPS configuration](../deploy/nginx/hotel-ai.conf). [Let's Encrypt IP certificates with Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)

```sh
sudo /snap/bin/certbot certonly --cert-name hotel-ai --preferred-profile shortlived \
  --webroot --webroot-path /var/lib/hotel-ai-acme --ip-address 92.5.19.10
```

Set `DJANGO_TRUST_PROXY_PROTO=1` in the server `.env` for HTTPS and recreate the Django container. This assumes requests reach Django through the private listener and Nginx sets `X-Forwarded-Proto` itself.

```sh
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml -f compose.asr.yaml up --build -d --wait
sudo install -m 0644 deploy/nginx/hotel-ai.conf /etc/nginx/conf.d/hotel-ai.conf
sudo install -m 0644 deploy/nginx/hotel-ai-admin.inc /etc/nginx/snippets/hotel-ai-admin.inc
sudo nginx -t
sudo systemctl reload nginx
```

Keep Certbot's renewal timer active. Save the following deploy hook at `/etc/letsencrypt/renewal-hooks/deploy/reload-hotel-ai-nginx`, make it executable, and verify renewal with `sudo /snap/bin/certbot renew --dry-run --run-deploy-hooks`. The HTTP entry point redirects visitors to HTTPS and serves ACME challenges for renewals. The HTTPS proxy applies the Secure flag to session and CSRF cookies.

```sh
#!/bin/sh
/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx
```

Verify from a separate machine. Expect `308` for the HTTP entry point, `200` for the reception, JavaScript, admin login and admin stylesheet over HTTPS, and `404` for the booking API. The speech smoke exercises the HTTPS session and checks that Piper returns WAV audio in both languages.

```sh
curl -sS -o /dev/null -w '%{http_code}\n' http://92.5.19.10/
curl -fsS 'https://92.5.19.10/?lang=pl'
curl -fsS -o /dev/null https://92.5.19.10/static/reception/chat.js
curl -fsS -o /dev/null https://92.5.19.10/admin/login/
curl -fsS -o /dev/null https://92.5.19.10/static/admin/css/base.css
curl -sS -o /dev/null -w '%{http_code}\n' https://92.5.19.10/api/availability/
python3 scripts/smoke_piper.py https://92.5.19.10 --asr
```
