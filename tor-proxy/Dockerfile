# Lightweight Tor SOCKS5 proxy for routing LND REST API traffic
# to .onion hidden services (e.g., Start9 nodes)
#
# Provides a SOCKS5 proxy on port 9050 that other containers can
# use to reach Tor hidden services.

FROM alpine:3.20

RUN apk add --no-cache tor

COPY torrc /etc/tor/torrc

# Tor runs as non-root
USER tor

EXPOSE 9050

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD nc -z 127.0.0.1 9050 || exit 1

CMD ["tor", "-f", "/etc/tor/torrc"]
