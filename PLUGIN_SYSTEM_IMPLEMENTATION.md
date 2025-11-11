# Plugin System Implementation Summary

This document provides a comprehensive overview of the plugin system implementation for streamdeck-ui.

## Overview

The plugin system allows users to extend StreamDeck UI functionality by writing Python scripts that can:
- Monitor external services and APIs
- Update button displays dynamically
- Respond to button presses with custom logic
- Request page switches on important events
- Run continuously or only when their button is visible

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                   StreamDeck UI (Host)                  │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              StreamDeckServer (api.py)          │   │
│  │                                                 │   │
│  │  ┌──────────────────────────────────────────┐  │   │
│  │  │      PluginManager                       │  │   │
│  │  │  - Discovers plugins                     │  │   │
│  │  │  - Creates plugin instances              │  │   │
│  │  │  - Manages lifecycle                     │  │   │
│  │  │  - Handles communication                 │  │   │
│  │  └──────────────┬───────────────────────────┘  │   │
│  │                 │                               │   │
│  └─────────────────┼───────────────────────────────┘   │
│                    │                                   │
└────────────────────┼───────────────────────────────────┘
                     │ Unix Socket
                     │ (per plugin instance)
         ┌───────────┴───────────┬───────────────┐
         │                       │               │
┌────────▼─────────┐   ┌─────────▼────────┐    │
│ Plugin Instance  │   │ Plugin Instance  │   ...
│   (Process 1)    │   │   (Process 2)    │
│                  │   │                  │
│ ┌──────────────┐ │   │ ┌──────────────┐ │
│ │ BasePlugin   │ │   │ │ BasePlugin   │ │
│ │ (User Code)  │ │   │ │ (User Code)  │ │
│ └──────────────┘ │   │ └──────────────┘ │
└──────────────────┘   └──────────────────┘
```

### Key Design Decisions

1. **Process Isolation**: Each plugin instance runs as a separate process for stability and security
2. **Unix Sockets**: Communication via Unix domain sockets for efficiency and security
3. **Length-Prefixed Messages**: JSON messages with 4-byte length prefix for reliable parsing
4. **Lifecycle Management**: Automatic restart on crash with configurable retry limits
5. **Dual Update Methods**: Support both raw image data and rendering instructions

## File Structure

### Core Plugin System

```
streamdeck_ui/plugin_system/
├── __init__.py                 # Package init
├── schema.py                   # Manifest schema and validation
├── protocol.py                 # Communication protocol
├── base_plugin.py              # Base class for plugins
└── plugin_manager.py           # Plugin lifecycle management
```

### Example Plugin

```
plugins/example_alertmanager/
├── manifest.yaml               # Plugin metadata
├── alertmanager_plugin.py      # Plugin implementation
├── alertmanager_icon.png       # Plugin icon
└── README.md                   # Plugin documentation
```

### Documentation

```
PLUGIN_DEVELOPMENT.md           # Complete developer guide
PLUGIN_SYSTEM_IMPLEMENTATION.md # This file
README.md                       # Updated with plugin info
```

## Implementation Details

### 1. Plugin Manifest (`schema.py`)

Defines the plugin metadata and configuration:

**Key Features:**
- Plugin name, version, description, author
- Entry point (Python script)
- Lifecycle mode (always_running, on_visible)
- Configuration variables with types
- Page switching permission
- Retry configuration

**Variable Types Supported:**
- `string`, `int`, `float`, `bool`
- `file_path`, `dir_path`
- `url`
- `password` (masked in UI)
- `certificate` (special file picker)

**Validation:**
- Required fields check
- Duplicate variable detection
- Default/required consistency

### 2. Communication Protocol (`protocol.py`)

**Message Format:**
```
[4 bytes: length][N bytes: JSON data]
```

**Message Types:**

**Host → Plugin:**
- `BUTTON_PRESSED`: Physical button was pressed
- `BUTTON_RELEASED`: Physical button was released
- `BUTTON_VISIBLE`: Button page is now visible
- `BUTTON_HIDDEN`: Button page is now hidden
- `CONFIG_UPDATE`: Configuration was changed
- `SHUTDOWN`: Plugin should terminate

**Plugin → Host:**
- `UPDATE_IMAGE_RAW`: Send raw image data (PIL Image as base64)
- `UPDATE_IMAGE_RENDER`: Send rendering instructions (text, icon, colors)
- `REQUEST_PAGE_SWITCH`: Request to switch to plugin's page
- `LOG_MESSAGE`: Send log message to host
- `HEARTBEAT`: Periodic alive signal (every 5 seconds)
- `READY`: Plugin initialized successfully

**Bidirectional:**
- `ERROR`: Error occurred
- `ACK`: Acknowledge message

### 3. Base Plugin Class (`base_plugin.py`)

Provides foundation for all plugins:

**Connection Management:**
- `connect()`: Establish Unix socket connection
- `disconnect()`: Close connection
- `send_message()`: Send protocol message
- `receive_message()`: Receive with timeout

**Display Updates:**
- `update_image_raw(image, format)`: Send PIL Image
- `update_image_render(text, icon, colors, alignment, ...)`: Send render instructions

**Actions:**
- `request_page_switch(duration)`: Request page switch (temporary or permanent)
- `log(level, message)`: Send log message
- `send_heartbeat()`: Send alive signal
- `send_error(error, details)`: Report error

**Abstract Methods (must implement):**
- `on_start()`: Initialization
- `on_button_pressed()`: Button press handler
- `on_button_released()`: Button release handler
- `on_button_visible(page, button)`: Visibility change
- `on_button_hidden()`: Hidden handler
- `update()`: Periodic update (~10Hz)

**Optional Methods:**
- `on_config_update(config)`: Config change handler
- `on_shutdown()`: Cleanup handler
- `on_error(error, details)`: Error handler

**Main Loop:**
- Connects to host
- Sends READY message
- Calls `on_start()`
- Enters loop:
  - Receives messages (non-blocking, 0.1s timeout)
  - Calls appropriate handlers
  - Calls `update()` periodically
  - Sends heartbeat every 5 seconds
  - Sleeps 0.1s between iterations

### 4. Plugin Manager (`plugin_manager.py`)

**PluginInstance Class:**
Represents a running plugin for a specific button.

**Responsibilities:**
- Start/stop plugin process
- Create Unix socket for communication
- Send messages to plugin
- Receive messages from plugin
- Handle image updates
- Handle page switch requests
- Monitor plugin health

**Key Methods:**
- `start()`: Create socket, spawn process, wait for connection
- `stop()`: Send shutdown, wait for exit, cleanup
- `send_button_pressed/released()`: Notify plugin of button events
- `send_button_visible/hidden()`: Notify plugin of visibility
- `send_config_update(config)`: Send new configuration
- `is_alive()`: Check if process is running
- `is_responsive()`: Check heartbeat (<30s old)

**Communication Thread:**
- Runs in background (`_communication_loop()`)
- Receives messages from plugin
- Dispatches to appropriate handlers
- Handles image updates, page switches, logs

**PluginManager Class:**
Manages all plugin instances.

**Responsibilities:**
- Discover plugins in plugins directory
- Create/remove plugin instances
- Monitor instance health
- Restart crashed plugins

**Key Methods:**
- `discover_plugins()`: Scan plugin directory, load manifests
- `create_instance()`: Create new instance for button
- `start_instance()`: Start plugin process
- `stop_instance()`: Stop plugin process
- `remove_instance()`: Stop and remove instance
- `get_instances_for_button()`: Find instances for specific button
- `monitor_instances()`: Background monitoring (runs in thread)

**Health Monitoring:**
- Checks process alive every 5 seconds
- Checks heartbeat freshness
- Retries restart up to `max_retries`
- Waits `retry_delay` seconds between retries

### 5. API Integration (`api.py`)

**StreamDeckServer Extensions:**

**Initialization:**
```python
def __init__(self):
    # ...existing code...
    plugins_dir = Path.home() / '.streamdeck_ui' / 'plugins'
    self.plugin_manager = PluginManager(plugins_dir)
    self.plugin_manager.discover_plugins()
    # Start monitor thread
```

**Plugin Management API:**
- `get_button_plugin_id()`: Get plugin ID for button
- `set_button_plugin_id()`: Set plugin ID for button
- `get_button_plugin_config()`: Get plugin configuration
- `set_button_plugin_config()`: Update plugin configuration
- `get_button_plugin_can_switch_page()`: Get page switch permission
- `set_button_plugin_can_switch_page()`: Set page switch permission
- `attach_plugin_to_button()`: Attach and start plugin
- `detach_plugin_from_button()`: Stop and remove plugin

**Event Handlers:**
- `_handle_plugin_image_update()`: Process image updates from plugin
  - Raw: Save temp file, create ImageFilter, update display
  - Render: Update ButtonState, rebuild filters
- `_handle_plugin_page_switch()`: Handle page switch requests
  - Temporary: Switch, schedule restore
  - Permanent: Just switch
- `_handle_plugin_log_message()`: Log plugin messages

**Notification Methods:**
- `notify_button_press()`: Inform plugin of button press/release
- `notify_page_change()`: Inform plugins of page visibility changes

### 6. Model Extensions (`model.py`)

**ButtonState Extended:**
```python
@dataclass
class ButtonState:
    # ...existing fields...
    plugin_id: str = ""
    plugin_config: Dict[str, Any] = field(default_factory=dict)
    plugin_can_switch_page: bool = False
```

These fields are:
- Persisted in config file
- Used to recreate plugin instances on restart
- Updated through API methods

## Message Flow Examples

### Plugin Startup

```
1. Host creates socket at /tmp/streamdeck_plugin_ABC
2. Host spawns: python3 plugin.py /tmp/streamdeck_plugin_ABC '{"key":"value"}'
3. Plugin connects to socket
4. Plugin sends READY message
5. Host sends BUTTON_VISIBLE (if applicable)
6. Plugin calls on_start()
7. Plugin enters main loop
```

### Button Press

```
1. User presses physical button
2. StreamDeck library fires callback
3. Host calls api.notify_button_press(serial, page, button, True)
4. Host finds plugin instances for button
5. Host sends BUTTON_PRESSED to each instance
6. Plugin receives message in communication loop
7. Plugin calls on_button_pressed()
8. Plugin updates display or performs action
```

### Image Update

```
1. Plugin calls update_image_render(text="Hello", background_color="#FF0000")
2. Plugin sends UPDATE_IMAGE_RENDER message
3. Host receives message in PluginInstance communication thread
4. Host calls on_image_update callback
5. API _handle_plugin_image_update() updates ButtonState
6. API calls _update_button_filters()
7. DisplayGrid rebuilds filter chain
8. Image rendered to Stream Deck
```

### Page Switch Request

```
1. Plugin calls request_page_switch(duration=10)
2. Plugin sends REQUEST_PAGE_SWITCH message
3. Host receives message
4. Host checks can_switch_page permission
5. If allowed: Host calls set_temp_page(serial, page)
6. After 10s: Host calls restore_previous_page(serial)
```

### Plugin Crash

```
1. Plugin process terminates unexpectedly
2. Monitor thread detects process not alive
3. If retry_count < max_retries:
   a. Increment retry_count
   b. Wait retry_delay seconds
   c. Call instance.start()
   d. Repeat startup sequence
4. If max retries exceeded:
   a. Log error
   b. Mark instance as failed
   c. Button shows last known state
```

## Integration Points

### Where UI Changes Are Needed (Not Yet Implemented)

The core plugin system is complete, but UI integration is pending:

1. **Plugin Selection Dialog** (gui.py)
   - Add "Plugin" tab in button configuration
   - List available plugins from plugin_manager.get_all_plugins()
   - Show plugin description and metadata
   - Allow selecting plugin and assigning to button

2. **Plugin Configuration Form** (gui.py)
   - Dynamically generate form from manifest.variables
   - Handle different variable types (string, int, password, file, etc.)
   - Validate required fields
   - Show/hide based on variable requirements
   - Save config to ButtonState.plugin_config

3. **Plugin Management UI** (new dialog)
   - List installed plugins
   - Show plugin status (running, stopped, crashed)
   - Manual start/stop/restart controls
   - View plugin logs
   - Install new plugins (upload .zip)
   - Remove plugins

4. **Button Context Menu** (gui.py)
   - "Configure Plugin" option
   - "Detach Plugin" option
   - "View Plugin Logs" option

5. **Settings Dialog** (ui_settings.py)
   - Global plugin settings
   - Plugin directory location
   - Default retry settings
   - Debug mode for plugins

### Integration with Existing Features

The plugin system integrates with:

**Page System:**
- Plugins receive visibility events on page change
- Plugins can request page switches
- Works with temp_switch_page feature

**Button Rendering:**
- Plugins can use existing rendering pipeline
- Plugins can send raw images
- Filters work same as manual configuration

**Configuration:**
- Plugin state saved in ~/.streamdeck_ui.json
- Export/import includes plugin config
- Backward compatible (old configs work)

**Button Actions:**
- Plugins receive press/release events
- Compatible with other button actions (can coexist)
- Plugin updates don't interfere with manual updates

## Example: Prometheus AlertManager Plugin

A complete example demonstrating all features:

**Features:**
- Polls AlertManager API at configurable intervals
- Displays environment name and alert count
- Color-codes based on severity (green/orange/red)
- Flashes "NEW!" on new alerts
- Requests page switch on new alerts (temporary, 10s)
- Opens AlertManager in browser on button press
- Supports multiple auth methods (basic, bearer, certs)
- Handles configuration updates
- Comprehensive error handling

**Files:**
- `manifest.yaml`: 13 configuration variables
- `alertmanager_plugin.py`: Full implementation
- `alertmanager_icon.png`: Custom icon
- `README.md`: Usage documentation

**Configuration Example:**
```yaml
alertmanager_url: https://alertmanager.prod.example.com
environment_name: Production
poll_interval: 30
flash_duration: 10
auth_type: basic
username: admin
password: ********
```

## Security Considerations

1. **Process Isolation**: Each plugin runs in separate process with own memory space

2. **Unix Socket Permissions**: Sockets created with restrictive permissions (user-only)

3. **No Arbitrary Code Execution**: Plugins must be installed manually in plugin directory

4. **Configuration Validation**: Manifest variables are validated before plugin starts

5. **Crash Isolation**: Plugin crash doesn't affect host or other plugins

6. **Resource Limits**: Each plugin is a separate process (can be limited with ulimit/cgroups)

## Testing Strategy

### Unit Tests Needed

1. **schema.py**
   - Manifest loading from YAML
   - Validation logic
   - Variable type handling

2. **protocol.py**
   - Message serialization/deserialization
   - Length-prefix encoding
   - All message types

3. **base_plugin.py**
   - Socket connection
   - Message send/receive
   - Abstract method enforcement

4. **plugin_manager.py**
   - Plugin discovery
   - Instance creation
   - Lifecycle management
   - Health monitoring

5. **api.py**
   - Plugin attachment/detachment
   - Image update handling
   - Page switch handling
   - Event notifications

### Integration Tests

1. **Full Plugin Lifecycle**
   - Start plugin
   - Send events
   - Receive updates
   - Graceful shutdown

2. **Crash Recovery**
   - Kill plugin process
   - Verify restart
   - Test retry limit

3. **Multi-Instance**
   - Multiple buttons with same plugin
   - Different configurations
   - Independent operation

4. **Page Switching**
   - Visibility events
   - Page switch requests
   - Temporary switches

### Manual Testing

1. **Example Plugin**
   - Install AlertManager plugin
   - Configure with test AlertManager
   - Verify display updates
   - Test button press
   - Test flash behavior

2. **Error Conditions**
   - Invalid configuration
   - Network failures
   - Permission errors
   - Resource exhaustion

3. **UI Integration** (when implemented)
   - Plugin selection
   - Configuration forms
   - Status display
   - Log viewing

## Performance Considerations

1. **Process Overhead**: Each instance is a process (~10-20MB base)
   - Acceptable for typical use (5-10 plugins)
   - Can be optimized with process pools if needed

2. **Communication**: Unix sockets are very efficient
   - Minimal latency (<1ms)
   - Low CPU overhead

3. **Image Updates**: Two methods with different tradeoffs
   - Render instructions: Very efficient (just JSON)
   - Raw images: More data, but allows complex graphics

4. **Polling**: Plugins control their own poll intervals
   - Encourage reasonable intervals (30s+)
   - Document best practices

5. **Monitoring**: Health check every 5 seconds
   - Minimal overhead
   - Can be adjusted if needed

## Future Enhancements

### Potential Improvements

1. **Plugin Marketplace**
   - Central repository of community plugins
   - One-click install
   - Automatic updates
   - Rating/review system

2. **Plugin SDK**
   - Helper functions for common tasks
   - Reusable UI components
   - Easier testing framework
   - Code generators

3. **Advanced Communication**
   - Support network plugins (TCP/TLS)
   - Plugin-to-plugin communication
   - Event bus for system events

4. **Resource Management**
   - CPU/memory limits per plugin
   - Rate limiting for updates
   - Quota system

5. **Enhanced Debugging**
   - Debug mode with verbose logging
   - Performance profiling
   - Memory leak detection
   - Live variable inspection

6. **Plugin Types**
   - Background services (no button)
   - Multi-button plugins
   - Global overlays
   - Custom UI panels

## Migration Path

For users upgrading from previous versions:

1. **Backward Compatibility**: Old configs work without changes
   - New fields default to empty
   - No migration needed

2. **Opt-In**: Plugins are optional
   - Users don't need to use plugins
   - All existing features work as before

3. **Documentation**: Comprehensive guides
   - User guide for using plugins
   - Developer guide for creating plugins
   - Example plugins as templates

## Conclusion

The plugin system is a powerful extension mechanism that:
- ✅ Maintains stability through process isolation
- ✅ Provides flexible communication protocol
- ✅ Offers two display update methods
- ✅ Includes comprehensive lifecycle management
- ✅ Has detailed documentation
- ✅ Includes production-ready example
- ⏳ Needs UI integration (planned)

The architecture is solid, extensible, and ready for community contributions.

## Next Steps

1. **Implement UI Integration** (highest priority)
   - Plugin selection in button config
   - Configuration forms
   - Management dialog

2. **Add Tests**
   - Unit tests for all modules
   - Integration tests
   - Example plugin tests

3. **Community Beta**
   - Release for testing
   - Gather feedback
   - Iterate on API

4. **Documentation Improvements**
   - Video tutorials
   - More examples
   - FAQ section

5. **Plugin Gallery**
   - Collect community plugins
   - Create showcase
   - Maintain plugin list
