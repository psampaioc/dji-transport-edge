# Normalized navigation. Header is ROS delivery bookkeeping only; source-time
# fields below retain Android/DJI timing evidence.
std_msgs/Header header

float64 latitude_deg
float64 longitude_deg
float64 altitude_m
float64 heading_deg

uint8 POSITION_UNKNOWN=0
uint8 POSITION_GPS_FALLBACK=1
uint8 POSITION_RTK=2
uint8 position_source
bool position_valid
bool rtk_valid

bool gimbal_pitch_valid
float64 gimbal_pitch_deg
uint64 gimbal_android_mono_ns

string session
uint64 android_mono_ns
uint64 edge_receive_mono_ns
float64 transport_age_s
