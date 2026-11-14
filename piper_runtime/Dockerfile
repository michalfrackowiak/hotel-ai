# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# Both voices and their configuration files come from the same pinned revision.
ARG VOICE_SOURCE=https://huggingface.co/rhasspy/piper-voices/resolve/6b9cfaacb4fb2e02a9621227e3c1a48bec9710c3
ADD --checksum=sha256:abf4b998e34045cbd00eaf050de2de620f81ded6eb8b85a24b022a1c2b3f08d2 ${VOICE_SOURCE}/en/en_US/joe/medium/en_US-joe-medium.onnx /models/en_US-joe-medium.onnx
ADD --checksum=sha256:7b5f0293c0bf5bda8e377be1696da84af798befeef31038e43beefc01fe858da ${VOICE_SOURCE}/en/en_US/joe/medium/en_US-joe-medium.onnx.json /models/en_US-joe-medium.onnx.json
ADD --checksum=sha256:4b319248135916e8f5433ce7a9fe5271026b05cb598bab05807b22cb17fd129b ${VOICE_SOURCE}/en/en_US/joe/medium/MODEL_CARD /models/en_US-joe-medium.MODEL_CARD
ADD --checksum=sha256:cf757ee307e3a7c23d5d8f69934deab51683d01c1c6be376d3e13146020f1530 ${VOICE_SOURCE}/pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx /models/pl_PL-darkman-medium.onnx
ADD --checksum=sha256:0d98a67829a1b7ffbce7faacf4ea7bb7a7ce2ebc1598ad0289c0f6c1f352dbd0 ${VOICE_SOURCE}/pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx.json /models/pl_PL-darkman-medium.onnx.json
ADD --checksum=sha256:a2c89d3059a830d47fb0159973088c02ae3a4c7e56235a3abe63b9507340bdab ${VOICE_SOURCE}/pl/pl_PL/darkman/medium/MODEL_CARD /models/pl_PL-darkman-medium.MODEL_CARD

RUN chmod a+r /models/*

COPY service.py ./
USER 10001:10001
EXPOSE 5000
CMD ["python", "service.py"]
