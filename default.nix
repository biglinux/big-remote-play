{ lib
, python3Packages
, gtk4
, libadwaita
, libsecret
, pkg-config
, wrapGAppsHook4
, gobject-introspection
, gettext
, curl
, iproute2
}:
python3Packages.buildPythonApplication {
  pname = "big-remote-play";
  version = "2.0.0";
  src = ./.;
  pyproject = true;

  build-system = with python3Packages; [ uv-build ];
  dependencies = with python3Packages; [ pygobject3 pycairo ];

  nativeBuildInputs = [ pkg-config wrapGAppsHook4 gobject-introspection gettext ];
  buildInputs = [ gtk4 libadwaita libsecret ];

  # Runtime tools resolved from PATH at use time (sunshine, moonlight, docker,
  # tailscale, zerotier) are not Nix build inputs; the app degrades gracefully
  # when they are absent.
  propagatedBuildInputs = [ curl iproute2 ];

  dontWrapGApps = false;

  postInstall = ''
    cp -a $src/usr/share/big-remote-play $out/share/big-remote-play
    install -Dm644 $src/usr/share/applications/big-remote-play.desktop \
      $out/share/applications/big-remote-play.desktop
    cp -a $src/usr/share/locale $out/share/locale
  '';

  meta = with lib; {
    description = "Integrated remote cooperative gaming system";
    homepage = "https://github.com/biglinux/big-remote-play";
    license = licenses.gpl3Plus;
    platforms = platforms.linux;
  };
}
