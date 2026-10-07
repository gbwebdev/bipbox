# Janus Gateway, built from a pinned release.
#
# There is no official Meetecho image, and the community ones are unpinned and
# of unknown provenance — not something to bake into a published project. This
# builds from source so the artifact is reproducible and auditable.
FROM debian:bookworm-slim AS build

ARG JANUS_VERSION=v1.4.2

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git pkg-config libtool automake autoconf make gcc \
      gengetopt libmicrohttpd-dev libjansson-dev libssl-dev libglib2.0-dev \
      libopus-dev libogg-dev libcurl4-openssl-dev libconfig-dev \
      libnice-dev libsrtp2-dev libwebsockets-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${JANUS_VERSION}" \
      https://github.com/meetecho/janus-gateway.git .

# Data channels, RabbitMQ, MQTT and the scripting plugins are not used by
# bipbox; leaving them out shrinks both the image and the attack surface.
RUN sh autogen.sh \
    && ./configure --prefix=/opt/janus \
         --disable-data-channels \
         --disable-rabbitmq \
         --disable-mqtt \
         --disable-plugin-lua \
         --disable-plugin-duktape \
         --disable-docs \
    && make -j"$(nproc)" \
    && make install \
    && make configs

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libmicrohttpd12 libjansson4 libssl3 libglib2.0-0 libopus0 libogg0 \
      libcurl4 libconfig9 libnice10 libsrtp2-1 libwebsockets17 \
      openssl curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/janus /opt/janus

# DTLS needs a certificate even when only plain-RTP participants are used,
# because Janus builds its DTLS context at startup regardless.
RUN mkdir -p /opt/janus/share/janus/certs \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
         -subj "/CN=bipbox-janus" \
         -keyout /opt/janus/share/janus/certs/mycert.key \
         -out /opt/janus/share/janus/certs/mycert.pem 2>/dev/null

EXPOSE 8088 7088 20000-20100/udp
CMD ["/opt/janus/bin/janus", "--configs-folder=/opt/janus/etc/janus"]
