"""Fixed local fixture executable; never accepts shell, network or arbitrary code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def exclusive(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_bytes())
    if config["worker_sha256"] != sha(Path(__file__).read_bytes()):
        return 42
    content = Path(config["input_path"]).read_bytes()
    if sha(content) != config["input_sha256"]:
        return 43
    params = config["params"]
    start = time.monotonic_ns()
    limit = params.get("max_input_bytes", 1048576)
    receipt = {key: config[key] for key in ("operation_id", "script_sha256", "input_sha256", "worker_sha256")}
    receipt.update({"schema_version": 1, "process_started_mono_ns": start,
                    "observed_input_bytes": len(content), "output_sha256": None,
                    "python_version": platform.python_version()})
    if len(content) > limit:
        receipt.update(status="blocked", exit_code=12, reason="max_input_bytes",
                       limit={"name": "max_input_bytes", "value": limit, "source": "adapter_observed"})
    else:
        time.sleep(params.get("delay_ms", 0) / 1000)
        noise = params.get("noise_bytes", 0)
        if noise:
            sys.stdout.buffer.write(b"L" * noise)
            sys.stdout.flush()
        fault = params.get("fixture_fault")
        if fault == "exit_nonzero":
            receipt.update(status="failed", exit_code=13, reason="fixture_exit_nonzero")
        else:
            text = content.decode("utf-8")
            output = (text.upper() if params["transform"] == "uppercase" else text.replace("\r\n", "\n").replace("\r", "\n")).encode()
            if fault == "wrong_result":
                output += b"WRONG"
            exclusive("result.txt", output)
            receipt.update(status="completed", exit_code=0, reason=None, output_sha256=sha(output))
    receipt["process_finished_mono_ns"] = time.monotonic_ns()
    exclusive("receipt.json", json.dumps(receipt, sort_keys=True).encode())
    directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(directory)
    os.close(directory)
    return receipt["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
