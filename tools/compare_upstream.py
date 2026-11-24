"""Compare native request registrations; optionally cross-check a full loopback audit."""
import argparse
import ast
import json
from pathlib import Path


FACTORIES = {"private_request": "handler", "private_badge_tree": "handler",
             "private_appearance": "handler", "private_campfires": "campfire_handler"}


def handlers(folder):
    result = set()
    for path in folder.glob("*.py"):
        module = ast.parse(path.read_bytes(), filename=str(path))
        factories = {alias.asname or alias.name for node in module.body
                     if isinstance(node, ast.ImportFrom) and node.module in FACTORIES
                     for alias in node.names if alias.name == FACTORIES[node.module]}
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
                            and decorator.func.id == "packet_handler" and decorator.args
                            and isinstance(decorator.args[0], ast.Attribute)):
                        result.add(decorator.args[0].attr)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "Handler" for t in node.targets):
                call = node.value
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in factories
                        and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    result.add(call.args[0].value + "Req")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="Path to an existing byzp/of-ps source checkout")
    parser.add_argument("--server", type=Path, default=Path(__file__).resolve().parents[1] / "OverField-Server")
    parser.add_argument("--audit", type=Path, help="full JSON report from audit_requests.py for this server build")
    args = parser.parse_args()
    upstream = handlers(args.upstream / "handlers")
    local = handlers(args.server / "app/upstream/handlers")
    if not upstream or not local:
        parser.error("Expected two populated source trees")
    result = {"upstreamDedicated": len(upstream), "localDedicated": len(local),
              "sharedDedicated": len(local & upstream), "upstreamOnly": sorted(upstream - local),
              "localOnly": sorted(local - upstream)}
    if args.audit:
        report = json.loads(args.audit.read_bytes())
        rows = {row["request"]: row["runtime_route"] for row in report.get("requests", [])}
        if not rows or len(rows) != report["counts"]["native_requests"]:
            parser.error("Expected a full native request audit")
        registered = {name for name, route in rows.items() if route in ("registered", "registered_fixed_reply")}
        if local & rows.keys() != registered:
            parser.error("Audited native registrations differ from this server checkout")
        result["upstreamWithLocalFallback"] = sorted(name for name in upstream if rows.get(name) == "explicit_unsupported")
        result["upstreamOutsideClientCatalog"] = sorted(upstream - rows.keys())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
