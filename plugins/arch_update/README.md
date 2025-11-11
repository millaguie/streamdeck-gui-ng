# Arch Linux Update Monitor Plugin

This plugin monitors available system updates for Arch Linux and displays the update count on a Stream Deck button. Inspired by the [arch-update](https://github.com/RaphaelRochet/arch-update) GNOME Shell extension.

## Features

- **Multi-Source Updates**: Monitor pacman, AUR, and Flatpak updates simultaneously
- **Visual Indicators**: Button color changes based on update count (green = up to date, blue = some updates, orange/red = many updates)
- **Update Breakdown**: Shows count per source (P: pacman, A: AUR, F: Flatpak)
- **New Update Notifications**: Automatically switches to plugin's page when new updates are detected
- **One-Click Updates**: Press button to launch your configured update command in a terminal
- **Flexible Configuration**: Support for any AUR helper (yay, paru, etc.) and terminal emulator
- **Automatic Checks**: Configurable check interval

## Requirements

- Arch Linux (or Arch-based distribution)
- `pacman-contrib` package (for `checkupdates` command)
- Optional: AUR helper (yay, paru, etc.) for AUR updates
- Optional: Flatpak for Flatpak updates

### Installation

```bash
# Install required package
sudo pacman -S pacman-contrib

# Optional: Install AUR helper
# yay
yay -S yay

# paru
sudo pacman -S paru

# Optional: Install Flatpak
sudo pacman -S flatpak
```

## Configuration

### Required Variables

None! The plugin works with defaults, but you'll probably want to customize it.

### Basic Configuration

- **check_command**: Command to check for updates (default: `checkupdates`)
- **update_command**: Command to run when updating (default: `sudo pacman -Syu`)
- **terminal**: Terminal emulator to use (default: `gnome-terminal`)
- **system_name**: Display name for this system (default: `System`)

### AUR Support

- **aur_check_command**: Command to check AUR updates
  - For yay: `yay -Qu`
  - For paru: `paru -Qu`
  - Leave empty to disable AUR checking

### Flatpak Support

- **flatpak_check**: Enable Flatpak update checking (default: `false`)

### Terminal Configuration

Different terminals use different flags to execute commands:

| Terminal | terminal value | terminal_execute_flag |
|----------|---------------|----------------------|
| gnome-terminal | `gnome-terminal` | `--` |
| konsole | `konsole` | `-e` |
| xfce4-terminal | `xfce4-terminal` | `-x` |
| alacritty | `alacritty` | `-e` |
| kitty | `kitty` | `-e` |
| xterm | `xterm` | `-e` |
| urxvt | `urxvt` | `-e` |
| terminator | `terminator` | `-e` |

### Notification Settings

- **check_interval**: Seconds between checks (default: `3600` = 1 hour)
- **notify_on_updates**: Request page switch when new updates available (default: `true`)
- **notify_duration**: Seconds to show page on new updates (default: `10`)

### Display Settings

- **show_count_when_zero**: Show "0 updates" instead of "Up to date" (default: `false`)

## How It Works

### Update Checking

The plugin runs three checks (if configured):

1. **Pacman**: Uses `checkupdates` command (from pacman-contrib)
   - This is safe to run as regular user
   - Doesn't require root access
   - Doesn't modify any system state

2. **AUR**: Uses your configured AUR helper
   - Common: `yay -Qu` or `paru -Qu`
   - Shows updates for AUR packages

3. **Flatpak**: Uses `flatpak remote-ls --updates`
   - Shows updates for Flatpak applications

### Display States

**Up to Date** (Green):
- No updates available
- Shows "Up to date" or "0 updates"

**Updates Available** (Blue/Orange/Red):
- Blue: 1-9 updates
- Orange: 10-19 updates
- Red: 20+ updates
- Shows total count and breakdown by source

**Checking** (Gray):
- Currently checking for updates
- Shows "Checking..."

**Error** (Dark Red):
- Error occurred during check
- Shows "Error"

### Button Press

When you press the button:
1. Opens your configured terminal emulator
2. Runs your configured update command
3. You can interact with the update process
4. Plugin continues monitoring in background

### New Update Detection

When new updates appear:
1. Plugin detects the change
2. If `notify_on_updates` is enabled:
   - Switches to the plugin's page
   - Shows for `notify_duration` seconds
   - Returns to previous page automatically
3. Button shows new update count

## Example Configurations

### Minimal Configuration

```yaml
system_name: Main PC
```

Uses all defaults - checks pacman updates every hour.

### Full Arch + AUR with yay

```yaml
system_name: Desktop
check_command: checkupdates
aur_check_command: yay -Qu
update_command: yay -Syu
terminal: alacritty
terminal_execute_flag: -e
check_interval: 1800  # 30 minutes
notify_on_updates: true
notify_duration: 15
```

### Pacman + Flatpak

```yaml
system_name: Laptop
flatpak_check: true
update_command: sudo pacman -Syu && flatpak update
terminal: konsole
terminal_execute_flag: -e
check_interval: 7200  # 2 hours
```

### Server (No AUR, frequent checks)

```yaml
system_name: Server
check_interval: 600  # 10 minutes
notify_on_updates: true
notify_duration: 30  # Show longer for servers
terminal: xterm
terminal_execute_flag: -e
```

### Using paru instead of yay

```yaml
system_name: Workstation
aur_check_command: paru -Qu
update_command: paru -Syu
terminal: kitty
terminal_execute_flag: -e
```

### Custom Check Command

For systems with custom update scripts:

```yaml
check_command: /usr/local/bin/my-check-updates
update_command: /usr/local/bin/my-update-system
```

## Troubleshooting

### "Command not found: checkupdates"

Install pacman-contrib:
```bash
sudo pacman -S pacman-contrib
```

### AUR Updates Not Showing

1. Verify AUR helper is installed:
   ```bash
   which yay
   # or
   which paru
   ```

2. Test the command manually:
   ```bash
   yay -Qu
   # Should list available updates
   ```

3. Check plugin logs for errors

### Terminal Doesn't Open

1. Verify terminal is installed:
   ```bash
   which gnome-terminal
   ```

2. Check terminal execute flag is correct (see table above)

3. Test command manually:
   ```bash
   gnome-terminal -- bash -c "echo test; sleep 5"
   ```

### Button Shows "Error"

Check the plugin logs for detailed error messages. Common causes:
- Missing dependencies
- Incorrect command syntax
- Permission issues
- Network problems (for AUR/Flatpak)

### Updates Not Detected

1. Verify check interval hasn't expired yet
2. Manually check for updates:
   ```bash
   checkupdates
   ```
3. Check if updates are actually available
4. Review plugin logs for check failures

### Too Many False Positives

Some AUR helpers might show packages that don't really need updates:
- Check your AUR helper configuration
- Increase `check_interval` to reduce checks
- Disable `notify_on_updates` for less interruptions

## Command Examples

### Update Commands

**Basic pacman update:**
```bash
sudo pacman -Syu
```

**Update with yay (AUR):**
```bash
yay -Syu
```

**Update with paru (AUR):**
```bash
paru -Syu
```

**Update pacman and Flatpak:**
```bash
sudo pacman -Syu && flatpak update
```

**Update with confirmation:**
```bash
sudo pacman -Syu --noconfirm  # Be careful!
```

**Custom script:**
```bash
/usr/local/bin/system-update.sh
```

### Check Commands

**Standard pacman:**
```bash
checkupdates
```

**Alternative (requires root):**
```bash
sudo pacman -Sy && pacman -Qu
```

**Custom sync first:**
```bash
sudo pacman -Sy > /dev/null 2>&1; checkupdates
```

## Tips

1. **Adjust Check Interval**: Don't check too frequently to avoid network overhead
   - Desktop: 30-60 minutes
   - Laptop: 1-2 hours
   - Server: 5-10 minutes

2. **Multiple Instances**: Create multiple buttons for different systems
   - One for pacman only (frequent checks)
   - One for AUR (less frequent)
   - One for Flatpak

3. **Terminal Preferences**: Configure your terminal for update commands
   - Enable scrollback for long update lists
   - Use a profile with readable colors
   - Consider terminal multiplexers (tmux/screen)

4. **Update Strategy**:
   - Review updates before applying (don't use --noconfirm)
   - Check Arch news before major updates
   - Test in VM for critical systems

5. **Notification Management**:
   - Enable `notify_on_updates` for production systems
   - Disable for development systems with frequent updates
   - Adjust `notify_duration` based on importance

## Integration with arch-update

This plugin provides similar functionality to the [arch-update](https://github.com/RaphaelRochet/arch-update) GNOME extension but for Stream Deck:

| Feature | arch-update | This Plugin |
|---------|------------|-------------|
| Check for updates | ✓ | ✓ |
| Show count | ✓ | ✓ |
| AUR support | ✓ | ✓ |
| Flatpak support | ✓ | ✓ |
| Notifications | ✓ | ✓ (page switch) |
| One-click update | ✓ | ✓ |
| Custom commands | ✓ | ✓ |
| Update breakdown | ✗ | ✓ |
| Visual color coding | Limited | ✓ |
| Multiple systems | ✗ | ✓ (multiple buttons) |

## Security Notes

- The plugin runs `checkupdates` which is read-only and safe
- Update commands may require sudo password
- Consider using `sudo` with NOPASSWD for update commands (not recommended for most users)
- Plugin runs as your user, not root
- Commands are not executed in a shell (no shell injection)

## Performance

- Update checks take 1-5 seconds (pacman)
- AUR checks can take 10-30 seconds
- Flatpak checks take 2-5 seconds
- Plugin uses minimal resources when idle
- Checks run in background thread

## License

MIT License - Feel free to modify and distribute

## Credits

Inspired by:
- [arch-update](https://github.com/RaphaelRochet/arch-update) by Raphaël Rochet
- The Arch Linux community and package maintainers
