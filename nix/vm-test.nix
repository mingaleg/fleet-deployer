{ pkgs, self }:

pkgs.testers.runNixOSTest {
  name = "fleet-deployer";

  nodes.machine = {
    imports = [ self.nixosModules.default ];
    environment.etc."fleet-token".text = "testtoken";
    environment.systemPackages = [ pkgs.curl ];
    services.fleet-deployer = {
      enable = true;
      # Unreachable on purpose: the service must keep serving when fetches and
      # polls fail.
      repoUrl = "file:///nonexistent.git";
      tokenFile = "/etc/fleet-token";
      hosts.machine.target = "localhost";
      hosts.watched = {
        target = null;
        deploy = false;
      };
    };
  };

  testScript = ''
    machine.wait_for_unit("fleet-deployer.service")
    machine.wait_for_open_port(8090)

    machine.succeed("curl -sf localhost:8090/ | grep -q 'Fleet Deployer'")
    machine.succeed("curl -sf localhost:8090/dist/main.js > /dev/null")
    hosts = machine.succeed("curl -sf localhost:8090/api/hosts")
    assert '"machine"' in hosts and '"watched"' in hosts, hosts

    # Reads are open, actions need the token.
    code = machine.succeed("curl -s -o /dev/null -w '%{http_code}' -X POST localhost:8090/api/refresh")
    assert code == "401", code
    code = machine.succeed(
        "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer testtoken' localhost:8090/api/auth"
    )
    assert code == "200", code
    code = machine.succeed(
        "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer testtoken' "
        "-H 'Content-Type: application/json' -d '{\"host\":\"watched\",\"mode\":\"test\"}' localhost:8090/api/deploy"
    )
    assert code == "409", code
  '';
}
