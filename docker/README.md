# OpenFabric Docker

Simple HTTP compose stack for the Agent service and manual only.

It starts:

- Agent runtime and Agent UI on `8011`
- Manual on `8013`

This stack does not build or run the local audio transcription runtime. Use
`docker-all-services/` when you want Docker voice dictation.

Run it from this folder:

```bash
./build.sh
./start.sh
```

Build a specific image version:

```bash
./build.sh v1.0.0
./start.sh v1.0.0
```

You can still use Compose directly:

```bash
docker compose up --build
```

Or from the repository root:

```bash
./docker/start-server.sh
```

Open:

- `http://127.0.0.1:8011/agent-ui`
- `http://127.0.0.1:8013/manual`

The gateway is not managed by this compose file. Install and run it separately;
the Agent service reaches it at `http://host.docker.internal:8787`.
