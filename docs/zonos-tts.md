# ZONOS3-Mini browser speech

The optional ZONOS3-Mini sidecar reads reception replies in English and Polish. It runs locally with Zyphra's CPU runtime and the stock Savannah voice. The browser still provides speech recognition and the Django booking flow still controls quotes and confirmation. An unavailable voice engine falls back to browser speech. Twilio phone calls use their existing Polly voices. [ZONOS3-Mini model card](https://huggingface.co/Zyphra/ZONOS3-Mini) · [Zyphra runtime](https://github.com/Zyphra/ZONOS3-Mini)

## Install and run

From the project root, download the pinned public models and start the Compose stack with the speech override. The download is about 1.12 GB. The downloader checks SHA-256 hashes and keeps weights in the ignored `data/zonos/` directory. A Hugging Face or GitHub token is unnecessary.

```sh
python tts_runtime/download_models.py
docker compose -f compose.yaml -f compose.zonos.yaml up --build --wait --wait-timeout 300
```

The first Docker build compiles the pinned upstream CPU server. Startup can take a few minutes while the model loads. The sidecar is reachable on the Compose network. Open [Hotel-ai reception](http://127.0.0.1:8080/), choose a language, and click **Start voice booking**. The chat displays the reply while audio is being prepared. The microphone resumes after playback ends. Set `HOTEL_ZONOS_TIMEOUT` in `.env` to change how many seconds Django waits for synthesis. The default is 60 seconds.

```sh
docker compose -f compose.yaml -f compose.zonos.yaml logs zonos
```

To use browser speech directly, stop the optional service and recreate the web service with the base configuration.

```sh
docker compose -f compose.yaml -f compose.zonos.yaml stop zonos
docker compose up -d web
```
