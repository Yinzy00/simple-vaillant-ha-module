DOMAIN = "mypyllant_readonly"

# Option / entry.data keys (match MyPyllantAPI constructor kwargs)
OPTION_BRAND = "brand"
OPTION_COUNTRY = "country"

# Update interval option (stored in entry.options)
OPTION_UPDATE_INTERVAL = "update_interval"

# Defaults
DEFAULT_UPDATE_INTERVAL = 30 * 60  # 1800 s
DEFAULT_COUNTRY = "germany"
QUOTA_PAUSE_INTERVAL = 3 * 3600  # 10800 s
API_DOWN_PAUSE_INTERVAL = 15 * 60  # 900 s

# Operation mode strings (must match DeviceData.operation_mode values exactly)
OM_HEATING = "HEATING"
OM_DOMESTIC_HOT_WATER = "DOMESTIC_HOT_WATER"
OM_COOLING = "COOLING"

TARGET_OPERATION_MODES = [OM_HEATING, OM_DOMESTIC_HOT_WATER, OM_COOLING]
