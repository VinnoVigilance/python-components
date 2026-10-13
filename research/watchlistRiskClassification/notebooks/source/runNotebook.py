"""Convert a percent-format .py into .ipynb and execute it with jupyter_client (no nbconvert needed)."""
import json
import re
import sys
from pathlib import Path

from jupyter_client.manager import start_new_kernel


def parse(src):
    cells = []
    for block in re.split(r"(?m)^# %%", src)[1:]:
        head, _, body = block.partition("\n")
        if "[markdown]" in head:
            text = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in body.strip("\n").split("\n"))
            cells.append({"cell_type": "markdown", "metadata": {}, "source": text})
        else:
            cells.append({"cell_type": "code", "metadata": {}, "source": body.strip("\n"), "outputs": [], "execution_count": None})
    return cells


def run(cells, cwd):
    km, kc = start_new_kernel(kernel_name="python3", cwd=cwd)
    kc.execute("%matplotlib inline", silent=True); kc.get_shell_msg(timeout=60)
    n = 0
    try:
        for c in cells:
            if c["cell_type"] != "code":
                continue
            n += 1
            mid = kc.execute(c["source"])
            outs = []
            while True:
                msg = kc.get_iopub_msg(timeout=7200)
                if msg["parent_header"].get("msg_id") != mid:
                    continue
                t, ct = msg["msg_type"], msg["content"]
                if t == "status" and ct["execution_state"] == "idle":
                    break
                if t == "stream":
                    if outs and outs[-1]["output_type"] == "stream" and outs[-1]["name"] == ct["name"]:
                        outs[-1]["text"] += ct["text"]
                    else:
                        outs.append({"output_type": "stream", "name": ct["name"], "text": ct["text"]})
                elif t in ("execute_result", "display_data"):
                    o = {"output_type": t, "data": ct["data"], "metadata": ct.get("metadata", {})}
                    if t == "execute_result":
                        o["execution_count"] = n
                    outs.append(o)
                elif t == "error":
                    outs.append({"output_type": "error", "ename": ct["ename"], "evalue": ct["evalue"], "traceback": ct["traceback"]})
                    print("ERROR in cell", n, ct["ename"], ct["evalue"], file=sys.stderr)
            c["outputs"], c["execution_count"] = outs, n
            if any(o["output_type"] == "error" for o in outs):
                break
    finally:
        kc.stop_channels(); km.shutdown_kernel(now=True)


def main(src, out):
    cells = parse(Path(src).read_text(encoding="utf-8"))
    run(cells, str(Path(out).resolve().parent))
    nb = {"cells": cells, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
          "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    for i, c in enumerate(cells):
        c["id"] = f"c{i:03d}"
    Path(out).write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
