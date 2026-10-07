{
  description = "Big Remote Play — integrated remote cooperative gaming";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        # The commit date as YY.MM.DD, the version every build carries.
        date = self.lastModifiedDate;
        version = "${builtins.substring 2 2 date}.${builtins.substring 4 2 date}.${builtins.substring 6 2 date}";
      in {
        packages.default = pkgs.callPackage ./. { inherit version; };
        apps.default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/big-remote-play";
        };
      });
}
