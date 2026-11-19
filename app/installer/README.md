# Installers

The installers are thin **bootstrappers** (PROJECT.md D11, §9). They do not bundle Python, because OpenSim, ONNX Runtime and PySide6 do not bundle reliably with PyInstaller-style tools. Instead they download a pinned toolchain and then the **locked** environment.

- **uv:** a pinned version, verified by SHA-256.
- **The app source:** the tagged release, downloaded from GitHub.
- **Python and dependencies:** a managed Python 3.12 and every package exactly as pinned in `app/uv.lock` (`uv sync --frozen`).
- **The pose models:** downloaded once. Afterwards everything works offline.

Everything goes into a private folder; no admin rights are needed:

- Windows: `%LOCALAPPDATA%\mocapapp`
- macOS: `~/Library/Application Support/mocapapp`

Running the installer again repairs or updates the installation. Uninstalling never touches session folders.

| File | What it is |
|---|---|
| `windows/install.ps1`, `windows/uninstall.ps1` | The bootstrapper and its remover. Must stay ASCII: Windows PowerShell 5.1 reads BOM-less files as ANSI. Besides the shortcuts, `install.ps1` registers the `.mocapproj` file type for the user (`HKCU\Software\Classes`, no admin rights), so double-clicking a project file opens it in the app; `-NoShortcuts` skips both. `uninstall.ps1` removes the file type, not the project files. |
| `windows/mocapapp.iss` | Inno Setup 6 script. It builds `mocapapp-setup.exe`, a per-user setup that runs `install.ps1` in a visible progress window and registers the uninstaller. It sets `RedirectionGuard=no`: with Windows' RedirectionGuard on, uv's Python junction fails with "untrusted mount point". |
| `windows/license.txt` | Shown by the setup: research use only, plus Pose2Sim's BSD-3 licence. |
| `macos/Install mocapapp.command`, `macos/Uninstall mocapapp.command` | Double-clickable in Finder. They create `~/Applications/mocapapp.app`. They need macOS 15 or newer, because OpenSim's wheels require it. |

## Testing an installer from a checkout

The `-Source` option installs from a local checkout instead of downloading the release. To reuse your uv cache and save downloading several GB again, set `UV_CACHE_DIR` first:

```powershell
$env:UV_CACHE_DIR = (uv cache dir)
powershell -ExecutionPolicy Bypass -File app\installer\windows\install.ps1 -Source D:\GitHub\mocap -Prefix C:\temp\mocap-test -NoShortcuts
powershell -ExecutionPolicy Bypass -File app\installer\windows\uninstall.ps1 -Prefix C:\temp\mocap-test
```

On macOS, use `MOCAPAPP_SOURCE=~/mocap MOCAPAPP_PREFIX=/tmp/mocap-test ./"Install mocapapp.command"`.

## Making a release

1. Set the version in `app/pyproject.toml` and `app/src/mocapapp/__init__.py`, and use the same version in the default ref of the three installer scripts (`mocapapp-v<version>`). `tests/test_m6.py` checks that they agree.
2. If the fork was rebased on a newer Pose2Sim release, update the Pose2Sim version in `install.ps1` and `Install mocapapp.command`. Installs from a source zip cannot read it from git.
3. Run `uv lock`, the fast tests and the slow tests (`uv run pytest -m slow`).
4. Push a tag `mocapapp-v<version>`. The `mocapapp` GitHub workflow then:
   - runs the tests on Windows and macOS;
   - installs a test build of `mocapapp-setup.exe` silently on Windows, runs `doctor`, and uninstalls it;
   - builds `mocapapp-setup.exe` (Inno Setup) and `mocapapp-macos.zip`;
   - attaches both to a **draft** pre-release.
5. Try both installers on a clean machine, then publish the draft.

The installers are not code-signed. Windows SmartScreen asks *More info > Run anyway*. On macOS, right-click the file and choose *Open* the first time. The user guide explains both.
