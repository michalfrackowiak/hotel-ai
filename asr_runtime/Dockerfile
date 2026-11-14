# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72 AS models

# Published as Apache 2.0 by https://alphacephei.com/vosk/models.
ADD --checksum=sha256:30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498 https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip /tmp/en.zip
ADD --checksum=sha256:c4cd16498ea544f446f9e9a55cbd602b71cfe5a2b6f2b0834d81e1b6fce15f0d https://alphacephei.com/vosk/models/vosk-model-small-pl-0.22.zip /tmp/pl.zip
RUN python -m zipfile -e /tmp/en.zip /models \
    && python -m zipfile -e /tmp/pl.zip /models \
    && chmod -R a+rX /models

FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --only-binary=:all: --no-binary=srt -r requirements.txt \
    && python -c 'import vosk'
COPY --from=models /models /models
COPY service.py ./
USER 10001:10001
EXPOSE 5001
CMD ["python", "service.py"]
