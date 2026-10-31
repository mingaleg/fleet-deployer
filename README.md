# fleet-deployer

Runs on a hub host (ronove). It tracks a branch of a NixOS flake, evaluates what
each `nixosConfigurations.<host>` would build, polls every host over SSH to see
what it actually runs, and deploys on request with
`nixos-rebuild --target-host`. It comes with a small dashboard and a JSON API.

It never deploys on its own: it only shows which hosts are behind.

## How it works

- **Mirror.** A bare clone of the repo lives in `/var/lib/fleet-deployer/mirror.git`.
  Every evaluation and deploy uses `git+file://…?ref=<branch>&rev=<sha>`, so what the
  dashboard shows is exactly what gets deployed.
- **State.** Each host's running `/run/current-system` is compared with the store path
  `main` evaluates to:
  - `in sync` or `behind main`;
  - `unreachable`, with when the host was last seen;
  - flags for a live `test` deploy (current ≠ boot default) and for a pending reboot (new
    kernel).
- **Deploys.** Jobs run one at a time and their output is kept as logs.
- **Self-deploys.** The hub can deploy itself. The module sets `restartIfChanged = false`,
  because a switch that stopped this service would kill its own SSH session and leave the
  host half-switched. Instead the service exits, and systemd restarts it, once
  `/run/current-system` changes and no deploy is running. If a job does get cut off anyway,
  it is marked succeeded or failed on startup according to what the host runs.

## API

GETs are open. POSTs, and `/api/auth`, need `Authorization: Bearer <token>`.

| Method | Path | |
|---|---|---|
| GET | `/api/hosts`, `/api/hosts/{name}` | host states and timestamps |
| GET | `/api/main` | tracked commit and recent history |
| GET | `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/log?offset=N` | deploys and their logs |
| GET | `/api/auth` | check a token |
| POST | `/api/refresh` | fetch the branch and poll every host now |
| POST | `/api/deploy` `{"host", "mode": "test"\|"switch"\|"boot", "rev"?}` | queue a deploy; `rev` defaults to the tracked head |

```
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"host":"vps","mode":"test"}' http://ronove.home.mingalev.net:8090/api/deploy
```

## NixOS module

```nix
imports = [ fleet-deployer.nixosModules.default ];
services.fleet-deployer = {
  enable = true;
  repoUrl = "https://github.com/me/nixos-config.git";
  sshUser = "deploy";              # needs passwordless sudo on targets
  sshKeyFile = "/run/agenix/fleet-deployer-ssh";
  tokenFile = "/run/agenix/fleet-deployer-token";
  hosts.hub.target = "localhost";
  hosts.laptop = { target = "laptop"; deploy = false; };  # status only
};
```

Host keys are checked strictly against the system `known_hosts`, e.g. via
`programs.ssh.knownHosts`. For status-only hosts, authorize the key with a forced command
that runs `fleet_deployer.status.STATUS_SCRIPT`.

## Development

```
nix develop            # python + deps + typescript
pytest
(cd frontend && tsc -p .)
nix flake check        # package tests + NixOS VM test
```
