FROM rust:1.91-bookworm@sha256:c1e5f19e773b7878c3f7a805dd00a495e747acbdc76fb2337a4ebf0418896b33 AS build

RUN apt-get update && apt-get install -y --no-install-recommends cmake libopenblas-pthread-dev ninja-build \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init \
    && git remote add origin https://github.com/Zyphra/ZONOS3-Mini.git \
    && git fetch --depth=1 origin 96d9e0ec1e39083ed336ac78f24e3dbc93721c0f \
    && git checkout --detach FETCH_HEAD \
    && git submodule update --init --depth=1 servers/ggml/ggml third_party/zyphra-textnorm
RUN cmake -S servers/ggml -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS \
    && cmake --build /build --target zonos3-server --parallel 3

FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc

RUN apt-get update && apt-get install -y --no-install-recommends curl libgomp1 libopenblas0-pthread libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/zonos3-server /usr/local/bin/zonos3-server
COPY --from=build /src/LICENSE /src/NOTICE /usr/share/doc/zonos3/
COPY --from=build /src/third_party/licenses /usr/share/doc/zonos3/third_party/
ENV OPENBLAS_NUM_THREADS=4 ZAC_THREADS=4
USER 10001:10001
WORKDIR /tmp
EXPOSE 1919
ENTRYPOINT ["zonos3-server"]
CMD ["/models/backbone-ptq.gguf", "--zac", "/models/zac-keep2.gguf", "--tts-default-voices-dir", "/models/voices", "--host", "0.0.0.0", "--port", "1919", "--cpu", "--batch", "1", "--zac-threads", "1"]
