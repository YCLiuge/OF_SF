FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OF_STATE=/state/realm OF_BIND=0.0.0.0
WORKDIR /opt/overfield
COPY app/ app/
COPY tests/ tests/
COPY resources/ resources/
COPY vendor/common/ vendor/common/
COPY vendor/linux-x86_64/ vendor/linux-x86_64/
COPY of.py LICENSE-of-ps.txt resource-sources.json ./
RUN apt-get update && apt-get install -y --no-install-recommends libgd3 && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 overfield && useradd --no-create-home -u 10001 -g overfield overfield && install -d -o overfield -g overfield -m 0700 /state /state/realm
USER 10001:10001
EXPOSE 18881/tcp 11033/tcp
STOPSIGNAL SIGTERM
ENTRYPOINT ["python","-B","/opt/overfield/of.py"]
CMD ["start"]
