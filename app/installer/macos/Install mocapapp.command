#!/bin/bash
# mocapapp installer for macOS (PROJECT.md D11). Double-click in Finder (first time: right-click > Open).
# Bootstraps a private, pinned environment in ~/Library/Application Support/mocapapp - no admin rights:
#   uv (pinned, SHA-256 checked) -> app source (tagged release) -> Python 3.12 + locked dependencies
#   -> pose models (once; then offline) -> ~/Applications/mocapapp.app
# Re-running updates/repairs the installation. Session folders are never touched.
# Options (environment): MOCAPAPP_REF=<tag or branch>  MOCAPAPP_SOURCE=<local checkout>  MOCAPAPP_NO_MODELS=1
set -euo pipefail

REF="${MOCAPAPP_REF:-mocapapp-v0.1.0rc16}"
SOURCE="${MOCAPAPP_SOURCE:-}"
PREFIX="${MOCAPAPP_PREFIX:-$HOME/Library/Application Support/mocapapp}"
APPDIR="$HOME/Applications"
REPO="https://github.com/Florin5678/mocap"
# Pose2Sim takes its version from git (setuptools-scm); a source zip has no .git folder, so it is
# given here: the upstream release this fork is based on, marked as the mocapapp fork.
POSE2SIM_VERSION="0.10.48+mocapapp"
UV_VERSION="0.13.4"
case "$(uname -m)" in
  arm64) UV_TARGET="aarch64-apple-darwin"; UV_SHA="e0b1d4743ce64c55d9f0f4a444afa6b81125680070ee04e853fcd0f6c1ebf8f1" ;;
  x86_64) UV_TARGET="x86_64-apple-darwin"; UV_SHA="7be2dcd316122f4b3e5a0a7889f6248266c14425374e105ff7dc9779184603a6" ;;
  *) echo "Unsupported processor: $(uname -m)"; exit 1 ;;
esac
STEP=0
step() { STEP=$((STEP + 1)); printf '\n\033[36m[%s/6] %s\033[0m\n' "$STEP" "$1"; }
finish() { echo; read -r -p "Press Enter to close this window " _ || true; exit "$1"; }
fail() {
  printf '\n\033[31mInstallation stopped at step %s: %s\033[0m\n' "$STEP" "$1"
  echo "What to do: check the internet connection and free disk space (about 10 GB), then run the installer again;"
  echo "finished steps are reused. The full log is $PREFIX/install.log."
  finish 1
}
trap 'fail "an unexpected error (see the messages above)"' ERR

major=$(sw_vers -productVersion | cut -d. -f1)
if [ "$major" -lt 15 ]; then
  echo "mocapapp needs macOS 15 (Sequoia) or newer: OpenSim's Python package is built for it. This Mac has $(sw_vers -productVersion)."
  finish 1
fi

mkdir -p "$PREFIX"
exec > >(tee "$PREFIX/install.log") 2>&1
printf '\033[32mInstalling mocapapp (%s) into %s\033[0m\n' "$REF" "$PREFIX"
echo "This needs an internet connection and takes about 10-30 minutes. Research use only."

step "Getting the installer tool (uv $UV_VERSION) ..."
UV="$PREFIX/uv/uv"
if ! { [ -x "$UV" ] && "$UV" --version | grep -q "$UV_VERSION"; }; then
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz"
  got=$(shasum -a 256 "$tmp/uv.tar.gz" | cut -d' ' -f1)
  [ "$got" = "$UV_SHA" ] || fail "the downloaded uv does not match its checksum (got $got). Try again later."
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  rm -rf "$PREFIX/uv" && mkdir -p "$PREFIX/uv"
  mv "$tmp/uv-$UV_TARGET/uv" "$UV"
  rm -rf "$tmp"
fi

step "Getting the app ($REF) ..."
if [ -n "$SOURCE" ]; then
  APP="$SOURCE/app"
  [ -f "$APP/pyproject.toml" ] || fail "$SOURCE is not a checkout of the mocap repository."
else
  case "$REF" in mocapapp-v*) kind=tags ;; *) kind=heads ;; esac
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/src.zip" "$REPO/archive/refs/$kind/$REF.zip"
  unzip -q "$tmp/src.zip" -d "$tmp/x"
  rm -rf "$PREFIX/src"
  mv "$tmp/x/"* "$PREFIX/src"
  rm -rf "$tmp"
  APP="$PREFIX/src/app"
fi

step "Installing Python and the app's components (the longest step) ..."
export UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_PROJECT_ENVIRONMENT="$PREFIX/venv" UV_CACHE_DIR="${UV_CACHE_DIR:-$PREFIX/cache}"
[ -e "$APP/../.git" ] || export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_POSE2SIM="$POSE2SIM_VERSION"
"$UV" sync --frozen --no-dev --python-preference only-managed --project "$APP"
"$UV" cache prune --project "$APP" >/dev/null 2>&1 || true
EXE="$PREFIX/venv/bin/mocapapp"

step "Downloading the pose-estimation models (about 145 MB, once) ..."
if [ -n "${MOCAPAPP_NO_MODELS:-}" ]; then
  echo "Skipped; the app offers the download at first start."
elif ! "$EXE" models --download; then
  echo "The models could not be downloaded now; the app will offer it again at first start."
fi

step "Checking the installation ..."
doctor_ok=1
"$EXE" doctor --dir "$HOME" || doctor_ok=0

step "Creating the app in $APPDIR ..."
BUNDLE="$APPDIR/mocapapp.app"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>mocapapp</string>
  <key>CFBundleDisplayName</key><string>mocapapp</string>
  <key>CFBundleIdentifier</key><string>io.github.florin5678.mocapapp</string>
  <key>CFBundleExecutable</key><string>mocapapp</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$("$EXE" --version | cut -d' ' -f2)</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$BUNDLE/Contents/MacOS/mocapapp" <<LAUNCH
#!/bin/bash
cd "\$HOME"
exec "$PREFIX/venv/bin/mocapapp-gui" "\$@"
LAUNCH
chmod +x "$BUNDLE/Contents/MacOS/mocapapp"
printf '{"ref": "%s", "version": "%s", "installed": "%s"}\n' "$REF" "$("$EXE" --version)" "$(date +%Y-%m-%dT%H:%M:%S)" \
  > "$PREFIX/install.json"

echo
if [ "$doctor_ok" = 1 ]; then
  printf '\033[32mDone: mocapapp is installed. Open it from %s (or Spotlight: mocapapp).\033[0m\n' "$APPDIR"
else
  echo "Installed, but the check above found problems; the app explains them when it starts."
fi
finish 0
