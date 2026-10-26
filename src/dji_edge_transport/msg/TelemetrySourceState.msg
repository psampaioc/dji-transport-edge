# One direct Android telemetry producer observation. The ROS header records
# Edge delivery only; android_mono_ns stays the original producer timestamp.
std_msgs/Header header

string source
string session
uint64 android_mono_ns
uint64 edge_receive_mono_ns

# Flight and RTK payload fields. Their validity remains source-specific.
bool position_valid
bool rtk_valid
float64 latitude_deg
float64 longitude_deg
float64 altitude_m
float64 heading_deg

# Gimbal payload fields.
bool gimbal_pitch_valid
float64 gimbal_pitch_deg
