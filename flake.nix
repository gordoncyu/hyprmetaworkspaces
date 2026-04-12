{
  description = "Meta-workspace daemon and CLI for Hyprland";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python314;
      in {
        packages.default = python.pkgs.buildPythonApplication {
          pname = "hyprmetaworkspaces";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = [
            python.pkgs.hatchling
          ];

          dependencies = [];

          meta = {
            description = "Meta-workspace daemon and CLI for Hyprland";
            mainProgram = "hyprmetaworkspaced";
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            python
            python.pkgs.hatchling
            python.pkgs.pip
          ];
        };
      }
    );
}
