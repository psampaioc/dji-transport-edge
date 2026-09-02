import tempfile
from pathlib import Path
import unittest

from dji_edge_receiver.cli import rebase_evidence_directory, resolve_config_path
from dji_edge_receiver import cli
from dji_edge_receiver.config import load_config


class ConfigTest(unittest.TestCase):
    def load(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_default_stream_is_primary(self):
        config = self.load("")
        self.assertEqual(config.video_streams[0].name, "primary")
        self.assertIn("fakesink", config.video_streams[0].sink)

    def test_rejects_noncanonical_stream_name(self):
        with self.assertRaisesRegex(ValueError, "primary.*secondary"):
            self.load("[[video_streams]]\nname='fpv'\n")

    def test_rejects_local_udp_port_collision(self):
        with self.assertRaisesRegex(ValueError, "ports must be unique"):
            self.load("[network]\ntelemetry_port=5500\nframe_metadata_port=5500\n")

    def test_rejects_invalid_interval_and_payload_size(self):
        with self.assertRaisesRegex(ValueError, "clock_ping_interval_s"):
            self.load("[network]\nclock_ping_interval_s=0\n")
        with self.assertRaisesRegex(ValueError, "max_datagram_bytes"):
            self.load("[network]\nmax_datagram_bytes=100\n")

    def test_dashboard_defaults_to_loopback_and_rejects_other_host(self):
        self.assertEqual(self.load("").dashboard.host, "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "dashboard.host"):
            self.load("[dashboard]\nhost='0.0.0.0'\n")

    def test_resolves_explicit_and_local_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "config.toml"
            local.write_text("", encoding="utf-8")
            self.assertEqual(resolve_config_path(None, cwd=root), local)
            self.assertEqual(resolve_config_path(str(local), cwd=Path("/")), local)

    def test_copy_rebases_relative_evidence_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "config.toml"
            source.write_text("[storage]\nevidence_dir = './evidence'\n", encoding="utf-8")
            copied = rebase_evidence_directory(source.read_text(encoding="utf-8"), source)
            self.assertIn(f'evidence_dir = "{source.parent / "evidence"}"', copied)

    def test_no_subcommand_defaults_to_dashboard(self):
        captured = []
        original = cli._parser
        try:
            class Parser:
                def parse_args(self, args):
                    captured.extend(args)
                    raise RuntimeError("stop after normalized arguments")
            cli._parser = lambda: Parser()
            with self.assertRaisesRegex(RuntimeError, "stop"):
                cli.main([])
        finally:
            cli._parser = original
        self.assertEqual(captured, ["dashboard"])


if __name__ == "__main__":
    unittest.main()
