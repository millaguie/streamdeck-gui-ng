# Plugin Development Guide for StreamDeck UI

This guide explains how to create plugins for streamdeck-ui that can control Stream Deck buttons with custom logic.

## Table of Contents

- [Overview](#overview)
- [Plugin Architecture](#plugin-architecture)
- [Getting Started](#getting-started)
- [Manifest File](#manifest-file)
- [Plugin Implementation](#plugin-implementation)
- [Communication Protocol](#communication-protocol)
- [Updating Button Display](#updating-button-display)
- [Lifecycle Management](#lifecycle-management)
- [Best Practices](#best-practices)
- [Example Plugin](#example-plugin)
- [Testing Your Plugin](#testing-your-plugin)

## Overview

StreamDeck UI plugins allow you to extend the functionality of your Stream Deck by writing Python scripts that:

- Monitor external services (APIs, databases, servers, etc.)
- Update button appearance dynamically
- Respond to button presses
- Request page switches when important events occur
- Run continuously in the background or only when visible

Each plugin runs as a separate process and communicates with the main StreamDeck UI application via Unix sockets.

## Plugin Architecture

### Communication Model

```
┌─────────────────────────┐
│   StreamDeck UI (Host)  │
│                         │
│  ┌──────────────────┐   │
│  │ Plugin Manager   │   │
│  └────────┬─────────┘   │
│           │             │
└───────────┼─────────────┘
            │ Unix Socket
            │
┌───────────▼─────────────┐
│  Plugin Process         │
│  (Your Python Script)   │
│                         │
│  ┌──────────────────┐   │
│  │  BasePlugin      │   │
│  │  (Your Class)    │   │
│  └──────────────────┘   │
└─────────────────────────┘
```

### Message Flow

**Host → Plugin:**
- Button pressed/released
- Button visible/hidden (page changes)
- Configuration updates
- Shutdown requests

**Plugin → Host:**
- Update button image (raw data or rendering instructions)
- Request page switch
- Log messages
- Heartbeat (plugin alive signal)

## Getting Started

### 1. Create Plugin Directory

Create a new directory in `~/.streamdeck_ui/plugins/` for your plugin:

```bash
mkdir -p ~/.streamdeck_ui/plugins/my_plugin
cd ~/.streamdeck_ui/plugins/my_plugin
```

### 2. Required Files

Every plugin needs at minimum:
- `manifest.yaml` - Plugin metadata and configuration
- `<entry_point>.py` - Your plugin implementation

Optional files:
- `icon.png` - Plugin icon (shown in UI)
- `README.md` - Plugin documentation
- Any additional Python modules or resources

## Manifest File

The `manifest.yaml` file describes your plugin and its configuration variables.

### Basic Structure

```yaml
name: My Plugin Name
version: 1.0.0
description: A brief description of what your plugin does
author: Your Name
entry_point: my_plugin.py  # Main Python script (relative to plugin directory)
license: MIT

# Lifecycle configuration
lifecycle_mode: always_running  # or: on_visible
can_switch_page: true  # Can the plugin request page switches?

# Error handling
max_retries: 3  # How many times to restart on crash
retry_delay: 5  # Seconds to wait between restart attempts

# Configuration variables
variables:
  - name: api_url
    type: url
    description: API endpoint to monitor
    required: true

  - name: poll_interval
    type: int
    description: Polling interval in seconds
    required: false
    default: 30

  - name: api_key
    type: password
    description: API authentication key
    required: true

# Optional metadata
homepage: https://github.com/yourname/your-plugin
icon: icon.png  # Path relative to plugin directory
```

### Lifecycle Modes

**`always_running`** (default):
- Plugin starts when StreamDeck UI starts
- Runs continuously in background
- Receives all button events regardless of page
- Best for: Monitoring services, polling APIs, continuous updates

**`on_visible`**:
- Plugin starts only when its button's page is visible
- Stops when page changes away
- Best for: On-demand actions, resource-intensive operations

### Variable Types

- `string`: Text input
- `int`: Integer number
- `float`: Decimal number
- `bool`: True/False checkbox
- `file_path`: File picker
- `dir_path`: Directory picker
- `url`: URL input with validation
- `password`: Masked text input
- `certificate`: File picker for certificate files

### Variable Properties

```yaml
- name: variable_name      # Internal name (used in code)
  type: string             # Data type
  description: Help text   # Shown to user in UI
  required: true           # Must be provided?
  default: "value"         # Default value (optional)
```

## Plugin Implementation

### Basic Plugin Structure

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
from typing import Any, Dict

# Import base plugin class
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from streamdeck_ui.plugin_system.base_plugin import BasePlugin
from streamdeck_ui.plugin_system.protocol import LogLevel


class MyPlugin(BasePlugin):
    """My custom StreamDeck plugin."""

    def __init__(self, socket_path: str, config: Dict[str, Any]):
        super().__init__(socket_path, config)

        # Extract configuration
        self.api_url = config.get('api_url', '')
        self.poll_interval = int(config.get('poll_interval', 30))

        # Initialize state
        self.last_poll = 0

    def on_start(self) -> None:
        """Called when plugin starts."""
        self.log(LogLevel.INFO, "Plugin started!")
        # Perform initialization
        self.update_image_render(
            text="Ready",
            background_color="#00AA00"
        )

    def on_button_pressed(self) -> None:
        """Called when button is pressed."""
        self.log(LogLevel.INFO, "Button pressed!")
        # Handle button press

    def on_button_released(self) -> None:
        """Called when button is released."""
        pass

    def on_button_visible(self, page: int, button: int) -> None:
        """Called when button becomes visible."""
        self.log(LogLevel.INFO, f"Now visible on page {page}")

    def on_button_hidden(self) -> None:
        """Called when button is hidden."""
        self.log(LogLevel.INFO, "Now hidden")

    def on_config_update(self, config: Dict[str, Any]) -> None:
        """Called when configuration changes."""
        self.api_url = config.get('api_url', self.api_url)
        self.poll_interval = int(config.get('poll_interval', self.poll_interval))

    def update(self) -> None:
        """Called periodically (~10 times per second)."""
        import time
        current_time = time.time()

        # Poll API at configured interval
        if current_time - self.last_poll >= self.poll_interval:
            self._poll_api()
            self.last_poll = current_time

    def _poll_api(self) -> None:
        """Poll external API and update display."""
        try:
            # Your API polling logic here
            import requests
            response = requests.get(self.api_url, timeout=10)
            data = response.json()

            # Update button display
            self.update_image_render(
                text=f"Status: {data['status']}",
                background_color="#00AA00" if data['ok'] else "#AA0000"
            )
        except Exception as e:
            self.log(LogLevel.ERROR, f"API poll failed: {e}")


def main():
    """Entry point."""
    import json

    if len(sys.argv) < 3:
        print("Usage: my_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    config = json.loads(sys.argv[2])

    plugin = MyPlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
```

### Required Methods

You **must** implement these abstract methods:

```python
def on_start(self) -> None:
    """Initialization logic."""
    pass

def on_button_pressed(self) -> None:
    """Handle button press."""
    pass

def on_button_released(self) -> None:
    """Handle button release."""
    pass

def on_button_visible(self, page: int, button: int) -> None:
    """Handle button becoming visible."""
    pass

def on_button_hidden(self) -> None:
    """Handle button becoming hidden."""
    pass

def update(self) -> None:
    """Periodic update (called ~10 times per second)."""
    pass
```

### Optional Methods

These have default implementations but can be overridden:

```python
def on_config_update(self, config: Dict[str, Any]) -> None:
    """Handle configuration changes."""
    pass

def on_shutdown(self) -> None:
    """Cleanup before shutdown."""
    pass

def on_error(self, error: str, details: Optional[str] = None) -> None:
    """Handle error messages from host."""
    pass
```

## Communication Protocol

### Sending Log Messages

```python
self.log(LogLevel.DEBUG, "Debug message")
self.log(LogLevel.INFO, "Info message")
self.log(LogLevel.WARNING, "Warning message")
self.log(LogLevel.ERROR, "Error message")
```

### Sending Heartbeats

Heartbeats are sent automatically every 5 seconds, but you can send manual heartbeats:

```python
self.send_heartbeat()
```

### Sending Errors

```python
self.send_error("Something went wrong", details="Stack trace or details here")
```

## Updating Button Display

### Method 1: Rendering Instructions (Recommended)

Use the built-in rendering pipeline:

```python
self.update_image_render(
    text="Hello\nWorld",                    # Multi-line text
    icon="/path/to/icon.png",               # Icon path
    background_color="#FF0000",             # Hex color
    font_color="#FFFFFF",                   # Hex color
    font_size=14,                           # Font size
    text_vertical_align="middle",           # top, middle-top, middle, middle-bottom, bottom
    text_horizontal_align="center"          # left, center, right
)
```

**Advantages:**
- Uses StreamDeck UI's built-in rendering
- Consistent with other buttons
- Efficient (no image encoding)
- Automatic text wrapping and formatting

### Method 2: Raw Image Data

Send a PIL Image directly:

```python
from PIL import Image, ImageDraw

# Create custom image
img = Image.new('RGB', (72, 72), color='blue')
draw = ImageDraw.Draw(img)
draw.text((10, 30), "Custom", fill='white')

# Send to display
self.update_image_raw(img, format="PNG")
```

**Advantages:**
- Full control over rendering
- Can create animations
- Can use custom graphics libraries

**Disadvantages:**
- More CPU intensive
- Larger data transfer
- Need to handle image sizing

### Animations

For animations, repeatedly call `update_image_render()` or `update_image_raw()` in your `update()` method:

```python
def update(self) -> None:
    import time
    import math

    # Pulse effect
    t = time.time()
    brightness = int((math.sin(t * 2) + 1) * 127.5)
    color = f"#{brightness:02x}0000"

    self.update_image_render(
        text="Pulsing",
        background_color=color
    )
```

## Page Switching

If your plugin needs to show important notifications, it can request a page switch:

```python
# Temporary page switch (returns after duration)
self.request_page_switch(duration=10)  # Show for 10 seconds

# Permanent page switch
self.request_page_switch(duration=None)
```

**Important:**
- Set `can_switch_page: true` in manifest
- User must grant permission in UI when configuring
- Use sparingly to avoid disrupting user workflow

## Lifecycle Management

### Startup Sequence

1. Host creates Unix socket
2. Host starts your plugin process: `python3 your_plugin.py <socket_path> <config_json>`
3. Plugin calls `__init__()` with config
4. Plugin calls `connect()` to establish socket connection
5. Plugin sends `READY` message
6. Host sends `BUTTON_VISIBLE` if button is currently visible
7. Plugin calls `on_start()`
8. Plugin enters main loop

### Shutdown Sequence

1. Host sends `SHUTDOWN` message
2. Plugin calls `on_shutdown()`
3. Plugin exits main loop
4. Plugin disconnects socket
5. Process terminates

### Crash Recovery

If your plugin crashes:
1. Host detects process termination
2. If `retry_count < max_retries`:
   - Wait `retry_delay` seconds
   - Restart plugin
   - Increment retry counter
3. If max retries exceeded:
   - Give up and log error
   - Button shows last known state

### Best Practices for Stability

```python
def update(self) -> None:
    """Wrap all operations in try-except."""
    try:
        # Your update logic
        self._do_something()
    except Exception as e:
        # Log error but don't crash
        self.log(LogLevel.ERROR, f"Update failed: {e}")
        # Optionally inform host
        self.send_error(str(e))
```

## Best Practices

### 1. Handle Configuration Gracefully

```python
def __init__(self, socket_path: str, config: Dict[str, Any]):
    super().__init__(socket_path, config)

    # Provide defaults for optional config
    self.api_url = config.get('api_url', '')
    self.timeout = int(config.get('timeout', 10))

    # Validate required config
    if not self.api_url:
        self.log(LogLevel.ERROR, "api_url is required!")
```

### 2. Use Appropriate Polling Intervals

```python
def update(self) -> None:
    # Don't poll too frequently
    if time.time() - self.last_check < self.interval:
        return

    self.last_check = time.time()
    self._check_status()
```

### 3. Handle Network Errors

```python
def _fetch_data(self):
    try:
        response = requests.get(self.url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        self.log(LogLevel.WARNING, "Request timed out")
        return None
    except requests.RequestException as e:
        self.log(LogLevel.ERROR, f"Request failed: {e}")
        return None
```

### 4. Cache Data When Possible

```python
def __init__(self, socket_path, config):
    super().__init__(socket_path, config)
    self._cache = None
    self._cache_time = 0
    self._cache_ttl = 60  # seconds

def _get_data(self):
    now = time.time()
    if self._cache and (now - self._cache_time) < self._cache_ttl:
        return self._cache

    # Fetch new data
    self._cache = self._fetch_from_api()
    self._cache_time = now
    return self._cache
```

### 5. Provide User Feedback

```python
def on_start(self):
    # Show loading state
    self.update_image_render(text="Loading...", background_color="#666666")

    # Initialize
    success = self._initialize()

    # Show result
    if success:
        self.update_image_render(text="Ready", background_color="#00AA00")
    else:
        self.update_image_render(text="Error", background_color="#AA0000")
```

### 6. Clean Up Resources

```python
def on_shutdown(self):
    """Clean up before exit."""
    # Close connections
    if hasattr(self, 'connection'):
        self.connection.close()

    # Cancel timers
    # Save state
    # etc.

    self.log(LogLevel.INFO, "Plugin shutdown complete")
```

## Example Plugin

See [plugins/example_alertmanager/](plugins/example_alertmanager/) for a complete, production-ready plugin that:

- Monitors Prometheus AlertManager
- Polls API with authentication
- Shows alert counts with color coding
- Flashes on new alerts
- Requests page switch for notifications
- Opens AlertManager in browser on button press
- Handles configuration updates
- Includes comprehensive error handling

## Testing Your Plugin

### Manual Testing

1. **Install Your Plugin:**
   ```bash
   mkdir -p ~/.streamdeck_ui/plugins/my_plugin
   cp manifest.yaml my_plugin.py ~/.streamdeck_ui/plugins/my_plugin/
   ```

2. **Restart StreamDeck UI** or reload plugins from the UI

3. **Configure Button:**
   - Select a button in the UI
   - Choose your plugin from the list
   - Fill in configuration variables
   - Save

4. **Test Functionality:**
   - Check button display updates
   - Press the button
   - Change pages to test visibility events
   - Update configuration to test config updates

### Debug Logging

View plugin logs:

```bash
# StreamDeck UI logs include plugin messages
tail -f ~/.streamdeck_ui.log
```

Add debug logging to your plugin:

```python
def update(self):
    self.log(LogLevel.DEBUG, f"Update called, state={self.state}")
```

### Unit Testing

Create a test that runs your plugin without the host:

```python
# test_my_plugin.py
import json
import tempfile
from my_plugin import MyPlugin

def test_plugin():
    config = {
        'api_url': 'http://localhost:8080',
        'poll_interval': 5
    }

    # Create fake socket
    with tempfile.NamedTemporaryFile() as f:
        plugin = MyPlugin(f.name, config)

        # Test methods
        plugin.on_start()
        plugin.on_button_pressed()
        # etc.
```

### Common Issues

**Plugin doesn't start:**
- Check Python path and imports
- Verify all dependencies are installed
- Check manifest.yaml syntax
- Look at StreamDeck UI logs

**Button doesn't update:**
- Verify `update_image_render()` or `update_image_raw()` is called
- Check that rendering instructions are valid
- Ensure plugin is connected (check logs)

**Config not loading:**
- Verify variable names match manifest
- Check types match (int vs string)
- Ensure required variables are provided

## Publishing Your Plugin

When ready to share your plugin:

1. **Include documentation:**
   - README.md with usage instructions
   - Example configurations
   - Troubleshooting section

2. **Specify dependencies:**
   - List required Python packages
   - Include requirements.txt

3. **Test thoroughly:**
   - Test with various configurations
   - Test error conditions
   - Test on fresh installation

4. **License your code:**
   - Choose appropriate license (MIT, GPL, etc.)
   - Include LICENSE file

5. **Share:**
   - Create GitHub repository
   - Submit to StreamDeck UI plugin directory
   - Share on community forums

## Need Help?

- Check the example plugin: `plugins/example_alertmanager/`
- Read the source code: `streamdeck_ui/plugin_system/`
- Ask on GitHub Issues: https://github.com/streamdeck-linux-gui/streamdeck-linux-gui/issues
- Join the community discussions

Happy plugin development! 🚀
