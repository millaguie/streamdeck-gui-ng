# Prometheus AlertManager Monitor Plugin

This plugin monitors a Prometheus AlertManager instance and displays the current alert count on a Stream Deck button.

## Features

- **Real-time Monitoring**: Polls AlertManager at configurable intervals
- **Visual Alerts**: Button color changes based on alert count (green = no alerts, orange = some alerts, red = many alerts)
- **New Alert Notification**: Automatically flashes "NEW!" and switches to the plugin's page when new alerts are detected
- **One-Click Access**: Press the button to open AlertManager in your default browser
- **Flexible Authentication**: Supports basic auth, bearer tokens, and client certificates
- **Environment Labels**: Display custom environment names (e.g., "Production", "Staging")

## Configuration

### Required Variables

- **alertmanager_url**: Full URL to your AlertManager instance (e.g., `https://alertmanager.example.com`)
- **environment_name**: Display name for this environment (e.g., "Production", "Staging", "Dev")

### Optional Variables

- **poll_interval**: How often to check AlertManager in seconds (default: 30)
- **flash_duration**: How long to flash and show the page on new alerts in seconds (default: 10)
- **browser_command**: Custom command to open browser (leave empty for system default)

### Authentication

The plugin supports multiple authentication methods:

#### No Authentication
Set `auth_type` to `none` (default)

#### Basic Authentication
- Set `auth_type` to `basic`
- Provide `username` and `password`

#### Bearer Token
- Set `auth_type` to `bearer`
- Provide `bearer_token`

#### Client Certificates
- Provide `client_cert` (path to .crt or .pem file)
- Provide `client_key` (path to .key file)
- Optionally provide `ca_cert` for server verification
- Optionally provide `cert_password` if certificate is encrypted

## Installation

1. Copy this folder to `~/.streamdeck_ui/plugins/example_alertmanager/`
2. Install required Python dependencies:
   ```bash
   pip install requests
   ```
3. Restart StreamDeck UI or reload plugins
4. In the UI, select a button and choose "AlertManager Monitor" plugin
5. Configure the required variables
6. The button will start monitoring your AlertManager instance

## How It Works

### Normal Operation
- The button displays the environment name and current alert count
- Background color indicates status:
  - **Green**: No active alerts
  - **Orange**: 1-4 active alerts
  - **Red**: 5+ active alerts

### New Alert Detection
When new alerts are detected:
1. The button starts flashing between the alert count and "NEW!"
2. The plugin requests to switch to its page (if permission granted)
3. Flashing continues for `flash_duration` seconds
4. Pressing the button stops the flashing and opens AlertManager

### Button Press
Pressing the button opens your AlertManager instance in:
- Your default web browser (if `browser_command` is empty)
- A custom browser command (if `browser_command` is provided)

## Example Configurations

### Simple Configuration (No Auth)
```yaml
alertmanager_url: http://localhost:9093
environment_name: Local Dev
poll_interval: 60
```

### Production with Basic Auth
```yaml
alertmanager_url: https://alertmanager.prod.example.com
environment_name: Production
auth_type: basic
username: admin
password: your-secure-password
poll_interval: 30
flash_duration: 15
```

### Production with Client Certificates
```yaml
alertmanager_url: https://alertmanager.prod.example.com
environment_name: Production
client_cert: /path/to/client.crt
client_key: /path/to/client.key
ca_cert: /path/to/ca.crt
poll_interval: 30
```

## Troubleshooting

### Plugin Not Starting
- Check the StreamDeck UI logs for error messages
- Verify the `alertmanager_url` is correct and accessible
- Ensure Python 3.11+ is installed
- Verify `requests` library is installed

### Authentication Failures
- Verify credentials are correct
- Check certificate paths are absolute and files exist
- For self-signed certificates, provide the CA certificate

### No Alerts Showing
- Verify AlertManager is running and accessible
- Check the AlertManager API endpoint: `https://your-alertmanager/api/v2/alerts`
- Verify authentication is working
- Check plugin logs for API errors

## API Reference

This plugin uses the AlertManager v2 API:
- Endpoint: `/api/v2/alerts`
- Documentation: https://prometheus.io/docs/alerting/latest/clients/

## License

MIT License - Feel free to modify and distribute
