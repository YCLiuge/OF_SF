"""Compare names of dedicated native request handlers in two source checkouts."""
import argparse
import ast
import json
from pathlib import Path

def handlers(folder):
    result=set()
    for path in folder.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_bytes(),filename=str(path))):
            if not isinstance(node,ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Name)
                        and decorator.func.id=="packet_handler" and decorator.args
                        and isinstance(decorator.args[0],ast.Attribute)):
                    result.add(decorator.args[0].attr)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream",type=Path,help="Path to an existing byzp/of-ps source checkout")
    parser.add_argument("--server",type=Path,default=Path(__file__).resolve().parents[1]/"OverField-Server")
    args=parser.parse_args()
    upstream=handlers(args.upstream/"handlers")
    local=handlers(args.server/"app/upstream/handlers")
    if not upstream or not local:
        parser.error("Expected two populated source trees")
    result={"upstreamDedicated":len(upstream),"localDedicated":len(local),
            "sharedDedicated":len(local & upstream),"upstreamOnly":sorted(upstream-local),
            "localOnly":sorted(local-upstream)}
    print(json.dumps(result,indent=2))
if __name__=="__main__":
    main()
