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
, hicolor-icon-theme
}:
python3Packages.buildPythonApplication {
  pname = "big-remote-play";
  version = "0.0.0";
  src = ./.;
  pyproject = true;

  build-system = with python3Packages; [ uv-build ];
  dependencies = with python3Packages; [ pygobject3 pycairo ];

  nativeBuildInputs = [ pkg-config wrapGAppsHook4 gobject-introspection gettext ];
  buildInputs = [ gtk4 libadwaita libsecret hicolor-icon-theme ];

  # Runtime tools resolved from PATH at use time (sunshine, moonlight, docker,
  # tailscale, zerotier) are not Nix build inputs; the app degrades gracefully
  # when they are absent.
  propagatedBuildInputs = [ curl iproute2 ];

  dontWrapGApps = false;

  preFixup = ''
    gappsWrapperArgs+=(
      --set BIG_REMOTE_PLAY_DATADIR "$out/share/big-remote-play"
      --set BIG_REMOTE_PLAY_LOCALEDIR "$out/share/locale"
    )
  '';

  postInstall = ''
    mkdir -p "$out/share"
    cp -a $src/usr/share/big-remote-play $out/share/big-remote-play
    install -Dm644 $src/usr/share/applications/br.com.biglinux.remoteplay.desktop \
      $out/share/applications/br.com.biglinux.remoteplay.desktop
    install -Dm644 $src/usr/share/metainfo/br.com.biglinux.remoteplay.metainfo.xml \
      $out/share/metainfo/br.com.biglinux.remoteplay.metainfo.xml
    install -Dm644 $src/usr/share/icons/hicolor/scalable/apps/br.com.biglinux.remoteplay.svg \
      $out/share/icons/hicolor/scalable/apps/br.com.biglinux.remoteplay.svg

    while IFS= read -r raw || [ -n "$raw" ]; do
      lang="''${raw%%#*}"
      lang="$(printf '%s' "$lang" | tr -d '[:space:]')"
      [ -n "$lang" ] || continue
      po="$src/locale/$lang.po"
      test -f "$po"
      install -d "$out/share/locale/$lang/LC_MESSAGES"
      msgfmt --check --check-format \
        -o "$out/share/locale/$lang/LC_MESSAGES/big-remote-play.mo" \
        "$po"
    done < $src/locale/LINGUAS
  '';

  meta = with lib; {
    description = "Integrated remote cooperative gaming system";
    homepage = "https://github.com/biglinux/big-remote-play";
    license = licenses.gpl3Plus;
    platforms = platforms.linux;
    mainProgram = "big-remote-play";
  };
}
