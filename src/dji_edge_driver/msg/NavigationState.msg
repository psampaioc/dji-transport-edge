# One normalized, map-ready navigation sample. Source telemetry stays available
# separately so this message never implies synchronized DJI callbacks.
std_msgs/Header header

float64 latitude_deg
float64 longitude_deg
float64 altitude_m
float64 heading_deg
float64 velocity_north_m_s
float64 velocity_east_m_s
float64 velocity_down_m_s

uint8 POSITION_UNKNOWN=0
uint8 POSITION_GPS_FALLBACK=1
uint8 POSITION_RTK=2
uint8 position_source
bool position_valid
bool rtk_valid
uint8 gps_signal_level

string session
uint64 android_mono_ns
uint64 edge_receive_mono_ns
float64 transport_age_s
