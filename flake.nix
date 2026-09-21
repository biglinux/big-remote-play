{
  description = "Big Remote Play — integrated remote cooperative gaming";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = nixpkgs.legacyPackages.${system};
      in {
        packages.default = pkgs.callPackage ./. { };
        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/big-remote-play";
        };
      });
}
