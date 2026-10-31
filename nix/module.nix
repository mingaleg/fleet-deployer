{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.fleet-deployer;
  stateDir = "/var/lib/fleet-deployer";

  configFile = pkgs.writeText "fleet-deployer.json" (
    builtins.toJSON {
      inherit (cfg)
        repoUrl
        branch
        pollInterval
        sshUser
        sshOptions
        ;
      inherit stateDir;
      listenHost = cfg.listen.address;
      listenPort = cfg.listen.port;
      staticDir = "${cfg.package.frontend}";
      hosts = lib.mapAttrs (_: h: { inherit (h) target deploy; }) cfg.hosts;
    }
  );
in
{
  options.services.fleet-deployer = {
    enable = lib.mkEnableOption "fleet-deployer, which builds a NixOS flake's branch here and deploys it to hosts";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "fleet-deployer.packages.\${system}.default";
    };

    repoUrl = lib.mkOption {
      type = lib.types.str;
      example = "https://github.com/me/nixos-config.git";
      description = "Git URL of the flake whose nixosConfigurations get deployed.";
    };

    branch = lib.mkOption {
      type = lib.types.str;
      default = "main";
    };

    pollInterval = lib.mkOption {
      type = lib.types.ints.positive;
      default = 300;
      description = "Seconds between fetching the branch and polling every host.";
    };

    sshUser = lib.mkOption {
      type = lib.types.str;
      default = "root";
      description = "User on target hosts. Deploys need passwordless sudo for it.";
    };

    sshKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path to the SSH private key (e.g. an agenix secret). Passed via LoadCredential.";
    };

    sshOptions = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Extra ssh arguments. Host keys are checked strictly against the system known_hosts.";
    };

    tokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path to the API bearer token. Without it, all actions are disabled.";
    };

    listen = {
      address = lib.mkOption {
        type = lib.types.str;
        default = "0.0.0.0";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 8090;
      };
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
    };

    hosts = lib.mkOption {
      description = "Hosts shown on the dashboard, keyed by nixosConfigurations name.";
      default = { };
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            target = lib.mkOption {
              type = lib.types.nullOr lib.types.str;
              description = "SSH host to poll and deploy to; null shows the host without polling.";
            };
            deploy = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Whether deploys are allowed; false makes the host status-only.";
            };
          };
        }
      );
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.fleet-deployer = {
      isSystemUser = true;
      group = "fleet-deployer";
      home = stateDir;
    };
    users.groups.fleet-deployer = { };

    systemd.services.fleet-deployer = {
      description = "Fleet deployer";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      path = [
        config.nix.package
        pkgs.git
        pkgs.openssh
        config.system.build.nixos-rebuild
      ];
      environment = {
        FLEET_DEPLOYER_CONFIG = configFile;
        HOME = stateDir;
      };
      serviceConfig = {
        User = "fleet-deployer";
        Group = "fleet-deployer";
        StateDirectory = "fleet-deployer";
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = 5;
        LoadCredential =
          lib.optional (cfg.tokenFile != null) "token:${cfg.tokenFile}"
          ++ lib.optional (cfg.sshKeyFile != null) "ssh-key:${cfg.sshKeyFile}";
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.listen.port ];
  };
}
