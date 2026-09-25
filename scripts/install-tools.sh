#!/usr/bin/env bash
# Install the pinned toolchain (tools.env) into a local directory, verifying the SHA-256 of every
# download against the checksum file the publisher ships next to it.
#
#   scripts/install-tools.sh                    # everything
#   scripts/install-tools.sh terraform conftest # just these
#
# Tools: terraform tflint conftest trivy terraform-docs checkov
# Environment:
#   TOOLS_DIR   install directory (default: <repo>/.tools/bin, which is git-ignored)
#
# Add the directory to PATH afterwards:  export PATH="$PWD/.tools/bin:$PATH"
#
# Supply-chain notes: downloads come only from the publishers' own release hosts over HTTPS and are
# refused on a checksum mismatch. Terraform's checksum file is fetched from the same host as the
# binary, so this defends against corruption and a tampered mirror of the binary alone; verify
# HashiCorp's GPG signature of the SHA256SUMS file yourself if that threat model matters to you.
# checkov is installed with pip at an exact version.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../tools.env
source "$repo_root/tools.env"

dest="${TOOLS_DIR:-$repo_root/.tools/bin}"
mkdir -p "$dest"

die() { echo "install-tools: $*" >&2; exit 1; }

case "$(uname -s)" in
  Linux*)               os=linux ;;
  Darwin*)              os=darwin ;;
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  *) die "unsupported OS: $(uname -s)" ;;
esac
case "$(uname -m)" in
  x86_64|amd64)  arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) die "unsupported CPU: $(uname -m)" ;;
esac
exe=""; [ "$os" = windows ] && exe=".exe"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# fetch_verified <asset-url> <checksums-url> -> prints the path of the verified download
fetch_verified() {
  local url="$1" sums_url="$2" asset file expected actual
  asset="$(basename "$url")"
  file="$tmp/$asset"
  curl -fsSL --retry 3 --retry-delay 2 -o "$file" "$url" || die "download failed: $url"
  curl -fsSL --retry 3 --retry-delay 2 -o "$tmp/$asset.sums" "$sums_url" || die "checksum download failed: $sums_url"
  expected="$(grep -E "[[:space:]*]${asset//./\\.}\$" "$tmp/$asset.sums" | head -n 1 | awk '{print $1}')"
  [ -n "$expected" ] || die "$asset is not listed in $sums_url"
  actual="$(sha256_of "$file")"
  [ "$expected" = "$actual" ] || die "SHA-256 MISMATCH for $asset (expected $expected, got $actual)"
  echo "  verified $asset ($actual)" >&2
  echo "$file"
}

unpack() { # <archive> <dir>
  mkdir -p "$2"
  case "$1" in
    *.zip)    unzip -q -o "$1" -d "$2" ;;
    *.tar.gz) tar -xzf "$1" -C "$2" ;;
    *) die "unknown archive type: $1" ;;
  esac
}

install_bin() { # <dir> <name-without-exe>
  local found
  found="$(find "$1" -type f -name "$2$exe" | head -n 1)"
  [ -n "$found" ] || die "$2$exe not found in archive"
  install -m 0755 "$found" "$dest/$2$exe"
}

have_version() { # <binary> <needle>
  [ -x "$dest/$1$exe" ] && "$dest/$1$exe" --version 2>/dev/null | grep -qF "$2"
}

install_terraform() {
  have_version terraform "$TERRAFORM_VERSION" && { echo "terraform $TERRAFORM_VERSION already installed"; return; }
  echo "terraform $TERRAFORM_VERSION"
  local base="https://releases.hashicorp.com/terraform/$TERRAFORM_VERSION"
  local f; f="$(fetch_verified "$base/terraform_${TERRAFORM_VERSION}_${os}_${arch}.zip" "$base/terraform_${TERRAFORM_VERSION}_SHA256SUMS")"
  unpack "$f" "$tmp/terraform" && install_bin "$tmp/terraform" terraform
}

install_tflint() {
  have_version tflint "$TFLINT_VERSION" && { echo "tflint $TFLINT_VERSION already installed"; return; }
  echo "tflint $TFLINT_VERSION"
  local base="https://github.com/terraform-linters/tflint/releases/download/v$TFLINT_VERSION"
  local f; f="$(fetch_verified "$base/tflint_${os}_${arch}.zip" "$base/checksums.txt")"
  unpack "$f" "$tmp/tflint" && install_bin "$tmp/tflint" tflint
}

install_conftest() {
  have_version conftest "$CONFTEST_VERSION" && { echo "conftest $CONFTEST_VERSION already installed"; return; }
  echo "conftest $CONFTEST_VERSION"
  local base="https://github.com/open-policy-agent/conftest/releases/download/v$CONFTEST_VERSION"
  local cos carch ext
  case "$os" in linux) cos=Linux ;; darwin) cos=Darwin ;; windows) cos=Windows ;; esac
  case "$arch" in amd64) carch=x86_64 ;; arm64) carch=arm64 ;; esac
  ext=tar.gz; [ "$os" = windows ] && ext=zip
  local f; f="$(fetch_verified "$base/conftest_${CONFTEST_VERSION}_${cos}_${carch}.$ext" "$base/checksums.txt")"
  unpack "$f" "$tmp/conftest" && install_bin "$tmp/conftest" conftest
}

install_trivy() {
  have_version trivy "$TRIVY_VERSION" && { echo "trivy $TRIVY_VERSION already installed"; return; }
  echo "trivy $TRIVY_VERSION"
  local base="https://github.com/aquasecurity/trivy/releases/download/v$TRIVY_VERSION"
  local tos tarch ext
  case "$os" in linux) tos=Linux ;; darwin) tos=macOS ;; windows) tos=windows ;; esac
  case "$arch" in amd64) tarch=64bit ;; arm64) tarch=ARM64 ;; esac
  ext=tar.gz; [ "$os" = windows ] && ext=zip
  local f; f="$(fetch_verified "$base/trivy_${TRIVY_VERSION}_${tos}-${tarch}.$ext" "$base/trivy_${TRIVY_VERSION}_checksums.txt")"
  unpack "$f" "$tmp/trivy" && install_bin "$tmp/trivy" trivy
}

install_terraform_docs() {
  have_version terraform-docs "$TERRAFORM_DOCS_VERSION" && { echo "terraform-docs $TERRAFORM_DOCS_VERSION already installed"; return; }
  echo "terraform-docs $TERRAFORM_DOCS_VERSION"
  local base="https://github.com/terraform-docs/terraform-docs/releases/download/v$TERRAFORM_DOCS_VERSION"
  local ext=tar.gz; [ "$os" = windows ] && ext=zip
  local f; f="$(fetch_verified "$base/terraform-docs-v${TERRAFORM_DOCS_VERSION}-${os}-${arch}.$ext" "$base/terraform-docs-v${TERRAFORM_DOCS_VERSION}.sha256sum")"
  unpack "$f" "$tmp/terraform-docs" && install_bin "$tmp/terraform-docs" terraform-docs
}

# checkov is a Python package with a large dependency tree. It goes into its own venv (system
# Pythons on current Linux distributions refuse global pip installs) and is exposed through a
# two-line wrapper, so it behaves the same on Linux, macOS and Git Bash.
install_checkov() {
  local venv py wrapper="$dest/checkov"
  venv="$(dirname "$dest")/venv"
  if [ -x "$wrapper" ] && "$wrapper" --version 2>/dev/null | grep -qF "$CHECKOV_VERSION"; then
    echo "checkov $CHECKOV_VERSION already installed"; return
  fi
  echo "checkov $CHECKOV_VERSION (pip, in $venv)"
  local python="python3"; command -v python3 >/dev/null 2>&1 || python="python"
  "$python" -m venv "$venv"
  if [ "$os" = windows ]; then py="$venv/Scripts/python.exe"; else py="$venv/bin/python"; fi
  "$py" -m pip install --quiet --disable-pip-version-check "checkov==$CHECKOV_VERSION"
  printf '#!/usr/bin/env bash\nexec "%s" -m checkov.main "$@"\n' "$py" > "$wrapper"
  chmod +x "$wrapper"
}

tools=("$@")
[ "${#tools[@]}" -gt 0 ] || tools=(terraform tflint conftest trivy terraform-docs checkov)

for t in "${tools[@]}"; do
  case "$t" in
    terraform)      install_terraform ;;
    tflint)         install_tflint ;;
    conftest)       install_conftest ;;
    trivy)          install_trivy ;;
    terraform-docs) install_terraform_docs ;;
    checkov)        install_checkov ;;
    *) die "unknown tool: $t" ;;
  esac
done

echo "installed into $dest"
