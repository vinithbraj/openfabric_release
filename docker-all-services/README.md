# OpenFabric All Services Docker

Personal-use compose stack for the full local OpenFabric experience.

It starts:

- Agent runtime and Agent UI on `8011`
- Manual on `8013`
- Website portal on `8014`
- Audio runtime internally for voice transcription

Run it:

```bash
./docker-all-services/build.sh
./docker-all-services/start.sh
```

Build a specific image version:

```bash
./docker-all-services/build.sh v1.0.0
./docker-all-services/start.sh v1.0.0
```

Run from the folder itself with `./build.sh` and `./start.sh`.

Run in the background:

```bash
./docker-all-services/start.sh -d
```

Open:

- `http://127.0.0.1:8011/agent-ui`
- `http://127.0.0.1:8013/manual`
- `http://127.0.0.1:8014/website`

This stack opts into the Docker audio build, so the image builds `whisper-cli`
from whisper.cpp source. The audio runtime downloads the selected Whisper model
into the shared `/data` volume on first start. It is not published to the host;
the Agent service calls it over Docker networking at `http://audio:8012`.

The gateway is not managed by this compose file. Install and run it separately;
the Agent service reaches it at `http://host.docker.internal:8787`.
