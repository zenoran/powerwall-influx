# Changelog

All notable changes to the Powerwall InfluxDB Service project.

## [1.0.0] - 2025-10-27

### Initial Release

First standalone release after extracting from pypowerwall repository.

#### Added
- **Core Service**: Continuous polling service that writes Powerwall metrics to InfluxDB
- **Wi-Fi Auto-Connect**: Automatic connection to Powerwall Wi-Fi AP using NetworkManager
- **String Status Viewer**: Query and display per-string solar data from InfluxDB
- **Systemd Integration**: Service file and installer for automatic startup on boot
- **Configuration Management**: Environment-based configuration via `.env` file
- **Comprehensive Documentation**: README and supporting references
- **Verification Tool**: Script to verify installation and connectivity
- **Shell Utilities**: Wrapper scripts for common operations

#### Features
- Poll Powerwall every 5 seconds (configurable)
- Export comprehensive metrics including:
  - Power (battery, site, load, solar)
  - Energy (state of charge, capacity)
  - Voltage and frequency
  - Per-string solar data (voltage, current, power, connection status)
- Write metrics to InfluxDB v2.x in line protocol format
- Automatic error handling and retry logic
- Debug logging support
- TLS verification toggle for InfluxDB

#### Dependencies
- `pypowerwall>=0.10.0` - Tesla Powerwall API client
- `requests>=2.31.0` - HTTP library

#### Installation
- Automated setup script (`setup.sh`)
- Systemd service installer (`install-service.sh`)
- Support for virtual environments and system-wide installation

#### Documentation
- `README.md` - Complete project documentation (includes configuration, MQTT, troubleshooting)
- `.env.example` - Example configuration with all options

#### Scripts
- `show-strings.sh` - View current string status
- `verify.sh` - Verify installation and configuration
- `setup.sh` - Automated setup wizard
- `install-service.sh` - Systemd service installer

#### Service Files
- `powerwall-influx.service` - Systemd service unit file
- Auto-restart on failure (30s delay)
- Runs as user service (non-root)
- Waits for network on startup

---

### Migration from pypowerwall/src

This release represents the extraction of the Powerwall InfluxDB service from the pypowerwall source repository into a standalone, independently maintained project.

**Previous location**: `~/dev/pypowerwall/src/powerwall_service/`  
**New location**: `~/dev/powerwall-influx/`

**Changes**:
- ✅ Standalone repository structure
- ✅ Independent dependency management
- ✅ Clean git history ready
- ✅ Removed coupling to pypowerwall source
- ✅ Self-contained documentation
- ✅ Production-ready deployment

**Backward Compatibility**:
- All functionality preserved
- Configuration format unchanged
- Same Python module structure
- Existing `.env` files compatible

---

## Future Roadmap

Potential features for future releases:

### v1.1.0 (Planned)
- [ ] Support for multiple Powerwall sites
- [ ] Grafana dashboard templates
- [ ] Docker container support
- [ ] Configuration validation tool
- [ ] Prometheus exporter option

### v1.2.0 (Planned)
- [ ] Web UI for monitoring
- [ ] Historical data export
- [ ] Alert/notification system
- [ ] InfluxDB v3 support
- [ ] Additional metric sources (weather, utility rates)

### v2.0.0 (Future)
- [ ] Plugin architecture for extensibility
- [ ] Support for other battery systems
- [ ] Cloud deployment options
- [ ] API server for external queries
- [ ] Mobile app integration

---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Update documentation
6. Submit a pull request

## Version History

- **1.0.0** (2025-10-27) - Initial standalone release

---

## Support

For issues, questions, or feature requests:
- Open an issue on GitHub
- Check existing documentation
- Review troubleshooting section in README
- Enable DEBUG logging for diagnostics

## License

MIT License - See LICENSE file for details
