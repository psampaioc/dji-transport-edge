#!/usr/bin/env python3
"""Direct Android UDP/RTP ingestion; no HTTP polling or loopback relay."""
import json, socket, threading, time
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from dji_edge_driver.msg import NavigationState
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import Image
from std_msgs.msg import String
from dji_edge_transport_core.protocol import CLOCK_TYPES, FRAME_TYPES, ProtocolError, decode_json_packet
from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.evidence import EvidenceWriter
from dji_edge_transport_core.navigation import build_navigation
from dji_edge_transport_core.rtp import RawRtpCapture, RtpMetrics
from dji_edge_transport_core.state import LatestState, SequenceTracker

class Endpoint(threading.Thread):
    def __init__(self, node, category, port):
        super().__init__(daemon=True); self.node, self.category, self.port, self.stop = node, category, port, threading.Event(); self.sock = None
    def run(self):
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); self.sock.settimeout(.2)
        try:
            self.sock.bind((self.node.bind_host,self.port))
        except OSError as error:
            self.node.endpoint_errors[self.port] = str(error)
            self.node.get_logger().error(f"UDP {self.port} unavailable: {error}")
            return
        while not self.stop.is_set():
            try: data, remote=self.sock.recvfrom(self.node.max_json_bytes+1)
            except socket.timeout: continue
            except OSError: break
            self.node.ingest(self.category, data, remote, time.monotonic_ns(), self.sock)
    def close(self):
        self.stop.set()
        if self.sock: self.sock.close()
        self.join(timeout=2)

class Video:
    def __init__(self,node,name,port,topic,capture_path):
        self.node,self.name,self.frames,self.width,self.height,self.error=node,name,0,0,0,None; self.pipeline=self.Gst=None
        self.metrics=RtpMetrics(node.rtp_payload_type); self.capture=RawRtpCapture(capture_path)
        self.pub=node.create_publisher(Image,topic,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
        try:
            import gi; gi.require_version("Gst","1.0")
            from gi.repository import Gst
            self.Gst=Gst; Gst.init(None)
            preview="queue leaky=downstream max-size-buffers=1 ! avdec_h264 ! videoconvert ! ximagesink sync=false" if node.preview_windows else "fakesink sync=false"
            desc=(f"udpsrc name={name}_source address={node.bind_host} port={port} buffer-size=4194304 caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload={node.rtp_payload_type} ! "
                  f"rtpjitterbuffer latency={node.rtp_latency_ms} drop-on-latency=true do-lost=true ! rtph264depay wait-for-keyframe=true request-keyframe=true ! h264parse config-interval=-1 ! tee name=t "
                  f"t. ! queue leaky=downstream max-size-buffers=1 ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! appsink name={name}_sink emit-signals=true max-buffers=1 drop=true sync=false t. ! {preview}")
            self.pipeline=Gst.parse_launch(desc); self.pipeline.get_by_name(f"{name}_sink").connect("new-sample",self.sample); self.pipeline.get_by_name(f"{name}_source").get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER,self.rtp_probe); self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e: self.error=str(e); node.get_logger().error(f"{name} video unavailable: {e}")
    def sample(self,sink):
        sample=sink.emit("pull-sample")
        if not sample: return self.Gst.FlowReturn.OK
        caps=sample.get_caps().get_structure(0); w,h=caps.get_value("width"),caps.get_value("height"); buf=sample.get_buffer(); ok,mapped=buf.map(self.Gst.MapFlags.READ)
        if ok:
            try:
                msg=Image(); msg.header.stamp=self.node.get_clock().now().to_msg(); msg.header.frame_id=f"dji_{self.name}_camera"; msg.width,msg.height,msg.encoding,msg.step,msg.data=w,h,"bgr8",w*3,bytes(mapped.data); self.pub.publish(msg); self.frames+=1; self.width,self.height=w,h
            finally: buf.unmap(mapped)
        return self.Gst.FlowReturn.OK
    def rtp_probe(self,pad,info):
        buffer=info.get_buffer()
        if buffer:
            data=buffer.extract_dup(0,buffer.get_size())
            if self.metrics.observe(data) is not None: self.capture.write(data)
        return self.Gst.PadProbeReturn.OK
    def close(self):
        if self.pipeline: self.pipeline.set_state(self.Gst.State.NULL)
        self.capture.close()

class EdgeBridge(Node):
    def __init__(self):
        super().__init__("dji_edge_driver")
        defaults={"bind_host":"0.0.0.0","telemetry_port":5500,"frame_metadata_port":5501,"clock_port":5502,"primary_rtp_port":5600,"fpv_rtp_port":5610,"max_json_bytes":1200,"rtp_payload_type":96,"rtp_latency_ms":20,"preview_windows":True,"publish_video":True,"capture_rtp":False,"evidence_dir":"evidence","android_clock_host":"","android_clock_port":5502,"clock_ping_interval_s":1.0,"primary_topic":"/dji/primary/image_raw","fpv_topic":"/dji/fpv/image_raw","navigation_topic":"/dji/navigation/state","flight_topic":"/dji/telemetry/flight","rtk_topic":"/dji/telemetry/rtk","gimbal_topic":"/dji/telemetry/gimbal","frame_metadata_topic":"/dji/telemetry/frame_metadata","diagnostics_topic":"/dji/diagnostics","transport_metrics_topic":"/dji/edge/transport_metrics"}
        for k,v in defaults.items(): self.declare_parameter(k,v)
        p=lambda k:self.get_parameter(k).value
        for k in ("bind_host","max_json_bytes","rtp_payload_type","rtp_latency_ms","preview_windows"): setattr(self,k,p(k))
        self.nav=self.create_publisher(NavigationState,p("navigation_topic"),10); self.out={"flight":self.create_publisher(String,p("flight_topic"),10),"rtk":self.create_publisher(String,p("rtk_topic"),10),"gimbal":self.create_publisher(String,p("gimbal_topic"),10),"frame_meta":self.create_publisher(String,p("frame_metadata_topic"),10),"video_au":self.create_publisher(String,p("frame_metadata_topic"),10)}; self.diag=self.create_publisher(DiagnosticArray,p("diagnostics_topic"),10); self.metrics=self.create_publisher(DiagnosticArray,p("transport_metrics_topic"),10)
        self.evidence=EvidenceWriter(p("evidence_dir")); self.clock_mapper=ClockMapper(); self.latest_state=LatestState(self.clock_mapper); self.seq=SequenceTracker(); self.accepted=self.rejected=0; self.endpoint_errors={}; self.clock_pongs=0; self.clock_sequence=0; self.clock_stop=threading.Event(); self.inputs=[Endpoint(self,"telemetry",p("telemetry_port")),Endpoint(self,"frame_metadata",p("frame_metadata_port")),Endpoint(self,"clock",p("clock_port"))]
        for e in self.inputs:e.start()
        evidence_dir=p("evidence_dir"); capture=p("capture_rtp"); self.videos=[] if not p("publish_video") else [Video(self,"primary",p("primary_rtp_port"),p("primary_topic"),f"{evidence_dir}/primary.rtpbin" if capture else None),Video(self,"fpv",p("fpv_rtp_port"),p("fpv_topic"),f"{evidence_dir}/fpv.rtpbin" if capture else None)]
        self.create_timer(1.0,self.publish_diagnostics)
        self.clock_host,self.clock_port,self.clock_interval=p("android_clock_host"),p("android_clock_port"),p("clock_ping_interval_s")
        self.clock_thread=threading.Thread(target=self.clock_loop,daemon=True); self.clock_thread.start()
    def ingest(self,category,data,remote,edge_ns,sock):
        try:
            packet=decode_json_packet(data,expected_version=1,max_bytes=self.max_json_bytes)
            raw,typ,session,stream,seq,mono=packet.raw,packet.packet_type,packet.session,packet.stream,packet.sequence,packet.android_mono_ns
            if category=="telemetry" and typ in FRAME_TYPES|CLOCK_TYPES: raise ProtocolError(f"{typ} is not telemetry")
            if category=="frame_metadata" and typ not in FRAME_TYPES: raise ProtocolError(f"{typ} is not frame metadata")
            if category=="clock" and typ not in CLOCK_TYPES: raise ProtocolError(f"{typ} is not a clock packet")
        except ProtocolError as error:
            self.rejected+=1; self.latest_state.reject(); self.evidence.write("protocol_errors",{"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","error":str(error),"raw":data.decode("utf-8",errors="replace")}); return
        if category=="clock":
            self.ingest_clock(packet,remote,edge_ns,sock); return
        result=self.seq.observe((session,typ,stream),seq); self.latest_state.update_packet(packet,edge_ns,remote,result)
        if not result.is_newest: return
        self.accepted+=1; record={"session":session,"type":typ,"stream":stream,"sequence":seq,"android_mono_ns":mono,"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","data":raw.get("data",raw)}; self.evidence.write("frame_metadata" if typ in FRAME_TYPES else "telemetry",record)
        if typ in self.out: msg=String(); msg.data=json.dumps(record,separators=(",",":"),sort_keys=True); self.out[typ].publish(msg)
        if typ in {"flight","rtk"}: self.publish_navigation()
    def ingest_clock(self,packet,remote,edge_ns,sock):
        try:
            raw=packet.raw
            if packet.packet_type=="clock_ping":
                response={"v":1,"type":"clock_pong","session":packet.session,"stream":packet.stream,"seq":packet.sequence,"t0_edge_send_mono_ns":raw["t0_edge_send_mono_ns"],"t1_android_rx_mono_ns":edge_ns,"t2_android_tx_mono_ns":time.monotonic_ns()}; sock.sendto(json.dumps(response,separators=(",",":")).encode(),remote); return
            t0,t1,t2=(int(raw[k]) for k in ("t0_edge_send_mono_ns","t1_android_rx_mono_ns","t2_android_tx_mono_ns"));
            sample=self.clock_mapper.add_exchange(t0,t1,t2,edge_ns); self.clock_pongs+=1; self.evidence.write("clock",{**sample.as_dict(),"estimate":self.clock_mapper.estimate(),"remote":f"{remote[0]}:{remote[1]}"})
        except (KeyError, TypeError, ValueError) as error: self.rejected+=1; self.latest_state.reject(); self.evidence.write("protocol_errors",{"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","error":f"invalid clock pong: {error}"})
    def clock_loop(self):
        if not self.clock_host: return
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        while not self.clock_stop.wait(self.clock_interval):
            self.clock_sequence+=1; now=time.monotonic_ns(); payload={"v":1,"type":"clock_ping","session":"edge-clock","stream":"clock","seq":self.clock_sequence,"t0_edge_send_mono_ns":now}
            try: sock.sendto(json.dumps(payload,separators=(",",":")).encode(),(self.clock_host,self.clock_port))
            except OSError as error: self.endpoint_errors["clock_pinger"]=str(error)
        sock.close()
    def publish_navigation(self):
        navigation=build_navigation(self.latest_state.snapshot())
        if navigation is None: return
        m=NavigationState(); m.header.stamp=self.get_clock().now().to_msg(); m.header.frame_id="wgs84"; m.latitude_deg=navigation["latitude_deg"]; m.longitude_deg=navigation["longitude_deg"]; m.altitude_m=navigation["altitude_m"]; m.heading_deg=navigation["heading_deg"]; m.velocity_north_m_s=navigation["velocity_north_m_s"]; m.velocity_east_m_s=navigation["velocity_east_m_s"]; m.velocity_down_m_s=navigation["velocity_down_m_s"]; m.position_source=NavigationState.POSITION_RTK if navigation["position_source"]=="rtk" else NavigationState.POSITION_GPS_FALLBACK; m.position_valid=navigation["position_valid"]; m.rtk_valid=navigation["rtk_valid"]; m.gps_signal_level=navigation["gps_signal_level"]; m.session=navigation["session"]; m.android_mono_ns=navigation["android_mono_ns"]; m.edge_receive_mono_ns=navigation["edge_receive_mono_ns"]; m.transport_age_s=float("nan") if navigation["transport_age_s"] is None else navigation["transport_age_s"]; self.nav.publish(m)
    def publish_diagnostics(self):
        evidence=self.evidence.health(); a=DiagnosticArray(); a.header.stamp=self.get_clock().now().to_msg(); s=DiagnosticStatus(name="dji_edge_driver/direct",level=DiagnosticStatus.OK if self.accepted and not self.endpoint_errors else DiagnosticStatus.WARN,message="direct Android ingress"); s.values=[KeyValue(key="post_network.accepted",value=str(self.accepted)),KeyValue(key="post_network.rejected",value=str(self.rejected)),KeyValue(key="clock.pongs",value=str(self.clock_pongs)),KeyValue(key="evidence.path",value=str(self.evidence.root)),KeyValue(key="evidence.write_errors",value=str(evidence["write_errors"])),KeyValue(key="evidence.dropped_records",value=str(evidence["dropped_records"])),KeyValue(key="legacy_http_polling",value="disabled")]
        for port,error in self.endpoint_errors.items(): s.values.append(KeyValue(key=f"udp.{port}.error",value=error))
        for v in self.videos:
            metrics=v.metrics.snapshot(); s.values += [KeyValue(key=f"{v.name}.frames",value=str(v.frames)),KeyValue(key=f"{v.name}.resolution",value=f"{v.width}x{v.height}"),KeyValue(key=f"{v.name}.error",value=v.error or ""),KeyValue(key=f"{v.name}.rtp_packets",value=str(metrics["packets_received"])),KeyValue(key=f"{v.name}.rtp_gaps",value=str(metrics["sequence_gaps"])),KeyValue(key=f"{v.name}.fps",value=str(metrics["estimated_fps"]))]
        a.status=[s]; self.diag.publish(a); self.metrics.publish(a)
    def destroy_node(self):
        self.clock_stop.set(); self.clock_thread.join(timeout=2)
        for e in self.inputs:e.close()
        for v in self.videos:v.close()
        self.evidence.close()
        return super().destroy_node()

def main():
    rclpy.init(); n=EdgeBridge()
    try:rclpy.spin(n)
    except (KeyboardInterrupt, ExternalShutdownException):pass
    finally:
        n.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
if __name__=="__main__":main()
