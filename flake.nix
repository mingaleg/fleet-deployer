{
  description = "Build a NixOS flake's main branch on a hub host and ship it to the fleet";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        default = pkgs.callPackage ./nix/package.nix { };
        frontend = default.frontend;
      });

      nixosModules.default = import ./nix/module.nix { inherit self; };

      # For status-only hosts: authorize the deployer's key with
      # `restrict,command="<this script>"` so it can only read status.
      lib.statusScript = builtins.readFile ./fleet_deployer/status.sh;

      checks = forAll (
        pkgs:
        {
          package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        }
        // nixpkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
          vm = import ./nix/vm-test.nix { inherit pkgs self; };
        }
      );

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.fastapi
              ps.uvicorn
              ps.sqlalchemy
              ps.pydantic
              ps.pytest
              ps.httpx
            ]))
            pkgs.typescript
          ];
        };
      });
    };
}
