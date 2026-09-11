# Source identity is immutable Android-origin data. Header is ROS delivery
# bookkeeping only and is never a source-time correlation key.
std_msgs/Header header

string session
string feed
uint64 frame_seq
uint32 rtp_ssrc
uint32 rtp_ts
uint64 android_au_first_byte_mono_ns
uint64 android_au_complete_mono_ns

bool has_dji_source_timestamp
uint64 dji_source_timestamp_ns
string dji_timestamp_source

uint8 ASSOCIATION_UNAVAILABLE=0
uint8 ASSOCIATION_INTERPOLATED=1
uint8 ASSOCIATION_NEAREST=2
uint8 association_quality
string association_reason
int64 navigation_time_offset_ns

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
uint64 flight_android_mono_ns
uint64 rtk_android_mono_ns
uint64 gimbal_android_mono_ns

# Explicitly Edge-only latency observation; never used as source time.
uint64 edge_decoded_mono_ns
