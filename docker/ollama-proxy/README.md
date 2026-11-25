# usm-ollama-proxy

Authenticated front door for the sandbox Ollama (`usodclpsandadm1`), so dev/prod k8s can
use the sandbox LLM until cloud compute exists. Ollama has no auth of its own and stays
bound to `127.0.0.1:11434`; this proxy is the only network-facing path to it.

| | |
|---|---|
| Listens | `:11435` (plain HTTP, corp network) |
| Auth | `Authorization: Bearer <token>`; anything else → `401` |
| Allowed | `/api/chat`, `/api/generate`, `/api/embed`, `/api/embeddings`, `/api/tags`, `/api/show`, `/api/version` |
| Refused | everything else → `403` (incl. `/api/pull`, `/api/delete`, `/api/push`, `/api/create`) |
| Token | KeePass entry **`SIP_Ollama_Proxy_Token`** (Password field), read at start from the local broker `127.0.0.1:2000`. 32+ chars of `A-Z a-z 0-9 - _` |

## Deploy / update (on the sandbox)
```bash
cd ~/unified_storage_monitoring/docker/ollama-proxy     # or copy this folder there
docker compose -p usm-ollama-proxy up -d
docker logs usm-ollama-proxy | tail -3                   # "listening on :11435 ..."
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:11435/api/tags   # 401 = auth on
```
This is a separate compose project; it never touches the main USM stack.

## Rotate the token
1. Change the Password of `SIP_Ollama_Proxy_Token` in KeePass (generator: 48 chars, letters + digits).
2. `docker restart usm-ollama-proxy` (it re-reads KeePass on start).
3. k8s backends pick it up within an hour, or immediately after a `401` on
   `/api/v1/chat/status` (or roll the `usm-backend` deployment).

## Add a model (the registry is web-filtered on corp networks)
`ollama pull` fails on the sandbox (registry.ollama.ai is blocked), so models come in
through GitHub:
1. Actions → **Ollama model bundle** → Run workflow, `model` = e.g. `nomic-embed-text:latest`.
   A GitHub-hosted runner pulls it and uploads `ollama-model-<name>.tar` (+ `.sha256`)
   as a 7-day artifact.
2. Download the artifact zip, unzip, check `sha256sum -c` (or compare by eye).
3. Stream it into the running Ollama — additive, existing models and the
   container are untouched:
   ```bash
   ssh usodclpsandadm1.corp.intranet 'docker cp - usm-ollama:/root/.ollama' < ollama-model-<name>.tar
   ssh usodclpsandadm1.corp.intranet 'docker exec usm-ollama ollama list'
   ```
Loaded so far: `qwen2.5:3b` (chat), `nomic-embed-text` (dictionary retrieval, 2026-11-24).

## Clients
The backend sends the token when `OLLAMA_CRED_KEY` is set (read from `OLLAMA_KEEPASS_URL`,
default `KEEPASS_URL`) or `OLLAMA_API_KEY` is given. See `deploy/overlays/dev/patch-chat.yaml`.
