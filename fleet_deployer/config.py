"""Service configuration, read from the JSON file the NixOS module writes."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class HostConfig:
    name: str
    # SSH destination host (no user); None means status can't be polled.
    target: str | None
    # Whether the hub may deploy to it; False makes it status-only.
    deploy: bool = True
    # SSH user for this host; None means the global ssh_user.
    user: str | None = None


@dataclass(frozen=True)
class Config:
    repo_url: str
    state_dir: Path
    hosts: dict[str, HostConfig]
    branch: str = "main"
    poll_interval: int = 300
    ssh_user: str = "mingaleg"
    ssh_key_file: Path | None = None
    token_file: Path | None = None
    static_dir: Path | None = None
    listen_host: str = "0.0.0.0"
    listen_port: int = 8090
    ssh_options: list[str] = field(default_factory=list)
    # Exit (for systemd to restart us) once this machine's system changes.
    restart_on_system_change: bool = False
    # Clients in these networks may act without the token.
    trusted_networks: list[str] = field(default_factory=list)

    def is_trusted(self, address: str | None) -> bool:
        try:
            ip = ipaddress.ip_address(address or "")
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in ipaddress.ip_network(n, strict=False) for n in self.trusted_networks)

    @property
    def mirror_dir(self) -> Path:
        return self.state_dir / "mirror.git"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite"

    def ssh_args(self) -> list[str]:
        """Options shared by status polls and nixos-rebuild (via NIX_SSHOPTS)."""
        args = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes",
        ]
        if self.ssh_key_file:
            args += ["-i", str(self.ssh_key_file), "-o", "IdentitiesOnly=yes"]
        return args + self.ssh_options

    def user_for(self, host: HostConfig) -> str:
        return host.user or self.ssh_user

    def token(self) -> str | None:
        if not self.token_file:
            return None
        return self.token_file.read_text().strip() or None

    @classmethod
    def from_dict(cls, raw: dict) -> Config:
        hosts = {
            name: HostConfig(name=name, target=h.get("target"), deploy=h.get("deploy", True), user=h.get("user"))
            for name, h in raw.get("hosts", {}).items()
        }
        opt_path = lambda key: Path(raw[key]) if raw.get(key) else None  # noqa: E731
        return cls(
            repo_url=raw["repoUrl"],
            state_dir=Path(raw["stateDir"]),
            hosts=hosts,
            branch=raw.get("branch", "main"),
            poll_interval=int(raw.get("pollInterval", 300)),
            ssh_user=raw.get("sshUser", "mingaleg"),
            ssh_key_file=opt_path("sshKeyFile"),
            token_file=opt_path("tokenFile"),
            static_dir=opt_path("staticDir"),
            listen_host=raw.get("listenHost", "0.0.0.0"),
            listen_port=int(raw.get("listenPort", 8090)),
            ssh_options=list(raw.get("sshOptions", [])),
            restart_on_system_change=bool(raw.get("restartOnSystemChange", False)),
            trusted_networks=list(raw.get("trustedNetworks", [])),
        )

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> Config:
        path = path or os.environ.get("FLEET_DEPLOYER_CONFIG")
        if not path:
            raise SystemExit("FLEET_DEPLOYER_CONFIG is not set")
        config = cls.from_dict(json.loads(Path(path).read_text()))
        # systemd LoadCredential paths are only known at runtime.
        creds = os.environ.get("CREDENTIALS_DIRECTORY")
        if creds:
            overrides = {}
            if (Path(creds) / "token").exists():
                overrides["token_file"] = Path(creds) / "token"
            if (Path(creds) / "ssh-key").exists():
                overrides["ssh_key_file"] = Path(creds) / "ssh-key"
            if overrides:
                config = cls(**{**config.__dict__, **overrides})
        return config
