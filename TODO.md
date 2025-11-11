# StreamDeck UI - TODO List

## Plugin Ideas (Planned/Proposed)

### RocketChat Plugin
- Monitor unread messages in RocketChat
- Show unread count on button
- Flash/alert on new messages
- Open RocketChat in browser on button press
- Configuration: RocketChat server URL, user token, channels to monitor

### Thunderbird Plugin
- Monitor unread email count in Thunderbird
- Show unread count on button
- Flash/alert on new emails
- Launch Thunderbird or bring it to focus on button press
- Configuration: Thunderbird profile path, refresh interval

## Application Features (Planned/Proposed)

### GNOME Bar Integration
- Run StreamDeck UI as a system tray/GNOME bar icon
- Minimize to tray instead of closing
- Quick access to settings from tray icon
- Show/hide main window from tray
- Display deck connection status in tray

## Additional Plugin Ideas

### System Monitor Plugin
- Display CPU usage, RAM usage, disk usage, or temperature
- Configurable thresholds with color changes (green/yellow/red)
- Click to open system monitor application
- Configuration: metric to monitor, refresh interval, warning thresholds

### Spotify/Media Control Plugin
- Show currently playing track (scrolling text)
- Album art as button icon
- Click to play/pause
- Long press to skip track
- Configuration: player to control (Spotify, VLC, etc. via MPRIS)

### Docker Container Plugin
- Monitor status of Docker containers
- Show container count (running/stopped)
- Click to start/stop specific container
- Configuration: container names to monitor, docker socket path

### VPN Plugin
- Display VPN connection status
- Toggle VPN connection on/off
- Show current IP address or location
- Configuration: VPN provider (OpenVPN, WireGuard, NetworkManager)

### Meeting Status Plugin
- Integrate with Google Calendar/Outlook
- Show upcoming meetings
- Display "In Meeting" status with countdown
- Click to join meeting link
- Configuration: calendar API credentials, meeting URL extraction

### Weather Plugin
- Display current weather and temperature
- Weather icon based on conditions
- Click to show forecast
- Configuration: location, units (C/F), weather API key

### GitHub/GitLab/Gitea Plugin
- Monitor repository issues, PRs, or CI/CD status
- Show notification count
- Click to open repository in browser
- Configuration: repo URL, API token, notification types

### Smart Home Plugin
- Control Home Assistant/MQTT devices
- Toggle lights, switches, scenes
- Display sensor states
- Configuration: Home Assistant URL, entity IDs, MQTT broker

### Screenshot/Screen Recording Plugin
- Take screenshot of full screen or region
- Start/stop screen recording
- Upload to clipboard or cloud service
- Configuration: save location, screenshot tool (scrot, flameshot, etc.)

### Clipboard Manager Plugin
- Show clipboard history
- Click to cycle through recent clipboard items
- Pin frequently used snippets
- Configuration: history size, exclusion patterns

### Audio Device Switcher Plugin
- Switch between audio output devices (headphones, speakers, HDMI)
- Display current active device with icon
- Configuration: available audio devices, switch method (PulseAudio/PipeWire)

### Pomodoro Timer Plugin
- Start/stop Pomodoro timer
- Display remaining time
- Visual/notification alerts on completion
- Configuration: work/break duration, notification method

## Core Application Enhancements

### Profile/Preset System
- Create multiple configuration profiles
- Quick switch between profiles (work, gaming, streaming)
- Import/export profiles
- Share profiles with community

### Macro Recording
- Record keyboard/mouse actions
- Play back recorded macros on button press
- Edit macro steps in UI
- Support for delays and loops

### Multi-Deck Support Improvements
- Better visual organization for multiple decks
- Deck-specific profiles
- Sync configurations across decks
- Priority/fallback deck selection

### Plugin Marketplace/Repository
- Central repository for community plugins
- One-click plugin installation
- Plugin ratings and reviews
- Automatic plugin updates
- Plugin search and discovery

### Accessibility Features
- Screen reader support
- Keyboard navigation for all UI elements
- High contrast themes
- Configurable button haptic feedback (if supported by hardware)

### Advanced Button Features
- Button hold duration actions (short press vs long press)
- Multi-tap detection (double-tap, triple-tap)
- Gesture support (swipe patterns)
- Button groups/layers (shift/modifier keys for buttons)

### Logging and Debugging
- Plugin execution logs viewer
- Performance metrics for plugins
- Button press history
- Error reporting and diagnostics

### Cloud Sync (Optional)
- Backup configurations to cloud
- Sync across multiple machines
- Version history for configurations
- End-to-end encryption for sensitive data

### Integration with Desktop Environments
- KDE Plasma integration
- XFCE panel support
- i3/sway workspace control
- Global hotkeys for page switching
