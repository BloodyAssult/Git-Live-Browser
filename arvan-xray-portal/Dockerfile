FROM ghcr.io/xtls/xray-core:26.3.27

COPY config.json /usr/local/etc/xray/custom.json

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/xray"]
CMD ["run", "-config", "/usr/local/etc/xray/custom.json"]
