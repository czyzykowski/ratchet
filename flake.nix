{
  description = "Ratchet — AI-driven software development orchestration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in {
        devShells.default = pkgs.mkShell {
          buildInputs = [
            pkgs.python312
            pkgs.python312Packages.pip
            pkgs.ruff
            pkgs.postgresql_16
            pkgs.git
          ];

          shellHook = ''
            if [ -z "$DATABASE_URL" ]; then
              echo "WARNING: DATABASE_URL is not set."
              echo "  Example: export DATABASE_URL=postgresql+psycopg://ratchet@127.0.0.1:5432/ratchet"
            fi
            if [ -z "$TEST_DATABASE_URL" ]; then
              echo "WARNING: TEST_DATABASE_URL is not set."
              echo "  Example: export TEST_DATABASE_URL=postgresql+psycopg://ratchet_test@127.0.0.1:5432/ratchet"
            fi
          '';
        };
      }
    );
}
