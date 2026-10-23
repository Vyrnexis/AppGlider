{
  description = "AppGlider: a Linux AppImage installer, updater and launcher";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      pyproject = builtins.fromTOML (builtins.readFile ./pyproject.toml);

      mkAppGlider =
        pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "appglider";
          version = pyproject.project.version;
          pyproject = true;

          # Only the files the build needs, so editing flake.nix or docs
          # does not trigger a rebuild.
          src = pkgs.lib.fileset.toSource {
            root = ./.;
            fileset = pkgs.lib.fileset.unions [
              ./pyproject.toml
              ./README.md
              ./LICENSE
              ./ATTRIBUTION.md
              ./THIRD_PARTY_NOTICES.md
              ./src
              ./packaging/appglider.desktop
            ];
          };

          build-system = [ pkgs.python3Packages.setuptools ];

          # nixpkgs ships Qt for Python as one package; PySide6-Essentials
          # (the pip split) does not exist there.
          dependencies = [ pkgs.python3Packages.pyside6 ];
          pythonRemoveDeps = [ "PySide6-Essentials" ];

          nativeBuildInputs = [ pkgs.qt6.wrapQtAppsHook ];
          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtwayland
          ];

          # Wrap through the Python wrapper instead of wrapping twice.
          dontWrapQtApps = true;
          preFixup = ''
            makeWrapperArgs+=("''${qtWrapperArgs[@]}")
          '';

          # AppGlider writes its own ~/.local/share/applications entry using
          # sys.executable, which here is the bare store Python without the
          # Qt/PySide6 wrapper environment, so the menu entry cannot start.
          # Point it at the wrapped `appglider` on PATH instead (this also
          # survives rebuilds, unlike a store path).
          postPatch = ''
            substituteInPlace src/appglider/app.py \
              --replace-fail 'return [sys.executable, "-O", "-m", "appglider"]' \
                             'return ["appglider"]'
          '';

          postInstall = ''
            install -Dm644 packaging/appglider.desktop \
              $out/share/applications/appglider.desktop
            install -Dm644 src/appglider/assets/icons/appglider.png \
              $out/share/icons/hicolor/1024x1024/apps/appglider.png
          '';

          pythonImportsCheck = [ "appglider" ];

          # The unit tests need a display and network; run them with
          # `nix develop` instead (see devShells.default).
          doCheck = false;

          meta = {
            description = pyproject.project.description;
            homepage = "https://github.com/Vyrnexis/AppGlider";
            license = pkgs.lib.licenses.mit;
            mainProgram = "appglider";
            platforms = pkgs.lib.platforms.linux;
          };
        };
    in
    {
      packages = forAllSystems (pkgs: rec {
        appglider = mkAppGlider pkgs;
        default = appglider;
      });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/appglider";
        };
      });

      overlays.default = final: _prev: { appglider = mkAppGlider final; };

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [
              ps.pyside6
              ps.setuptools
              ps.mypy
            ]))
            pkgs.ruff
          ];
          # Let PySide6 find the Wayland platform plugin inside the shell.
          QT_PLUGIN_PATH = "${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtwayland}/${pkgs.qt6.qtbase.qtPluginPrefix}";
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            echo "AppGlider dev shell. Run:  python -O -m appglider"
            echo "Tests:                     QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -q"
          '';
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}