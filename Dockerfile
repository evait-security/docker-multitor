FROM alpine:3.21

RUN apk add --no-cache \
      tor bash git haproxy privoxy procps curl netcat-openbsd sudo \
    && cd /etc/privoxy && for f in *.new; do mv "$f" "${f%.new}"; done \
    && git clone https://github.com/trimstray/multitor /opt/multitor \
    && cd /opt/multitor && chmod +x setup.sh bin/multitor && ./setup.sh install \
    && sed -i 's/127.0.0.1:16379/0.0.0.0:16379/g' /opt/multitor/templates/haproxy-template.cfg \
    && sed -i 's/polipo privoxy hpts/privoxy/' /opt/multitor/src/__init__ \
    && mkdir -p /var/log/multitor/privoxy \
    && rm -rf /opt/multitor/.git

COPY startup.sh /usr/local/bin/startup.sh
RUN chmod +x /usr/local/bin/startup.sh

EXPOSE 16379

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -sf --proxy http://127.0.0.1:16379 https://check.torproject.org/api/ip || exit 1

ENTRYPOINT ["startup.sh"]