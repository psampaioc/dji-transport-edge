#!/usr/bin/env python3
"""Direct Android UDP/RTP ingestion; no HTTP polling or loopback relay."""
import json, math, socket, threading, time
from pathlib import Path
from queue import Full, Queue
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from dji_edge_bridge.msg import NavigationState
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import Image
from std_msgs.msg import String
from dji_edge_receiver.protocol import FRAME_TYPES, ProtocolError, decode_json_packet

def unwrap(fields, key, default=None):
    v = fields.get(key, default)
    return v.get("value", default) if isinstance(v, dict) else v

def finite(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

class Endpoint(threading.Thread):
    def __init__(self, node, port, callback):
        super().__init__(daemon=True); self.node, self.port, self.callback, self.stop = node, port, callback, threading.Event(); self.sock = None
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
            self.callback(data, remote, time.monotonic_ns())
    def close(self):
        self.stop.set()
        if self.sock: self.sock.close()
        self.join(timeout=2)

class Evidence:
    """Small append-only NDJSON writer; never blocks the UDP receive path."""
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True); self.lock=threading.Lock(); self.dropped=self.errors=0; self.queue=Queue(maxsize=16384); self.stop=threading.Event(); self.worker=threading.Thread(target=self.run,daemon=True); self.worker.start()
    def run(self):
        while not self.stop.is_set():
            try: category,record=self.queue.get(timeout=.2)
            except Exception: continue
            try:
                with (self.root/f"{category}.ndjson").open("a",encoding="utf-8") as f: f.write(json.dumps(record,separators=(",",":"),sort_keys=True)+"\n")
            except OSError:
                with self.lock:self.errors+=1
            finally:self.queue.task_done()
    def write(self, category, record):
        try:self.queue.put_nowait((category,record))
        except Full:
            with self.lock:self.dropped+=1

class Video:
    def __init__(self,node,name,port,topic):
        self.node,self.name,self.frames,self.width,self.height,self.error=node,name,0,0,0,None; self.pipeline=self.Gst=None
        self.pub=node.create_publisher(Image,topic,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT))
        try:
            import gi; gi.require_version("Gst","1.0")
            from gi.repository import Gst
            self.Gst=Gst; Gst.init(None)
            preview="queue leaky=downstream max-size-buffers=1 ! avdec_h264 ! videoconvert ! ximagesink sync=false" if node.preview_windows else "fakesink sync=false"
            desc=(f"udpsrc address={node.bind_host} port={port} buffer-size=4194304 caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload={node.rtp_payload_type} ! "
                  f"rtpjitterbuffer latency={node.rtp_latency_ms} drop-on-latency=true do-lost=true ! rtph264depay wait-for-keyframe=true request-keyframe=true ! h264parse config-interval=-1 ! tee name=t "
                  f"t. ! queue leaky=downstream max-size-buffers=1 ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! appsink name={name}_sink emit-signals=true max-buffers=1 drop=true sync=false t. ! {preview}")
            self.pipeline=Gst.parse_launch(desc); self.pipeline.get_by_name(f"{name}_sink").connect("new-sample",self.sample); self.pipeline.set_state(Gst.State.PLAYING)
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
    def close(self):
        if self.pipeline: self.pipeline.set_state(self.Gst.State.NULL)

class EdgeBridge(Node):
    def __init__(self):
        super().__init__("dji_edge_bridge")
        defaults={"bind_host":"0.0.0.0","telemetry_port":5500,"frame_metadata_port":5501,"clock_port":5502,"primary_rtp_port":5600,"fpv_rtp_port":5610,"max_json_bytes":1200,"rtp_payload_type":96,"rtp_latency_ms":20,"preview_windows":True,"publish_video":True,"evidence_dir":"evidence","primary_topic":"/dji/primary/image_raw","fpv_topic":"/dji/fpv/image_raw","navigation_topic":"/dji/navigation/state","flight_topic":"/dji/telemetry/flight","rtk_topic":"/dji/telemetry/rtk","gimbal_topic":"/dji/telemetry/gimbal","frame_metadata_topic":"/dji/telemetry/frame_metadata","diagnostics_topic":"/dji/diagnostics"}
        for k,v in defaults.items(): self.declare_parameter(k,v)
        p=lambda k:self.get_parameter(k).value
        for k in ("bind_host","max_json_bytes","rtp_payload_type","rtp_latency_ms","preview_windows"): setattr(self,k,p(k))
        self.nav=self.create_publisher(NavigationState,p("navigation_topic"),10); self.out={"flight":self.create_publisher(String,p("flight_topic"),10),"rtk":self.create_publisher(String,p("rtk_topic"),10),"gimbal":self.create_publisher(String,p("gimbal_topic"),10),"frame_meta":self.create_publisher(String,p("frame_metadata_topic"),10),"video_au":self.create_publisher(String,p("frame_metadata_topic"),10)}; self.diag=self.create_publisher(DiagnosticArray,p("diagnostics_topic"),10)
        self.evidence=Evidence(p("evidence_dir")); self.latest={}; self.seq={}; self.accepted=self.rejected=0; self.endpoint_errors={}; self.clock_pongs=0; self.inputs=[Endpoint(self,p("telemetry_port"),self.ingest),Endpoint(self,p("frame_metadata_port"),self.ingest),Endpoint(self,p("clock_port"),self.ingest_clock)]
        for e in self.inputs:e.start()
        self.videos=[] if not p("publish_video") else [Video(self,"primary",p("primary_rtp_port"),p("primary_topic")),Video(self,"fpv",p("fpv_rtp_port"),p("fpv_topic"))]
        self.create_timer(1.0,self.publish_diagnostics)
    def ingest(self,data,remote,edge_ns):
        try:
            packet=decode_json_packet(data,expected_version=1,max_bytes=self.max_json_bytes)
            raw,typ,session,stream,seq,mono=packet.raw,packet.packet_type,packet.session,packet.stream,packet.sequence,packet.android_mono_ns
        except ProtocolError:
            self.rejected+=1; self.evidence.write("protocol_errors",{"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","raw":data.decode("utf-8",errors="replace")}); return
        key=(session,typ,stream)
        if seq<=self.seq.get(key,-1): return
        self.seq[key]=seq; self.accepted+=1; record={"session":session,"type":typ,"stream":stream,"sequence":seq,"android_mono_ns":mono,"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","data":raw.get("data",raw)}; self.evidence.write("frame_metadata" if typ in {"frame_meta","video_au"} else "telemetry",record); self.latest[typ if typ!="gimbal" else f"gimbal:{stream}"]=record
        if typ in self.out: msg=String(); msg.data=json.dumps(record,separators=(",",":"),sort_keys=True); self.out[typ].publish(msg)
        if typ in {"flight","rtk"}: self.publish_navigation()
    def ingest_clock(self,data,remote,edge_ns):
        try:
            raw=json.loads(data.decode());
            if raw.get("type")!="clock_pong": raise ValueError()
            t0,t1,t2=(int(raw[k]) for k in ("t0_edge_send_mono_ns","t1_android_rx_mono_ns","t2_android_tx_mono_ns"));
            if min(t0,t1,t2)<=0 or t2<t1 or edge_ns<t0: raise ValueError()
            self.clock_pongs+=1; self.evidence.write("clock",{"edge_receive_mono_ns":edge_ns,"remote":f"{remote[0]}:{remote[1]}","t0_edge_send_mono_ns":t0,"t1_android_rx_mono_ns":t1,"t2_android_tx_mono_ns":t2,"round_trip_ns":(edge_ns-t0)-(t2-t1)})
        except Exception: self.rejected+=1
    def publish_navigation(self):
        flight=self.latest.get("flight",{}); rtk=self.latest.get("rtk",{}); ff=flight.get("data",{}).get("fields",{}); rf=rtk.get("data",{}).get("fields",{}); rlat,rlon=unwrap(rf,"fusion.latitude_deg"),unwrap(rf,"fusion.longitude_deg"); rv=bool(unwrap(rf,"is_being_used",False)) and finite(rlat) and finite(rlon); lat,lon=(rlat,rlon) if rv else (unwrap(ff,"aircraft.latitude_deg"),unwrap(ff,"aircraft.longitude_deg"))
        if not(finite(lat) and finite(lon)): return
        m=NavigationState(); m.header.stamp=self.get_clock().now().to_msg(); m.header.frame_id="wgs84"; m.latitude_deg,m.longitude_deg=float(lat),float(lon); m.altitude_m=float(unwrap(ff,"aircraft.altitude_m",0) or 0); m.heading_deg=float(unwrap(ff,"heading_deg",0) or 0); m.velocity_north_m_s=float(unwrap(ff,"velocity.north_m_s",0) or 0); m.velocity_east_m_s=float(unwrap(ff,"velocity.east_m_s",0) or 0); m.velocity_down_m_s=float(unwrap(ff,"velocity.down_m_s",0) or 0); m.position_source=NavigationState.POSITION_RTK if rv else NavigationState.POSITION_GPS_FALLBACK; m.position_valid,m.rtk_valid=True,rv; m.gps_signal_level=int(unwrap(ff,"gps.signal_level",0) or 0); source=rtk if rv else flight; m.session=source.get("session",""); m.android_mono_ns=int(source.get("android_mono_ns",0)); m.edge_receive_mono_ns=int(source.get("edge_receive_mono_ns",0)); m.transport_age_s=float("nan"); self.nav.publish(m)
    def publish_diagnostics(self):
        a=DiagnosticArray(); a.header.stamp=self.get_clock().now().to_msg(); s=DiagnosticStatus(name="dji_edge_bridge/direct",level=DiagnosticStatus.OK if self.accepted and not self.endpoint_errors else DiagnosticStatus.WARN,message="direct Android ingress"); s.values=[KeyValue(key="post_network.accepted",value=str(self.accepted)),KeyValue(key="post_network.rejected",value=str(self.rejected)),KeyValue(key="clock.pongs",value=str(self.clock_pongs)),KeyValue(key="evidence.path",value=str(self.evidence.root)),KeyValue(key="evidence.write_errors",value=str(self.evidence.errors)),KeyValue(key="evidence.dropped_records",value=str(self.evidence.dropped)),KeyValue(key="legacy_http_polling",value="disabled")]
        for port,error in self.endpoint_errors.items(): s.values.append(KeyValue(key=f"udp.{port}.error",value=error))
        for v in self.videos:s.values += [KeyValue(key=f"{v.name}.frames",value=str(v.frames)),KeyValue(key=f"{v.name}.resolution",value=f"{v.width}x{v.height}"),KeyValue(key=f"{v.name}.error",value=v.error or "")]
        a.status=[s]; self.diag.publish(a)
    def destroy_node(self):
        for e in self.inputs:e.close()
        for v in self.videos:v.close()
        return super().destroy_node()

def main():
    rclpy.init(); n=EdgeBridge()
    try:rclpy.spin(n)
    except (KeyboardInterrupt, ExternalShutdownException):pass
    finally:
        n.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
if __name__=="__main__":main()
