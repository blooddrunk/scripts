# WSL Frontend Dev Setup

An opinionated, repeatable bootstrap for a fresh Ubuntu-on-WSL installation.
It is deliberately small and user-local where possible, so it is easy to inspect, rerun, and maintain in your own GitHub repository.

## What it installs

- Zsh, with Zsh set as the default login shell.
- Powerlevel10k, plus the recommended MesloLGS NF font family.
- `zsh-autosuggestions` and `zsh-syntax-highlighting`.
- `nvm`, with the latest Node.js LTS installed and selected as the default Node version.
- Useful frontend/developer basics: GitHub CLI (`gh`), Git, build tools, Python 3, `libssl-dev`, `fzf`, ripgrep, fd, bat, jq, tree, and direnv.

The extra CLI tools are convenient defaults for frontend work. Disable them with `INSTALL_EXTRA_TOOLS=0` if you want only the core setup.

## Install directly from GitHub

After creating this repository on GitHub, replace the placeholder with your username and run this inside Ubuntu/WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/wsl-dev-setup/main/install.sh | bash
```

Do not prefix that command with `sudo`; the script asks for sudo only when it needs to install Ubuntu packages or change your login shell.

For a version you can inspect before running:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/wsl-dev-setup.git
cd wsl-dev-setup
./install.sh
```

## After installation

1. Restart the WSL terminal, or run `exec zsh`.
2. Run `p10k configure` and choose your preferred prompt style.
3. In Windows Terminal, set the profile font face to `MesloLGS NF` if it is not already selected. The installer attempts to register the fonts in your Windows user font directory when it detects WSL; Windows Terminal may need to be restarted before the font appears.
4. Authenticate GitHub CLI when you are ready to work with GitHub:

   ```bash
   gh auth login
   gh auth setup-git
   ```

5. Verify the result:

   ```bash
   node --version
   npm --version
   nvm current
   git --version
   ```

## Rerunning and options

The installer is safe to rerun:

- It does not overwrite an existing `.zshrc`; it creates a one-time backup at `~/.zshrc.pre-wsl-dev-setup` and owns only the marked block between `wsl-dev-setup` markers.
- Existing plugin/theme Git clones are left intact.
- The latest Node.js LTS is checked and installed through `nvm` each time.

Useful environment variables:

```bash
# Skip the additional CLI tools.
INSTALL_EXTRA_TOOLS=0 ./install.sh

# Keep bash (or another shell) as the login shell.
SKIP_DEFAULT_SHELL=1 ./install.sh

# Skip downloading/registering the Meslo fonts.
SKIP_FONTS=1 ./install.sh

# Choose a different pinned nvm installer release.
NVM_VERSION=v0.40.6 ./install.sh
```

## Layout

The setup keeps third-party shell code out of the repository and stores it under the user profile:

```text
~/.local/share/zsh/plugins/zsh-autosuggestions
~/.local/share/zsh/plugins/zsh-syntax-highlighting
~/.local/share/powerlevel10k
${XDG_CONFIG_HOME:-~}/nvm
${XDG_DATA_HOME:-~/.local/share}/fonts/MesloLGS-NF
```

The script uses no shell framework, which keeps startup behavior transparent. If you later decide to use Oh My Zsh, Zim, or another plugin manager, remove the managed plugin/theme lines first so the same components are not loaded twice.

## Good next additions

This intentionally does not install Docker, VS Code, pnpm, or language-specific tools because those choices depend on how you work and whether you use Windows or Linux versions. A natural next step would be a small optional profile for your preferred package manager, for example pnpm, plus a project template containing `.editorconfig`, `.nvmrc`, and common frontend linting defaults.

Powerlevel10k is included because it is your requested theme. Its upstream project currently describes maintenance/support as limited, so keeping the theme isolated in one directory makes a future switch straightforward if you ever want one.

## Upstream references

- [nvm installation and LTS commands](https://github.com/nvm-sh/nvm#install--update-script)
- [GitHub CLI installation on Linux](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)
- [`gh auth login` and Git authentication](https://cli.github.com/manual/gh_auth_login)
- [Powerlevel10k manual installation and recommended font](https://github.com/romkatv/powerlevel10k#manual)
- [zsh-autosuggestions manual installation](https://github.com/zsh-users/zsh-autosuggestions#manual-git-clone)
- [zsh-syntax-highlighting installation](https://github.com/zsh-users/zsh-syntax-highlighting/blob/master/INSTALL.md)
