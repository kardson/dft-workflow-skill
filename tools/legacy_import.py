"""Import selected historical outputs for limited offline parsing, without invented approval."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

FILES = ("POSCAR", "CONTCAR", "INCAR", "KPOINTS", "OUTCAR", "OSZICAR", "vasprun.xml")
BACKEND = """import importlib,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
module='result_analysis' if (Path(sys.argv[1])/'result_analysis.py').is_file() else 'vasp_result_analysis'
summary=importlib.import_module(module).analyze(sys.argv[2],sys.argv[3])
assert isinstance(summary,dict)
print('LEGACY_RESULT:'+json.dumps(summary))
"""

def import_outputs(source, output, workspace, tool_root, python, max_bytes=1000000000):
    root = Path(workspace).resolve(strict=True)
    source = Path(source).resolve(strict=True); source.relative_to(root)
    output = Path(output).resolve(); output.relative_to(root)
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Keep import and original source separate")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Positive byte budget required")
    selected = []; total = 0
    for name in FILES:
        path = source / name
        if path.is_symlink(): raise ValueError("Linked source refused")
        if path.exists():
            if not path.is_file(): raise ValueError("Regular source required")
            total += path.stat().st_size; selected.append(path)
    if not selected or total > max_bytes:
        raise ValueError("No supported files or byte budget exceeded")
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"; raw_dir.mkdir()
    identities = []
    for path in selected:
        before = path.stat()
        with path.open("rb") as stream:
            raw = stream.read(max_bytes - sum(row['bytes'] for row in identities) + 1)
        after = path.stat()
        if path.is_symlink() or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source changed during snapshot")
        if len(raw) + sum(row['bytes'] for row in identities) > max_bytes:
            raise ValueError("Source grew beyond byte budget")
        (raw_dir / path.name).write_bytes(raw)
        identities.append({"name": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    result = {"schema": "dft-legacy-import/v1", "source": str(source), "files": identities,
              "approval": "NOT_AVAILABLE", "controlled_workflow": False,
              "scientific_acceptance": "NOT_EVALUATED", "backend_status": "FAILED",
              "limitations": ["Historical inputs and execution provenance not approved or fully verified", "Parsed observations cannot establish comparable energies or scientific acceptance"]}
    try:
        child = subprocess.run([str(python), "-B", "-c", BACKEND, str(Path(tool_root).resolve()), str(raw_dir), str(output / "parser")], capture_output=True, text=True, timeout=180, check=True)
        payload = next(line[len("LEGACY_RESULT:"):] for line in child.stdout.splitlines() if line.startswith("LEGACY_RESULT:"))
        summary = json.loads(payload)
        if not isinstance(summary, dict) or summary.get("schema") != "vasp-practical-analysis/v1":
            raise ValueError("Invalid parser record")
        saved = json.loads((output / "parser" / "summary.json").read_text(encoding="utf-8"))
        if saved != summary:
            raise ValueError("Parser summary differs from backend result")
        for row in identities:
            if hashlib.sha256((raw_dir / row["name"]).read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Imported snapshot changed during analysis")
        result.update(backend_status="SUCCEEDED", observations=summary)
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as error:
        result["backend_error"] = type(error).__name__
    (output / "import.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 旧计算导入：有限分析", "", "后端：" + result["backend_status"],
             "历史批准与完整执行来源：未核实；不属于已批准受控流程。",
             "科学接受：未作判断。", ""]
    if result["backend_status"] == "SUCCEEDED":
        lines += ["解析状态：" + str(result["observations"].get("status")),
                  "数据与解析错误见 import.json 和 parser/summary.json。",
                  "能量可比性、实际方法与收敛需进一步核对，不据此直接排列稳定性。"]
    else:
        lines += ["解析未成功完成，不能交付为已解析结果。", "下一步：核对解析环境与实际输出；必要时补充材料。"]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "workspace", "tool-root", "python"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--max-bytes", type=int, default=1000000000)
    args = parser.parse_args()
    value = import_outputs(args.source, args.output, args.workspace, args.tool_root, args.python, args.max_bytes)
    return 0 if value["backend_status"] == "SUCCEEDED" else 1

if __name__ == "__main__":
    raise SystemExit(main())
