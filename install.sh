#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

# This script is intentionally self-contained so it can be run directly from
# GitHub with `curl ... | bash`.

readonly NVM_VERSION="${NVM_VERSION:-v0.40.6}"
readonly NVM_INSTALL_URL="https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh"
readonly P10K_REPO="https://github.com/romkatv/powerlevel10k.git"
readonly AUTOSUGGESTIONS_REPO="https://github.com/zsh-users/zsh-autosuggestions.git"
readonly SYNTAX_HIGHLIGHTING_REPO="https://github.com/zsh-users/zsh-syntax-highlighting.git"
readonly FONT_BASE_URL="https://github.com/romkatv/powerlevel10k-media/raw/master"
readonly GH_KEYRING_URL="https://cli.github.com/packages/githubcli-archive-keyring.gpg"
readonly GH_KEYRING_SHA256="6084d5d7bd8e288441e0e94fc6275570895da18e6751f70f057485dc2d1a811b"
readonly PNPM_INSTALL_URL="https://get.pnpm.io/install.sh"
readonly TARGET_USER="$(id -un)"
readonly ZDOTDIR_PATH="${ZDOTDIR:-$HOME}"
readonly ZSHRC="${ZDOTDIR_PATH}/.zshrc"
readonly ZSH_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/zsh"
readonly ZSH_PLUGIN_DIR="${ZSH_DATA_DIR}/plugins"
readonly P10K_DIR="${P10K_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/powerlevel10k}"
readonly NVM_DIR_PATH="${NVM_DIR:-${XDG_CONFIG_HOME:-$HOME}/nvm}"
readonly PNPM_HOME_PATH="${PNPM_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/pnpm}"
readonly LINUX_FONT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/MesloLGS-NF"
readonly ZSHRC_START="# >>> wsl-dev-setup >>>"
readonly ZSHRC_END="# <<< wsl-dev-setup <<<"

readonly -a FONT_FILES=(
  "MesloLGS NF Regular.ttf"
  "MesloLGS NF Bold.ttf"
  "MesloLGS NF Italic.ttf"
  "MesloLGS NF Bold Italic.ttf"
)

if [[ -n "${INSTALL_EXTRA_TOOLS+x}" && -z "${INSTALL_EXTRAS+x}" ]]; then
  INSTALL_EXTRAS="$INSTALL_EXTRA_TOOLS"
fi

INSTALL_SHELL="${INSTALL_SHELL:-1}"
INSTALL_NODE="${INSTALL_NODE:-1}"
INSTALL_GH="${INSTALL_GH:-1}"
INSTALL_PNPM="${INSTALL_PNPM:-1}"
INSTALL_EXTRAS="${INSTALL_EXTRAS:-1}"

log() {
  printf '\033[1;32m==>\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

on_error() {
  local exit_code=$?
  printf '\033[1;31merror:\033[0m setup failed near line %s (exit %s)\n' "$1" "$exit_code" >&2
  exit "$exit_code"
}

trap 'on_error "$LINENO"' ERR

validate_component_selection() {
  local component value

  for component in INSTALL_SHELL INSTALL_NODE INSTALL_GH INSTALL_PNPM INSTALL_EXTRAS; do
    value="${!component}"
    [[ "$value" == "0" || "$value" == "1" ]] || die "${component} must be 0 or 1; received: ${value}"
  done
}

ask_yes_no() {
  local label=$1
  local default=$2
  local answer

  if [[ "$default" == "1" ]]; then
    printf '%s [Y/n] ' "$label"
  else
    printf '%s [y/N] ' "$label"
  fi

  if ! IFS= read -r answer < /dev/tty; then
    SELECTION_REPLY="$default"
    return
  fi

  case "${answer,,}" in
    y|yes) SELECTION_REPLY=1 ;;
    n|no) SELECTION_REPLY=0 ;;
    '') SELECTION_REPLY="$default" ;;
    *)
      warn "Unrecognized answer; keeping the default."
      SELECTION_REPLY="$default"
      ;;
  esac
}

select_components() {
  validate_component_selection

  if [[ "${SETUP_NONINTERACTIVE:-0}" == "1" || "${CI:-0}" == "1" || ! -r /dev/tty ]]; then
    log "Using component defaults; set INSTALL_* variables to customize a non-interactive run."
    return
  fi

  printf '\nChoose setup components. Press Enter to keep each default.\n\n'
  ask_yes_no "Install the Zsh + Powerlevel10k shell experience?" "$INSTALL_SHELL"
  INSTALL_SHELL="$SELECTION_REPLY"
  ask_yes_no "Install nvm and the latest Node.js LTS?" "$INSTALL_NODE"
  INSTALL_NODE="$SELECTION_REPLY"
  ask_yes_no "Install GitHub CLI (gh)?" "$INSTALL_GH"
  INSTALL_GH="$SELECTION_REPLY"
  ask_yes_no "Install pnpm?" "$INSTALL_PNPM"
  INSTALL_PNPM="$SELECTION_REPLY"
  ask_yes_no "Install extra CLI tools (fzf, ripgrep, fd, bat, jq, tree, direnv)?" "$INSTALL_EXTRAS"
  INSTALL_EXTRAS="$SELECTION_REPLY"
  validate_component_selection

  if [[ "$INSTALL_SHELL" == "0" && ( "$INSTALL_NODE" == "1" || "$INSTALL_PNPM" == "1" ) ]]; then
    warn "Shell setup is disabled; Node/pnpm will be installed and configured for Bash instead."
  fi
}

require_ubuntu() {
  [[ -r /etc/os-release ]] || die "This script needs an Ubuntu-based WSL distro."

  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "This script targets Ubuntu; detected ${PRETTY_NAME:-an unknown distro}."
}

require_non_root() {
  [[ "$EUID" -ne 0 ]] || die "Run this as your normal user, without sudo. The script uses sudo only for apt and the login shell."
  command -v sudo >/dev/null 2>&1 || die "sudo is required. Install it first, then rerun this script."
}

install_apt_packages() {
  local -a packages=(
    ca-certificates
    curl
    git
    build-essential
    libssl-dev
    pkg-config
    python3
    unzip
    zip
    xz-utils
  )

  if [[ "$INSTALL_SHELL" == "1" ]]; then
    packages+=(
      fontconfig
      zsh
    )
  fi

  if [[ "$INSTALL_PNPM" == "1" ]]; then
    packages+=(libatomic1)
  fi

  if [[ "$INSTALL_EXTRAS" == "1" ]]; then
    packages+=(
      bat
      direnv
      fd-find
      fzf
      jq
      ripgrep
      tree
    )
  fi

  log "Installing Ubuntu packages"
  sudo -v
  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
}

install_github_cli() {
  if command -v gh >/dev/null 2>&1; then
    log "Already installed: GitHub CLI"
    return
  fi

  local architecture keyring_file source_file temporary_file actual_sha256
  architecture="$(dpkg --print-architecture)"
  keyring_file="/etc/apt/keyrings/githubcli-archive-keyring.gpg"
  source_file="/etc/apt/sources.list.d/github-cli.list"
  temporary_file="$(mktemp)"

  log "Installing GitHub CLI from the official package repository"
  if ! curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 \
    "$GH_KEYRING_URL" --output "$temporary_file"; then
    rm -f "$temporary_file"
    die "Could not download the GitHub CLI package keyring."
  fi

  actual_sha256="$(sha256sum "$temporary_file" | cut -d' ' -f1)"
  if [[ "$actual_sha256" != "$GH_KEYRING_SHA256" ]]; then
    rm -f "$temporary_file"
    die "The GitHub CLI package keyring checksum did not match the expected value."
  fi

  sudo install -D -m 0644 "$temporary_file" "$keyring_file"
  rm -f "$temporary_file"

  temporary_file="$(mktemp)"
  printf 'deb [arch=%s signed-by=%s] https://cli.github.com/packages stable main\n' \
    "$architecture" "$keyring_file" > "$temporary_file"
  sudo install -D -m 0644 "$temporary_file" "$source_file"
  rm -f "$temporary_file"

  sudo apt-get update
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gh
}

clone_if_missing() {
  local repository=$1
  local destination=$2

  if [[ -d "${destination}/.git" ]]; then
    log "Already installed: ${destination}"
    return
  fi

  [[ ! -e "$destination" ]] || die "Refusing to replace an existing non-Git directory: ${destination}"

  mkdir -p "$(dirname "$destination")"
  log "Cloning $(basename "$destination")"
  git clone --depth=1 --single-branch "$repository" "$destination"
}

install_zsh_plugins() {
  clone_if_missing "$AUTOSUGGESTIONS_REPO" "${ZSH_PLUGIN_DIR}/zsh-autosuggestions"
  clone_if_missing "$SYNTAX_HIGHLIGHTING_REPO" "${ZSH_PLUGIN_DIR}/zsh-syntax-highlighting"
  clone_if_missing "$P10K_REPO" "$P10K_DIR"
}

install_nvm_and_node() {
  mkdir -p "$NVM_DIR_PATH"

  if [[ ! -s "${NVM_DIR_PATH}/nvm.sh" ]]; then
    local installer profile_stub
    installer="$(mktemp)"
    profile_stub="$(mktemp)"

    log "Installing nvm ${NVM_VERSION}"
    curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 \
      "$NVM_INSTALL_URL" --output "$installer"

    # The official installer appends shell init lines to PROFILE. Use a
    # temporary profile because this script owns the init block in .zshrc.
    PROFILE="$profile_stub" NVM_DIR="$NVM_DIR_PATH" bash "$installer"
    rm -f "$installer" "$profile_stub"
  else
    log "Already installed: nvm"
  fi

  export NVM_DIR="$NVM_DIR_PATH"
  # shellcheck disable=SC1090
  source "${NVM_DIR_PATH}/nvm.sh"

  log "Installing the latest Node.js LTS"
  nvm install --lts

  local lts_version
  lts_version="$(nvm version 'lts/*')"
  [[ "$lts_version" != "N/A" && "$lts_version" != "none" ]] || die "nvm could not resolve the latest Node.js LTS version."

  nvm alias default "$lts_version"
  nvm use "$lts_version" >/dev/null
}

install_pnpm() {
  export PNPM_HOME="$PNPM_HOME_PATH"
  export PATH="$PNPM_HOME:$PNPM_HOME/bin:$PATH"

  if command -v pnpm >/dev/null 2>&1; then
    log "Already installed: pnpm"
    return
  fi

  local installer setup_home profile_shell
  installer="$(mktemp)"
  setup_home="$(mktemp -d)"
  profile_shell="${SHELL:-$(command -v bash)}"

  if [[ "$INSTALL_SHELL" == "1" ]] && command -v zsh >/dev/null 2>&1; then
    profile_shell="$(command -v zsh)"
  fi

  log "Installing pnpm"
  curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 \
    "$PNPM_INSTALL_URL" --output "$installer"

  if [[ -n "${PNPM_VERSION:-}" ]]; then
    if ! HOME="$setup_home" PNPM_HOME="$PNPM_HOME_PATH" PNPM_VERSION="$PNPM_VERSION" ENV="$setup_home/.profile" SHELL="$profile_shell" \
      sh "$installer"; then
      rm -rf "$installer" "$setup_home"
      die "pnpm installation failed."
    fi
  elif ! HOME="$setup_home" PNPM_HOME="$PNPM_HOME_PATH" ENV="$setup_home/.profile" SHELL="$profile_shell" \
    sh "$installer"; then
    rm -rf "$installer" "$setup_home"
    die "pnpm installation failed."
  fi

  rm -f "$installer"
  rm -rf "$setup_home"
  command -v pnpm >/dev/null 2>&1 || die "pnpm was installed but is not available on PATH."
  log "Installed pnpm $(pnpm --version)"
}

download_font() {
  local font_name=$1
  local encoded_name=${font_name// /%20}
  local destination="${LINUX_FONT_DIR}/${font_name}"
  local temporary_file

  [[ -s "$destination" ]] && return

  temporary_file="$(mktemp)"
  if ! curl --fail --location --retry 3 --retry-all-errors --connect-timeout 15 \
    "${FONT_BASE_URL}/${encoded_name}" --output "$temporary_file"; then
    rm -f "$temporary_file"
    die "Could not download ${font_name}."
  fi
  mv "$temporary_file" "$destination"
}

install_windows_font() {
  local font_name=$1
  local windows_path=$2
  local font_path_quoted font_name_quoted powershell_command

  font_path_quoted="$(printf '%s' "$windows_path" | sed "s/'/''/g" | sed "s/^/'/; s/$/'/")"
  font_name_quoted="$(printf '%s' "$font_name" | sed "s/'/''/g" | sed "s/^/'/; s/$/'/")"
  printf -v powershell_command '$ErrorActionPreference = "Stop"; $fontPath = %s; $fontName = %s; $fontDirectory = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"; $registryPath = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"; New-Item -ItemType Directory -Force -Path $fontDirectory | Out-Null; $targetPath = Join-Path $fontDirectory $fontName; Copy-Item -LiteralPath $fontPath -Destination $targetPath -Force; New-Item -Path $registryPath -Force | Out-Null; $registryName = "{0} (TrueType)" -f [IO.Path]::GetFileNameWithoutExtension($fontName); New-ItemProperty -Path $registryPath -Name $registryName -Value $targetPath -PropertyType String -Force | Out-Null' "$font_path_quoted" "$font_name_quoted"

  if ! powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
    -Command "$powershell_command"; then
    warn "Windows font registration failed for ${font_name}; install it manually if it does not appear in Windows Terminal."
  fi
}

install_fonts() {
  [[ "${SKIP_FONTS:-0}" == "1" ]] && { warn "Skipping Meslo Nerd Font installation (SKIP_FONTS=1)."; return; }

  log "Installing MesloLGS NF fonts for Powerlevel10k"
  mkdir -p "$LINUX_FONT_DIR"

  local font_name
  for font_name in "${FONT_FILES[@]}"; do
    download_font "$font_name"
  done

  if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f "$LINUX_FONT_DIR" >/dev/null 2>&1 || warn "Could not refresh the Linux font cache."
  fi

  if [[ -n "${WSL_INTEROP:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
    if command -v powershell.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1; then
      for font_name in "${FONT_FILES[@]}"; do
        install_windows_font "$font_name" "$(wslpath -w "${LINUX_FONT_DIR}/${font_name}")"
      done
      log "Registered MesloLGS NF in the Windows user font directory"
    else
      warn "WSL was detected, but powershell.exe or wslpath is unavailable; install MesloLGS NF on Windows manually."
    fi
  fi
}

quote_for_shell() {
  printf '%q' "$1"
}

append_zsh_block() {
  local nvm_dir_quoted p10k_dir_quoted plugin_dir_quoted pnpm_home_quoted zdotdir_quoted

  nvm_dir_quoted="$(quote_for_shell "$NVM_DIR_PATH")"
  p10k_dir_quoted="$(quote_for_shell "$P10K_DIR")"
  plugin_dir_quoted="$(quote_for_shell "$ZSH_PLUGIN_DIR")"
  pnpm_home_quoted="$(quote_for_shell "$PNPM_HOME_PATH")"
  zdotdir_quoted="$(quote_for_shell "$ZDOTDIR_PATH")"

  printf '%s\n' "$ZSHRC_START"
  printf '%s\n' '# Managed by wsl-dev-setup. Rerunning the installer replaces only this block.'

  if [[ "$INSTALL_NODE" == "1" ]]; then
    printf 'export NVM_DIR=%s\n' "$nvm_dir_quoted"
    printf '[[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"\n'
    printf '\n'
  fi

  printf '# Powerlevel10k\n'
  printf 'if [[ -r %s/powerlevel10k.zsh-theme ]]; then\n' "$p10k_dir_quoted"
  printf '  source %s/powerlevel10k.zsh-theme\n' "$p10k_dir_quoted"
  printf 'fi\n'
  printf '[[ ! -r %s/.p10k.zsh ]] || source %s/.p10k.zsh\n' "$zdotdir_quoted" "$zdotdir_quoted"

  if [[ "$INSTALL_PNPM" == "1" ]]; then
    printf '\n# pnpm\n'
    printf 'export PNPM_HOME=%s\n' "$pnpm_home_quoted"
    printf 'case ":$PATH:" in *":$PNPM_HOME:"*) ;; *) export PATH="$PNPM_HOME:$PATH" ;; esac\n'
    printf 'case ":$PATH:" in *":$PNPM_HOME/bin:"*) ;; *) export PATH="$PNPM_HOME/bin:$PATH" ;; esac\n'
  fi

  printf '\n# Completion and useful Ubuntu developer tools\n'
  printf 'autoload -Uz compinit && compinit\n'
  if [[ "$INSTALL_EXTRAS" == "1" ]]; then
    printf '[[ -r /usr/share/doc/fzf/examples/key-bindings.zsh ]] && source /usr/share/doc/fzf/examples/key-bindings.zsh\n'
    printf '[[ -r /usr/share/doc/fzf/examples/completion.zsh ]] && source /usr/share/doc/fzf/examples/completion.zsh\n'
    printf 'if command -v direnv >/dev/null 2>&1; then eval "$(direnv hook zsh)"; fi\n'
    printf 'if command -v fdfind >/dev/null 2>&1; then alias fd="fdfind"; fi\n'
    printf 'if command -v batcat >/dev/null 2>&1; then alias bat="batcat"; fi\n'
  fi

  printf '\n# zsh-autosuggestions\n'
  printf '[[ -r %s/zsh-autosuggestions/zsh-autosuggestions.zsh ]] && source %s/zsh-autosuggestions/zsh-autosuggestions.zsh\n' "$plugin_dir_quoted" "$plugin_dir_quoted"
  printf '\n# zsh-syntax-highlighting must be sourced last\n'
  printf '[[ -r %s/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]] && source %s/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh\n' "$plugin_dir_quoted" "$plugin_dir_quoted"
  printf '%s\n' "$ZSHRC_END"
}

append_bash_block() {
  local nvm_dir_quoted pnpm_home_quoted

  nvm_dir_quoted="$(quote_for_shell "$NVM_DIR_PATH")"
  pnpm_home_quoted="$(quote_for_shell "$PNPM_HOME_PATH")"

  printf '%s\n' "$ZSHRC_START"
  printf '%s\n' '# Managed by wsl-dev-setup. Rerunning the installer replaces only this block.'

  if [[ "$INSTALL_NODE" == "1" ]]; then
    printf 'export NVM_DIR=%s\n' "$nvm_dir_quoted"
    printf '[[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"\n'
  fi

  if [[ "$INSTALL_PNPM" == "1" ]]; then
    printf '\n# pnpm\n'
    printf 'export PNPM_HOME=%s\n' "$pnpm_home_quoted"
    printf 'case ":$PATH:" in *":$PNPM_HOME:"*) ;; *) export PATH="$PNPM_HOME:$PATH" ;; esac\n'
    printf 'case ":$PATH:" in *":$PNPM_HOME/bin:"*) ;; *) export PATH="$PNPM_HOME/bin:$PATH" ;; esac\n'
  fi

  printf '%s\n' "$ZSHRC_END"
}

write_managed_block() {
  local profile=$1
  local callback=$2
  local temporary_file

  mkdir -p "$(dirname "$profile")"
  touch "$profile"

  if ! grep -Fqx "$ZSHRC_START" "$profile" 2>/dev/null; then
    cp -p "$profile" "${profile}.pre-wsl-dev-setup"
  fi

  temporary_file="$(mktemp "${profile}.tmp.XXXXXX")"
  awk -v start="$ZSHRC_START" -v end="$ZSHRC_END" '
    $0 == start { inside = 1; next }
    $0 == end { inside = 0; next }
    !inside { print }
  ' "$profile" > "$temporary_file"

  {
    [[ ! -s "$temporary_file" ]] || printf '\n'
    "$callback"
  } >> "$temporary_file"

  if ! cmp -s "$temporary_file" "$profile"; then
    mv "$temporary_file" "$profile"
  else
    rm -f "$temporary_file"
  fi

  log "Configured ${profile}"
}

configure_shell_profiles() {
  if [[ "$INSTALL_SHELL" == "1" ]]; then
    write_managed_block "$ZSHRC" append_zsh_block
  elif [[ "$INSTALL_NODE" == "1" || "$INSTALL_PNPM" == "1" ]]; then
    write_managed_block "$HOME/.bashrc" append_bash_block
  fi
}

set_default_shell() {
  [[ "${SKIP_DEFAULT_SHELL:-0}" == "1" ]] && { warn "Leaving the default login shell unchanged (SKIP_DEFAULT_SHELL=1)."; return; }

  local zsh_path current_shell
  zsh_path="$(command -v zsh)"
  current_shell="$(getent passwd "$TARGET_USER" | cut -d: -f7 || true)"

  if [[ "$current_shell" == "$zsh_path" ]]; then
    log "zsh is already the default login shell"
    return
  fi

  if sudo usermod --shell "$zsh_path" "$TARGET_USER"; then
    log "Set zsh as the default login shell for ${TARGET_USER}"
  else
    warn "Could not set zsh as the default login shell; run: sudo usermod --shell ${zsh_path} ${TARGET_USER}"
  fi
}

main() {
  require_non_root
  require_ubuntu
  select_components
  install_apt_packages
  if [[ "$INSTALL_GH" == "1" ]]; then
    install_github_cli
  fi
  if [[ "$INSTALL_SHELL" == "1" ]]; then
    install_zsh_plugins
    install_fonts
    set_default_shell
  fi
  if [[ "$INSTALL_NODE" == "1" ]]; then
    install_nvm_and_node
  fi
  if [[ "$INSTALL_PNPM" == "1" ]]; then
    install_pnpm
  fi
  configure_shell_profiles

  printf '\n'
  log "Setup complete"
  if [[ "$INSTALL_SHELL" == "1" ]]; then
    printf 'Open a new WSL terminal, or run: exec zsh\n'
    printf 'Then run: p10k configure\n'
  fi
  printf 'Verify with:'
  [[ "$INSTALL_NODE" == "1" ]] && printf ' node --version'
  [[ "$INSTALL_PNPM" == "1" ]] && printf ' pnpm --version'
  [[ "$INSTALL_GH" == "1" ]] && printf ' gh --version'
  printf '\n'
}

main "$@"
