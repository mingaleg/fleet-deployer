{
  lib,
  python3Packages,
  stdenvNoCC,
  typescript,
}:

let
  frontend = stdenvNoCC.mkDerivation {
    pname = "fleet-deployer-frontend";
    version = "0.1.0";
    src = ../frontend;
    nativeBuildInputs = [ typescript ];
    buildPhase = ''
      runHook preBuild
      tsc -p .
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p $out/dist
      cp index.html styles.css $out/
      cp dist/*.js $out/dist/
      runHook postInstall
    '';
  };
in
python3Packages.buildPythonApplication {
  pname = "fleet-deployer";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../fleet_deployer
      ../tests
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [
    fastapi
    uvicorn
    sqlalchemy
    pydantic
  ];

  nativeCheckInputs = with python3Packages; [
    pytestCheckHook
    httpx
  ];

  passthru = { inherit frontend; };

  meta = {
    description = "Build a NixOS flake's main branch on a hub host and ship it to the fleet";
    mainProgram = "fleet-deployer";
  };
}
