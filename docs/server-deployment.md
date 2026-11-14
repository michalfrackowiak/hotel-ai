# Private server deployment

The Linux server runs Django, Gunicorn, SQLite, and Piper. It stores room rates, availability, reservations, and configuration in a Docker volume. Django uses its built-in parser for the demo. The MacBook runs the reception in Chrome, captures the microphone, submits recognized text, and plays back the WAV replies. Chrome may send microphone audio to its speech-recognition service. [Speech recognition documentation](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)

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
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml up --build -d --wait
docker compose -p hotel-ai -f compose.yaml -f compose.server.yaml -f compose.piper.yaml exec web python manage.py seed_demo
python3 scripts/smoke_piper.py http://127.0.0.1:8080
```

Use the same Compose project name on updates so that the stack keeps its `hotel-ai_hotel-data` volume. The initial deployment seeds the demo inventory in a fresh SQLite database.

## Connect from the MacBook

Run this from the MacBook with the private key that corresponds to the server account. Substitute the path to your key, your SSH account, and your server address.

```sh
ssh -i ~/.ssh/your-server-key -o ExitOnForwardFailure=yes -N \
  -L 127.0.0.1:8080:127.0.0.1:8080 ubuntu@your-server
```

Leave the terminal open and visit [reception](http://127.0.0.1:8080/) or [Django admin](http://127.0.0.1:8080/admin/). If port 8080 is busy on the Mac, change the first port in `-L` to `18080` and open `http://127.0.0.1:18080/`. If the server uses a different `HOTEL_PORT`, use it in server smoke checks and for the last port in `-L`. The web service binds only to loopback, and Piper listens only on its internal Docker network. Public HTTPS and the live phone connector use separate deployment steps.
